"""Public Derive v3 native vault data.

Derive v3 vaults are managed exchange subaccounts, not ERC-4626 contracts.
The unauthenticated HTTP API exposes discovery, live NAV and sampled
performance history. See `Derive's vault documentation
<https://docs.derive.xyz/vaults/create-a-vault>`__ and the `public API
<https://docs.derive.xyz/api-reference/vault-shareholders/publicget_vaults>`__.
"""

import datetime
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from eth_typing import HexAddress
from requests import Session
from requests.adapters import HTTPAdapter

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.logging_retry import LoggingRetry

logger = logging.getLogger(__name__)

DERIVE_V3_MAINNET_API_URL = "https://api.derive.xyz/v3"
DERIVE_V3_TESTNET_API_URL = "https://testnet.api.derive.xyz/v3"
_MILLISECONDS_THRESHOLD = 100_000_000_000
_MAX_VAULT_PAGE_SIZE = 100
_MAX_PERFORMANCE_PAGE_SIZE = 10_000


@dataclass(slots=True)
class DeriveV3Vault:
    """One public native vault record.

    The vault subaccount ID is unique only within the chosen Derive v3
    deployment. Monetary values are decimal strings in the source API.

    """

    #: Native vault subaccount ID.
    subaccount_id: int

    #: Curator-supplied display name.
    name: str

    #: Curator-supplied strategy description.
    description: str

    #: Curator wallet address.
    curator: HexAddress

    #: Derive spot asset address accepted for deposits.
    deposit_spot_asset: HexAddress

    #: Live mark-to-market NAV in USD, if priceable.
    nav_usd: Decimal | None

    #: Live USD price per share, assuming accrued fees were settled now.
    share_price_usd: Decimal | None

    #: Outstanding native shares.
    total_shares: Decimal

    #: Annual management fee in basis points.
    management_fee_bps: int

    #: Performance fee in basis points.
    performance_fee_bps: int

    #: Minimum holder withdrawal cooldown in seconds.
    cooldown_sec: int

    #: Whether deposits are limited to the vault whitelist.
    whitelist_only: bool

    #: Whether the vault has entered its terminal closed state.
    closed: bool

    #: Deposit currency symbol resolved from ``public/get_all_currencies``.
    deposit_symbol: str | None = None

    #: Underlying ERC-20 decimal count, when the spot asset provides one.
    deposit_decimals: int | None = None

    #: Complete source record, retained for future metadata fields.
    raw_metadata: dict | None = None

    @classmethod
    def from_api(cls, value: dict) -> "DeriveV3Vault":
        """Parse a public vault record without rounding decimal amounts.

        :param value: ``Vault`` object from ``public/get_vaults``.
        :return: Parsed vault metadata.
        """
        protocol = value["protocol"]
        config = protocol["config"]
        return cls(
            subaccount_id=int(protocol["subaccount_id"]),
            name=value["name"],
            description=value["description"],
            curator=HexAddress(value["curator"]),
            deposit_spot_asset=HexAddress(config["deposit_spot_asset"]),
            nav_usd=Decimal(str(value["nav_usd"])) if value.get("nav_usd") is not None else None,
            share_price_usd=Decimal(str(value["simulated_share_price_usd"])) if value.get("simulated_share_price_usd") is not None else None,
            total_shares=Decimal(str(protocol["total_shares"])),
            management_fee_bps=int(config["management_fee_bps"]),
            performance_fee_bps=int(config["performance_fee_bps"]),
            cooldown_sec=int(config["cooldown_sec"]),
            whitelist_only=bool(value["whitelist_only"]),
            closed=bool(protocol["closed"]),
            raw_metadata=value,
        )


