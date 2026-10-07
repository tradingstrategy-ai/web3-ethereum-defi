"""First-party Nest vault metadata.

Nest publishes the canonical contract deployment catalogue through its public
API and richer product information through its public CMS API.  The contract
catalogue maps every Nest share token to its chain-specific ``NestVault``
deposit and redemption entrypoints; the CMS supplements that mapping with the
strategy description, redemption estimate and yield-source partners.

- `Nest app <https://app.nest.credit/vaults>`__
- `Nest API <https://api.nest.credit/v1/vaults?status=all>`__
- `Nest CMS API <https://cms.nest.credit/api/vaults?limit=100&depth=2>`__
"""

import datetime
import json
import logging
from json import JSONDecodeError
from pathlib import Path
from typing import TypedDict

import requests
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.types import Percent
from eth_defi.utils import wait_other_writers

#: Where cached first-party Nest API responses are stored.
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "nest"

#: Public contract and live-statistics API used by the Nest web application.
DEFAULT_API_BASE_URL = "https://api.nest.credit/v1"

#: Public Payload CMS endpoint used by the Nest web application.
DEFAULT_CMS_API_BASE_URL = "https://cms.nest.credit/api"

#: The cache is deliberately short because vault routes and their CMS metadata evolve.
DEFAULT_CACHE_DURATION = datetime.timedelta(days=2)

logger = logging.getLogger(__name__)

#: Arc mainnet chain ID used by Nest's current application.
NEST_ARC_CHAIN_ID = 5042

#: Nest's API uses these chain names in its product ``chain`` object.
NEST_CHAIN_NAMES: dict[int, str] = {
    1: "mainnet",
    56: "bsc",
    143: "monad",
    480: "worldchain",
    2818: "morph",
    4663: "robinhood",
    NEST_ARC_CHAIN_ID: "arc",
    8453: "base",
    9745: "plasma",
    43114: "avalanche",
    98866: "plume",
}

#: Reviewed Nest product managers. These are strategy or asset managers, not
#: necessarily curators of the Nest vault contracts.
#: https://www.plume.org/blog/plume-and-bybit-expand-access-to-3-trillion-brazilian-credit-card-financing-market-through-nopal
#: https://app.nest.credit/vaults/nest-falconx-clo
#: https://app.nest.credit/vaults/plume-factor-vault
#: https://www.plume.org/blog/plume-vaults-expands-access-to-figures-30b-home-equity-ecosystem-through-nprime
#: https://bitwiseinvestments.com/crypto-funds/uscc
NEST_REVIEWED_MANAGERS: dict[str, str] = {
    "nest-acrdx-vault": "Apollo",
    "nest-basis-vault": "Bitwise",
    "nest-falconx-clo": "M11 Credit",
    "nest-onre-vault": "OnRe",
    "nest-opal-vault": "BlackOpal",
    "nest-wisdom-vault": "WisdomTree",
    "nprime": "Hastra",
    "plume-factor-vault": "Plume",
}

#: Nest identifies Nest DAO LLC as the primary curator of its vaults.
#: https://nest.credit/
NEST_CURATOR_SLUG = "nest-dao"


def select_nest_manager_name(slug: str, yield_source_partners: list[str]) -> str | None:
    """Select the best available partner name for a Nest manager label.

    Nest's CMS lists yield-source partners without roles. Prefer a reviewed
    manager where one is documented; otherwise retain the first published
    partner as an indicative label. FACTOR's curator is stated in its product
    description although its partner list is empty.

    :param slug:
        Stable Nest product slug.
    :param yield_source_partners:
        Partner names in the order published by Nest's CMS.
    :return:
        Reviewed manager, first available partner, or ``None``.
    """
    reviewed = NEST_REVIEWED_MANAGERS.get(slug)
    if reviewed:
        return reviewed
    return yield_source_partners[0] if yield_source_partners else None


