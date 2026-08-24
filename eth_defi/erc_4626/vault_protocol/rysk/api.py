"""Rysk Premium public application API client.

The Premium app publishes its live pool catalogue and compact event snapshots
at ``https://premium.rysk.finance``.  Snapshot records are the protocol's
published epoch accounting feed; the integration preserves their raw deposit
and withdrawal prices and uses only final epoch withdrawal PPS for returns.

"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import requests
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.rysk.constants import RyskPremiumPool

logger = logging.getLogger(__name__)

RYSK_PREMIUM_API_BASE_URL = "https://premium.rysk.finance"
RYSK_PREMIUM_API_TIMEOUT = 20.0
RYSK_FINAL_EPOCH_ACTION = "EPOCH"
EVM_ADDRESS_LENGTH = 42
EVM_TRANSACTION_HASH_LENGTH = 66


class RyskPremiumAPIError(RuntimeError):
    """Raised when a Rysk Premium public endpoint has an invalid response."""


@dataclass(slots=True, frozen=True)
class RyskPremiumSnapshot:
    """One raw accounting event in the public Premium snapshot feed.

    The immutable record preserves the application response before the
    historical reader selects final epoch prices. See the official `Premium
    explainer <https://docs.rysk.finance/rysk-premium/rysk-premium-explainer>`__
    for the epoch accounting model.
    """

    #: EVM chain containing the pool.
    chain_id: int
    #: Rysk LP share-token and pool address.
    pool: HexAddress
    #: EVM block associated with the application event.
    block_number: int
    #: Unix timestamp supplied by the application API.
    timestamp: int
    #: Transaction hash associated with the event.
    transaction_hash: str
    #: Premium event classification; ``EPOCH`` is a final epoch-price row.
    action: str
    #: Rysk epoch number.
    epoch: int
    #: Raw entry price per share retained for source auditing.
    deposit_pps: int
    #: Raw exit price per share encoded with collateral-token precision.
    withdrawal_pps: int
    #: Raw collateral TVL reported by the application, when present.
    tvl: int | None


def _as_int(value: object, field: str) -> int:
    """Parse a required integer-like JSON field.

    Rysk serialises several numeric fields as decimal strings. Centralising
    their conversion gives malformed application responses consistent errors.

    :param value:
        Value returned by the Rysk API.
    :param field:
        Field name for an actionable error.
    :return:
        Parsed integer.
    """

    try:
        return int(str(value))
    except (TypeError, ValueError) as error:
        raise RyskPremiumAPIError(f"Rysk Premium API field {field} is not an integer: {value!r}") from error


def _as_address(value: object, field: str, *, optional: bool = False) -> HexAddress | None:
    """Normalise one required or optional hexadecimal address.

    The application uses lower-case EVM addresses and an ``0x`` sentinel for
    some absent authorities. This helper normalises both response forms.

    :param value:
        API JSON field value.
    :param field:
        Field name for diagnostics.
    :param optional:
        Accept Rysk's ``0x`` sentinel for absent authority.
    :return:
        Lower-case EVM address or ``None`` for an optional sentinel.
    """

    text = str(value or "").lower()
    if optional and text in {"", "0x"}:
        return None
    if not (text.startswith("0x") and len(text) == EVM_ADDRESS_LENGTH):
        raise RyskPremiumAPIError(f"Rysk Premium API field {field} is not an EVM address: {value!r}")
    return HexAddress(text)


def _fetch_json(path: str, *, params: dict[str, object] | None = None, session: requests.Session | None = None) -> object:
    """Fetch one JSON resource from the Rysk Premium application API.

    Requests are bounded by :data:`RYSK_PREMIUM_API_TIMEOUT` and translate
    transport and JSON errors into :class:`RyskPremiumAPIError`. The application
    is the source linked from the official `Premium documentation
    <https://docs.rysk.finance/rysk-premium/rysk-premium-explainer>`__.

    :param path:
        Slash-prefixed resource path.
    :param params:
        Optional query parameters.
    :param session:
        Optional caller-managed HTTP session.
    :return:
        JSON-decoded response payload.
    """

    client = session or requests
    try:
        response = client.get(f"{RYSK_PREMIUM_API_BASE_URL}{path}", params=params, timeout=RYSK_PREMIUM_API_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as error:
        raise RyskPremiumAPIError(f"Rysk Premium API request failed for {path}: {error}") from error


def fetch_rysk_premium_pools(*, session: requests.Session | None = None) -> tuple[RyskPremiumPool, ...]:
    """Fetch the current official Rysk Premium pool catalogue.

    Reads the application-owned `public pools endpoint
    <https://premium.rysk.finance/api/pools>`__ and validates every identity
    needed by the vault adapter before returning it.

    :param session:
        Optional persistent HTTP session.
    :return:
        Normalised pools across every currently published EVM chain.
    :raise RyskPremiumAPIError:
        If the response does not have the documented list shape.
    """

    payload = _fetch_json("/api/pools", session=session)
    if not isinstance(payload, list):
        raise RyskPremiumAPIError(f"Rysk Premium pool catalogue must be a list, got {type(payload).__name__}")
    pools: list[RyskPremiumPool] = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise RyskPremiumAPIError(f"Rysk Premium pool entry must be an object, got {type(raw).__name__}")
        option_type = str(raw.get("type", "")).lower()
        if option_type not in {"call", "put"}:
            raise RyskPremiumAPIError(f"Rysk Premium pool has unsupported option type: {raw.get('type')!r}")
        pools.append(
            RyskPremiumPool(
                chain_id=_as_int(raw.get("chainId"), "chainId"),
                address=_as_address(raw.get("address"), "address"),
                name=str(raw.get("name") or "Rysk Premium pool"),
                description=str(raw["description"]) if raw.get("description") else None,
                registry=_as_address(raw.get("registry"), "registry"),
                option_handler=_as_address(raw.get("optionHandler"), "optionHandler"),
                asset=_as_address(raw.get("asset"), "asset"),
                option_type=option_type,
                option_sale_fee_bps=_as_int(raw.get("fees"), "fees"),
                authority=_as_address(raw.get("authority"), "authority", optional=True),
            )
        )
    return tuple(pools)


def _parse_snapshot(raw: object, pool: RyskPremiumPool) -> RyskPremiumSnapshot:
    """Convert one application snapshot into a typed immutable record.

    Pool and chain identity are checked against the catalogue request so that
    a malformed or cross-pool response cannot enter contextual history.

    :param raw:
        Snapshot object from the Rysk API.
    :param pool:
        Catalogue identity expected for the endpoint.
    :return:
        Parsed snapshot.
    """

    if not isinstance(raw, dict):
        raise RyskPremiumAPIError(f"Rysk Premium snapshot must be an object, got {type(raw).__name__}")
    snapshot_pool = _as_address(raw.get("pool"), "snapshot.pool")
    if snapshot_pool != pool.address:
        raise RyskPremiumAPIError(f"Rysk Premium snapshot pool mismatch: expected {pool.address}, got {snapshot_pool}")
    chain_id = _as_int(raw.get("chainId"), "snapshot.chainId")
    if chain_id != pool.chain_id:
        raise RyskPremiumAPIError(f"Rysk Premium snapshot chain mismatch: expected {pool.chain_id}, got {chain_id}")
    tx_hash = str(raw.get("txHash") or "").lower()
    if not (tx_hash.startswith("0x") and len(tx_hash) == EVM_TRANSACTION_HASH_LENGTH):
        raise RyskPremiumAPIError(f"Rysk Premium snapshot has invalid txHash: {tx_hash!r}")
    return RyskPremiumSnapshot(
        chain_id=chain_id,
        pool=snapshot_pool,
        block_number=_as_int(raw.get("block"), "snapshot.block"),
        timestamp=_as_int(raw.get("timestamp"), "snapshot.timestamp"),
        transaction_hash=tx_hash,
        action=str(raw.get("action") or "").upper(),
        epoch=_as_int(raw.get("epoch"), "snapshot.epoch"),
        deposit_pps=_as_int(raw.get("depositPps"), "snapshot.depositPps"),
        withdrawal_pps=_as_int(raw.get("withdrawalPps"), "snapshot.withdrawalPps"),
        tvl=_as_int(raw.get("tvl"), "snapshot.tvl") if raw.get("tvl") not in {None, ""} else None,
    )


def fetch_rysk_premium_snapshots(pool: RyskPremiumPool, *, session: requests.Session | None = None, max_pages: int = 100) -> Iterator[RyskPremiumSnapshot]:
    """Yield the complete published snapshot history for one Rysk pool.

    Reads ``https://premium.rysk.finance/api/{chain_id}/{pool}/snapshots`` in
    application order. Each page is logged before its bounded network request
    so long histories remain observable to scanner and backfill operators.

    :param pool:
        Pool whose history is requested.
    :param session:
        Optional persistent HTTP session.
    :param max_pages:
        Safety bound for the endpoint's pagination.
    :return:
        Typed snapshots in API order.
    """

    if max_pages <= 0:
        raise ValueError(f"max_pages must be positive, got {max_pages}")
    for page in range(1, max_pages + 1):
        logger.info("Fetching Rysk Premium snapshots for %s on chain %d, page %d", pool.address, pool.chain_id, page)
        payload = _fetch_json(f"/api/{pool.chain_id}/{pool.address}/snapshots", params={"page": page}, session=session)
        if not isinstance(payload, list):
            raise RyskPremiumAPIError(f"Rysk Premium snapshots page {page} must be a list, got {type(payload).__name__}")
        if not payload:
            return
        yield from (_parse_snapshot(raw, pool) for raw in payload)
    raise RyskPremiumAPIError(f"Rysk Premium snapshots exceeded max_pages={max_pages} for {pool.address}")
