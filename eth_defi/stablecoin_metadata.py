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

YAML file format
~~~~~~~~~~~~~~~~

Each file in ``eth_defi/data/stablecoins/`` describes one token symbol.
Files come in two shapes: *standard* (one project per symbol) and *entries*
(multiple competing projects that share the same symbol).

**Standard file** — all fields at the top level:

.. code-block:: yaml

    symbol: USDC                          # token ticker as used on-chain
    name: USD Coin (Circle)               # full human-readable name
    slug: usdc                            # lowercase identifier, matches filename stem
    category: stablecoin                  # stablecoin | yield_bearing | wrapped
    short_description: |                  # 1–3 sentence summary
      USD Coin is...
    long_description: |                   # multi-paragraph Markdown (empty string = not yet written)
      [USD Coin](https://circle.com/) is...
    token_symbols:                        # optional: additional ticker variants
      - USDC
      - USDC.e
    links:
      homepage: https://circle.com/usdc  # project website (empty string if unknown)
      coingecko: https://...             # CoinGecko listing URL (empty string if not listed)
      defillama: https://...             # DeFiLlama stablecoin page URL (empty string if none)
      twitter: https://x.com/circle     # official X/Twitter account URL (empty string if unknown)
    contract_addresses:                   # known on-chain deployments
      - chain: ethereum                   # chain slug (ethereum, arbitrum, base, …)
        address: '0xA0b8...'             # checksummed ERC-20 address
    source_currency: usd                  # nominal currency this token tracks, e.g. usd, eur, jpy
    source_currency_source: manual        # manual values can drive depeg checks; inferred/empty values cannot
    source_currency_usd_rate: 1.0         # USD per one source-currency unit used for native depeg checks
    source_currency_usd_rate_date: '2026-06-26'  # currency API calendar date for the FX rate
    source_currency_usd_rate_fetched_at: '2026-06-26T12:00:00'  # when our updater read the FX rate
    source_currency_usd_rate_source: fawazahmed0 # currency API source column for the FX rate
    usd_rate: 0.9998                      # latest token price in USD from CoinGecko
    usd_rate_fetched_at: '2026-06-26T12:00:00' # when our updater fetched the USD rate
    peg_rate: 0.9998                      # latest token price in source_currency units
    peg_rate_currency: usd                # currency key used for the native depeg check
    depegged_at: ''                       # sticky depeg marker, manually cleared by operators
    checks:                               # automated liveness checks (omitted if checks not run yet)
      twitter_last_post_at: '2026-03-17' # YYYY-MM-DD of most recent post, or empty string
      domain_up_at: '2026-03-17'         # YYYY-MM-DD when homepage last responded, or empty string
      marked_dead_at: ''                  # YYYY-MM-DD when confirmed dead, or empty string
      information_found_missing_at: ''    # YYYY-MM-DD when no info was findable, or empty string

**Entries file** — used when multiple unrelated projects share the same symbol.
The top level holds only ``symbol``, ``slug``, ``category``, and optionally
``token_symbols``; each entry under ``entries:`` carries the remaining fields:

.. code-block:: yaml

    symbol: RUSD
    slug: rusd
    category: stablecoin
    entries:
      - name: Reservoir rUSD
        short_description: ...
        long_description: |
          ...
        links:
          homepage: https://reservoir.xyz/
          coingecko: ''
          defillama: ''
          twitter: https://x.com/reservoir_xyz
        contract_addresses:
          - chain: ethereum
            address: '0x09D4...'
        source_currency: usd
        source_currency_source: manual
        source_currency_usd_rate: 1.0
        source_currency_usd_rate_date: '2026-06-26'
        source_currency_usd_rate_fetched_at: '2026-06-26T12:00:00'
        source_currency_usd_rate_source: fawazahmed0
        usd_rate: 0.9998
        usd_rate_fetched_at: '2026-06-26T12:00:00'
        peg_rate: 0.9998
        peg_rate_currency: usd
        depegged_at: ''
        checks:
          twitter_last_post_at: ''
          domain_up_at: '2026-03-17'
          marked_dead_at: ''
          information_found_missing_at: ''
      - name: Another rUSD
        ...

**Empty-string convention** — all optional string fields use ``''`` (empty string)
as the *not yet known* marker. The JSON export normalises these to ``null``.

Maintaining stablecoin files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Several Claude Code skills automate common maintenance tasks.  Invoke them
with the ``/skill-name`` command in the chat prompt.

**Adding or updating descriptions**

The ``long_description`` and ``links.twitter`` fields are filled manually or
with AI assistance.  Use an AI agent (spawning 8 parallel sub-agents is a good
batch size) and point it at ``eth_defi/data/stablecoins/``.  The agent should
use ``WebSearch`` to research each stablecoin and write a 2–4 paragraph
Markdown description in the ``long_description`` field.  Empty string ``''``
is the marker for "not yet written".

**Checking liveness** — ``/check-stablecoins``

The ``check-stablecoins`` skill audits all YAML files for liveness:

- Checks whether ``links.twitter`` accounts are still active and records the
  date of the most recent post in ``checks.twitter_last_post_at``.
- Checks whether ``links.homepage`` domains are reachable and records the
  date in ``checks.domain_up_at``.
- Sets ``checks.marked_dead_at`` when strong evidence of shutdown is found
  (domain down *and* last tweet more than 6 months ago).
- Sets ``checks.information_found_missing_at`` when no links exist at all.
- Appends a ``## Status`` section to ``long_description`` if wind-down news
  is found.

Run it periodically (e.g. monthly) to keep the ``checks`` block current.

**Logos** — ``/extract-project-logo`` and ``/post-process-logo``

Logo files live in ``eth_defi/data/stablecoins/formatted_logos/{slug}/``.
The only supported variant is ``light.png`` (256 × 256 PNG, suitable for
display on light backgrounds).

Workflow to add a logo for a new stablecoin:

1. Run ``/extract-project-logo`` — point it at the project website.
   It searches the brand kit, GitHub, meta tags, and CoinGecko in order of
   preference and saves the raw source file.
2. Run ``/post-process-logo`` — pass the raw source folder and the output
   path ``eth_defi/data/stablecoins/formatted_logos/{slug}/``.
   It converts to PNG, adds transparent padding to make the image square,
   and scales to 256 × 256.
3. Re-run the export script to upload the new logo to R2.
"""

import json
import logging
from pathlib import Path
from typing import Any, TypedDict

from strictyaml import load

from eth_defi.types import ISODateString, ISODateTimeString

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
        "apxUSD",
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
        "EURR",
        "EURS",
        "EURT",
        "EURe",
        "EUSD",
        "FDUSD",
        "FEI",
        "FIDD",
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
        "tGBP",
        "TGBP",
        "TCNH",
        "TOR",
        "TRYB",
        "TUSD",
        "USC",
        "USD",
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
        "USDm",
    ]
)


#: Stablecoins which can be used as collateral, but which also have built-in yield bearing function
#: with rebasing.
YIELD_BEARING_STABLES = {"sfrxUSD", "sUSDe", "sUSDai", "sBOLD", "sAUSD", "sUSG", "ynUSDx", "sNUSD", "savUSD", "syrupUSDC", "stcUSD", "apyUSD", "sUSD3"}

#: Stablecoins plus their interest wrapped counterparts on Compound and Aave.
#: Also contains other derivates.
WRAPPED_STABLECOIN_LIKE = {"cUSDC", "cUSDT", "sUSD", "aDAI", "cDAI", "tfUSDC", "alUSD", "agEUR", "gmdUSDC", "gDAI", "blUSD", "eEARN", "autoUSD", "bbqUSDC", "AA_FalconXUSDC", "USD3"}

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


class StablecoinContractAddress(TypedDict):
    """A contract address entry for a stablecoin on a specific chain."""

    #: Chain name (e.g. ``ethereum``, ``arbitrum``, ``base``)
    chain: str

    #: Contract address (checksummed hex, may be ``None`` if unknown)
    address: str | None


class StablecoinChecks(TypedDict):
    """Automated liveness checks for a stablecoin project."""

    #: Date (YYYY-MM-DD) of the most recent Twitter/X post, or empty string if unknown/missing
    twitter_last_post_at: ISODateString

    #: Date (YYYY-MM-DD) when the homepage was last confirmed reachable, or empty string
    domain_up_at: ISODateString

    #: Date (YYYY-MM-DD) when the project was confirmed dead, or empty string if alive/unknown
    marked_dead_at: ISODateString

    #: Date (YYYY-MM-DD) when liveness could not be determined (no links found), or empty string
    information_found_missing_at: ISODateString


class StablecoinMetadata(TypedDict):
    """Complete stablecoin metadata as exported to JSON."""

    #: Token symbol (e.g. ``USDC``, ``USG``)
    symbol: str

    #: Lowercase slug matching the YAML filename (e.g. ``usdc``, ``usg``)
    slug: str

    #: Human-readable name
    name: str

    #: Short description of the stablecoin (same as ``description`` for backwards compatibility)
    short_description: str | None

    #: Long description of the stablecoin (may be empty)
    long_description: str | None

    #: Short description (kept for backwards compatibility, same value as ``short_description``)
    description: str | None

    #: Category: ``stablecoin``, ``yield_bearing``, or ``wrapped``
    category: str

    #: Links
    links: StablecoinLinks

    #: Logo URLs
    logos: StablecoinLogos

    #: Known contract addresses across chains (may be empty list if unknown)
    contract_addresses: list[StablecoinContractAddress]

    #: Automated liveness checks (``None`` if checks have not been run yet)
    checks: StablecoinChecks | None

    #: Source currency this stablecoin is intended to track, e.g. ``usd`` or ``eur``.
    #:
    #: Used by :py:func:`eth_defi.feed.stablecoin_rate.refresh_stablecoin_rates`
    #: to choose the native depeg-check currency and exported through
    #: :py:func:`build_stablecoin_metadata_json`.
    source_currency: str | None

    #: How ``source_currency`` was selected, e.g. ``manual``.
    #:
    #: Only manually curated values are allowed to drive depeg decisions in
    #: :py:func:`eth_defi.feed.stablecoin_rate.refresh_stablecoin_rates`.
    source_currency_source: str | None

    #: USD per one source-currency unit used for native depeg checks.
    #:
    #: For non-USD stablecoins this is read from
    #: :py:class:`eth_defi.currency_api.database.CurrencyRateDatabase` by
    #: :py:func:`eth_defi.feed.stablecoin_rate.refresh_stablecoin_rates`.
    source_currency_usd_rate: float | None

    #: Calendar date of the source-currency FX rate.
    #:
    #: Rows older than the refresh policy are ignored by
    #: :py:func:`eth_defi.feed.stablecoin_rate.read_latest_usd_per_source_currency`.
    source_currency_usd_rate_date: ISODateString | None

    #: Time when the source-currency FX rate was read by our updater.
    #:
    #: Exported as an ISO timestamp by :py:func:`build_stablecoin_metadata_json`.
    source_currency_usd_rate_fetched_at: ISODateTimeString | None

    #: Provider/source for the source-currency FX rate.
    #:
    #: Matches the currency API source column selected by
    #: :py:func:`eth_defi.feed.stablecoin_rate.refresh_stablecoin_rates`.
    source_currency_usd_rate_source: str | None

    #: CoinGecko API coin id used for price refreshes.
    coingecko_id: str | None

    #: Human-readable CoinGecko page URL for this asset.
    coingecko_link: str | None

    #: How the CoinGecko id was selected: ``manual``, ``url``, or ``search``.
    coingecko_id_source: str | None

    #: Last time the CoinGecko id returned a valid price.
    coingecko_id_verified_at: ISODateTimeString | None

    #: Last time CoinGecko id verification failed.
    coingecko_id_verification_failed_at: ISODateTimeString | None

    #: Human-readable reason for the latest CoinGecko id verification failure.
    coingecko_id_verification_failed_reason: str | None

    #: Latest USD rate from the stablecoin rate feed.
    usd_rate: float | None

    #: Time when the USD rate was fetched by our updater.
    usd_rate_fetched_at: ISODateTimeString | None

    #: CoinGecko upstream update timestamp for the USD rate.
    usd_rate_updated_at: ISODateTimeString | None

    #: Price in the denomination's peg currency used for depeg checks.
    peg_rate: float | None

    #: CoinGecko currency key used for ``peg_rate``.
    peg_rate_currency: str | None

    #: Last failed rate refresh timestamp.
    rate_fetch_failed_at: ISODateTimeString | None

    #: Machine-readable reason for the latest rate refresh failure.
    rate_fetch_failed_reason: str | None

    #: Sticky depeg marker. Operators clear this manually.
    depegged_at: ISODateTimeString | None


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


def load_all_stablecoin_metadata(data_dir: Path = STABLECOINS_DATA_DIR) -> dict[str, list[StablecoinInfo]]:
    """Load all stablecoin metadata from YAML files.

    Returns a dict mapping symbol to list of StablecoinInfo entries.
    Cached in-process after first call for the default package data directory.

    Files with a ``token_symbols`` list register the same metadata
    under each variant symbol (e.g. ``USDT`` and ``USDt``).

    :return:
        Dictionary mapping token symbol to list of :py:class:`StablecoinInfo` entries
    """
    global _cached_metadata

    use_cache = data_dir == STABLECOINS_DATA_DIR
    if use_cache and _cached_metadata is not None:
        return _cached_metadata

    result: dict[str, list[StablecoinInfo]] = {}

    for yaml_path in sorted(data_dir.glob("*.yaml")):
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
                        description=entry.get("short_description", ""),
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
                    description=data.get("short_description", ""),
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

    if use_cache:
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

    The returned dictionaries expose the public
    :py:class:`StablecoinMetadata` JSON contract, including source-currency
    fields maintained by
    :py:func:`eth_defi.feed.stablecoin_rate.refresh_stablecoin_rates`.

    :param yaml_path:
        Path to the stablecoin metadata YAML file

    :param public_url:
        Public base URL for constructing logo URLs (e.g. ``https://pub-xyz.r2.dev``)

    :return:
        List of :py:class:`StablecoinMetadata` dicts ready for JSON export.
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

    def normalise_float(value: Any) -> float | None:
        normalised = normalise(value)
        if normalised is None:
            return None
        try:
            return float(normalised)
        except (TypeError, ValueError):
            return None

    def parse_rate_fields(source: dict) -> dict[str, str | float | None]:
        return {
            "source_currency": normalise(source.get("source_currency")),
            "source_currency_source": normalise(source.get("source_currency_source")),
            "source_currency_usd_rate": normalise_float(source.get("source_currency_usd_rate")),
            "source_currency_usd_rate_date": normalise(source.get("source_currency_usd_rate_date")),
            "source_currency_usd_rate_fetched_at": normalise(source.get("source_currency_usd_rate_fetched_at")),
            "source_currency_usd_rate_source": normalise(source.get("source_currency_usd_rate_source")),
            "coingecko_id": normalise(source.get("coingecko_id")),
            "coingecko_link": normalise(source.get("coingecko_link")),
            "coingecko_id_source": normalise(source.get("coingecko_id_source")),
            "coingecko_id_verified_at": normalise(source.get("coingecko_id_verified_at")),
            "coingecko_id_verification_failed_at": normalise(source.get("coingecko_id_verification_failed_at")),
            "coingecko_id_verification_failed_reason": normalise(source.get("coingecko_id_verification_failed_reason")),
            "usd_rate": normalise_float(source.get("usd_rate")),
            "usd_rate_fetched_at": normalise(source.get("usd_rate_fetched_at")),
            "usd_rate_updated_at": normalise(source.get("usd_rate_updated_at")),
            "peg_rate": normalise_float(source.get("peg_rate")),
            "peg_rate_currency": normalise(source.get("peg_rate_currency")),
            "rate_fetch_failed_at": normalise(source.get("rate_fetch_failed_at")),
            "rate_fetch_failed_reason": normalise(source.get("rate_fetch_failed_reason")),
            "depegged_at": normalise(source.get("depegged_at")),
        }

    # Build logo URLs based on availability
    available = get_stablecoin_available_logos(slug)
    public_url = public_url.rstrip("/") if public_url else ""
    logos: StablecoinLogos = {
        "light": f"{public_url}/stablecoin-metadata/{slug}/light.png" if available["light"] and public_url else None,
    }

    def parse_contract_addresses(source: dict) -> list[StablecoinContractAddress]:
        raw = source.get("contract_addresses") or []
        return [
            StablecoinContractAddress(
                chain=entry.get("chain", ""),
                address=entry.get("address") or None,
            )
            for entry in raw
        ]

    def parse_checks(source: dict) -> StablecoinChecks | None:
        raw = source.get("checks")
        if raw is None:
            return None
        return StablecoinChecks(
            twitter_last_post_at=raw.get("twitter_last_post_at") or "",
            domain_up_at=raw.get("domain_up_at") or "",
            marked_dead_at=raw.get("marked_dead_at") or "",
            information_found_missing_at=raw.get("information_found_missing_at") or "",
        )

    if "entries" in data:
        result = []
        for entry in data["entries"]:
            links_data = entry.get("links", {})
            links: StablecoinLinks = {field: normalise(links_data.get(field)) for field in STABLECOIN_LINK_FIELDS}
            short_desc = normalise(entry.get("short_description"))
            long_desc = normalise(entry.get("long_description"))
            result.append(
                StablecoinMetadata(
                    symbol=symbol,
                    slug=slug,
                    name=entry.get("name", ""),
                    short_description=short_desc,
                    long_description=long_desc,
                    description=short_desc,
                    category=category,
                    links=links,
                    logos=logos,
                    contract_addresses=parse_contract_addresses(entry),
                    checks=parse_checks(entry),
                    **parse_rate_fields(entry),
                )
            )
        return result
    else:
        links_data = data.get("links", {})
        links: StablecoinLinks = {field: normalise(links_data.get(field)) for field in STABLECOIN_LINK_FIELDS}
        short_desc = normalise(data.get("short_description"))
        long_desc = normalise(data.get("long_description"))
        return [
            StablecoinMetadata(
                symbol=symbol,
                slug=slug,
                name=data.get("name", ""),
                short_description=short_desc,
                long_description=long_desc,
                description=short_desc,
                category=category,
                links=links,
                logos=logos,
                contract_addresses=parse_contract_addresses(data),
                checks=parse_checks(data),
                **parse_rate_fields(data),
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

    # Upload metadata JSON
    json_bytes = json.dumps(metadata, indent=2).encode()
    metadata_uploaded = upload_to_r2_compressed(
        payload=json_bytes,
        bucket_name=bucket_name,
        object_name=f"stablecoin-metadata/{key_prefix}{slug}/metadata.json",
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        content_type="application/json",
        skip_if_current=True,
    )
    logger.info("%s stablecoin metadata for: %s", "Uploaded" if metadata_uploaded else "Skipped unchanged", slug)

    # Upload logo if available
    logo_path = STABLECOIN_FORMATTED_LOGOS_DIR / slug / "light.png"
    if logo_path.exists():
        logo_uploaded = upload_to_r2_compressed(
            payload=logo_path.read_bytes(),
            bucket_name=bucket_name,
            object_name=f"stablecoin-metadata/{key_prefix}{slug}/light.png",
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            content_type="image/png",
            skip_if_current=True,
        )
        logger.info("%s light logo for stablecoin: %s", "Uploaded" if logo_uploaded else "Skipped unchanged", slug)

    return metadata


def build_stablecoin_index(public_url: str = "", data_dir: Path = STABLECOINS_DATA_DIR) -> list[StablecoinMetadata]:
    """Build a single index of all stablecoin metadata.

    Loads every YAML file and returns a flat list of :py:class:`StablecoinMetadata`
    entries suitable for JSON serialisation as ``stablecoin-metadata/index.json``.

    :param public_url:
        Public base URL for constructing logo URLs

    :param data_dir:
        Stablecoin YAML data directory to export.

    :return:
        List of all stablecoin metadata entries
    """
    index: list[StablecoinMetadata] = []
    for yaml_path in sorted(data_dir.glob("*.yaml")):
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
    data_dir: Path = STABLECOINS_DATA_DIR,
) -> list[StablecoinMetadata]:
    """Build and upload the aggregate stablecoin index to R2.

    Uploads to ``stablecoin-metadata/{key_prefix}index.json``.

    :return:
        The full index list
    """
    from eth_defi.research.sparkline import upload_to_r2_compressed

    index = build_stablecoin_index(public_url=public_url, data_dir=data_dir)

    json_bytes = json.dumps(index, indent=2).encode()
    index_uploaded = upload_to_r2_compressed(
        payload=json_bytes,
        bucket_name=bucket_name,
        object_name=f"stablecoin-metadata/{key_prefix}index.json",
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        content_type="application/json",
        skip_if_current=True,
    )
    logger.info(
        "%s stablecoin index with %d entries",
        "Uploaded" if index_uploaded else "Skipped unchanged",
        len(index),
    )

    return index
