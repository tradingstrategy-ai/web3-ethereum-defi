"""Vault curator identification and metadata.

Vault curators are professional risk managers and strategy operators that
brand their vaults by embedding their organisation name in the vault's
token name or display name.  Examples include `Gauntlet`_, `RE7 Labs`_,
`Steakhouse Financial`_, and `MEV Capital`_.

.. _Gauntlet: https://www.gauntlet.xyz
.. _RE7 Labs: https://re7.capital
.. _Steakhouse Financial: https://steakhouse.financial
.. _MEV Capital: https://www.mevcapital.com

This module provides:

- **Curator identification** — given a vault name, detect which curator
  manages it using word-boundary regex matching against known curator
  names loaded from YAML feeder files
- **Protocol-curated detection** — some vaults are operated by the
  protocol itself (Ostium, Gains Network, Hyperliquid HLP, Lighter LLP) rather
  than a third-party curator; these are identified by address lookups
  against known system vault address sets
- **Curator metadata loading** — load curator metadata from the shared
  feeder YAML files in ``eth_defi/data/feeds/curators/``
- **R2 metadata export** — upload per-curator metadata JSON and an
  aggregate index to Cloudflare R2 for frontend consumption

Curator YAML files use the shared feeder schema defined in
:py:mod:`eth_defi.feed.sources`.  Each file lives under
``eth_defi/data/feeds/curators/`` and follows this format::

    feeder-id: gauntlet
    name: Gauntlet
    role: curator
    website: https://www.gauntlet.xyz
    twitter: gauntlet_xyz
    linkedin: gauntlet-xyz
    rss: https://medium.com/feed/gauntlet-networks

Canonical feeder aliases
~~~~~~~~~~~~~~~~~~~~~~~~

When the same organisation appears as both a curator and a stablecoin
issuer or vault protocol, duplicate feed fetching is avoided by using
``canonical-feeder-id``.  An alias YAML file contains only identity
metadata — no feed source fields (twitter, linkedin, rss)::

    feeder-id: ethena
    name: Ethena
    role: curator
    canonical-feeder-id: usde

The priority order determines which role keeps the feed sources:

1. **Stablecoin** — highest priority, always keeps feeds
2. **Protocol** — keeps feeds only when no stablecoin overlap exists
3. **Curator** — lowest priority, defers to stablecoin or protocol

Metadata inheritance happens at export time:
:py:func:`build_curator_metadata_json` resolves the canonical feeder
YAML by role priority and inherits website, twitter, linkedin and rss
from it.

Post resolution happens at consumption time — consumers use
:py:func:`~eth_defi.feed.sources.resolve_feeder_id` to map an alias
feeder_id to the canonical feeder_id, then look up posts under that
canonical feeder's tracked sources.  The ``canonical_feeder_id`` field
is included in the exported :py:class:`CuratorMetadata` JSON so that
frontends can resolve without accessing YAML files.

Identification approach
~~~~~~~~~~~~~~~~~~~~~~~

The curator name is matched against the vault display name using
word-boundary regular expressions (``\\b``).  This avoids false
positives such as bare ``"Gamma"`` matching ``"GammaSwap V1"`` or
``"August"`` matching ``"Prize imToken August Campaign"``.

For curators whose YAML ``name`` field alone is insufficient (e.g.
``"RE7 Labs"`` vaults often appear as just ``"RE7 …"``), additional
short patterns are registered in :py:data:`CURATOR_NAME_PATTERNS`.

Protocol-curated vaults are identified before name matching:

- **Ostium** and **Gains Network** — all vaults on these protocols are
  protocol-curated (no external curator)
- **Hyperliquid** — system vaults (HLP parent, HLP children, Liquidator)
  are identified by address against
  :py:data:`eth_defi.hyperliquid.constants.HYPERLIQUID_SYSTEM_VAULT_ADDRESSES`
- **Lighter** — the LLP system pool is identified by address against
  :py:data:`eth_defi.lighter.constants.LIGHTER_SYSTEM_POOL_ADDRESSES`

Usage example::

    from eth_defi.vault.curator import identify_curator, get_curator_name

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="gtUSDC",
        vault_name="Gauntlet USDC Core",
        vault_address="0x1234…",
        protocol_slug="morpho",
    )
    assert slug == "gauntlet"
    assert get_curator_name(slug) == "Gauntlet"

    # Protocol-curated vault — returns the protocol slug itself
    slug = identify_curator(
        chain_id=999,
        vault_token_symbol="HLP",
        vault_name="Hyperliquid Liquidity Pool",
        vault_address="0xdfc24b077bc1425ad1dea75bcb6f8158e10df303",
        protocol_slug="hyperliquid",
    )
    assert slug == "hyperliquid"
    assert get_curator_name(slug) == "Hyperliquid"
    assert is_protocol_curator(slug)
"""