class NestVaultMetadata(TypedDict):
    """First-party data for a chain-specific Nest vault entrypoint."""

    #: Nest product name, for example ``Nest BlackOpal LiquidStone II Vault``.
    name: str

    #: Current product display name from Nest's CMS, if published.
    display_name: str | None

    #: Stable Nest web-application product slug.
    slug: str

    #: Share token symbol, for example ``nOPAL``.
    symbol: str

    #: Marketing symbol from Nest's CMS; the onchain token symbol remains authoritative.
    display_symbol: str | None

    #: ERC-20 share token shared by the product's EVM routes.
    share_token_address: HexAddress

    #: Chain-specific NestVault entrypoint address.
    vault_address: HexAddress

    #: EVM chain ID of ``vault_address``.
    chain_id: int

    #: Deposit/redemption denomination symbol, for example ``USDC``.
    asset_symbol: str

    #: Deposit/redemption denomination token address on ``chain_id``.
    asset_address: HexAddress

    #: Block where Nest reports the product as deployed on this chain.
    #: A historical lower bound, not proof of an entrypoint's bytecode at this block.
    start_block: int | None

    #: Product category shown by Nest, if supplied by the CMS.
    category: str | None

    #: Short user-facing strategy summary from the Nest CMS.
    short_description: str | None

    #: Full user-facing strategy explanation from the Nest CMS.
    description: str | None

    #: CMS explanation of how the strategy earns its yield.
    yield_origin: str | None

    #: CMS summary of the strategy's risks.
    risk_summary: str | None

    #: CMS detail about the underlying assets' risks.
    underlying_risks: str | None

    #: Estimated redemption time in days, if supplied by the Nest CMS.
    redemption_time_days: int | None

    #: Product status, for example ``active``.
    status: str | None

    #: Yield-source partner names displayed by Nest.
    yield_source_partners: list[str]

    #: Product icon URL supplied by Nest.
    icon_url: str | None

    #: Catalogue SEC 30-day annualised yield as a fraction; its month-end window
    #: can differ from the application's rolling NAV yield.
    reported_apy: Percent | None

    #: Product target APY reported by the contract catalogue, distinct from realised yield.
    target_apy: Percent | None

    #: CMS yield estimate, which may be unpublished or stale on some products.
    estimated_apy: Percent | None

    #: Reported product TVL in USD.
    tvl_usd: float | None

    #: Holder count reported by Nest's live API.
    num_holders: int | None

    #: USD-denominated 24-hour volume reported by Nest's live API.
    volume_24h_usd: float | None


def _fetch_json(url: str, params: dict[str, str | int] | None = None) -> dict | None:
    """Fetch one JSON response from a first-party Nest endpoint.

    The public APIs are advisory metadata sources.  A temporary HTTP or JSON
    failure must not make an otherwise readable onchain vault unusable.

    :param url:
        Complete Nest API endpoint URL.

    :param params:
        Optional query parameters.

    :return:
        Decoded JSON object, or ``None`` when Nest's public API is unavailable.
    """
    try:
        response = requests.get(url, params=params, timeout=30, headers={"Accept": "application/json"})
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, JSONDecodeError) as error:
        logger.warning("Could not fetch Nest metadata from %s: %s", url, error)
        return None


def _parse_redemption_time_days(value: object) -> int | None:
    """Parse Nest CMS's optional redemption-time string as a day count.

    Invalid or negative estimates are omitted rather than presented as a
    settlement guarantee.

    :param value:
        Raw CMS value, normally a numeric string.

    :return:
        Non-negative redemption estimate in days, or ``None`` when unavailable.
    """
    try:
        parsed = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is None or parsed >= 0 else None


