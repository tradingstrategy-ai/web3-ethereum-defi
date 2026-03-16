"""Stablecoin metadata handling.

Manages stablecoin classification (symbol sets, stablecoin-like detection)
and rich metadata (name, homepage, description, external links) for ~183 stablecoins.

Metadata is stored as individual YAML files under ``eth_defi/data/stablecoins/``
and loaded on demand with in-process caching. The module follows the same pattern
as vault protocol metadata in :py:mod:`eth_defi.vault.protocol_metadata`.

Stablecoin classification
~~~~~~~~~~~~~~~~~~~~~~~~~

Three disjoint symbol sets classify tokens:

- :py:data:`STABLECOIN_LIKE` — primary stablecoins (USDC, DAI, GHO, …)
- :py:data:`YIELD_BEARING_STABLES` — rebasing/yield stables (sUSDe, sUSG, …)
- :py:data:`WRAPPED_STABLECOIN_LIKE` — interest-bearing wrappers (cUSDC, aDAI, …)

Their union :py:data:`ALL_STABLECOIN_LIKE` is used by :py:func:`is_stablecoin_like`
for quick symbol-based stablecoin detection.

Loading metadata
~~~~~~~~~~~~~~~~

.. code-block:: python

    from eth_defi.stablecoin_metadata import load_all_stablecoin_metadata

    meta = load_all_stablecoin_metadata()
    usdc = meta["USDC"][0]
    print(usdc["name"], usdc["homepage"])

R2 upload
~~~~~~~~~

The export script ``scripts/erc-4626/export-protocol-metadata.py`` uploads
stablecoin metadata to the same R2 bucket as vault protocol metadata,
under the ``stablecoin-metadata/`` prefix:

.. code-block:: text

    stablecoin-metadata/{slug}/metadata.json

Run the export with:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/export-protocol-metadata.py

Required environment variables (shared with vault protocol metadata):

- ``R2_VAULT_METADATA_BUCKET_NAME``
- ``R2_VAULT_METADATA_ACCESS_KEY_ID``
- ``R2_VAULT_METADATA_SECRET_ACCESS_KEY``
- ``R2_VAULT_METADATA_ENDPOINT_URL``
- ``R2_VAULT_METADATA_PUBLIC_URL``
"""

import json
import logging
from pathlib import Path
from typing import TypedDict

from strictyaml import load


logger = logging.getLogger(__name__)


#: Base directory for stablecoin data YAML files
STABLECOINS_DATA_DIR = Path(__file__).parent / "data" / "stablecoins"

#: Directory containing formatted 256x256 PNG logos
STABLECOIN_FORMATTED_LOGOS_DIR = STABLECOINS_DATA_DIR / "formatted_logos"

#: All link fields that should be present in the output
STABLECOIN_LINK_FIELDS = ["homepage", "coingecko", "defillama", "twitter"]