import json
import logging
import re
from pathlib import Path
from typing import TypedDict

from eth_defi.feed.sources import load_feeder_metadata, resolve_canonical_feeder_yaml
from eth_defi.hyperliquid.constants import HYPERLIQUID_SYSTEM_VAULT_ADDRESSES
from eth_defi.lighter.constants import LIGHTER_SYSTEM_POOL_ADDRESSES
from eth_defi.research.sparkline import upload_to_r2_compressed

logger = logging.getLogger(__name__)

#: Base directory for shared vault protocol and curator data.
VAULTS_DATA_DIR: Path = Path(__file__).parent.parent / "data" / "vaults"

#: Directory containing formatted 256x256 PNG logos.
FORMATTED_LOGOS_DIR: Path = VAULTS_DATA_DIR / "formatted_logos"

#: Logo variants supported by the shared vault logo folder.
LOGO_VARIANTS: tuple[str, ...] = ("generic", "dark", "light")

#: Path to the curator feeder YAML files.
#:
#: These files use the shared feeder schema from
#: :py:mod:`eth_defi.feed.sources` and are also consumed by the
#: feed post collector.
CURATORS_DATA_DIR: Path = Path(__file__).parent.parent / "data" / "feeds" / "curators"

#: Protocol slugs where **all** vaults are protocol-curated.
#:
#: For these protocols there is no external curator — the protocol
#: itself operates every vault.  The slug values correspond to
#: :py:func:`eth_defi.research.vault_metrics.slugify_protocol` output.
PROTOCOL_CURATED_SLUGS: set[str] = {
    "gains-network",
    "ostium",
}

#: Legacy protocol slug aliases that should resolve to the canonical
#: protocol-curator slug emitted by :py:func:`identify_curator`.
PROTOCOL_CURATOR_SLUG_ALIASES: dict[str, str] = {
    "gtrade": "gains-network",
}

#: Complete set of protocol slugs that can appear as curator slugs.
#:
#: Includes both blanket protocol-curated slugs and protocols whose
#: *system vaults* are protocol-curated (Hyperliquid HLP, Lighter LLP).
#: Use :py:func:`is_protocol_curator` to check membership.
ALL_PROTOCOL_CURATOR_SLUGS: set[str] = PROTOCOL_CURATED_SLUGS | {
    "hyperliquid",
    "lighter",
}

#: Human-readable names for protocol-curator slugs.
#:
#: These names are used by :py:func:`get_curator_name` when the curator
#: slug matches a protocol rather than a third-party curator YAML file.
PROTOCOL_CURATOR_NAMES: dict[str, str] = {
    "gains-network": "Gains Network",
    "ostium": "Ostium",
    "hyperliquid": "Hyperliquid",
    "lighter": "Lighter",
}