def _parse_estimated_apy(value: object) -> Percent | None:
    """Parse an optional CMS APY without treating it as realised performance.

    The CMS stores some estimates as decimal strings such as ``".08"``.
    Unparseable or missing estimates do not invalidate contract metadata.

    :param value:
        Raw first-party CMS estimate.
    :return:
        Fractional annualised estimate or ``None`` when unavailable.
    """
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_nest_vaults(contract_products: list[dict], cms_products: list[dict]) -> dict[str, NestVaultMetadata]:
    """Join Nest's contract catalogue with its CMS product descriptions.

    The public catalogue is authoritative for contracts and chain IDs.  The CMS
    uses the same stable slug and adds information suitable for a vault listing.
    Each output key is ``{chain_id}:{lowercase_vault_address}``, allowing the
    same deterministic deployment address to appear safely on several chains.

    :param contract_products:
        Raw ``GET /vaults?status=all`` product entries.

    :param cms_products:
        Raw CMS ``docs`` entries from ``GET /vaults?limit=100&depth=2``.

    :return:
        Chain-and-address keyed Nest vault metadata.
    """
    cms_by_slug = {product["slug"]: product for product in cms_products if product.get("slug")}
    result: dict[str, NestVaultMetadata] = {}

    for product in contract_products:
        slug = product.get("slug")
        share_token_address = product.get("vaultAddress")
        if not slug or not share_token_address:
            continue

        cms_product = cms_by_slug.get(slug, {})
        category = cms_product.get("category") or {}
        partners = cms_product.get("yieldSourcePartners", {}).get("docs", [])
        partner_names = [partner["name"] for partner in partners if partner.get("name")]
        start_blocks = product.get("chain", {})

        for route in product.get("nestVaults", []):
            vault_address = route.get("nestVaultAddress")
            if not vault_address:
                continue
            for chain_asset in route.get("chainAssets", []):
                chain_id = chain_asset.get("chainId")
                asset_address = chain_asset.get("assetAddress")
                if not isinstance(chain_id, int) or not asset_address:
                    continue
                chain_start = start_blocks.get(NEST_CHAIN_NAMES.get(chain_id), {}).get("startBlock")
                key = f"{chain_id}:{vault_address.lower()}"
                result[key] = NestVaultMetadata(
                    name=product.get("name", ""),
                    display_name=cms_product.get("name"),
                    slug=slug,
                    symbol=product.get("symbol", ""),
                    display_symbol=cms_product.get("symbol"),
                    share_token_address=Web3.to_checksum_address(share_token_address),
                    vault_address=Web3.to_checksum_address(vault_address),
                    chain_id=chain_id,
                    asset_symbol=route.get("asset", ""),
                    asset_address=Web3.to_checksum_address(asset_address),
                    start_block=chain_start,
                    category=category.get("name"),
                    short_description=cms_product.get("summary"),
                    description=cms_product.get("about"),
                    yield_origin=cms_product.get("yieldOrigin"),
                    risk_summary=cms_product.get("riskSummary"),
                    underlying_risks=cms_product.get("underlyingRisks"),
                    redemption_time_days=_parse_redemption_time_days(cms_product.get("redemptionTime")),
                    status=cms_product.get("status"),
                    yield_source_partners=partner_names,
                    icon_url=product.get("icon"),
                    reported_apy=product.get("sec30d"),
                    target_apy=product.get("targetApy"),
                    estimated_apy=_parse_estimated_apy(cms_product.get("estimatedApy")),
                    tvl_usd=product.get("tvl"),
                    num_holders=product.get("numHolders"),
                    volume_24h_usd=product.get("volume24h"),
                )

    return result


def _read_cached_vaults(cache_file: Path) -> dict[str, NestVaultMetadata]:
    """Read a previously fetched Nest route catalogue.

    A corrupt cache raises a hard error so callers cannot mistake it for an
    empty first-party catalogue.

    :param cache_file:
        JSON cache file written by :func:`fetch_nest_vaults`.

    :return:
        Chain-and-address keyed Nest vault metadata.
    """
    try:
        return json.loads(cache_file.read_text())
    except JSONDecodeError as error:
        raise RuntimeError(f"Could not decode Nest metadata cache {cache_file}") from error


