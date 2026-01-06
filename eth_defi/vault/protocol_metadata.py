"""Vault protocol metadata handling.

Read and export vault protocol metadata from YAML files to JSON format
for upload to R2 storage.
"""

import json
import logging
from pathlib import Path
from typing import TypedDict

from strictyaml import load


logger = logging.getLogger(__name__)


#: Base directory for vault data
VAULTS_DATA_DIR = Path(__file__).parent.parent / "data" / "vaults"

#: Directory containing protocol metadata YAML files
METADATA_DIR = VAULTS_DATA_DIR / "metadata"

#: Directory containing formatted 256x256 PNG logos
FORMATTED_LOGOS_DIR = VAULTS_DATA_DIR / "formatted_logos"

#: All link fields that should be present in the output
LINK_FIELDS = [
    "homepage",
    "app",
    "twitter",
    "github",
    "documentation",
    "defillama",
    "audits",
    "fees",
    "trading_strategy",
    "integration_documentation",
]


class VaultProtocolLinks(TypedDict):
    """Links associated with a vault protocol.

    All fields are optional and may be `None` if not available.
    """

    #: Protocol homepage URL
    homepage: str | None

    #: Direct link to the dApp page for vaults
    app: str | None

    #: Link to Twitter/X account
    twitter: str | None

    #: Link to smart contracts GitHub repository
    github: str | None

    #: Link to developer documentation
    documentation: str | None

    #: Link to DefiLlama protocol page
    defillama: str | None

    #: Link to security audits page or specific audit
    audits: str | None

    #: Link to fee structure documentation
    fees: str | None

    #: Link to TradingStrategy.ai protocol listing
    trading_strategy: str | None

    #: Link to this library's integration documentation
    integration_documentation: str | None


class VaultProtocolLogos(TypedDict):
    """Logo URLs for a vault protocol.

    Logo URLs point to 256x256 PNG files in R2 storage.
    `None` if the logo variant is not available.
    """

    #: Logo for dark backgrounds (light-coloured logo)
    dark: str | None

    #: Logo for light backgrounds (dark-coloured logo)
    light: str | None


class VaultProtocolMetadata(TypedDict):
    """Complete vault protocol metadata as exported to JSON.

    This structure is used for frontend consumption and API responses.
    """

    #: Human-readable protocol name
    name: str

    #: Lowercase slug with dashes (e.g., "lagoon-finance")
    slug: str

    #: One-line description of the protocol
    short_description: str | None

    #: Multi-paragraph Markdown description
    long_description: str | None

    #: Multi-paragraph Markdown description of fees
    fee_description: str | None

    #: Protocol links
    links: VaultProtocolLinks

    #: List of example vault contract URLs on blockchain explorers
    example_smart_contracts: list[str]

    #: Logo URLs for different background themes
    logos: VaultProtocolLogos


def read_protocol_metadata(yaml_path: Path) -> dict:
    """Read and parse a protocol metadata YAML file.

    :param yaml_path:
        Path to the YAML file

    :return:
        Parsed YAML as a dictionary
    """
    yaml_content = yaml_path.read_text()
    parsed = load(yaml_content)
    return parsed.data


def get_available_logos(slug: str) -> dict[str, bool]:
    """Check which logo variants are available for a protocol.

    :param slug:
        Protocol slug (e.g., "euler", "lagoon-finance")

    :return:
        Dictionary with 'dark' and 'light' keys indicating availability
    """
    logo_dir = FORMATTED_LOGOS_DIR / slug
    return {
        "dark": (logo_dir / "dark.png").exists(),
        "light": (logo_dir / "light.png").exists(),
    }