#: Additional name patterns for curator matching.
#:
#: Maps curator slug to a list of extra patterns (beyond the YAML
#: ``name`` field) that should also trigger a match.  Each pattern
#: is compiled as a word-boundary regex (``\\bPATTERN\\b``).
#:
#: Use full compound names to avoid false positives — e.g.
#: ``"Gamma Strategies"`` not ``"Gamma"`` (which would match
#: ``"GammaSwap V1"``).
CURATOR_NAME_PATTERNS: dict[str, list[str]] = {
    "re7-labs": ["RE7"],
    "steakhouse-financial": ["Steakhouse", "Smokehouse"],
    "growi-finance": ["Growi"],
    "avantgarde-finance": ["Avantgarde"],
    "fisher8-capital": ["Fisher8"],
    "ignight-capital": ["Ignight"],
    "clearstar-labs": ["Clearstar"],
    "insertive-capital": ["Insertive"],
    "varlamore-capital": ["Varlamore"],
    "k3-capital": ["K3 Capital", "K3"],
    "edge-and-hedge": ["Edge & Hedge", "Edge and Hedge"],
    "damm-capital": ["DAMM Capital"],
    "august-digital": ["August Digital"],
    "pareto-technologies": ["Pareto"],
    "tulipa-capital": ["Tulipa"],
    "systemic-strategies": ["Systemic Strategies"],
    "gamma-strategies": ["Gamma Strategies"],
    "rogue-traders": ["Rogue Traders"],
    "b-cube-ai": ["B-CUBE", "BCUBE"],
    "llama-risk": ["LlamaRisk"],
    "9summits": ["9 Summits"],
    "sentora": ["IntoTheBlock"],
    "frax-finance": ["Frax", "FRAX"],
    "usdai": ["USD.AI", "USDai"],
    "agora-finance": ["Agora"],
    "tangent-finance": ["Tangent"],
    "ipor": ["IPOR", "Autopilot"],
    "reservoir": ["Reservoir"],
    "tau": ["TAU"],
    "yo": ["yoUSD", "yoETH", "yoBTC", "yoEUR", "yoGOLD", "yoUSDT", "yUSD", "YO Treasury"],
    "harvest": ["Harvest"],
    "strata": ["Strata-Money", "Strata"],
    "pistachio": ["Pistachio"],
    "xerberus": ["Xerberus"],
    "tid-capital": ["TiD Capital", "TiD"],
    "tanken": ["Tanken"],
    "singularity": ["Singularity"],
    "fija": ["Fija"],
    "woo": ["Woo"],
    "telosc": ["TelosC"],
    "kappa-lab": ["Fire Liquidity Provider"],
    # New curators from Morpho verified list (2026-05-05)
    "b-protocol": ["B.Protocol"],
    "felix": ["Felix"],
    "stake-dao": ["StakeDAO"],
}


class CuratorInfo(TypedDict):
    """Metadata for a single curator loaded from YAML.

    Represents the in-memory view of a curator feeder file
    from ``eth_defi/data/feeds/curators/``.
    """

    #: URL-safe slug identifier, matches the YAML filename stem
    #: and ``feeder-id`` field (e.g. ``"gauntlet"``, ``"re7-labs"``).
    slug: str

    #: Human-readable display name (e.g. ``"Gauntlet"``, ``"RE7 Labs"``).
    name: str

    #: Company website URL, or ``None`` if not configured in YAML.
    website: str | None

    #: Twitter/X handle without ``@`` prefix (e.g. ``"gauntlet_xyz"``),
    #: or ``None`` if not configured.
    twitter: str | None

    #: LinkedIn company identifier (e.g. ``"gauntlet-xyz"``),
    #: or ``None`` if not configured.
    linkedin: str | None

    #: RSS or Atom feed URL for the curator's blog or newsletter,
    #: or ``None`` if not configured.
    rss: str | None

    #: Whether this is a protocol-native curator (the protocol itself
    #: acts as curator) rather than a third-party risk manager.
    #:
    #: ``True`` for protocol-curated vaults (Ostium, Gains Network, HLP, LLP).
    #: ``False`` for third-party curators (Gauntlet, RE7 Labs, etc.).
    protocol_curator: bool

    #: When set, this curator's feed sources are provided by another
    #: feeder identified by this slug.  The canonical feeder may be in
    #: a different role (e.g. a stablecoin feeder).  Posts for this
    #: curator should be looked up under the canonical feeder's sources.
    #: ``None`` for curators that have their own feed sources.
    canonical_feeder_id: str | None