def fetch_nest_vaults(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    cms_api_base_url: str = DEFAULT_CMS_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
    *,
    allow_stale: bool = True,
) -> dict[str, NestVaultMetadata]:
    """Fetch and cache the first-party catalogue of Nest vault entrypoints.

    One request obtains contract routes and live statistics; one CMS request
    obtains the associated product descriptions.  The output intentionally keys
    contracts by chain and address because Nest can deploy equal addresses on
    more than one EVM network.
    A successful refresh also updates the in-process adapter cache, so a
    migration's freshly verified catalogue is used when rebuilding rows.

    :param cache_path:
        Local directory for the process-safe JSON cache.

    :param api_base_url:
        Nest public API base URL, overrideable for tests.

    :param cms_api_base_url:
        Nest CMS public API base URL, overrideable for tests.

    :param now_:
        Override the current naive UTC time for deterministic cache tests.

    :param max_cache_duration:
        Age after which first-party metadata is refreshed.

    :param allow_stale:
        Use the cache and fall back to it after an API outage. Disable this for
        migrations that must refetch the current first-party route catalogue.

    :return:
        Chain-and-address keyed metadata for every NestVault route.
    """
    global _cached_vaults  # noqa: PLW0603 - explicit refreshes must also refresh scanner adapters.
    assert isinstance(cache_path, Path), "cache_path must be Path"
    cache_path.mkdir(parents=True, exist_ok=True)
    cache_file = (cache_path / "nest_vaults.json").resolve()
    now_ = now_ or native_datetime_utc_now()

    with wait_other_writers(cache_file):
        cache_is_fresh = allow_stale and cache_file.exists() and cache_file.stat().st_size > 0 and now_ - native_datetime_utc_fromtimestamp(cache_file.stat().st_mtime) <= max_cache_duration
        if cache_is_fresh:
            return _read_cached_vaults(cache_file)

        contracts_payload = _fetch_json(f"{api_base_url}/vaults", params={"status": "all"})
        cms_payload = _fetch_json(f"{cms_api_base_url}/vaults", params={"limit": 100, "depth": 2})
        if not contracts_payload or not cms_payload or not cms_payload.get("docs"):
            if not allow_stale:
                message = "Nest API or CMS returned no metadata; refusing to migrate from incomplete first-party data"
                raise RuntimeError(message)
            if cache_file.exists() and cache_file.stat().st_size > 0:
                logger.warning("Nest API or CMS is unavailable; preserving cached metadata %s", cache_file)
                return _read_cached_vaults(cache_file)
            logger.warning("Nest API or CMS is unavailable and no cache exists; deferring metadata")
            return {}
        contract_products = contracts_payload.get("data", []) if contracts_payload else []
        cms_products = cms_payload.get("docs", []) if cms_payload else []
        metadata = _parse_nest_vaults(contract_products, cms_products)
        if not metadata:
            if not allow_stale:
                message = "Nest API returned no vault routes; refusing to migrate from a stale cache"
                raise RuntimeError(message)
            if cache_file.exists() and cache_file.stat().st_size > 0:
                logger.warning("Nest API returned no vault routes; using stale cache %s", cache_file)
                return _read_cached_vaults(cache_file)
            logger.warning("Nest API returned no vault routes and no cache is available")
            return {}

        cache_file.write_text(json.dumps(metadata, indent=2, sort_keys=True))
        _cached_vaults = metadata
        return metadata


def fetch_nest_vault_metadata(web3: Web3, vault_address: HexAddress) -> NestVaultMetadata | None:
    """Look up first-party metadata for one NestVault deployment.

    Adapters share the latest explicitly fetched catalogue in this process.
    An empty response is not memoised, allowing recovery after an API outage.

    :param web3:
        Connected Web3 instance used to select the EVM chain.

    :param vault_address:
        NestVault deposit and redemption entrypoint address.

    :return:
        Product metadata, or ``None`` if the address is not in Nest's published catalogue.
    """
    global _cached_vaults  # noqa: PLW0603 - cache is intentionally shared by scanner adapters.
    if _cached_vaults is None:
        vaults = fetch_nest_vaults()
        if vaults:
            _cached_vaults = vaults
    key = f"{web3.eth.chain_id}:{vault_address.lower()}"
    return _cached_vaults.get(key) if _cached_vaults else None


#: In-process cache shared by Nest vault adapters within one scanner process.
_cached_vaults: dict[str, NestVaultMetadata] | None = None
