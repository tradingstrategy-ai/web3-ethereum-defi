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

from strictyaml import Int, Map, Optional, Str, load


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
        Optional("canonical-feeder-id"): Str(),
        Optional("website"): Str(),
        Optional("twitter"): Str(),
        Optional("linkedin"): Str(),
        Optional("linkedin-rss-hub-disabled-at"): Str(),
        Optional("twitter-dead-at"): Str(),
        Optional("twitter-handle-resolved-unknown-at"): Str(),
        Optional("rss"): Str(),
        Optional("rss-dead-at"): Str(),
        Optional("rss-failure-at"): Str(),
        Optional("rss-failure-status-code"): Int(),
        Optional("rss-failure-exception-message"): Str(),
    }
)

#: Feed source fields that must NOT appear in alias YAML files.
_FEED_SOURCE_KEYS = ("twitter", "linkedin", "rss")

#: Role subdirectory names under the feeds data directory.
ROLE_SUBDIRS = {
    "stablecoin": "stablecoins",
    "protocol": "protocols",
    "curator": "curators",
    "vault": "vaults",
}

#: Role priority order for canonical feeder resolution.
#: When a canonical-feeder-id matches files in multiple roles,
#: the role listed first wins.
ROLE_PRIORITY = ("stablecoin", "protocol", "curator", "vault")


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


@dataclass(slots=True, frozen=True)
class FeederAlias:
    """A feeder YAML that delegates feed sources to another feeder.

    Alias files contain only identity metadata (feeder-id, name, role)
    and a ``canonical-feeder-id`` pointing to the feeder whose feed
    sources should be collected.  They produce no
    :py:class:`TrackedPostSource` entries.
    """

    #: Feeder-id of the alias file.
    feeder_id: str
    #: Feeder-id of the canonical source-bearing feeder.
    canonical_feeder_id: str
    #: Role declared in the alias file (may differ from the canonical feeder's role).
    role: str
    #: Human-readable name from the alias file.
    name: str
    #: Path to the alias YAML file.
    mapping_file: Path


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


def _load_mapping_file(mapping_file: Path) -> tuple[list[TrackedPostSource], FeederAlias | None]:
    """Load one mapping YAML file.

    :return:
        Tuple of ``(sources, alias)``.  When the file has
        ``canonical-feeder-id`` set, ``sources`` is empty and ``alias``
        holds the alias metadata.  Otherwise ``alias`` is ``None``.
    """

    parsed = load(mapping_file.read_text(), _MAPPING_SCHEMA).data
    feeder_id = _validate_slug(parsed.get("feeder-id"), "feeder-id", mapping_file)
    name = parsed.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"name must be a non-empty string in {mapping_file}")

    role = _validate_role(parsed.get("role"), mapping_file)

    # Handle alias files — canonical-feeder-id delegates feed sources
    canonical_feeder_id = parsed.get("canonical-feeder-id")
    if canonical_feeder_id is not None:
        canonical_feeder_id = _validate_slug(canonical_feeder_id, "canonical-feeder-id", mapping_file)
        for forbidden_key in _FEED_SOURCE_KEYS:
            if parsed.get(forbidden_key):
                raise ValueError(f"Alias YAML {mapping_file} has canonical-feeder-id set but also contains {forbidden_key!r} — remove feed sources from alias files")
        alias = FeederAlias(
            feeder_id=feeder_id,
            canonical_feeder_id=canonical_feeder_id,
            role=role,
            name=name.strip(),
            mapping_file=mapping_file,
        )
        return [], alias

    website = parsed.get("website")
    twitter_username = parsed.get("twitter")
    linkedin_company_id = parsed.get("linkedin")
    linkedin_disabled_at = parsed.get("linkedin-rss-hub-disabled-at")
    twitter_dead_at = parsed.get("twitter-dead-at")
    rss_url = parsed.get("rss")
    rss_dead_at = parsed.get("rss-dead-at")

    if linkedin_company_id and linkedin_disabled_at:
        logger.debug(
            "LinkedIn source disabled for %s since %s (linkedin-rss-hub-disabled-at set in YAML)",
            feeder_id,
            linkedin_disabled_at,
        )
        linkedin_company_id = None

    if twitter_username and twitter_dead_at:
        logger.debug(
            "Twitter source disabled for %s since %s (twitter-dead-at set in YAML)",
            feeder_id,
            twitter_dead_at,
        )
        twitter_username = None

    twitter_handle_unknown_at = parsed.get("twitter-handle-resolved-unknown-at")
    if twitter_username and twitter_handle_unknown_at:
        logger.debug(
            "Twitter handle unresolvable for %s since %s (twitter-handle-resolved-unknown-at set in YAML)",
            feeder_id,
            twitter_handle_unknown_at,
        )
        twitter_username = None

    if rss_url and rss_dead_at:
        logger.debug(
            "RSS source disabled for %s since %s (rss-dead-at set in YAML)",
            feeder_id,
            rss_dead_at,
        )
        rss_url = None

    if website is not None:
        website = _normalise_http_url(website, mapping_file)

    if not any((twitter_username, linkedin_company_id, rss_url)):
        # All sources have been disabled (dead/unknown/disabled flags set) — skip this feeder
        logger.debug("All sources disabled for %s in %s, skipping", feeder_id, mapping_file)
        return [], None

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

    return sources, None


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