class CuratorLogos(TypedDict):
    """Logo URLs for a vault curator.

    Logo URLs point to 256x256 PNG files in R2 storage.
    ``None`` if the logo variant is not available.
    """

    #: Generic logo variant for neutral display contexts.
    generic: str | None

    #: Logo for dark background themes when available.
    dark: str | None

    #: Logo for light background themes when available.
    light: str | None


class CuratorMetadata(TypedDict):
    """Curator metadata as exported to JSON for R2 upload.

    This is the public API shape consumed by the frontend.
    Twitter and LinkedIn fields are expanded to full URLs
    rather than bare handles/identifiers.
    """

    #: URL-safe slug identifier (e.g. ``"gauntlet"``, ``"ostium"``).
    slug: str

    #: Human-readable display name.
    name: str

    #: Company website URL, or ``None``.
    website: str | None

    #: Full Twitter/X profile URL (e.g. ``"https://x.com/gauntlet_xyz"``),
    #: or ``None``.
    twitter: str | None

    #: Full LinkedIn company URL
    #: (e.g. ``"https://www.linkedin.com/company/gauntlet-xyz"``),
    #: or ``None``.
    linkedin: str | None

    #: RSS or Atom feed URL, or ``None``.
    rss: str | None

    #: Logo URLs for available 256x256 PNG variants.
    logos: CuratorLogos

    #: Whether this curator is the protocol itself (not a third party).
    #:
    #: ``True`` for protocol-curated vaults (e.g. Ostium, Gains Network, HLP, LLP).
    #: ``False`` for third-party curators (e.g. Gauntlet, RE7 Labs).
    protocol_curator: bool

    #: When this curator is an alias, the slug of the canonical feeder
    #: whose posts should be used.  ``None`` for non-alias curators.
    #: Consumers should look up posts under this feeder_id instead.
    canonical_feeder_id: str | None


#: In-process cache for :py:func:`load_curator_map`.
_cached_curator_map: dict[str, CuratorInfo] | None = None

#: In-process cache for :py:func:`_build_matching_patterns`.
_cached_patterns: list[tuple[re.Pattern, str]] | None = None


def _load_curator_yaml(yaml_path: Path) -> CuratorInfo:
    """Load a single curator YAML file into a :py:class:`CuratorInfo`.

    Uses :py:func:`eth_defi.feed.sources.load_feeder_metadata` for
    schema validation, then maps the raw dict to the typed structure.

    :param yaml_path:
        Path to a curator YAML file.
    """
    parsed = load_feeder_metadata(yaml_path)
    return CuratorInfo(
        slug=parsed["feeder-id"],
        name=parsed["name"],
        website=parsed.get("website"),
        twitter=parsed.get("twitter"),
        linkedin=parsed.get("linkedin"),
        rss=parsed.get("rss"),
        protocol_curator=parsed["feeder-id"] in ALL_PROTOCOL_CURATOR_SLUGS,
        canonical_feeder_id=parsed.get("canonical-feeder-id"),
    )


def load_curator_map() -> dict[str, CuratorInfo]:
    """Load all curator metadata from YAML files.

    Returns a dict mapping slug to :py:class:`CuratorInfo`.
    Cached in-process after first call (same pattern as
    :py:func:`eth_defi.stablecoin_metadata.load_all_stablecoin_metadata`).

    :return:
        Dict keyed by curator slug.
    """
    global _cached_curator_map  # noqa: PLW0603

    if _cached_curator_map is not None:
        return _cached_curator_map

    result: dict[str, CuratorInfo] = {}
    for yaml_path in sorted(CURATORS_DATA_DIR.glob("*.yaml")):
        info = _load_curator_yaml(yaml_path)
        result[info["slug"]] = info

    _cached_curator_map = result
    return result