#: Token symbols that are stablecoin like.
#: Note that it is *not* safe to to check the token symbol to know if a token is a specific stablecoin,
#: but you always need to check the contract address.
#: Checking against this list only works
#: USDf and USDF
STABLECOIN_LIKE = set(
    [
        "ALUSD",
        "AUDT",
        "AUSD",
        "BAC",
        "BDO",
        "BEAN",
        "BOB",
        "BOLD",
        "BUSD",
        "BYUSD",
        "CADC",
        "CEUR",
        "CJPY",
        "CNHT",
        "CRVUSD",
        "CUSD",
        "csUSD",
        "DAI",
        "DJED",
        "DOLA",
        "DOLADUSD",
        "EOSDT",
        "EURA",
        "EURCV",
        "EUROC",
        "EUROe",
        "EURS",
        "EURT",
        "EURe",
        "EUSD",
        "FDUSD",
        "FEI",
        "FRAX",
        "FLEXUSD",
        "feUSD",
        "FUSD",
        "FXD",
        "FXUSD",
        "GBPT",
        "GHO",
        "GHST",
        "GRAI",
        "GUSD",
        "GYD",
        "GYEN",
        "HAI",
        "HUSD",
        "IRON",
        "JCHF",
        "JPYC",
        "KDAI",
        "LISUSD",
        "LUSD",
        "MIM",
        "MIMATIC",
        "MKUSD",
        "MUSD",
        "NUSD",
        "ONC",
        "OUSD",
        "PAR",
        "PAXG",
        "PYUSD",
        "RAI",
        "RLUSD",
        "RUSD",
        "rUSD",
        "SAI",
        "SDAI",
        "SEUR",
        "SFRAX",
        "SILK",
        "STUSD",
        "SUSD",
        "TCNH",
        "TOR",
        "TRYB",
        "TUSD",
        "USC",
        "USD+",
        "USD0",
        "USD1",
        "USD8",
        "USDA",
        "USDB",
        "USDC",
        "USDC.e",
        "USDCV",
        "USDD",
        "USDE",
        "USDe",  # Mantle
        "USDF",
        "USDG",
        "USDH",
        "USDM",
        "USDN",
        "USDO",
        "USDP",
        "USDR",
        "USDS",
        "USDT",
        "USDT.e",
        "USDT0",
        "USD₮",
        "USDV",
        "USDX",
        "USDXL",
        "USDai",
        "USDbC",
        "USDe",
        "USDf",
        "USDs",
        "USDt",
        "USD₮0",
        "USG",
        "USH",
        "USK",
        "USR",
        "UST",
        "USTC",
        "USDtb",
        "USX",
        "USXAU",
        "UTY",
        "UUSD",
        "VAI",
        "VEUR",
        "VST",
        "VUSD",
        "WXDAI",
        "XAUT",
        "XDAI",
        "XIDR",
        "XSGD",
        "XSTUSD",
        "XUSD",
        "YUSD",
        "ZCHF",
        "ZSD",
        "ZUSD",
        "avUSD",
        "bvUSD",
        "crvUSD",
        "dUSD",
        "deUSD",
        "frxUSD",
        "ftUSD",
        "gmUSD",
        "iUSD",
        "jEUR",
        "kUSD",
        "lvlUSD",
        "mUSD",
        "meUSDT",
        "msUSD",
        "plUSD",
        "reUSD",
        "sUSDC",
        "satUSD",
        "scUSD",
        "sosUSDT",
        "vbUSDC",
        "vbUSDT",
        "wM",
        "xUSD",
        "YUSD",
        "MTUSD",
        "ysUSDC",
        "mtUSDC",
        "mtUSDT",
    ]
)


#: Stablecoins which can be used as collateral, but which also have built-in yield bearing function
#: with rebasing.
YIELD_BEARING_STABLES = {"sfrxUSD", "sUSDe", "sUSDai", "sBOLD", "sAUSD", "sUSG", "ynUSDx"}

#: Stablecoins plus their interest wrapped counterparts on Compound and Aave.
#: Also contains other derivates.
WRAPPED_STABLECOIN_LIKE = {"cUSDC", "cUSDT", "sUSD", "aDAI", "cDAI", "tfUSDC", "alUSD", "agEUR", "gmdUSDC", "gDAI", "blUSD"}

#: All stablecoin likes - both interested bearing and non interest bearing.
ALL_STABLECOIN_LIKE = STABLECOIN_LIKE | WRAPPED_STABLECOIN_LIKE | YIELD_BEARING_STABLES


class StablecoinLinks(TypedDict):
    """Links associated with a stablecoin project."""

    #: Project homepage URL
    homepage: str | None

    #: CoinGecko listing URL
    coingecko: str | None

    #: DefiLlama stablecoin page URL
    defillama: str | None

    #: Twitter/X account URL
    twitter: str | None


class StablecoinInfo(TypedDict):
    """Metadata for a single stablecoin-like token project.

    Loaded from YAML files in ``eth_defi/data/stablecoins/``.
    """

    #: Full human-readable name of the token
    name: str

    #: Homepage URL for the project (empty string if unknown)
    homepage: str

    #: Three-sentence description of the token
    description: str

    #: CoinGecko listing URL (empty string if not listed)
    coingecko: str

    #: DefiLlama listing URL (empty string if not listed)
    defillama: str

    #: Twitter/X account URL (empty string if not found)
    twitter: str


class StablecoinLogos(TypedDict):
    """Logo URLs for a stablecoin.

    Logo URLs point to 256x256 PNG files in R2 storage.
    ``None`` if the logo variant is not available.
    """

    #: Logo for light backgrounds (dark-coloured logo)
    light: str | None


class StablecoinMetadata(TypedDict):
    """Complete stablecoin metadata as exported to JSON."""

    #: Token symbol (e.g. ``USDC``, ``USG``)
    symbol: str

    #: Lowercase slug matching the YAML filename (e.g. ``usdc``, ``usg``)
    slug: str

    #: Human-readable name
    name: str

    #: Description of the stablecoin
    description: str | None

    #: Category: ``stablecoin``, ``yield_bearing``, or ``wrapped``
    category: str

    #: Links
    links: StablecoinLinks

    #: Logo URLs
    logos: StablecoinLogos


def read_stablecoin_metadata(yaml_path: Path) -> dict:
    """Read and parse a stablecoin metadata YAML file.

    :param yaml_path:
        Path to the YAML file

    :return:
        Parsed YAML as a dictionary
    """
    yaml_content = yaml_path.read_text()
    parsed = load(yaml_content)
    return parsed.data


