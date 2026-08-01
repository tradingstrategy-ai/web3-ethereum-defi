"""Persist Asseto's public product registry for scheduled vault scans.

Asseto exposes its registry through an undocumented web-application endpoint.
The registry is nevertheless adapter-critical because it supplies the product
identifier used for public daily NAV history.  This module keeps HTTP parsing
in :mod:`eth_defi.tokenised_fund.asseto.offchain_api` and owns only cache and
freshness policy.
"""

import datetime
import json
import logging
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

import requests
from atomicwrites import atomic_write
from eth_typing import HexAddress

from eth_defi.compat import native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.tokenised_fund.asseto.offchain_api import AssetoAPIError, AssetoOffchainProduct, fetch_asseto_products
from eth_defi.utils import wait_other_writers

logger = logging.getLogger(__name__)


#: Persistent cache shared by the scanner containers through ``DEFAULT_CACHE_ROOT``.
DEFAULT_ASSETO_REGISTRY_CACHE_PATH = DEFAULT_CACHE_ROOT / "asseto" / "registry-cache.json"

#: Current normalised JSON envelope version.
ASSETO_REGISTRY_CACHE_VERSION = 1

#: Longest safe age for a cached registry used to construct existing adapters.
DEFAULT_MAX_STALE_AGE = datetime.timedelta(days=7)

AssetoRegistryStatus = Literal["fresh", "stale", "not_found", "transient_error"]


@dataclass(frozen=True, slots=True)
class AssetoOffchainRegistryResult:
    """One fresh or stale Asseto registry read.

    :param status:
        Whether products came from a new API response or a retained cache.
    :param products:
        Normalised EVM registry products keyed by Asseto's API.
    :param source_timestamp:
        UTC timestamp at which the source snapshot was fetched.
    :param diagnostics:
        Safe operator-facing explanation of a stale or failed read.
    """

    status: AssetoRegistryStatus
    products: tuple[AssetoOffchainProduct, ...]
    source_timestamp: datetime.datetime | None
    diagnostics: str | None = None

    @property
    def is_usable(self) -> bool:
        """Return whether the result can construct known runtime adapters."""

        return self.status in {"fresh", "stale"} and bool(self.products)


def _encode_product(product: AssetoOffchainProduct) -> dict[str, object]:
    """Convert one product to JSON-compatible cache data."""

    data = asdict(product)
    for key in ("contract_address", "denomination_address"):
        if data[key] is not None:
            data[key] = str(data[key])
    for key in ("tvl", "apy"):
        if data[key] is not None:
            data[key] = str(data[key])
    return data


def _decode_product(data: object) -> AssetoOffchainProduct:
    """Parse one cache record using the same types as the live API client."""

    if not isinstance(data, dict):
        message = "Asseto registry cache product is not an object"
        raise ValueError(message)
    try:
        return AssetoOffchainProduct(
            product_id=int(data["product_id"]),
            product_name=str(data["product_name"]),
            full_name=data.get("full_name") if isinstance(data.get("full_name"), str) else None,
            symbol=data.get("symbol") if isinstance(data.get("symbol"), str) else None,
            product_type=data.get("product_type") if isinstance(data.get("product_type"), str) else None,
            chain_id=int(data["chain_id"]),
            chain_name=data.get("chain_name") if isinstance(data.get("chain_name"), str) else None,
            contract_address=HexAddress(str(data["contract_address"]).lower()),
            denomination_symbol=data.get("denomination_symbol") if isinstance(data.get("denomination_symbol"), str) else None,
            denomination_address=HexAddress(str(data["denomination_address"]).lower()) if data.get("denomination_address") else None,
            tvl=Decimal(str(data["tvl"])) if data.get("tvl") is not None else None,
            apy=Decimal(str(data["apy"])) if data.get("apy") is not None else None,
            introduction=data.get("introduction") if isinstance(data.get("introduction"), str) else None,
            protocol=data.get("protocol") if isinstance(data.get("protocol"), str) else None,
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        message = "Asseto registry cache contains an invalid product"
        raise ValueError(message) from exc


def _load_cache(cache_path: Path) -> tuple[tuple[AssetoOffchainProduct, ...], datetime.datetime]:
    """Load one validated versioned registry cache envelope."""

    with cache_path.open("rt", encoding="utf-8") as inp:
        payload = json.load(inp)
    if not isinstance(payload, dict) or payload.get("version") != ASSETO_REGISTRY_CACHE_VERSION:
        message = "Asseto registry cache has an unknown version"
        raise ValueError(message)
    source_timestamp = datetime.datetime.fromisoformat(str(payload["fetched_at"]))
    if source_timestamp.tzinfo is not None:
        source_timestamp = source_timestamp.replace(tzinfo=None)
    raw_products = payload.get("products")
    if not isinstance(raw_products, list) or not raw_products:
        message = "Asseto registry cache has no products"
        raise ValueError(message)
    products = tuple(_decode_product(product) for product in raw_products)
    keys = {(product.chain_id, product.contract_address.lower()) for product in products}
    if len(keys) != len(products):
        message = "Asseto registry cache contains duplicate chain/address products"
        raise ValueError(message)
    return products, source_timestamp


def _write_cache(cache_path: Path, products: tuple[AssetoOffchainProduct, ...], fetched_at: datetime.datetime) -> None:
    """Atomically persist a complete validated registry snapshot."""

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": ASSETO_REGISTRY_CACHE_VERSION,
        "fetched_at": fetched_at.isoformat(),
        "products": [_encode_product(product) for product in products],
    }
    with atomic_write(str(cache_path), mode="w", encoding="utf-8", overwrite=True) as out:
        json.dump(payload, out, indent=2, sort_keys=True)