def _build_matching_patterns() -> list[tuple[re.Pattern, str]]:
    """Build word-boundary regex patterns for curator name matching.

    Combines each curator's YAML ``name`` field with any extra
    patterns from :py:data:`CURATOR_NAME_PATTERNS`.  Patterns are
    sorted by length descending so that longer (more specific)
    patterns match first, preventing ambiguous short matches.

    :return:
        List of ``(compiled_regex, curator_slug)`` pairs, longest
        pattern first.
    """
    global _cached_patterns  # noqa: PLW0603

    if _cached_patterns is not None:
        return _cached_patterns

    curator_map = load_curator_map()
    raw_pairs: list[tuple[str, str]] = []

    for slug, info in curator_map.items():
        # Always include the YAML name
        raw_pairs.append((info["name"], slug))
        # Include any supplementary patterns
        for extra in CURATOR_NAME_PATTERNS.get(slug, []):
            raw_pairs.append((extra, slug))

    # Sort by pattern length descending — longest match wins
    raw_pairs.sort(key=lambda pair: len(pair[0]), reverse=True)

    patterns = []
    for pattern_text, slug in raw_pairs:
        regex = re.compile(r"\b" + re.escape(pattern_text) + r"\b", re.IGNORECASE)
        patterns.append((regex, slug))

    _cached_patterns = patterns
    return patterns


def identify_curator(
    chain_id: int,
    vault_token_symbol: str,
    vault_name: str,
    vault_address: str,
    protocol_slug: str = "",
) -> str | None:
    """Identify the curator managing a vault.

    Checks protocol-curated status first (by protocol slug and vault
    address), then falls back to word-boundary regex matching against
    the vault display name.

    :param chain_id:
        Chain ID where the vault is deployed.

    :param vault_token_symbol:
        The vault's share token symbol (e.g. ``"gtUSDC"``).

    :param vault_name:
        The vault's human-readable display name, which curators
        typically brand with their organisation name.

    :param vault_address:
        The vault's on-chain address (hex or synthetic format).

    :param protocol_slug:
        Slugified protocol name from
        :py:func:`eth_defi.research.vault_metrics.slugify_protocol`
        (e.g. ``"morpho"``, ``"hyperliquid"``, ``"lighter"``).

    :return:
        Curator slug (e.g. ``"gauntlet"``, ``"ostium"``, ``"hyperliquid"``),
        or ``None`` if no curator could be identified.
        For protocol-curated vaults the protocol slug itself is returned.
        Use :py:func:`is_protocol_curator` to distinguish protocol-curated
        from third-party curators.
    """

    del chain_id, vault_token_symbol

    protocol_slug = PROTOCOL_CURATOR_SLUG_ALIASES.get(protocol_slug, protocol_slug)

    # 1. Blanket protocol-curated protocols (all vaults are protocol-operated)
    if protocol_slug in PROTOCOL_CURATED_SLUGS:
        return protocol_slug

    # 2. Hyperliquid system vaults (HLP parent, children, Liquidator)
    if protocol_slug == "hyperliquid":
        if vault_address.lower() in HYPERLIQUID_SYSTEM_VAULT_ADDRESSES:
            return "hyperliquid"

    # 3. Lighter system pools (LLP)
    if protocol_slug == "lighter":
        if vault_address in LIGHTER_SYSTEM_POOL_ADDRESSES:
            return "lighter"

    # 4. Word-boundary regex matching against vault name
    patterns = _build_matching_patterns()
    for regex, slug in patterns:
        if regex.search(vault_name):
            return slug

    return None


def is_protocol_curator(slug: str) -> bool:
    """Check whether a curator slug represents a protocol-curated vault.

    Protocol-curated means the protocol itself operates the vault
    rather than a third-party risk manager.

    :param slug:
        Curator slug as returned by :py:func:`identify_curator`.

    :return:
        ``True`` if the slug identifies a protocol acting as its own curator.
    """
    return slug in ALL_PROTOCOL_CURATOR_SLUGS