#: In-process cache of loaded metadata
_cached_metadata: dict[str, list[StablecoinInfo]] | None = None


def load_all_stablecoin_metadata() -> dict[str, list[StablecoinInfo]]:
    """Load all stablecoin metadata from YAML files.

    Returns a dict mapping symbol to list of StablecoinInfo entries.
    Cached in-process after first call.

    Files with a ``token_symbols`` list register the same metadata
    under each variant symbol (e.g. ``USDT`` and ``USDt``).

    :return:
        Dictionary mapping token symbol to list of :py:class:`StablecoinInfo` entries
    """
    global _cached_metadata

    if _cached_metadata is not None:
        return _cached_metadata

    result: dict[str, list[StablecoinInfo]] = {}

    for yaml_path in sorted(STABLECOINS_DATA_DIR.glob("*.yaml")):
        data = read_stablecoin_metadata(yaml_path)
        symbol = data["symbol"]

        # Build the StablecoinInfo list for this file
        if "entries" in data:
            # Multi-project symbol
            info_list = []
            for entry in data["entries"]:
                links = entry.get("links", {})
                info_list.append(
                    StablecoinInfo(
                        name=entry.get("name", ""),
                        homepage=links.get("homepage", ""),
                        description=entry.get("description", ""),
                        coingecko=links.get("coingecko", ""),
                        defillama=links.get("defillama", ""),
                        twitter=links.get("twitter", ""),
                    )
                )
        else:
            links = data.get("links", {})
            info_list = [
                StablecoinInfo(
                    name=data.get("name", ""),
                    homepage=links.get("homepage", ""),
                    description=data.get("description", ""),
                    coingecko=links.get("coingecko", ""),
                    defillama=links.get("defillama", ""),
                    twitter=links.get("twitter", ""),
                )
            ]

        # Register under primary symbol
        result[symbol] = info_list

        # Also register under all token_symbols variants
        for variant in data.get("token_symbols", []):
            if variant != symbol:
                result[variant] = info_list

    _cached_metadata = result
    return result


def is_stablecoin_like(token_symbol: str | None, symbol_list=ALL_STABLECOIN_LIKE) -> bool:
    """Check if specific token symbol is likely a stablecoin.

    Useful for quickly filtering stable/stable pairs in the pools.
    However, you should never rely on this check alone.

    Note that new stablecoins might be introduced, so this check
    is never going to be future proof.

    Example:

    .. code-block:: python

        assert is_stablecoin_like("USDC") == True
        assert is_stablecoin_like("USDT") == True
        assert is_stablecoin_like("GHO") == True
        assert is_stablecoin_like("crvUSD") == True
        assert is_stablecoin_like("WBTC") == False

    :param token_symbol:
        Token symbol as it is written on the contract.
        May contain lower and uppercase latter.

    :param symbol_list:
        Which filtering list we use.

    :return:
        ``True`` if the symbol is in the stablecoin list
    """

    if token_symbol is None:
        return False

    assert isinstance(token_symbol, str), f"We got {token_symbol}"
    return token_symbol in symbol_list


def normalise_token_symbol(token_symbol: str | None) -> str | None:
    """Normalise token symbol for stablecoin detection.

    - Uppercase
    - Remove bridge suffixes
    - Fix USDT variations

    :param token_symbol:
        Token symbol as it is written on the contract.

    :return:
        Normalised token symbol
    """

    if token_symbol is None:
        return None

    assert isinstance(token_symbol, str), f"We got {token_symbol}"

    token_symbol = token_symbol.upper()

    if token_symbol.endswith(".E"):
        token_symbol = token_symbol.removesuffix(".E")

    if token_symbol in {"USDT0", "USD₮0"}:
        token_symbol = "USDT"

    return token_symbol


def get_stablecoin_available_logos(slug: str) -> dict[str, bool]:
    """Check which logo variants are available for a stablecoin.

    :param slug:
        Stablecoin slug (e.g. ``usdc``, ``usg``)

    :return:
        Dictionary with ``light`` key indicating availability
    """
    logo_dir = STABLECOIN_FORMATTED_LOGOS_DIR / slug
    return {
        "light": (logo_dir / "light.png").exists(),
    }


