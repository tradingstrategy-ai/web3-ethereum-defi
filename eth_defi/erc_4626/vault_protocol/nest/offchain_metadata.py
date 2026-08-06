"""First-party Nest vault metadata.

Nest publishes the canonical contract deployment catalogue through its public
API and richer product information through its public CMS API.  The contract
catalogue maps every Nest share token to its chain-specific ``NestVault``
deposit and redemption entrypoints; the CMS supplements that mapping with the
strategy description, redemption estimate and yield-source partners.

- `Nest app <https://www.nest.credit/vaults#vaults-explore>`__
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

#: Nest's API uses these chain names in its product ``chain`` object.
NEST_CHAIN_NAMES: dict[int, str] = {
    1: "mainnet",
    56: "bsc",
    480: "worldchain",
    9745: "plasma",
    43114: "avalanche",
    98866: "plume",
}


class NestVaultMetadata(TypedDict):
    """First-party data for a chain-specific Nest vault entrypoint."""

    #: Nest product name, for example ``Nest BlackOpal LiquidStone II Vault``.
    name: str

    #: Stable Nest web-application product slug.
    slug: str

    #: Share token symbol, for example ``nOPAL``.
    symbol: str

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
    start_block: int | None

    #: Product category shown by Nest, if supplied by the CMS.
    category: str | None

    #: Short user-facing strategy summary from the Nest CMS.
    short_description: str | None

    #: Full user-facing strategy explanation from the Nest CMS.
    description: str | None

    #: Estimated redemption time in days, if supplied by the Nest CMS.
    redemption_time_days: int | None

    #: Product status, for example ``active``.
    status: str | None

    #: Yield-source partner names displayed by Nest.
    yield_source_partners: list[str]

    #: Product icon URL supplied by Nest.
    icon_url: str | None

    #: Reported 30-day annualised yield as a fraction.
    reported_apy: float | None

    #: Reported product TVL in USD.
    tvl_usd: float | None


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
                    slug=slug,
                    symbol=product.get("symbol", ""),
                    share_token_address=Web3.to_checksum_address(share_token_address),
                    vault_address=Web3.to_checksum_address(vault_address),
                    chain_id=chain_id,
                    asset_symbol=route.get("asset", ""),
                    asset_address=Web3.to_checksum_address(asset_address),
                    start_block=chain_start,
                    category=category.get("name"),
                    short_description=cms_product.get("summary"),
                    description=cms_product.get("about"),
                    redemption_time_days=_parse_redemption_time_days(cms_product.get("redemptionTime")),
                    status=cms_product.get("status"),
                    yield_source_partners=partner_names,
                    icon_url=product.get("icon"),
                    reported_apy=product.get("sec30d"),
                    tvl_usd=product.get("tvl"),
                )

    return result


def fetch_nest_vaults(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    cms_api_base_url: str = DEFAULT_CMS_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
) -> dict[str, NestVaultMetadata]:
    """Fetch and cache the first-party catalogue of Nest vault entrypoints.

    One request obtains contract routes and live statistics; one CMS request
    obtains the associated product descriptions.  The output intentionally keys
    contracts by chain and address because Nest can deploy equal addresses on
    more than one EVM network.

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

    :return:
        Chain-and-address keyed metadata for every NestVault route.
    """
    assert isinstance(cache_path, Path), "cache_path must be Path"
    cache_path.mkdir(parents=True, exist_ok=True)
    cache_file = (cache_path / "nest_vaults.json").resolve()
    now_ = now_ or native_datetime_utc_now()

    with wait_other_writers(cache_file):
        cache_is_fresh = cache_file.exists() and cache_file.stat().st_size > 0 and now_ - native_datetime_utc_fromtimestamp(cache_file.stat().st_mtime) <= max_cache_duration
        if cache_is_fresh:
            try:
                return json.loads(cache_file.read_text())
            except JSONDecodeError as error:
                raise RuntimeError(f"Could not decode Nest metadata cache {cache_file}") from error

        contracts_payload = _fetch_json(f"{api_base_url}/vaults", params={"status": "all"})
        cms_payload = _fetch_json(f"{cms_api_base_url}/vaults", params={"limit": 100, "depth": 2})
        contract_products = contracts_payload.get("data", []) if contracts_payload else []
        cms_products = cms_payload.get("docs", []) if cms_payload else []
        metadata = _parse_nest_vaults(contract_products, cms_products)
        if not metadata:
            logger.warning("Nest API returned no vault routes; not replacing %s", cache_file)
            return {}

        cache_file.write_text(json.dumps(metadata, indent=2, sort_keys=True))
        return metadata


def fetch_nest_vault_metadata(web3: Web3, vault_address: HexAddress) -> NestVaultMetadata | None:
    """Look up first-party metadata for one NestVault deployment.

    :param web3:
        Connected Web3 instance used to select the EVM chain.

    :param vault_address:
        NestVault deposit and redemption entrypoint address.

    :return:
        Product metadata, or ``None`` if the address is not in Nest's published catalogue.
    """
    global _cached_vaults  # noqa: PLW0603 - cache is intentionally shared by scanner adapters.
    if _cached_vaults is None:
        _cached_vaults = fetch_nest_vaults()
    key = f"{web3.eth.chain_id}:{vault_address.lower()}"
    return _cached_vaults.get(key)


#: In-process cache shared by Nest vault adapters within one scanner process.
_cached_vaults: dict[str, NestVaultMetadata] | None = None