def get_curator_name(slug: str) -> str | None:
    """Look up the human-readable name for a curator slug.

    Handles both third-party curators (looked up from YAML) and
    protocol-curated slugs (looked up from
    :py:data:`PROTOCOL_CURATOR_NAMES`).

    :param slug:
        Curator slug as returned by :py:func:`identify_curator`.

    :return:
        Human-readable curator name, or ``None`` if not found.
    """
    # Check protocol-curator names first
    protocol_name = PROTOCOL_CURATOR_NAMES.get(slug)
    if protocol_name:
        return protocol_name
    curator_map = load_curator_map()
    info = curator_map.get(slug)
    return info["name"] if info else None


# ---------------------------------------------------------------------------
# R2 metadata export
# ---------------------------------------------------------------------------


def get_curator_available_logos(slug: str) -> dict[str, bool]:
    """Check which logo variants are available for a curator.

    Curator and vault protocol logos share
    ``eth_defi/data/vaults/formatted_logos/{slug}/``.

    :param slug:
        Curator slug, e.g. ``"gauntlet"``.

    :return:
        Dictionary keyed by ``generic``, ``dark`` and ``light``.
    """
    logo_dir = FORMATTED_LOGOS_DIR / slug
    return {variant: (logo_dir / f"{variant}.png").exists() for variant in LOGO_VARIANTS}


def _build_curator_logo_urls(slug: str, public_url: str = "") -> CuratorLogos:
    """Build public logo URLs for available curator logo variants.

    :param slug:
        Curator slug.

    :param public_url:
        Public base URL for constructing logo URLs.

    :return:
        Logo URL mapping for JSON export.
    """
    available_logos = get_curator_available_logos(slug)
    public_url = public_url.rstrip("/") if public_url else ""
    return CuratorLogos(
        generic=f"{public_url}/curator-metadata/{slug}/generic.png" if available_logos["generic"] and public_url else None,
        dark=f"{public_url}/curator-metadata/{slug}/dark.png" if available_logos["dark"] and public_url else None,
        light=f"{public_url}/curator-metadata/{slug}/light.png" if available_logos["light"] and public_url else None,
    )


def build_curator_metadata_json(yaml_path: Path, public_url: str = "") -> CuratorMetadata:
    """Build a :py:class:`CuratorMetadata` dict from a curator YAML file.

    Twitter handles are expanded to full ``https://x.com/{handle}`` URLs.
    LinkedIn company identifiers are expanded to full
    ``https://www.linkedin.com/company/{id}`` URLs.

    :param yaml_path:
        Path to a curator YAML file.

    :param public_url:
        Public base URL for constructing logo URLs.

    :return:
        Metadata dict ready for JSON serialisation.
    """
    info = _load_curator_yaml(yaml_path)
    slug = info["slug"]

    # If alias, inherit metadata from the canonical feeder.
    # Derive the feeds root from yaml_path: curators/foo.yaml -> parent.parent
    if info["canonical_feeder_id"]:
        feeds_root = yaml_path.parent.parent
        canonical_yaml = resolve_canonical_feeder_yaml(
            info["canonical_feeder_id"],
            mappings_dir=feeds_root,
        )
        canonical = load_feeder_metadata(canonical_yaml)
        website = canonical.get("website")
        twitter_handle = canonical.get("twitter")
        linkedin_id = canonical.get("linkedin")
        rss = canonical.get("rss")
    else:
        website = info["website"]
        twitter_handle = info["twitter"]
        linkedin_id = info["linkedin"]
        rss = info["rss"]

    twitter_url: str | None = None
    if twitter_handle:
        twitter_url = f"https://x.com/{twitter_handle}"

    linkedin_url: str | None = None
    if linkedin_id:
        linkedin_url = f"https://www.linkedin.com/company/{linkedin_id}"

    return CuratorMetadata(
        slug=slug,
        name=info["name"],
        website=website,
        twitter=twitter_url,
        linkedin=linkedin_url,
        rss=rss,
        logos=_build_curator_logo_urls(slug, public_url=public_url),
        protocol_curator=info["protocol_curator"],
        canonical_feeder_id=info["canonical_feeder_id"],
    )


