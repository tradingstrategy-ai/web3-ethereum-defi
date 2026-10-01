"""Editable, source-bound monthly report tweet campaigns.

Ghost supplies the final editorial content. Markdown supplies the exact tweet
wording. Only a reviewed, immutable compilation can be consumed by the runner.
See ``README-tweets.md`` and https://docs.ghost.org/admin-api/posts/overview.
"""

import dataclasses
import datetime
import hashlib
import io
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from PIL import Image
from tqdm_loggable.auto import tqdm
from twitter_text import parse_tweet

from eth_defi.compat import native_datetime_utc_now
from eth_defi.vault_report.ghost import BLOG_URL, GhostAdminClient

logger = logging.getLogger(__name__)

#: Ordinary standalone tweets, spaced half a day apart.
INTERVAL_SECONDS = 12 * 3600
#: Images can be fetched only from explicitly trusted Ghost media hosts.
MEDIA_HOSTS = frozenset({"storage.ghost.io", "static.ghost.org"})
#: Generated charts; other large editorial images also receive draft entries.
CHART_KEYS = frozenset({"new_performance", "lending_performance", "rwa_performance", "perp_dex_performance", "perp_dex_sharpe_performance", "amm_performance", "by_chain_best", "tokenised_funds_performance", "protocol_yields", "protocol_high_yields", "chain_yields", "risk_return", "protocol_tvl", "chain_tvl", "fund_nav", "tvl_changes", "chain_tvl_changes"})
HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
MENTION = re.compile(r"(?:(?<![A-Za-z0-9_!#$%&*@＠])|(?<=RT)|(?<=rt))[＠@]([A-Za-z0-9_]{1,20})")