def mark_twitter_source_dead(yaml_path: Path, dead_at: str) -> bool:
    """Append ``twitter-dead-at`` to a feeder YAML without rewriting it.

    Used when a Twitter account has not posted for the configured
    death detection period.

    :param yaml_path: Path to the feeder YAML file.
    :param dead_at: ISO date string to stamp, e.g. ``2026-04-04``.
    :return: ``True`` when the file was updated, ``False`` when the field was already present.
    """
    content = yaml_path.read_text()
    if "twitter-dead-at" in content:
        return False
    if not content.endswith("\n"):
        content += "\n"
    content += f"twitter-dead-at: {dead_at}\n"
    yaml_path.write_text(content)
    return True


def mark_twitter_handle_unknown(yaml_path: Path, unknown_at: str) -> bool:
    """Append ``twitter-handle-resolved-unknown-at`` to a feeder YAML.

    Used when the X API cannot resolve a Twitter handle to a user ID,
    typically because the account has been suspended, deleted, or renamed.

    :param yaml_path: Path to the feeder YAML file.
    :param unknown_at: ISO date string, e.g. ``2026-04-06``.
    :return: ``True`` when the file was updated, ``False`` when already present.
    """
    content = yaml_path.read_text()
    if "twitter-handle-resolved-unknown-at" in content:
        return False
    if not content.endswith("\n"):
        content += "\n"
    content += f"twitter-handle-resolved-unknown-at: {unknown_at}\n"
    yaml_path.write_text(content)
    return True


def mark_rss_source_dead(yaml_path: Path, dead_at: str) -> bool:
    """Append ``rss-dead-at`` to a feeder YAML without rewriting it.

    Used when an RSS feed is valid but has not published any new posts
    for a year or more.

    :param yaml_path: Path to the feeder YAML file.
    :param dead_at: ISO date string, e.g. ``2026-04-06``.
    :return: ``True`` when the file was updated, ``False`` when already present.
    """
    content = yaml_path.read_text()
    if "rss-dead-at" in content:
        return False
    if not content.endswith("\n"):
        content += "\n"
    content += f"rss-dead-at: {dead_at}\n"
    yaml_path.write_text(content)
    return True


def mark_rss_source_failure(
    yaml_path: Path,
    failure_at: str,
    status_code: int | None = None,
    exception_message: str | None = None,
) -> bool:
    """Stamp RSS failure fields on a feeder YAML.

    Records the most recent RSS failure so operators can see which feeds
    are broken.  Overwrites any previous failure fields.

    :param yaml_path: Path to the feeder YAML file.
    :param failure_at: ISO date string, e.g. ``2026-04-06``.
    :param status_code: HTTP status code, or ``None`` for non-HTTP failures.
    :param exception_message: Exception message or HTTP status text.
    :return: ``True`` when the file was updated.
    """
    content = yaml_path.read_text()
    # Remove existing failure fields so we always write the latest
    lines = [line for line in content.splitlines(keepends=True) if not line.startswith("rss-failure-at:") and not line.startswith("rss-failure-status-code:") and not line.startswith("rss-failure-exception-message:")]
    content = "".join(lines)
    if not content.endswith("\n"):
        content += "\n"
    content += f"rss-failure-at: {failure_at}\n"
    if status_code is not None:
        content += f"rss-failure-status-code: {status_code}\n"
    if exception_message is not None:
        # Truncate, sanitise, and quote for YAML — exception messages contain
        # colons and other YAML-breaking characters that corrupt the file.
        safe_msg = exception_message.replace("\n", " ").replace('"', "'").strip()[:200]
        content += f'rss-failure-exception-message: "{safe_msg}"\n'
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