def _build_protocol_curator_entries(public_url: str = "") -> list[CuratorMetadata]:
    """Build metadata entries for protocol-curated slugs.

    One entry per protocol in :py:data:`PROTOCOL_CURATOR_NAMES` is
    created or loaded from YAML so that frontend slug lookups always
    find metadata for every slug that :py:func:`identify_curator` can emit.

    :param public_url:
        Public base URL for constructing logo URLs.

    :return:
        List of :py:class:`CuratorMetadata` dicts.
    """
    entries: list[CuratorMetadata] = []
    for slug, name in sorted(PROTOCOL_CURATOR_NAMES.items()):
        yaml_path = CURATORS_DATA_DIR / f"{slug}.yaml"
        if yaml_path.exists():
            entries.append(build_curator_metadata_json(yaml_path, public_url=public_url))
        else:
            entries.append(
                CuratorMetadata(
                    slug=slug,
                    name=name,
                    website=None,
                    twitter=None,
                    linkedin=None,
                    rss=None,
                    logos=_build_curator_logo_urls(slug, public_url=public_url),
                    protocol_curator=True,
                    canonical_feeder_id=None,
                )
            )
    return entries


def process_and_upload_curator_metadata(  # noqa: PLR0917
    yaml_path: Path,
    bucket_name: str,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    public_url: str = "",
    key_prefix: str = "",
) -> CuratorMetadata:
    """Process and upload a single curator's metadata and logos to R2.

    Uploads:

    - ``curator-metadata/{key_prefix}{slug}/metadata.json`` — JSON metadata
    - ``curator-metadata/{key_prefix}{slug}/{variant}.png`` — 256x256 logo

    :param yaml_path:
        Path to the curator YAML file.

    :param bucket_name:
        R2 bucket name.

    :param endpoint_url:
        R2 API endpoint URL.

    :param access_key_id:
        R2 access key ID.

    :param secret_access_key:
        R2 secret access key.

    :param public_url:
        Public base URL for constructing logo URLs in metadata.

    :param key_prefix:
        Optional prefix for R2 keys (e.g. ``"test-"`` for testing).

    :return:
        The processed :py:class:`CuratorMetadata`.
    """
    metadata = build_curator_metadata_json(yaml_path, public_url=public_url)
    slug = metadata["slug"]

    json_bytes = json.dumps(metadata, indent=2).encode()
    metadata_uploaded = upload_to_r2_compressed(
        payload=json_bytes,
        bucket_name=bucket_name,
        object_name=f"curator-metadata/{key_prefix}{slug}/metadata.json",
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        content_type="application/json",
        skip_if_current=True,
    )
    logger.info("%s curator metadata for: %s", "Uploaded" if metadata_uploaded else "Skipped unchanged", slug)

    logo_dir = FORMATTED_LOGOS_DIR / slug
    for variant in LOGO_VARIANTS:
        logo_path = logo_dir / f"{variant}.png"
        if logo_path.exists():
            logo_uploaded = upload_to_r2_compressed(
                payload=logo_path.read_bytes(),
                bucket_name=bucket_name,
                object_name=f"curator-metadata/{key_prefix}{slug}/{variant}.png",
                endpoint_url=endpoint_url,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                content_type="image/png",
                skip_if_current=True,
            )
            logger.info(
                "%s %s logo for curator: %s",
                "Uploaded" if logo_uploaded else "Skipped unchanged",
                variant,
                slug,
            )

    return metadata