def digest(value: object) -> str:
    """Hash a canonical JSON value.

    Stable serialisation binds compiled payloads to their evidence.

    :param value: JSON-compatible data.
    :return: SHA-256 hex digest.
    """
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path: Path) -> str:
    """Hash frozen file bytes.

    The same check is used for source, review and outgoing attachments.

    :param path: File to read.
    :return: SHA-256 hex digest.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    """Atomically save JSON without partial state.

    Temporary files are kept beside the destination for atomic replacement.

    :param path: Destination.
    :param value: JSON-compatible contents.
    """
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def parse_utc(value: str) -> datetime.datetime:
    """Parse an explicit UTC schedule time.

    Offset times are normalised to the repository's naive UTC convention.

    :param value: ISO timestamp with timezone.
    :return: Naive UTC datetime.
    """
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Schedule timestamps need an explicit UTC offset or Z")
    return parsed.astimezone(datetime.UTC).replace(tzinfo=None)


def normalise_handle(value: str) -> str:
    """Normalise a repository social handle.

    Full official profile URLs and bare usernames share one representation.

    :param value: Bare handle or X/Twitter URL.
    :return: Handle without the at sign.
    """
    if value.startswith("https://"):
        parsed = urlparse(value)
        if parsed.hostname not in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}:
            raise ValueError("A handle URL must be an X profile")
        value = parsed.path.strip("/")
    value = value.removeprefix("@")
    if not HANDLE.fullmatch(value):
        raise ValueError("Invalid X username")
    return value


def load_social_handles() -> dict[str, dict]:
    """Resolve social identities from repository feeder metadata.

    Canonical aliases inherit the highest-priority feeder's handle, including
    protocol/curator aliases to stablecoin issuers. Chain identities use the
    explicitly sourced local registry.

    :return: Slug or chain key to identity and official-source evidence.
    """
    base = Path(__file__).parents[1] / "data" / "feeds"
    records = {}
    for directory in ("curators", "protocols", "stablecoins"):
        for path in sorted((base / directory).glob("*.yaml")):
            item = yaml.safe_load(path.read_text())
            records[item["feeder-id"]] = item
    result = {}
    for key, item in records.items():
        canonical = item
        seen = {key}
        while canonical.get("canonical-feeder-id"):
            alias = canonical["canonical-feeder-id"]
            if alias in seen or alias not in records:
                raise ValueError(f"Unresolved social alias: {key}")
            seen.add(alias)
            canonical = records[alias]
        handle = normalise_handle(canonical["twitter"]) if canonical.get("twitter") else None
        result[key] = {"name": item["name"], "handle": handle, "profile": f"https://x.com/{handle}" if handle else None, "official_source": canonical.get("website"), "verified_at": None, "user_id": None}
    registry = Path(__file__).with_name("chain-socials.json")
    if registry.exists():
        for chain_id, social in json.loads(registry.read_text()).items():
            result[f"chain:{chain_id}"] = social
            for name in social["aliases"]:
                result[f"chain:{name}"] = social
    return result


def fetch_image(url: str, allowed_hosts: frozenset[str] = MEDIA_HOSTS) -> bytes:
    """Fetch and validate a bounded Ghost image without following unsafe redirects.

    URLs carrying credentials or query parameters are never accepted or logged.

    :param url: Trusted HTTPS image URL.
    :param allowed_hosts: Explicit media-host allow-list.
    :return: Verified PNG or JPEG bytes.
    """
    for _ in range(4):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in allowed_hosts or parsed.username or parsed.password or parsed.query or parsed.port not in (None, 443):
            raise ValueError("Untrusted image URL; configure MEDIA_HOSTS explicitly")
        try:
            with requests.get(url, timeout=30, allow_redirects=False, stream=True) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    url = response.headers["Location"]
                    continue
                if response.status_code != 200:
                    raise ValueError(f"Chart image download failed: HTTP {response.status_code}")
                parts, total = [], 0
                for chunk in response.iter_content(65536):
                    total += len(chunk)
                    if total > 20_000_000:
                        raise ValueError("Chart image exceeds the download limit")
                    parts.append(chunk)
                data = b"".join(parts)
        except requests.RequestException as exc:
            raise ValueError(f"Chart download failed: {type(exc).__name__}") from None
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG"} or image.width * image.height > 30_000_000:
                raise ValueError("Unsupported chart image")
            image.verify()
        return data
    raise ValueError("Too many image redirects")


def extract_charts(post_html: str, evidence_urls: dict[str, str] | None = None) -> tuple[list[dict], list[dict]]:
    """Enumerate actual report images and their surrounding editorial context.

    Tables and podcasts carry decorative images; large editorial additions
    remain included even without a generated chart filename.

    :param post_html: Final Ghost HTML.
    :param evidence_urls: Optional upload-response URL to logical chart key.
    :return: Ordered charts and ignored-image reasons.
    """
    soup = BeautifulSoup(post_html, "html.parser")
    charts, ignored, keys = [], [], set()
    for image in soup.find_all("img"):
        heading = image.find_previous(["h2", "h3"])
        title = heading.get_text(" ", strip=True) if heading else "Editorial chart"
        url = image.get("src", "")
        if image.find_parent("table") or "podcast" in title.lower() or not url:
            ignored.append({"reason": "table, podcast or missing image URL", "heading": title})
            continue
        filename = Path(urlparse(url).path).stem
        original_filename = re.sub(r"-\d+$", "", filename)
        if filename.startswith(("hero", "logo")):
            ignored.append({"reason": "hero or logo", "heading": title})
            continue
        key = (evidence_urls or {}).get(url) or (filename if filename in CHART_KEYS else original_filename if original_filename in CHART_KEYS else re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_"))
        if key in keys:
            raise ValueError(f"Ambiguous duplicate chart identity: {key}")
        keys.add(key)
        context = []
        if heading:
            for sibling in heading.next_siblings:
                if getattr(sibling, "name", None) in {"h2", "h3"}:
                    break
                if getattr(sibling, "name", None) in {"table", "figure"}:
                    context.append(sibling.get_text(" ", strip=True))
        charts.append({"key": key, "heading": title, "url": url, "alt": " ".join((image.get("alt") or title).split()), "context": " ".join(" ".join(context).split())})
    return charts, ignored


def source_fields(source: dict) -> dict:
    """Select campaign-relevant fields for a stable preflight digest.

    Whitespace/editor re-saves are ignored while winner-bearing context is bound.

    :param source: Read-back source snapshot.
    :return: Fields that invalidate an approved campaign.
    """
    return {"id": source["id"], "slug": source["slug"], "title": source["title"], "url": source["url"], "charts": [{k: c[k] for k in ("key", "heading", "url", "context", "sha256")} for c in source["charts"]]}


def prepare_campaign(client: GhostAdminClient, slug: str, output_root: Path, evidence_path: Path | None = None, previous_plan: Path | None = None, allowed_hosts: frozenset[str] = MEDIA_HOSTS) -> Path:
    """Read the edited post and create a fresh editable campaign revision.

    Neither Ghost nor existing Markdown is modified. Evidence must match the
    served image hash, so stale bundle data cannot choose new winners.

    :param client: Authenticated Ghost reader.
    :param slug: Explicit report slug.
    :param output_root: Parent for post/revision directories.
    :param evidence_path: Optional chart evidence JSON.
    :param previous_plan: Previous revision whose unchanged edits carry forward.
    :param allowed_hosts: Trusted image hosts.
    :return: New Markdown plan path.
    """
    post = client.fetch_post_by_slug(slug)
    if not post or not post.html:
        raise ValueError("The selected Ghost report is missing or empty")
    month_match = re.search(r"([A-Za-z]+) (\d{4})$", post.title)
    if not month_match:
        raise ValueError("Report title must end in Month YYYY")
    month = datetime.datetime.strptime(month_match.group(), "%B %Y")
    root = output_root / post.id / native_datetime_utc_now().strftime("%Y%m%dT%H%M%S%f")
    root.mkdir(parents=True, exist_ok=False)
    (root / "charts").mkdir()
    supplied = json.loads(evidence_path.read_text()) if evidence_path else {}
    charts, ignored = extract_charts(post.html, {record["url"]: key for key, record in supplied.items() if record.get("url")})
    old_entries, old_evidence, old_charts = {}, {}, {}
    if previous_plan:
        previous = read_plan(previous_plan)
        old_entries = {entry.metadata["chart_key"]: entry for entry in previous.entries}
        old_evidence = json.loads(previous_plan.with_name("chart-evidence.json").read_text())
        old_charts = {chart["key"]: chart for chart in json.loads(previous_plan.with_name("source-post.json").read_text())["charts"]}
    evidence = {}
    for chart in tqdm(charts, desc="Freezing blog charts"):
        data = fetch_image(chart["url"], allowed_hosts)
        with Image.open(io.BytesIO(data)) as im:
            chart.update(width=im.width, height=im.height, format=im.format)
        chart["sha256"] = hashlib.sha256(data).hexdigest()
        chart["path"] = f"charts/{chart['key']}.{'png' if chart['format'] == 'PNG' else 'jpg'}"
        (root / chart["path"]).write_bytes(data)
        record = supplied.get(chart["key"], {"metric": "Needs chart-specific evidence", "winners": [], "issues": ["Identify the chart winners and verified handles"]})
        if "entities" in record and "winners" not in record:
            record = derive_winner_evidence(chart["key"], record, load_social_handles())
        if record.get("sha256") != chart["sha256"]:
            record = {"metric": record.get("metric", "Needs chart-specific evidence"), "winners": [], "issues": ["Evidence is not bound to these served image bytes"]}
        previous_record = old_evidence.get(chart["key"])
        if previous_record and evidence_selection(previous_record) == evidence_selection(record):
            record = previous_record
        evidence[chart["key"]] = record | {"sha256": chart["sha256"]}
    source = {"id": post.id, "slug": post.slug, "title": post.title, "url": f"{BLOG_URL}/{post.slug}", "status": post.status, "updated_at": post.updated_at, "published_at": post.published_at.isoformat() if post.published_at else None, "fetched_at": native_datetime_utc_now().isoformat(), "html_digest": hashlib.sha256(post.html.encode()).hexdigest(), "charts": charts, "ignored_images": ignored}
    source["digest"] = digest(source_fields(source))
    write_json(root / "source-post.json", source)
    (root / "source-post.html").write_text(post.html)
    write_json(root / "chart-evidence.json", evidence)
    header = {"schema_version": 1, "campaign_id": post.id, "account": "tradingprotocol", "report_month": month.strftime("%Y-%m"), "source_digest": source["digest"], "start_at": None, "interval_hours": 12}
    entries = []
    for chart in charts:
        key = chart["key"]
        record = evidence[key]
        labels = ["@" + w["handle"] if w.get("handle") else w.get("organisation") or w["name"] for w in record.get("winners", [])]
        leaders = " Leaders: " + ", ".join(labels) + "." if labels else ""
        body = record.get("suggested_body") or f"{month:%B %Y}: {chart['heading']}.{leaders}\n{source['url']}"
        metadata = {"id": f"{post.id}:{key}", "chart_key": key, "winner_evidence": f"chart-evidence.json#{key}", "status": "draft", "acknowledged_exceptions": []}
        missing = [w.get("organisation") or w["name"] for w in record.get("winners", []) if not w.get("handle")]
        if missing:
            metadata["acknowledged_exceptions"] = [{"code": "missing_handle", "rationale": "Name these winners without tags; editor to review: " + ", ".join(missing)}]
        entry = TweetEntry(chart["heading"], metadata, body, chart["path"], chart["alt"])
        if key in old_entries and old_evidence.get(key) == record:
            old = old_entries[key]
            if old_charts[key]["sha256"] == chart["sha256"]:
                entry = dataclasses.replace(old, image=chart["path"])
                if any(old_charts[key].get(field) != chart.get(field) for field in ("heading", "context", "alt")):
                    entry = dataclasses.replace(entry, heading=chart["heading"], metadata=entry.metadata | {"status": "draft"})
        entries.append(entry)
    path = root / "tweet-plan.md"
    path.write_text(render_plan(TweetPlan(header, entries)))
    return path


def evidence_selection(value: object) -> object:
    """Compare selection provenance independently of later identity verification.

    A changed metric, entity, handle or chart hash invalidates carry-forward.
    Verification receipts and editorial suggestions can survive identical data.

    :param value: JSON evidence mapping or nested value.
    :return: Selection fields without verification/editorial receipts.
    """
    if isinstance(value, dict):
        ignored = {"verified_at", "user_id", "verification_method", "verification_note", "suggested_body", "issues", "url"}
        return {key: evidence_selection(item) for key, item in value.items() if key not in ignored}
    if isinstance(value, list):
        return [evidence_selection(item) for item in value]
    return value


def fetch_campaign_assets(path: Path, allowed_hosts: frozenset[str] = MEDIA_HOSTS) -> None:
    """Restore frozen campaign charts after checking out a text-only bundle.

    Existing files are verified and never replaced. Missing charts are fetched
    from the saved source URLs and must match the reviewed SHA-256 exactly.

    :param path: Editable Markdown beside its source snapshot.
    :param allowed_hosts: Explicit trusted download hosts.
    """
    source = json.loads(path.with_name("source-post.json").read_text())
    if digest(source_fields(source)) != source["digest"]:
        raise ValueError("Source snapshot was changed")
    root = path.parent.resolve()
    for chart in tqdm(source["charts"], desc="Restoring reviewed chart assets"):
        target = (root / chart["path"]).resolve()
        if not target.is_relative_to(root) or Path(chart["path"]).is_absolute():
            raise ValueError("Chart path escapes the campaign bundle")
        if target.exists():
            if file_hash(target) != chart["sha256"]:
                raise ValueError("Existing chart differs from the reviewed source")
            continue
        data = fetch_image(chart["url"], allowed_hosts)
        if hashlib.sha256(data).hexdigest() != chart["sha256"]:
            raise ValueError("Served chart changed; generate a new campaign revision")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


@dataclasses.dataclass(slots=True)
class TweetEntry:
    """One editor-controlled tweet and its source-bound attachment."""

    #: Internal heading, never posted.
    heading: str
    #: Restricted per-entry YAML.
    metadata: dict
    #: Exact text to send.
    body: str
    #: Relative frozen attachment path.
    image: str
    #: Reviewed attachment alt text.
    alt: str


@dataclasses.dataclass(slots=True)
class TweetPlan:
    """Parsed editable campaign, with one entry per source chart."""

    #: Restricted campaign front matter.
    header: dict
    #: Ordered chart entries.
    entries: list[TweetEntry]


def render_plan(plan: TweetPlan) -> str:
    """Render the documented hand-editable Markdown format.

    Body text stays inside fences, separate from internal review metadata.

    :param plan: Campaign to render.
    :return: Editable Markdown.
    """
    output = ["---", yaml.safe_dump(plan.header, sort_keys=False).rstrip(), "---", "", f"# {plan.header['report_month']} vault report tweets", "", "Draft for manual wording and winner review. Set start_at only when ready to schedule."]
    for entry in plan.entries:
        alt = entry.alt.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        output.extend(["", f"## {entry.heading}", "", "```yaml", yaml.safe_dump(entry.metadata, sort_keys=False).rstrip(), "```", "", "```text", entry.body, "```", "", f"![{alt}]({entry.image})"])
    return "\n".join(output) + "\n"


def _load_yaml(text: str) -> dict:
    """Read YAML mappings while rejecting duplicate keys.

    Silent last-key-wins behaviour is unsuitable for approved payloads.

    :param text: Restricted YAML text.
    :return: Parsed mapping.
    """
    node = yaml.compose(text)
    if not isinstance(node, yaml.MappingNode):
        raise ValueError("Expected a YAML mapping")

    def check_nodes(current: yaml.Node) -> None:
        if isinstance(current, yaml.MappingNode):
            keys = [key.value for key, _ in current.value]
            if len(keys) != len(set(keys)):
                raise ValueError("Duplicate YAML field")
            for _, child in current.value:
                check_nodes(child)
        elif isinstance(current, yaml.SequenceNode):
            for child in current.value:
                check_nodes(child)

    check_nodes(node)
    value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError("Expected a YAML mapping")
    return value


def derive_winner_evidence(key: str, record: dict, identities: dict[str, dict]) -> dict:
    """Resolve displayed entities from new report metadata to draft winner evidence.

    Handle ownership remains an explicit verification step; absent managers are
    not replaced with an infrastructure protocol. Charts with unmatched manual
    edits must instead supply manually checked evidence.

    :param key: Logical chart key.
    :param record: Original/served-bound final chart metadata.
    :param identities: Canonical repository social metadata.
    :return: Draft evidence with unresolved issues retained.
    """
    entities = record["entities"]
    metric = record["metric"]
    if not key.endswith("_performance"):
        entities = sorted(entities, key=lambda row: row.get(metric, float("-inf")), reverse=True)
    winners, seen, issues, resolved = [], set(), [], []
    for rank, row in enumerate(entities, 1):
        if row.get("kind") == "vault":
            identity_key = row.get("curator_slug") or f"unattributed:{row['entity_id']}"
        elif row.get("kind") == "protocol":
            identity_key = row.get("protocol_slug") or f"unattributed:{row['entity_id']}"
        elif row.get("kind") == "chain":
            identity_key = f"chain:{row['entity_id']}"
        else:
            identity_key = row.get("manager_slug") or f"unattributed:{row['entity_id']}"
        social = identities.get(identity_key, {})
        winner = row | social
        winner["name"] = row.get("name", row["entity_id"])
        winner["chart_position"] = row.get("rank")
        winner["rank"] = rank
        winner["organisation"] = social.get("name", row.get("curator_name"))
        resolved.append(winner)
        if row["entity_id"] in {"Other", "Total"} or identity_key in seen or len(winners) >= 3:
            continue
        seen.add(identity_key)
        winners.append(winner)
    if len(winners) < 3:
        issues.append("Review the chart's distinct attributable organisations")
    return record | {"entities": resolved, "winners": winners, "issues": issues}


def selected_winners(entry: TweetEntry, record: dict) -> list[dict]:
    """Apply an explicitly reviewed entity selection override.

    The same selected identities must be used by validation and the runner.

    :param entry: Editable tweet entry.
    :param record: Chart-bound evidence.
    :return: Selected winner identities.
    """
    override = entry.metadata.get("selection_override")
    if not override:
        return record.get("winners", [])
    if set(override) != {"entity_ids", "metric", "rationale"} or not override["rationale"] or override["metric"] != record["metric"]:
        raise ValueError("Selection override needs matching metric and rationale")
    by_id = {w["entity_id"]: w for w in record.get("entities", record.get("winners", []))}
    by_id.update({w["entity_id"]: w for w in record.get("winners", [])})
    if not isinstance(override["entity_ids"], list) or any(entity_id not in by_id for entity_id in override["entity_ids"]):
        raise ValueError("Selection override contains an unknown chart entity")
    return [by_id[entity_id] for entity_id in override["entity_ids"]]


def read_plan(path: Path, markdown_text: str | None = None) -> TweetPlan:
    """Parse the restricted Markdown without altering body bytes.

    Unknown fields, extra body/image blocks and duplicate tweet identities fail.

    :param path: Editable Markdown file.
    :param markdown_text: Optional single-read snapshot for compilation.
    :return: Parsed campaign.
    """
    text = markdown_text if markdown_text is not None else path.read_bytes().decode("utf-8")
    if "\r" in text:
        raise ValueError("Save the Markdown as UTF-8 with LF line endings before review/compilation")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if not match:
        raise ValueError("Missing campaign YAML front matter")
    header = _load_yaml(match[1])
    fields = {"schema_version", "campaign_id", "account", "report_month", "source_digest", "start_at", "interval_hours"}
    if set(header) != fields or header["schema_version"] != 1 or header["account"] != "tradingprotocol" or header["interval_hours"] != 12:
        raise ValueError("Invalid campaign configuration or unknown fields")
    # Tweet bodies may contain Markdown headings; split only outside fences.
    blocks, fence = [], False
    for line in text[match.end() :].splitlines(keepends=True):
        if line.startswith("```"):
            fence = not fence
        if line.startswith("## ") and not fence:
            blocks.append(line)
        elif blocks:
            blocks[-1] += line
    entries = []
    allowed = {"id", "chart_key", "winner_evidence", "status", "exclusion_reason", "selection_override", "acknowledged_exceptions"}
    for block in blocks:
        yaml_blocks = re.findall(r"^```yaml\n(.*?)\n```$", block, re.M | re.S)
        bodies = re.findall(r"^```text\n(.*?)\n```$", block, re.M | re.S)
        images = re.findall(r"^!\[((?:\\.|[^\]\\\n])*)\]\(([^)\n]+)\)$", block, re.M)
        if len(yaml_blocks) != 1 or len(bodies) != 1 or len(images) != 1:
            raise ValueError("Each entry needs exactly one YAML block, text block and image")
        metadata = _load_yaml(yaml_blocks[0])
        if not {"id", "chart_key", "winner_evidence", "status"} <= set(metadata) or set(metadata) - allowed or metadata["status"] not in {"draft", "approved", "excluded"}:
            raise ValueError("Invalid tweet metadata or unknown fields")
        alt = re.sub(r"\\([\\\[\]])", r"\1", images[0][0])
        entries.append(TweetEntry(block.splitlines()[0][3:], metadata, bodies[0], images[0][1], alt))
    if not entries or len({e.metadata["id"] for e in entries}) != len(entries):
        raise ValueError("Empty plan or duplicate tweet identity")
    return TweetPlan(header, entries)


def safe_asset(root: Path, relative: str) -> Path:
    """Resolve only frozen assets inside a campaign bundle.

    Absolute paths and symlink escapes cannot become outgoing attachments.

    :param root: Bundle directory.
    :param relative: Markdown attachment path.
    :return: Validated local path.
    """
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Attachment must be a file inside the campaign bundle")
    return path


def validate_plan(path: Path, require_approved: bool = False, markdown_text: str | None = None) -> tuple[TweetPlan, dict, dict, list[str]]:
    """Check wording, images and evidence before review or compilation.

    Draft previews show unresolved issues; compilation rejects all of them.

    :param path: Editable plan.
    :param require_approved: Require manual approval and a schedule.
    :param markdown_text: Optional single-read snapshot for compilation.
    :return: Parsed plan, source, evidence and review issues.
    """
    plan = read_plan(path, markdown_text)
    source = json.loads(path.with_name("source-post.json").read_text())
    evidence = json.loads(path.with_name("chart-evidence.json").read_text())
    if source["digest"] != digest(source_fields(source)) or plan.header["source_digest"] != source["digest"] or plan.header["campaign_id"] != source["id"]:
        raise ValueError("Campaign source does not match its read-back snapshot")
    charts = {c["key"]: c for c in source["charts"]}
    if len(plan.entries) != len(charts) or {e.metadata["chart_key"] for e in plan.entries} != set(charts):
        raise ValueError("Every source chart must have exactly one entry")
    issues = []
    for entry in plan.entries:
        key = entry.metadata["chart_key"]
        record = evidence[key]
        if entry.metadata["id"] != f"{source['id']}:{key}" or entry.metadata["winner_evidence"] != f"chart-evidence.json#{key}":
            raise ValueError("Tweet identity/evidence reference was changed")
        attachment = safe_asset(path.parent, entry.image)
        expected = charts[key]["sha256"]
        if file_hash(attachment) != expected or record["sha256"] != charts[key]["sha256"]:
            raise ValueError(f"Changed or unbound image: {key}")
        if entry.metadata["status"] == "excluded":
            if not entry.metadata.get("exclusion_reason"):
                raise ValueError("Excluded charts need an explicit reason")
            continue
        problems = list(record.get("issues", []))
        parsed = parse_tweet(entry.body)
        if not parsed.valid:
            problems.append(f"Invalid tweet text ({parsed.weightedLength}/280)")
        if "<" in entry.body or "```" in entry.body or not entry.alt or len(entry.alt) > 1000:
            problems.append("Placeholder/fence text or invalid attachment alt text")
        if source["url"] not in entry.body:
            problems.append("Missing canonical report link")
        winners = selected_winners(entry, record)
        if len(winners) > 3 or len({w["entity_id"] for w in winners}) != len(winners):
            problems.append("Winner selection must contain at most three unique chart entities")
        exceptions = entry.metadata.get("acknowledged_exceptions") or []
        for exception in exceptions:
            if set(exception) != {"code", "rationale"} or exception["code"] not in {"fewer_than_three", "top_three_vaults", "missing_handle"} or not exception["rationale"]:
                raise ValueError("Invalid exception acknowledgement")
        mentions = {m.lower() for m in MENTION.findall(entry.body)}
        required = set()
        untagged = any(e["code"] == "missing_handle" for e in exceptions)
        named_without_tags = 0
        prose = MENTION.sub("", re.sub(r"https?://\S+", "", entry.body))
        for winner in winners:
            if not winner.get("handle") or not winner.get("verified_at") or not winner.get("official_source"):
                labels = [label for label in (winner.get("organisation"), winner["name"]) if isinstance(label, str) and label.strip()]
                if untagged and any(re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", prose, re.I) for label in labels):
                    named_without_tags += 1
                else:
                    problems.append(f"Unverified winner handle: {winner.get('name')}; name it without a tag and acknowledge missing_handle")
            else:
                required.add(normalise_handle(winner["handle"]).lower())
        if not winners:
            problems.append("No chart-bound winner evidence")
        if required - mentions:
            problems.append("Missing required winner mentions")
        if mentions - required:
            problems.append("Mention has no reviewed winner evidence")
        if len(required) + named_without_tags < 3 and not any(e["code"] in {"fewer_than_three", "top_three_vaults"} for e in exceptions):
            problems.append("Fewer than three distinct tags needs acknowledgement")
        if require_approved and entry.metadata["status"] != "approved":
            problems.append("Wording has not been manually approved")
        issues.extend(f"{key}: {problem}" for problem in problems)
    if require_approved:
        if not plan.header["start_at"]:
            issues.append("Choose an explicit start_at before compilation")
        else:
            parse_utc(str(plan.header["start_at"]))
        if issues:
            raise ValueError("Campaign is not ready:\n" + "\n".join(issues))
    return plan, source, evidence, issues


def compile_campaign(path: Path) -> Path:
    """Compile only manually approved entries to an immutable digest-named file.

    Later Markdown edits produce another file, leaving running payloads intact.

    :param path: Reviewed editable plan.
    :return: Immutable campaign JSON path.
    """
    markdown_bytes = path.read_bytes()
    plan, source, evidence, _ = validate_plan(path, True, markdown_bytes.decode())
    if path.read_bytes() != markdown_bytes:
        raise ValueError("Markdown changed during compilation; review and compile again")
    start = parse_utc(str(plan.header["start_at"]))
    source_charts = {chart["key"]: chart for chart in source["charts"]}
    entries = []
    for entry in plan.entries:
        if entry.metadata["status"] == "excluded":
            continue
        entries.append({"id": entry.metadata["id"], "chart_key": entry.metadata["chart_key"], "body": entry.body, "image": entry.image, "image_sha256": source_charts[entry.metadata["chart_key"]]["sha256"], "alt": entry.alt, "winners": selected_winners(entry, evidence[entry.metadata["chart_key"]]), "acknowledged_exceptions": entry.metadata.get("acknowledged_exceptions") or [], "scheduled_at": (start + datetime.timedelta(seconds=len(entries) * INTERVAL_SECONDS)).isoformat() + "Z"})
    if not entries:
        raise ValueError("Cannot compile an empty campaign")
    compiled = {"schema_version": 1, "account": "tradingprotocol", "post_id": source["id"], "source": source, "markdown_sha256": hashlib.sha256(markdown_bytes).hexdigest(), "evidence_digest": digest(evidence), "entries": entries}
    compiled["digest"] = digest(compiled)
    destination = path.with_name(f"approved-campaign-{compiled['digest']}.json")
    if destination.exists():
        if json.loads(destination.read_text()) != compiled:
            raise ValueError("Immutable campaign file collision")
    else:
        write_json(destination, compiled)
    return destination


def load_campaign(path: Path, expected_digest: str | None = None) -> dict:
    """Verify the frozen campaign and attachments without consulting live Markdown.

    Restarting reads only the reviewed immutable payload and unchanged bytes.

    :param path: Compiled file.
    :param expected_digest: Explicitly reviewed launch digest.
    :return: Verified campaign dictionary.
    """
    campaign = json.loads(path.read_text())
    actual = digest({k: v for k, v in campaign.items() if k != "digest"})
    if actual != campaign["digest"] or expected_digest is not None and expected_digest != actual or campaign["account"] != "tradingprotocol":
        raise ValueError("Approved campaign digest/account mismatch")
    for entry in campaign["entries"]:
        if file_hash(safe_asset(path.parent, entry["image"])) != entry["image_sha256"]:
            raise ValueError("Approved attachment bytes have changed")
    return campaign


def escape_github_mentions(value: str) -> str:
    """Prevent internal review prose from notifying unrelated GitHub users.

    A zero-width separator retains readable at signs outside fenced bodies.

    :param value: Internal heading, evidence or review rationale.
    :return: Non-mentioning review prose.
    """
    return value.replace("@", "@\u200b")


def render_preview(path: Path, remote_images: dict[str, str] | None = None) -> Path:
    """Render the exact draft/approved bodies and inline images for GitHub review.

    X handles remain fenced to avoid mentioning unrelated GitHub accounts.

    :param path: Editable plan.
    :param remote_images: Optional immutable image URLs keyed by chart key.
    :return: Derived comment file; no external writes occur.
    """
    plan, source, evidence, issues = validate_plan(path)
    lines = ["# October 2026 tweet plan" if plan.header["report_month"] == "2026-10" else f"# {plan.header['report_month']} tweet plan", "", f"Source: [{escape_github_mentions(source['title'])}]({source['url']})", "", "Account: **TradingProtocol** · one standalone tweet every **12 hours** · **draft for manual review**", "", f"Markdown SHA-256: `{file_hash(path)}`", "", "Start time: **awaiting editor selection**" if not plan.header["start_at"] else f"Start time: `{plan.header['start_at']}`"]
    for compiled_path in path.parent.glob("approved-campaign-*.json"):
        compiled = load_campaign(compiled_path)
        if compiled["markdown_sha256"] == file_hash(path) and compiled["evidence_digest"] == digest(evidence):
            lines += ["", f"Matching approved campaign digest: `{compiled['digest']}`"]
    slot = 0
    for entry in plan.entries:
        key = entry.metadata["chart_key"]
        if "```" in entry.body:
            raise ValueError("Tweet body fences cannot be rendered safely in GitHub review")
        lines += ["", f"## {escape_github_mentions(entry.heading)}", "", f"Status: **{entry.metadata['status']}**"]
        if entry.metadata["status"] == "excluded":
            lines += ["", escape_github_mentions(entry.metadata["exclusion_reason"])]
        else:
            when = (parse_utc(str(plan.header["start_at"])) + datetime.timedelta(seconds=slot * INTERVAL_SECONDS)).isoformat() + "Z" if plan.header["start_at"] else f"Start + {slot * 12}h"
            lines += [f"Schedule: `{when}` · {parse_tweet(entry.body).weightedLength}/280 weighted characters", "", "```text", entry.body, "```"]
            slot += 1
        url = (remote_images or {}).get(key, source["charts"][[c["key"] for c in source["charts"]].index(key)]["url"])
        lines += ["", f"![{escape_github_mentions(entry.alt)}]({url})", "", f"Ranking metric: {escape_github_mentions(evidence[key]['metric'])}", ""]
        for winner in selected_winners(entry, evidence[key]):
            handle = winner.get("handle")
            profile = f"[{handle}](https://x.com/{handle})" if handle else "handle unresolved"
            lines += [f"- Rank {winner.get('rank', '?')}: {escape_github_mentions(winner['name'])} — {profile}"]
        for exception in entry.metadata.get("acknowledged_exceptions") or []:
            lines += [f"- Exception for manual review ({exception['code']}): {escape_github_mentions(exception['rationale'])}"]
        lines += [f"- Review: {escape_github_mentions(i.split(': ', 1)[-1])}" for i in issues if i.startswith(key + ": ")]
    comment = "\n".join(lines) + "\n"
    if len(comment) > 60000:
        raise ValueError("Preview exceeds GitHub's comment size limit")
    destination = path.with_name("github-comment.md")
    destination.write_text(comment)
    return destination


def verify_source(client: GhostAdminClient, campaign: dict, allowed_hosts: frozenset[str] = MEDIA_HOSTS) -> None:
    """Refetch publication, source context and chart bytes before sending.

    Changes pause the queue; the reviewed text is never silently regenerated.

    :param client: Ghost reader.
    :param campaign: Verified compiled campaign.
    :param allowed_hosts: Trusted media hosts.
    """
    source = campaign["source"]
    post = client.fetch_post_by_slug(source["slug"])
    if not post or post.id != source["id"] or post.status != "published" or not post.html:
        raise ValueError("Source post is no longer published at the reviewed slug")
    charts, _ = extract_charts(post.html, {chart["url"]: chart["key"] for chart in source["charts"]})
    for chart in charts:
        chart["sha256"] = hashlib.sha256(fetch_image(chart["url"], allowed_hosts)).hexdigest()
    current = {"id": post.id, "slug": post.slug, "title": post.title, "url": f"{BLOG_URL}/{post.slug}", "charts": charts}
    if digest(source_fields(current)) != source["digest"]:
        raise ValueError("Ghost chart/content changed; pause and review a new revision")
    try:
        response = requests.get(source["url"], timeout=30)
        if response.status_code != 200:
            raise ValueError(f"Public blog unavailable: HTTP {response.status_code}")
    except requests.RequestException as exc:
        raise ValueError(f"Public blog check failed: {type(exc).__name__}") from None


def publish_preview(path: Path, pr: str, assets_branch: str) -> str:
    """Publish immutable chart assets and create/update the recorded review comment.

    Invoke only after authorisation to push assets and write this PR comment.
    Git plumbing uses a temporary index without touching the worktree.

    :param path: Editable campaign.
    :param pr: Explicit PR number.
    :param assets_branch: Explicit dedicated orphan branch name.
    :return: Review comment URL.
    """
    plan, _, _, _ = validate_plan(path)
    repo = json.loads(subprocess.check_output(["gh", "repo", "view", "--json", "nameWithOwner"], text=True))["nameWithOwner"]
    if not pr.isdigit() or not re.fullmatch(r"[a-zA-Z0-9/_-]+", assets_branch):
        raise ValueError("Explicit PR number and safe asset branch required")
    with tempfile.TemporaryDirectory(prefix="tweet-assets-") as temp:
        environment = os.environ | {"GIT_INDEX_FILE": str(Path(temp) / "index")}
        subprocess.run(["git", "read-tree", "--empty"], env=environment, check=True)
        image_paths = {}
        for entry in plan.entries:
            asset = safe_asset(path.parent, entry.image)
            blob = subprocess.check_output(["git", "hash-object", "-w", str(asset)], text=True).strip()
            target = f"charts/{file_hash(asset)}{asset.suffix}"
            subprocess.run(["git", "update-index", "--add", "--cacheinfo", f"100644,{blob},{target}"], env=environment, check=True)
            image_paths[entry.metadata["chart_key"]] = target
        tree = subprocess.check_output(["git", "write-tree"], env=environment, text=True).strip()
        commit = subprocess.check_output(["git", "commit-tree", tree, "-m", "Monthly vault tweet review charts"], text=True).strip()
    # A new unique branch per preview avoids overwriting earlier review assets.
    branch = f"{assets_branch}-{commit[:12]}"
    subprocess.run(["git", "push", "origin", f"{commit}:refs/heads/{branch}"], check=True)
    urls = {key: f"https://raw.githubusercontent.com/{repo}/{commit}/{name}" for key, name in image_paths.items()}
    for entry in tqdm(plan.entries, desc="Checking published review images"):
        data = fetch_image(urls[entry.metadata["chart_key"]], frozenset({"raw.githubusercontent.com"}))
        if hashlib.sha256(data).hexdigest() != file_hash(safe_asset(path.parent, entry.image)):
            raise ValueError("Published review image differs from the attachment")
    comment = render_preview(path, urls)
    record_path = path.with_name("github-review.json")
    previous = json.loads(record_path.read_text()) if record_path.exists() else None
    endpoint = f"repos/{repo}/issues/comments/{previous['id']}" if previous and previous["pr"] == pr else f"repos/{repo}/issues/{pr}/comments"
    result = json.loads(subprocess.check_output(["gh", "api", "-X", "PATCH" if previous and previous["pr"] == pr else "POST", endpoint, "-F", f"body=@{comment}"], text=True))
    write_json(record_path, {"pr": pr, "id": result["id"], "url": result["html_url"], "assets_commit": commit, "markdown_sha256": file_hash(path)})
    return result["html_url"]