def fetch_asseto_registry(
    *,
    cache_path: Path = DEFAULT_ASSETO_REGISTRY_CACHE_PATH,
    force_refresh: bool = True,
    now_: datetime.datetime | None = None,
    max_stale_age: datetime.timedelta = DEFAULT_MAX_STALE_AGE,
) -> AssetoOffchainRegistryResult:
    """Fetch Asseto's registry with a safe persisted stale fallback.

    A due scanner cycle passes ``force_refresh=True`` so the daily scheduler
    always attempts one request.  A malformed, empty or duplicate snapshot
    cannot replace an existing cache.  On failure, the cache can only rebuild
    existing adapters and callers must not use it for metadata writes.

    :param cache_path:
        JSON cache path. Override in tests or isolated operator runs.
    :param force_refresh:
        Whether to make one immediate registry request.
    :param now_:
        Naive UTC timestamp override for deterministic tests.
    :param max_stale_age:
        Maximum cache age eligible for degraded adapter construction.
    :return:
        Fresh, stale or failed registry result.
    """

    now_ = now_ or native_datetime_utc_now()
    cache_path = cache_path.expanduser().resolve()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cached_products: tuple[AssetoOffchainProduct, ...] | None = None
    cached_at: datetime.datetime | None = None
    cache_error: Exception | None = None

    with wait_other_writers(cache_path):
        if cache_path.exists():
            try:
                cached_products, cached_at = _load_cache(cache_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                cache_error = exc
                logger.error("Asseto registry cache %s is corrupt: %s", cache_path, exc, exc_info=True)

        if not force_refresh and cached_products is not None:
            return AssetoOffchainRegistryResult("stale", cached_products, cached_at, "cache reuse requested")

        try:
            products = tuple(fetch_asseto_products())
            keys = {(product.chain_id, product.contract_address.lower()) for product in products}
            if not products:
                message = "Asseto registry returned no parseable EVM products"
                raise AssetoAPIError(message)
            if len(keys) != len(products):
                message = "Asseto registry contains duplicate chain/address products"
                raise AssetoAPIError(message)
        except (AssetoAPIError, OSError, requests.RequestException) as exc:
            if cached_products is not None and cached_at is not None and now_ - cached_at <= max_stale_age:
                logger.warning("Asseto registry refresh failed; using stale cache from %s: %s", cached_at.isoformat(), exc)
                return AssetoOffchainRegistryResult("stale", cached_products, cached_at, str(exc))
            diagnostic = str(exc)
            if cache_error is not None:
                diagnostic = f"{diagnostic}; cache is unusable: {cache_error}"
            return AssetoOffchainRegistryResult("transient_error", (), None, diagnostic)

        if cache_error is not None and cache_path.exists():
            quarantine_path = cache_path.with_name(f"{cache_path.stem}.corrupt-{now_.strftime('%Y%m%dT%H%M%S')}{cache_path.suffix}")
            cache_path.replace(quarantine_path)
            logger.error("Quarantined corrupt Asseto registry cache at %s", quarantine_path)
        _write_cache(cache_path, products, now_)
        return AssetoOffchainRegistryResult("fresh", products, now_)