def build_stablecoin_metadata_json(yaml_path: Path, public_url: str = "") -> list[StablecoinMetadata]:
    """Build StablecoinMetadata list from a YAML file.

    :param yaml_path:
        Path to the stablecoin metadata YAML file

    :param public_url:
        Public base URL for constructing logo URLs (e.g. ``https://pub-xyz.r2.dev``)

    :return:
        List of StablecoinMetadata dicts ready for JSON export
    """
    data = read_stablecoin_metadata(yaml_path)
    symbol = data["symbol"]
    slug = data.get("slug") or yaml_path.stem
    category = data.get("category", "stablecoin")

    def normalise(value):
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else None
        return value

    # Build logo URLs based on availability
    available = get_stablecoin_available_logos(slug)
    public_url = public_url.rstrip("/") if public_url else ""
    logos: StablecoinLogos = {
        "light": f"{public_url}/stablecoin-metadata/{slug}/light.png" if available["light"] and public_url else None,
    }

    if "entries" in data:
        result = []
        for entry in data["entries"]:
            links_data = entry.get("links", {})
            links: StablecoinLinks = {field: normalise(links_data.get(field)) for field in STABLECOIN_LINK_FIELDS}
            result.append(
                StablecoinMetadata(
                    symbol=symbol,
                    slug=slug,
                    name=entry.get("name", ""),
                    description=normalise(entry.get("description")),
                    category=category,
                    links=links,
                    logos=logos,
                )
            )
        return result
    else:
        links_data = data.get("links", {})
        links: StablecoinLinks = {field: normalise(links_data.get(field)) for field in STABLECOIN_LINK_FIELDS}
        return [
            StablecoinMetadata(
                symbol=symbol,
                slug=slug,
                name=data.get("name", ""),
                description=normalise(data.get("description")),
                category=category,
                links=links,
                logos=logos,
            )
        ]


def process_and_upload_stablecoin_metadata(
    yaml_path: Path,
    bucket_name: str,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    public_url: str = "",
    key_prefix: str = "",
) -> list[StablecoinMetadata]:
    """Process and upload a single stablecoin's metadata and logo to R2.

    Uploads:

    - ``stablecoin-metadata/{slug}/metadata.json`` — JSON metadata
    - ``stablecoin-metadata/{slug}/light.png`` — 256x256 logo (if available)

    :param yaml_path:
        Path to the stablecoin YAML file

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
        The processed StablecoinMetadata list
    """
    from eth_defi.research.sparkline import upload_to_r2_compressed

    metadata = build_stablecoin_metadata_json(yaml_path, public_url=public_url)
    slug = yaml_path.stem

    logger.info("Uploading stablecoin metadata for: %s", slug)

    # Upload metadata JSON
    json_bytes = json.dumps(metadata, indent=2).encode()
    upload_to_r2_compressed(
        payload=json_bytes,
        bucket_name=bucket_name,
        object_name=f"stablecoin-metadata/{key_prefix}{slug}/metadata.json",
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        content_type="application/json",
    )

    # Upload logo if available
    logo_path = STABLECOIN_FORMATTED_LOGOS_DIR / slug / "light.png"
    if logo_path.exists():
        logger.info("Uploading light logo for stablecoin: %s", slug)
        upload_to_r2_compressed(
            payload=logo_path.read_bytes(),
            bucket_name=bucket_name,
            object_name=f"stablecoin-metadata/{key_prefix}{slug}/light.png",
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            content_type="image/png",
        )

    return metadata


def build_stablecoin_index(public_url: str = "") -> list[StablecoinMetadata]:
    """Build a single index of all stablecoin metadata.

    Loads every YAML file and returns a flat list of :py:class:`StablecoinMetadata`
    entries suitable for JSON serialisation as ``stablecoin-metadata/index.json``.

    :param public_url:
        Public base URL for constructing logo URLs

    :return:
        List of all stablecoin metadata entries
    """
    index: list[StablecoinMetadata] = []
    for yaml_path in sorted(STABLECOINS_DATA_DIR.glob("*.yaml")):
        entries = build_stablecoin_metadata_json(yaml_path, public_url=public_url)
        index.extend(entries)
    return index


def upload_stablecoin_index(
    bucket_name: str,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    public_url: str = "",
    key_prefix: str = "",
) -> list[StablecoinMetadata]:
    """Build and upload the aggregate stablecoin index to R2.

    Uploads to ``stablecoin-metadata/{key_prefix}index.json``.

    :return:
        The full index list
    """
    from eth_defi.research.sparkline import upload_to_r2_compressed

    index = build_stablecoin_index(public_url=public_url)

    logger.info("Uploading stablecoin index with %d entries", len(index))

    json_bytes = json.dumps(index, indent=2).encode()
    upload_to_r2_compressed(
        payload=json_bytes,
        bucket_name=bucket_name,
        object_name=f"stablecoin-metadata/{key_prefix}index.json",
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        content_type="application/json",
    )

    return index