@dataclass(slots=True)
class DeriveV3VaultPrice:
    """One sampled vault performance point.

    ``share_price`` is the historical mark-to-market share price reported by
    Derive. It can differ from the live simulated post-fee settlement price.

    See `public/get_vault_performance_history
    <https://docs.derive.xyz/api-reference/vault-shareholders/publicget_vault_performance_history>`__.
    """

    #: Native vault subaccount ID.
    subaccount_id: int

    #: Naive UTC observation time.
    timestamp: datetime.datetime

    #: Historical USD price per native share.
    share_price: Decimal

    #: Vault NAV in USD, or ``None`` when the API cannot value the vault.
    nav_usd: Decimal | None

    #: Outstanding native shares.
    total_shares: Decimal

    @classmethod
    def from_api(cls, subaccount_id: int, value: dict) -> "DeriveV3VaultPrice":
        """Parse a performance point and normalise its Unix timestamp.

        Derive's OpenAPI text describes milliseconds. Testnet responses on
        24 September 2026 used seconds, so the parser accepts both units.

        :param subaccount_id: Vault subaccount ID from the request.
        :param value: ``VaultPerformancePointResponse`` object.
        :return: Parsed performance observation.
        """
        unix_time = int(value["ts"])
        if abs(unix_time) >= _MILLISECONDS_THRESHOLD:
            unix_time /= 1000
        return cls(
            subaccount_id=subaccount_id,
            timestamp=native_datetime_utc_fromtimestamp(unix_time),
            share_price=Decimal(str(value["share_price"])),
            nav_usd=Decimal(str(value["nav"])) if value.get("nav") is not None else None,
            total_shares=Decimal(str(value["total_shares"])),
        )