def build_metadata_json(yaml_path: Path, public_url: str) -> VaultProtocolMetadata:
    """Build a VaultProtocolMetadata dict from a YAML file.

    Reads the YAML metadata, adds logo URLs based on available formatted logos,
    and ensures all fields are present (with None for missing values).

    :param yaml_path:
        Path to the protocol metadata YAML file

    :param public_url:
        Public base URL for constructing logo URLs (e.g., "https://pub-xyz.r2.dev")

    :return:
        Complete VaultProtocolMetadata dict ready for JSON export
    """
    data = read_protocol_metadata(yaml_path)
    slug = data.get("slug") or yaml_path.stem

    # Normalise empty strings to None
    def normalise(value):
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else None
        return value

    # Build links dict with all fields present
    links_data = data.get("links") or {}
    links: VaultProtocolLinks = {field: normalise(links_data.get(field)) for field in LINK_FIELDS}

    # Build example_smart_contracts list, filtering empty entries
    raw_contracts = data.get("example_smart_contracts") or []
    example_smart_contracts = [c for c in raw_contracts if c and isinstance(c, str) and c.strip()]

    # Build logo URLs based on availability
    # URL format: {public_url}/vault-protocol-metadata/{slug}/{variant}.png
    available = get_available_logos(slug)
    public_url = public_url.rstrip("/")
    logos: VaultProtocolLogos = {
        "dark": f"{public_url}/vault-protocol-metadata/{slug}/dark.png" if available["dark"] else None,
        "light": f"{public_url}/vault-protocol-metadata/{slug}/light.png" if available["light"] else None,
    }

    return VaultProtocolMetadata(
        name=data.get("name") or slug,
        slug=slug,
        short_description=normalise(data.get("short_description")),
        long_description=normalise(data.get("long_description")),
        fee_description=normalise(data.get("fee_description")),
        links=links,
        example_smart_contracts=example_smart_contracts,
        logos=logos,
    )


def process_and_upload_protocol_metadata(
    slug: str,
    bucket_name: str,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    public_url: str,
    key_prefix: str = "",
) -> VaultProtocolMetadata:
    """Process and upload a single protocol's metadata and logos to R2.

    Reads the YAML metadata file, builds the JSON with logo URLs,
    and uploads the JSON and any available logo files to R2.

    :param slug:
        Protocol slug (e.g., "euler", "lagoon-finance")

    :param bucket_name:
        R2 bucket name

    :param endpoint_url:
        R2 API endpoint URL

    :param access_key_id:
        R2 access key ID

    :param secret_access_key:
        R2 secret access key

    :param public_url:
        Public base URL for constructing logo URLs in metadata

    :param key_prefix:
        Optional prefix for R2 keys (e.g., "test-" for testing)

    :return:
        The processed VaultProtocolMetadata dict
    """
    from eth_defi.research.sparkline import upload_to_r2_compressed

    yaml_path = METADATA_DIR / f"{slug}.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"Metadata file not found: {yaml_path}")

    # Build metadata with logo URLs
    metadata = build_metadata_json(yaml_path, public_url)

    logger.info("Uploading metadata for protocol: %s", slug)

    # Upload metadata JSON
    # Object name format: vault-protocol-metadata/{key_prefix}{slug}/metadata.json
    json_bytes = json.dumps(metadata, indent=2).encode()
    upload_to_r2_compressed(
        payload=json_bytes,
        bucket_name=bucket_name,
        object_name=f"vault-protocol-metadata/{key_prefix}{slug}/metadata.json",
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        content_type="application/json",
    )

    # Upload available logos
    logo_dir = FORMATTED_LOGOS_DIR / slug
    for variant in ["dark", "light"]:
        logo_path = logo_dir / f"{variant}.png"
        if logo_path.exists():
            logger.info("Uploading %s logo for protocol: %s", variant, slug)
            upload_to_r2_compressed(
                payload=logo_path.read_bytes(),
                bucket_name=bucket_name,
                object_name=f"vault-protocol-metadata/{key_prefix}{slug}/{variant}.png",
                endpoint_url=endpoint_url,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                content_type="image/png",
            )

    return metadata