def upload_protocol_curator_metadata(  # noqa: PLR0917
    bucket_name: str,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    public_url: str = "",
    key_prefix: str = "",
) -> list[CuratorMetadata]:
    """Upload metadata entries for all protocol-curated slugs to R2.

    Ensures that ``curator-metadata/{slug}/metadata.json`` exists for
    every protocol in :py:data:`ALL_PROTOCOL_CURATOR_SLUGS` so that
    frontend slug lookups never 404.

    :param bucket_name:
        R2 bucket name.

    :param endpoint_url:
        R2 API endpoint URL.

    :param access_key_id:
        R2 access key ID.

    :param secret_access_key:
        R2 secret access key.

    :param public_url:
        Public base URL for constructing logo URLs in metadata.

    :param key_prefix:
        Optional prefix for R2 keys.

    :return:
        List of uploaded :py:class:`CuratorMetadata` entries.
    """
    entries = _build_protocol_curator_entries(public_url=public_url)

    for metadata in entries:
        slug = metadata["slug"]

        json_bytes = json.dumps(metadata, indent=2).encode()
        metadata_uploaded = upload_to_r2_compressed(
            payload=json_bytes,
            bucket_name=bucket_name,
            object_name=f"curator-metadata/{key_prefix}{slug}/metadata.json",
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            content_type="application/json",
            skip_if_current=True,
        )
        logger.info(
            "%s protocol-curator metadata for: %s",
            "Uploaded" if metadata_uploaded else "Skipped unchanged",
            slug,
        )

        logo_dir = FORMATTED_LOGOS_DIR / slug
        for variant in LOGO_VARIANTS:
            logo_path = logo_dir / f"{variant}.png"
            if logo_path.exists():
                logo_uploaded = upload_to_r2_compressed(
                    payload=logo_path.read_bytes(),
                    bucket_name=bucket_name,
                    object_name=f"curator-metadata/{key_prefix}{slug}/{variant}.png",
                    endpoint_url=endpoint_url,
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    content_type="image/png",
                    skip_if_current=True,
                )
                logger.info(
                    "%s %s logo for protocol-curator: %s",
                    "Uploaded" if logo_uploaded else "Skipped unchanged",
                    variant,
                    slug,
                )

    return entries


def build_curator_index(public_url: str = "") -> list[CuratorMetadata]:
    """Build the aggregate curator metadata index.

    Loads all curator YAML files and appends synthetic entries for
    protocol-curated slugs.  The result is suitable for JSON
    serialisation and R2 upload as ``curator-metadata/index.json``.

    :param public_url:
        Public base URL for constructing logo URLs.

    :return:
        List of :py:class:`CuratorMetadata` dicts for all known curators.
    """
    index: list[CuratorMetadata] = []
    for yaml_path in sorted(CURATORS_DATA_DIR.glob("*.yaml")):
        index.append(build_curator_metadata_json(yaml_path, public_url=public_url))

    existing_slugs = {entry["slug"] for entry in index}
    index.extend(entry for entry in _build_protocol_curator_entries(public_url=public_url) if entry["slug"] not in existing_slugs)

    return index


def upload_curator_index(  # noqa: PLR0917
    bucket_name: str,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    public_url: str = "",
    key_prefix: str = "",
) -> list[CuratorMetadata]:
    """Build and upload the aggregate curator index to R2.

    Uploads to ``curator-metadata/{key_prefix}index.json``.

    :param bucket_name:
        R2 bucket name.

    :param endpoint_url:
        R2 API endpoint URL.

    :param access_key_id:
        R2 access key ID.

    :param secret_access_key:
        R2 secret access key.

    :param public_url:
        Public base URL for constructing logo URLs in metadata.

    :param key_prefix:
        Optional prefix for R2 keys.

    :return:
        The full index list.
    """
    index = build_curator_index(public_url=public_url)

    json_bytes = json.dumps(index, indent=2).encode()
    index_uploaded = upload_to_r2_compressed(
        payload=json_bytes,
        bucket_name=bucket_name,
        object_name=f"curator-metadata/{key_prefix}index.json",
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        content_type="application/json",
        skip_if_current=True,
    )
    logger.info(
        "%s curator index with %d entries",
        "Uploaded" if index_uploaded else "Skipped unchanged",
        len(index),
    )

    return index