class DeriveV3VaultClient:
    """Synchronous client for the public Derive v3 vault endpoints.

    Defaults to testnet. Pass ``network="mainnet"`` for production data;
    subaccount IDs are scoped to a deployment. No credentials are needed.

    The default session retries HTTP 429 and selected server errors up to
    three times. A supplied session keeps its own retry configuration.
    :meth:`close` closes either session, including one supplied by the caller.

    :param network: ``testnet`` or ``mainnet``.
    :param session: Optional configured HTTP session.
    :param timeout: Per-request timeout in seconds.
    """

    def __init__(self, network: Literal["testnet", "mainnet"] = "testnet", session: Session | None = None, timeout: float = 30.0):
        if network not in {"testnet", "mainnet"}:
            raise ValueError(f"Unknown Derive v3 network: {network}")
        self.network = network
        self.url = DERIVE_V3_TESTNET_API_URL if network == "testnet" else DERIVE_V3_MAINNET_API_URL
        self.timeout = timeout
        self.session = session or Session()
        if session is None:
            retry = LoggingRetry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504], respect_retry_after_header=True, logger=logger, allowed_methods=LoggingRetry.DEFAULT_ALLOWED_METHODS | frozenset({"POST"}))
            self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def _post(self, method: str, params: dict) -> dict | list[dict]:
        """POST parameters to a public endpoint and unwrap its response.

        Derive accepts a JSON parameter object at ``/v3/public/<method>``
        and returns a JSON-RPC-style ``result`` or ``error`` envelope.

        :param method: Path such as ``public/get_vaults``.
        :param params: JSON request object.
        :return: The unwrapped ``result`` object or list.
        :raises ValueError: On a Derive application error or malformed response.
        """
        response = self.session.post(f"{self.url}/{method}", json=params, timeout=self.timeout)
        response.raise_for_status()
        body = response.json()
        if body.get("error") is not None:
            raise ValueError(f"Derive v3 {method} failed: {body['error']}")
        if "result" not in body:
            raise ValueError(f"Derive v3 {method} returned no result")
        return body["result"]

    def fetch_vaults(self, page_size: int = 100) -> Iterator[DeriveV3Vault]:
        """Fetch each page of the deployment's public vault listing.

        An empty listing yields no records. HTTP and response errors propagate
        to the caller, so an unavailable API is distinguishable from no vaults.

        :param page_size: API page size, between 1 and 100.
        :return: Vault records in API page order.
        """
        if not 1 <= page_size <= _MAX_VAULT_PAGE_SIZE:
            message = "page_size must be between 1 and 100"
            raise ValueError(message)
        page = 1
        while True:
            result = self._post("public/get_vaults", {"page": page, "page_size": page_size})
            if not isinstance(result, dict):
                message = "Derive v3 vault listing returned a non-object result"
                raise ValueError(message)
            for item in result["vaults"]:
                yield DeriveV3Vault.from_api(item)
            if page >= result["pagination"]["num_pages"]:
                break
            page += 1

    def fetch_vault(self, subaccount_id: int) -> dict:
        """Fetch a full public native vault record for manual inspection.

        Returns the response without parsing it into :class:`DeriveV3Vault`,
        so callers can inspect fields absent from the listing model.
        See `public/get_vault
        <https://docs.derive.xyz/api-reference/vault-shareholders/publicget_vault>`__.

        :param subaccount_id: Native vault subaccount ID.
        :return: Complete public vault response.
        """
        result = self._post("public/get_vault", {"subaccount_id": subaccount_id})
        if not isinstance(result, dict):
            message = "Derive v3 full vault response was not an object"
            raise ValueError(message)
        return result

    def fetch_spot_assets(self) -> dict[str, tuple[str, int | None]]:
        """Map configured spot asset addresses to symbols and decimals.

        Derive identifies a vault's deposit asset by its internal spot
        address. Currency metadata is the public source for a display symbol.
        See `public/get_all_currencies
        <https://docs.derive.xyz/api-reference/market-data/publicget_all_currencies>`__.

        :return: Lowercase spot address to ``(symbol, ERC-20 decimals)``.
        """
        currencies = self._post("public/get_all_currencies", {})
        if not isinstance(currencies, list):
            message = "Derive v3 currency listing returned a non-list result"
            raise ValueError(message)
        return {spot["address"].lower(): (spot["name"], spot.get("erc20", {}).get("decimals") if spot.get("erc20") else None) for currency in currencies for spot in currency.get("spot", [])}

    def fetch_vault_performance(self, subaccount_id: int, resolution: Literal["1h", "8h", "24h", "1wk"] = "24h", limit: int = 10000) -> Iterator[DeriveV3VaultPrice]:
        """Iterate the available sampled price history, newest first.

        The endpoint caps a page at 10,000 points. Older points are fetched
        using the exclusive ``to`` bound in Unix seconds, even when a response
        supplies bucket timestamps in milliseconds.

        :param subaccount_id: Native vault subaccount ID.
        :param resolution: Sampling resolution supported by Derive.
        :param limit: Points per request, 1 to 10,000. This does not limit the
            total number of points yielded across pages.
        :return: Historical observations, newest first.
        """
        if resolution not in {"1h", "8h", "24h", "1wk"}:
            raise ValueError(f"Unknown performance resolution: {resolution}")
        if not 1 <= limit <= _MAX_PERFORMANCE_PAGE_SIZE:
            message = "limit must be between 1 and 10000"
            raise ValueError(message)
        until = None
        while True:
            params = {"subaccount_id": subaccount_id, "resolution": resolution, "limit": limit}
            if until is not None:
                params["to"] = until
            result = self._post("public/get_vault_performance_history", params)
            if not isinstance(result, dict):
                message = "Derive v3 performance history returned a non-object result"
                raise ValueError(message)
            raw_points = result["points"]
            logger.info("Fetched %d Derive v3 %s performance points for vault %d", len(raw_points), self.network, subaccount_id)
            for value in raw_points:
                yield DeriveV3VaultPrice.from_api(subaccount_id, value)
            if len(raw_points) < limit:
                break
            # The request bound is always seconds; response buckets may be
            # seconds or milliseconds depending on the deployment.
            oldest_timestamp = min(int(value["ts"]) for value in raw_points)
            next_until = oldest_timestamp // 1000 if abs(oldest_timestamp) >= _MILLISECONDS_THRESHOLD else oldest_timestamp
            if until is not None and next_until >= until:
                message = "Derive v3 performance pagination did not advance"
                raise ValueError(message)
            until = next_until

    def close(self) -> None:
        """Close the underlying HTTP session.

        :return: ``None``.
        """
        self.session.close()
