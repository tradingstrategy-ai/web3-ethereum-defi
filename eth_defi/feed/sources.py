"""Feed source mappings.

Load and validate YAML-defined mappings for RSS feeds, Twitter/X usernames,
LinkedIn company identifiers, and feeder websites that should be tracked for
vault-related post collection.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence
from urllib.parse import urlparse, urlunparse

if TYPE_CHECKING:
    from eth_defi.feed.collector import CollectorRunSummary

from strictyaml import Map, Optional, Str, load


logger = logging.getLogger(__name__)


#: Repository-resolved base directory for feed source YAML files.
FEEDS_DATA_DIR = Path(__file__).parent.parent / "data" / "feeds"

#: Backwards-compatible alias for the old constant name.
POST_TRACKING_DATA_DIR = FEEDS_DATA_DIR

#: Supported feeder roles.
KNOWN_FEEDER_ROLES = {
    "curator",
    "protocol",
    "stablecoin",
    "vault",
}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_VALID_SOURCE_TYPES = {"rss", "twitter", "linkedin"}

_MAPPING_SCHEMA = Map(
    {
        "feeder-id": Str(),
        "name": Str(),
        "role": Str(),
        Optional("website"): Str(),
        Optional("twitter"): Str(),
        Optional("linkedin"): Str(),
        Optional("linkedin-rss-hub-disabled-at"): Str(),
        Optional("rss"): Str(),
    }
)


@dataclass(slots=True, frozen=True)
class TrackedPostSource:
    """A single logical source mapping for feed collection."""

    #: Canonical slug for the feeder, matching the curator, protocol, stablecoin, or vault slug.
    feeder_id: str
    #: Human-readable feeder name shown in diagnostics and exports.
    name: str
    #: Feeder role such as protocol, curator, stablecoin, or vault.
    role: str
    #: Company website for the feeder when configured in YAML.
    website: str | None
    #: Source transport type, currently rss, twitter, or linkedin.
    source_type: str
    #: Source-specific stable key, such as feed URL, Twitter username, or LinkedIn company id.
    source_key: str
    #: Canonical source URL used for fetching or bridge construction.
    canonical_url: str
    #: Path to the YAML file that defined this source.
    mapping_file: Path

    def get_logical_key(self) -> tuple[str, str, str, str]:
        """Return the natural unique key for this source."""

        return (
            self.feeder_id,
            self.role,
            self.source_type,
            self.source_key,
        )


def _validate_slug(value: object, field_name: str, mapping_file: Path) -> str:
    """Validate feeder identifiers and slugs."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be set in {mapping_file}")

    slug = value.strip()
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError(f"{field_name} must be lowercase slug format in {mapping_file}: {slug}")

    return slug


def _validate_role(role: object, mapping_file: Path) -> str:
    """Validate feeder role names."""

    role_value = _validate_slug(role, "role", mapping_file)
    if role_value not in KNOWN_FEEDER_ROLES:
        raise ValueError(f"role must be one of {sorted(KNOWN_FEEDER_ROLES)} in {mapping_file}: {role_value}")
    return role_value


def _normalise_http_url(url: str, mapping_file: Path) -> str:
    """Normalise a generic HTTP(S) URL for storage."""

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Expected http or https URL in {mapping_file}: {url}")
    if not parsed.netloc:
        raise ValueError(f"URL is missing host in {mapping_file}: {url}")
    path = parsed.path or "/"
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", parsed.query, ""))


def _normalise_rss_source(url: str, mapping_file: Path) -> tuple[str, str]:
    """Normalise an RSS source URL."""

    canonical_url = _normalise_http_url(url, mapping_file)
    return canonical_url, canonical_url


def _normalise_twitter_source(handle: str, mapping_file: Path) -> tuple[str, str]:
    """Normalise a Twitter/X username to a canonical URL and source key."""

    normalised_handle = handle.strip().lstrip("@").lower()
    if not normalised_handle or not re.fullmatch(r"[a-z0-9_]{1,25}", normalised_handle):
        raise ValueError(f"Invalid Twitter username in {mapping_file}: {handle}")

    canonical_url = f"https://x.com/{normalised_handle}"
    return canonical_url, normalised_handle


def _normalise_linkedin_source(company_id: str, mapping_file: Path) -> tuple[str, str]:
    """Normalise a LinkedIn company identifier for storage and bridge use."""

    normalised_company_id = company_id.strip().lower()
    if not normalised_company_id or not re.fullmatch(r"[a-z0-9-]+", normalised_company_id):
        raise ValueError(f"Invalid LinkedIn company id in {mapping_file}: {company_id}")

    canonical_url = f"https://www.linkedin.com/company/{normalised_company_id}"
    return canonical_url, normalised_company_id


def _iter_mapping_files(mappings_dir: Path) -> Iterable[Path]:
    """Iterate all mapping files in deterministic order across subfolders."""

    yield from sorted(mappings_dir.rglob("*.yaml"))


def _build_tracked_source(
    *,
    mapping_file: Path,
    feeder_id: str,
    name: str,
    role: str,
    website: str | None,
    source_type: str,
    raw_value: str,
) -> TrackedPostSource:
    """Create a validated tracked source entry."""

    if source_type not in _VALID_SOURCE_TYPES:
        raise ValueError(f"source_type must be one of {_VALID_SOURCE_TYPES} in {mapping_file}")

    if source_type == "twitter":
        canonical_url, source_key = _normalise_twitter_source(raw_value, mapping_file)
    elif source_type == "linkedin":
        canonical_url, source_key = _normalise_linkedin_source(raw_value, mapping_file)
    else:
        canonical_url, source_key = _normalise_rss_source(raw_value, mapping_file)

    return TrackedPostSource(
        feeder_id=feeder_id,
        name=name,
        role=role,
        website=website,
        source_type=source_type,
        source_key=source_key,
        canonical_url=canonical_url,
        mapping_file=mapping_file,
    )