def load_post_sources(mappings_dir: Path = FEEDS_DATA_DIR) -> tuple[list[TrackedPostSource], int, list[FeederAlias]]:
    """Load and validate all feed source mappings.

    :return:
        Tuple of ``(sources, feeders_skipped, aliases)`` where
        *feeders_skipped* counts YAML files with all sources disabled
        (not aliases) and *aliases* lists feeders that delegate their
        sources to a canonical feeder via ``canonical-feeder-id``.
    """

    mappings_dir = mappings_dir.expanduser().resolve()
    if not mappings_dir.exists():
        raise FileNotFoundError(f"Feed mappings directory does not exist: {mappings_dir}")

    entries: list[TrackedPostSource] = []
    seen: dict[tuple[str, str, str, str], Path] = {}
    aliases: list[FeederAlias] = []
    all_feeder_ids: set[str] = set()
    feeders_skipped = 0

    for mapping_file in _iter_mapping_files(mappings_dir):
        try:
            file_entries, alias = _load_mapping_file(mapping_file)
        except ValueError:
            raise  # Validation errors (mutual exclusion, bad slugs) are fatal
        except Exception as e:
            logger.error("Failed to parse feeder YAML %s: %s", mapping_file, e)
            feeders_skipped += 1
            continue

        if alias:
            aliases.append(alias)
            all_feeder_ids.add(alias.feeder_id)
        elif not file_entries:
            feeders_skipped += 1
        else:
            all_feeder_ids.add(file_entries[0].feeder_id)

        for entry in file_entries:
            logical_key = entry.get_logical_key()
            if logical_key in seen:
                other_file = seen[logical_key]
                raise ValueError(
                    f"Duplicate logical post source {logical_key} in {mapping_file} and {other_file}",
                )
            seen[logical_key] = mapping_file
            entries.append(entry)

    # Validate alias targets
    for alias in aliases:
        if alias.canonical_feeder_id not in all_feeder_ids:
            raise ValueError(f"canonical-feeder-id {alias.canonical_feeder_id!r} in {alias.mapping_file} does not match any known feeder-id")
        # Ensure the resolved target is a source-bearing feeder, not another alias
        target_yaml = resolve_canonical_feeder_yaml(alias.canonical_feeder_id, mappings_dir)
        target_parsed = load(target_yaml.read_text(), _MAPPING_SCHEMA).data
        if target_parsed.get("canonical-feeder-id") is not None:
            raise ValueError(f"Alias {alias.feeder_id!r} in {alias.mapping_file} points to {alias.canonical_feeder_id!r} which resolves to {target_yaml} — but that file is itself an alias.  Alias chains are not allowed; point directly to the source-bearing feeder instead.")

    return entries, feeders_skipped, aliases


def resolve_canonical_feeder_yaml(canonical_feeder_id: str, mappings_dir: Path) -> Path:
    """Find the YAML file for a canonical feeder, searching by role priority.

    Searches subdirectories in priority order: stablecoins/, protocols/,
    curators/, vaults/.  Returns the path to the first matching YAML file
    whose filename stem equals *canonical_feeder_id*.

    :param canonical_feeder_id:
        The feeder-id slug to look up.

    :param mappings_dir:
        Root directory containing role subdirectories (e.g. ``eth_defi/data/feeds``).

    :raises FileNotFoundError:
        When no matching YAML file is found in any role subdirectory.
    """

    for role in ROLE_PRIORITY:
        subdir = ROLE_SUBDIRS[role]
        candidate = mappings_dir / subdir / f"{canonical_feeder_id}.yaml"
        if candidate.exists():
            return candidate

    raise FileNotFoundError(f"No YAML file found for canonical feeder-id {canonical_feeder_id!r} in any role subdirectory of {mappings_dir}")


def resolve_feeder_id(feeder_id: str, aliases: Sequence[FeederAlias]) -> str:
    """Resolve a feeder_id through the alias mapping.

    Returns the canonical feeder_id when the input is an alias,
    or the input unchanged when it is not an alias.

    :param feeder_id:
        Feeder-id to resolve.

    :param aliases:
        Alias list returned by :py:func:`load_post_sources`.
    """

    for alias in aliases:
        if alias.feeder_id == feeder_id:
            return alias.canonical_feeder_id
    return feeder_id


def load_feeder_metadata(yaml_path: Path) -> dict:
    """Load a single feeder YAML file and return its metadata as a plain dict.

    Uses the shared feeder schema for validation, including slug format
    validation via :py:func:`_validate_slug` and role validation via
    :py:func:`_validate_role`.  Asserts that ``feeder-id`` matches the
    filename stem to catch slug/filename mismatches.

    This function provides a public API for modules that need feeder
    metadata (slug, name, website, twitter, linkedin, rss) without
    pulling in the full feed-collection machinery of
    :py:func:`load_post_sources`.

    :param yaml_path:
        Path to a feeder YAML file.

    :return:
        Dict with keys: ``feeder-id``, ``name``, ``role``, ``website``,
        ``twitter``, ``linkedin``, ``rss``, etc.
    """
    parsed = load(yaml_path.read_text(), _MAPPING_SCHEMA).data
    feeder_id = _validate_slug(parsed.get("feeder-id"), "feeder-id", yaml_path)
    _validate_role(parsed.get("role"), yaml_path)
    assert feeder_id == yaml_path.stem, f"feeder-id {feeder_id!r} does not match filename {yaml_path.stem!r} in {yaml_path}"
    return parsed