def _load_mapping_file(mapping_file: Path) -> list[TrackedPostSource]:
    """Load one mapping YAML file."""

    parsed = load(mapping_file.read_text(), _MAPPING_SCHEMA).data
    feeder_id = _validate_slug(parsed.get("feeder-id"), "feeder-id", mapping_file)
    name = parsed.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"name must be a non-empty string in {mapping_file}")

    role = _validate_role(parsed.get("role"), mapping_file)
    website = parsed.get("website")
    twitter_username = parsed.get("twitter")
    linkedin_company_id = parsed.get("linkedin")
    linkedin_disabled_at = parsed.get("linkedin-rss-hub-disabled-at")
    rss_url = parsed.get("rss")

    if linkedin_company_id and linkedin_disabled_at:
        logger.debug(
            "LinkedIn source disabled for %s since %s (linkedin-rss-hub-disabled-at set in YAML)",
            feeder_id,
            linkedin_disabled_at,
        )
        linkedin_company_id = None

    if website is not None:
        website = _normalise_http_url(website, mapping_file)

    if not any((twitter_username, linkedin_company_id, rss_url)):
        raise ValueError(f"At least one of twitter, linkedin or rss must be set in {mapping_file}")

    sources = []

    if twitter_username:
        sources.append(
            _build_tracked_source(
                mapping_file=mapping_file,
                feeder_id=feeder_id,
                name=name.strip(),
                role=role,
                website=website,
                source_type="twitter",
                raw_value=twitter_username,
            )
        )

    if linkedin_company_id:
        sources.append(
            _build_tracked_source(
                mapping_file=mapping_file,
                feeder_id=feeder_id,
                name=name.strip(),
                role=role,
                website=website,
                source_type="linkedin",
                raw_value=linkedin_company_id,
            )
        )

    if rss_url:
        sources.append(
            _build_tracked_source(
                mapping_file=mapping_file,
                feeder_id=feeder_id,
                name=name.strip(),
                role=role,
                website=website,
                source_type="rss",
                raw_value=rss_url,
            )
        )

    return sources


def mark_linkedin_source_disabled(yaml_path: Path, disabled_at: str) -> bool:
    """Append ``linkedin-rss-hub-disabled-at`` to a feeder YAML without rewriting it.

    Preserves existing file content including comments.  The new field is appended
    as a trailing line so that YAML structure and indentation are not disturbed.

    :param yaml_path: Path to the feeder YAML file.
    :param disabled_at: ISO date string to stamp, e.g. ``2026-04-04``.
    :return: ``True`` when the file was updated, ``False`` when the field was already present.
    """
    content = yaml_path.read_text()
    if "linkedin-rss-hub-disabled-at" in content:
        return False
    if not content.endswith("\n"):
        content += "\n"
    content += f"linkedin-rss-hub-disabled-at: {disabled_at}\n"
    yaml_path.write_text(content)
    return True


def auto_disable_failed_linkedin_sources(
    summary: "CollectorRunSummary",
    sources: Sequence[TrackedPostSource],
    disabled_at: str,
) -> int:
    """Write ``linkedin-rss-hub-disabled-at`` to YAML for every all-503 LinkedIn failure.

    When all configured RSSHub bridges return HTTP 503 for a LinkedIn source, LinkedIn is
    gating that company page behind authentication.  This function stamps the feeder YAML
    so future scan runs skip the source entirely rather than retrying.

    :param summary: Collector run result from :func:`~eth_defi.feed.collector.collect_posts`.
    :param sources: Source list passed to the same :func:`~eth_defi.feed.collector.collect_posts` call.
    :param disabled_at: ISO date string to stamp in the YAML, e.g. ``2026-04-04``.
    :return: Number of YAML files updated.
    """
    linkedin_yaml: dict[str, Path] = {s.feeder_id: s.mapping_file for s in sources if s.source_type == "linkedin"}
    count = 0
    for result in summary.source_results or []:
        if result.source_type != "linkedin" or result.status != "failed":
            continue
        if not result.auth_blocked:
            continue
        yaml_path = linkedin_yaml.get(result.feeder_id)
        if yaml_path is None:
            continue
        if mark_linkedin_source_disabled(yaml_path, disabled_at):
            logger.info(
                "Auto-disabled LinkedIn feed for %s in %s (all bridges returned 503)",
                result.feeder_id,
                yaml_path.name,
            )
            count += 1
    return count


def load_post_sources(mappings_dir: Path = FEEDS_DATA_DIR) -> list[TrackedPostSource]:
    """Load and validate all feed source mappings."""

    mappings_dir = mappings_dir.expanduser().resolve()
    if not mappings_dir.exists():
        raise FileNotFoundError(f"Feed mappings directory does not exist: {mappings_dir}")

    entries: list[TrackedPostSource] = []
    seen: dict[tuple[str, str, str, str], Path] = {}

    for mapping_file in _iter_mapping_files(mappings_dir):
        for entry in _load_mapping_file(mapping_file):
            logical_key = entry.get_logical_key()
            if logical_key in seen:
                other_file = seen[logical_key]
                raise ValueError(
                    f"Duplicate logical post source {logical_key} in {mapping_file} and {other_file}",
                )
            seen[logical_key] = mapping_file
            entries.append(entry)

    return entries
