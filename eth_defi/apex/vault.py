"""Typed ApeX public vault API parsing and fetching.

The public endpoints are:

- `Vault ranking <https://omni.apex.exchange/api/v3/vault/ranking>`__
- `Vault fund net values
  <https://omni.apex.exchange/api/v3/vault/fund-net-values>`__

The history endpoint was verified on 2026-07-23 to return one unpaginated
``data.timeValue`` array with no completeness token or range parameters.
"""

# ruff: noqa: EM101

import datetime
import logging
import math
import time
from collections import Counter
from dataclasses import dataclass

from eth_typing import HexAddress
from eth_utils import is_address

from eth_defi.apex.constants import (
    APEX_DEFAULT_HISTORY_DEADLINE,
    APEX_DEFAULT_RANKING_ATTEMPTS,
    APEX_DEFAULT_RANKING_DEADLINE,
    APEX_RANKING_PAGE_SIZE,
)
from eth_defi.apex.session import ApexAPIError, ApexSessionPool

logger = logging.getLogger(__name__)


def _parse_float(value: object, field_name: str, *, required: bool = False) -> float | None:
    """Parse one finite public API numeric value."""
    if value is None or value == "":
        if required:
            raise ApexAPIError(f"ApeX field {field_name} is required")
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ApexAPIError(f"ApeX field {field_name} is not numeric: {value!r}") from exc
    if not math.isfinite(parsed):
        raise ApexAPIError(f"ApeX field {field_name} is non-finite: {value!r}")
    return parsed


def _parse_millisecond_timestamp(value: object, field_name: str, *, zero_is_none: bool = False) -> datetime.datetime | None:
    """Convert one millisecond unix timestamp to naive UTC."""
    if value is None or value == "":
        return None
    try:
        milliseconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ApexAPIError(f"ApeX field {field_name} is not a millisecond timestamp: {value!r}") from exc
    if zero_is_none and milliseconds == 0:
        return None
    if milliseconds < 0:
        raise ApexAPIError(f"ApeX field {field_name} is negative: {value!r}")
    try:
        return datetime.datetime.fromtimestamp(milliseconds / 1000, tz=datetime.UTC).replace(tzinfo=None)
    except (OSError, OverflowError, ValueError) as exc:
        raise ApexAPIError(f"ApeX field {field_name} is outside the supported datetime range: {value!r}") from exc


def _parse_envelope(payload: object) -> dict:
    """Validate the common ApeX HTTP-200 application envelope."""
    if not isinstance(payload, dict):
        raise ApexAPIError("ApeX response must be a JSON object")
    code = payload.get("code")
    if code not in {None, 0, "0"}:
        raise ApexAPIError(f"ApeX application error {code}: {payload.get('msg', '')}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ApexAPIError("ApeX response data must be an object")
    return data


@dataclass(slots=True, frozen=True)
class ApexVaultSummary:
    """One vault returned by the ApeX ranking endpoint."""

    #: Platform-unique vault identifier.
    vault_id: str

    #: Stable synthetic reader address.
    synthetic_address: str

    #: Ethereum address reported by ApeX, which is not unique per vault.
    reported_ethereum_address: HexAddress | None

    #: Display name.
    name: str

    #: Strategy description.
    description: str

    #: Raw vault lifecycle status.
    status: str

    #: Raw collection-vault type.
    vault_type: str

    #: Current share price in ApeX USDT terms.
    share_price: float | None

    #: Current total value in ApeX USDT terms.
    tvl: float | None

    #: Current reported share count.
    share_count: float | None

    #: Creation timestamp as naive UTC.
    created_at: datetime.datetime | None

    #: Source update timestamp as naive UTC.
    source_updated_at: datetime.datetime | None

    #: Terminal timestamp as naive UTC.
    finished_at: datetime.datetime | None

    #: Subscription cap as a raw numeric value.
    max_amount: float | None

    #: Unverified fee-unit source value.
    purchase_fee_rate_raw: str | None

    #: Unverified profit-share-unit source value.
    share_profit_ratio_raw: str | None


@dataclass(slots=True, frozen=True)
class ApexHistoryPoint:
    """One native ApeX historical vault value."""

    #: Exact naive UTC source timestamp.
    timestamp: datetime.datetime

    #: Native share price.
    net_value: float

    #: Native total vault value.
    total_value: float

    @property
    def total_supply(self) -> float | None:
        """Derive total share supply when division is valid."""
        return self.total_value / self.net_value if self.net_value > 0 else None


@dataclass(slots=True, frozen=True)
class ApexRankingPage:
    """One validated ranking page."""

    #: Total source rows reported for the complete ranking.
    total_size: int

    #: Parsed page vaults.
    vaults: tuple[ApexVaultSummary, ...]


def parse_vault_summary(raw: object) -> ApexVaultSummary:
    """Parse one ranking vault without status or TVL filtering.

    :param raw:
        Raw ``vaultList`` object.
    :return:
        Typed vault summary.
    """
    if not isinstance(raw, dict):
        raise ApexAPIError("ApeX vaultList entry must be an object")
    vault_id = str(raw.get("vaultId", "")).strip()
    if not vault_id:
        raise ApexAPIError("ApeX vaultId is required")
    raw_address = str(raw.get("vaultEthAddress", "")).strip()
    address = HexAddress(raw_address.lower()) if raw_address and is_address(raw_address) else None
    return ApexVaultSummary(
        vault_id=vault_id,
        synthetic_address=f"apex-vault-{vault_id}",
        reported_ethereum_address=address,
        name=str(raw.get("name") or ""),
        description=str(raw.get("desc") or ""),
        status=str(raw.get("status") or ""),
        vault_type=str(raw.get("collectVaultType") or ""),
        share_price=_parse_float(raw.get("vaultNetValue"), "vaultNetValue"),
        tvl=_parse_float(raw.get("tvl"), "tvl"),
        share_count=_parse_float(raw.get("share"), "share"),
        created_at=_parse_millisecond_timestamp(raw.get("createdTime"), "createdTime", zero_is_none=True),
        source_updated_at=_parse_millisecond_timestamp(raw.get("updatedTime"), "updatedTime", zero_is_none=True),
        finished_at=_parse_millisecond_timestamp(raw.get("finishedTime"), "finishedTime", zero_is_none=True),
        max_amount=_parse_float(raw.get("maxAmount"), "maxAmount"),
        purchase_fee_rate_raw=None if raw.get("purchaseFeeRate") is None else str(raw.get("purchaseFeeRate")),
        share_profit_ratio_raw=None if raw.get("shareProfitRatio") is None else str(raw.get("shareProfitRatio")),
    )


def parse_ranking_page(payload: object) -> ApexRankingPage:
    """Parse and validate one ranking response page.

    The application envelope, total size, vault array and every retained vault
    record are validated before the page is returned.

    :param payload:
        Decoded JSON response.
    :return:
        Typed page with its complete-source row count.
    """
    data = _parse_envelope(payload)
    vault_list = data.get("vaultList")
    if not isinstance(vault_list, list):
        raise ApexAPIError("ApeX ranking data.vaultList must be an array")
    total_size = data.get("totalSize")
    if not isinstance(total_size, int) or isinstance(total_size, bool) or total_size < 0:
        raise ApexAPIError("ApeX ranking data.totalSize must be a non-negative integer")
    return ApexRankingPage(total_size=total_size, vaults=tuple(parse_vault_summary(item) for item in vault_list))


def parse_history(payload: object) -> tuple[ApexHistoryPoint, ...]:
    """Parse, canonicalise and order one unpaginated history response.

    Equivalent duplicate timestamps collapse to one point. Conflicting values
    at one timestamp reject the complete response so no ambiguous history is
    staged.

    :param payload:
        Decoded JSON response.
    :return:
        Timestamp-ordered immutable history points.
    """
    data = _parse_envelope(payload)
    time_values = data.get("timeValue")
    if not isinstance(time_values, list):
        raise ApexAPIError("ApeX history data.timeValue must be an array")
    points: dict[datetime.datetime, ApexHistoryPoint] = {}
    for raw in time_values:
        if not isinstance(raw, dict):
            raise ApexAPIError("ApeX history entry must be an object")
        timestamp = _parse_millisecond_timestamp(raw.get("timestamp"), "timestamp")
        if timestamp is None:
            raise ApexAPIError("ApeX history timestamp is required")
        point = ApexHistoryPoint(
            timestamp=timestamp,
            net_value=_parse_float(raw.get("netValue"), "netValue", required=True),
            total_value=_parse_float(raw.get("totalValue"), "totalValue", required=True),
        )
        existing = points.get(timestamp)
        if existing is not None and existing != point:
            raise ApexAPIError(f"ApeX history contains conflicting values at {timestamp.isoformat()}")
        points[timestamp] = point
    return tuple(points[timestamp] for timestamp in sorted(points))


def fetch_ranking_page(
    session_pool: ApexSessionPool,
    page: int,
    *,
    limit: int = APEX_RANKING_PAGE_SIZE,
    operation_deadline: float,
) -> ApexRankingPage:
    """Fetch one zero-based ranking page.

    Endpoint validation runs inside the session pool retry boundary, so an
    invalid HTTP-200 application envelope is retried as one request operation.

    :param session_pool:
        Configured bounded ApeX session pool.
    :param page:
        Zero-based page number.
    :param limit:
        Positive page size.
    :param operation_deadline:
        Absolute monotonic deadline shared by the full ranking operation.
    :return:
        Validated typed ranking page.
    """
    if page < 0 or limit <= 0:
        raise ValueError("ApeX ranking page must be non-negative and limit positive")
    return session_pool.fetch_json(
        "vault/ranking",
        params={"page": page, "limit": limit},
        operation_deadline=operation_deadline,
        validator=parse_ranking_page,
    )


def _fetch_ranking_pass(
    session_pool: ApexSessionPool,
    *,
    limit: int,
    operation_deadline: float,
) -> tuple[ApexVaultSummary, ...]:
    """Fetch and validate one complete in-memory ranking pass."""
    first = fetch_ranking_page(session_pool, 0, limit=limit, operation_deadline=operation_deadline)
    page_count = (first.total_size + limit - 1) // limit
    vaults = list(first.vaults)
    for page_number in range(1, page_count):
        page = fetch_ranking_page(session_pool, page_number, limit=limit, operation_deadline=operation_deadline)
        if page.total_size != first.total_size:
            raise ApexAPIError(f"ApeX ranking total changed within pass: {first.total_size} to {page.total_size}")
        vaults.extend(page.vaults)
    identifiers = [vault.vault_id for vault in vaults]
    duplicates = sorted(vault_id for vault_id, count in Counter(identifiers).items() if count > 1)
    if duplicates:
        logger.warning("Duplicate ApeX vault IDs in ranking pass: %s", duplicates)
        raise ApexAPIError(f"ApeX ranking contains duplicate vault IDs: {duplicates}")
    if len(vaults) != first.total_size:
        raise ApexAPIError(f"ApeX ranking returned {len(vaults)} rows but reported {first.total_size}")
    return tuple(vaults)


def fetch_stabilised_vaults(
    session_pool: ApexSessionPool,
    *,
    limit: int = APEX_RANKING_PAGE_SIZE,
    operation_timeout: float = APEX_DEFAULT_RANKING_DEADLINE,
    attempts: int = APEX_DEFAULT_RANKING_ATTEMPTS,
) -> tuple[ApexVaultSummary, ...]:
    """Fetch two complete matching ranking passes.

    The second pass supplies the stored metric values after both passes report
    identical vault membership.
    """
    if operation_timeout <= 0 or attempts <= 0:
        raise ValueError("ApeX ranking timeout and attempts must be positive")
    deadline = time.monotonic() + operation_timeout
    last_error: ApexAPIError | None = None
    for attempt in range(attempts):
        try:
            first = _fetch_ranking_pass(session_pool, limit=limit, operation_deadline=deadline)
            second = _fetch_ranking_pass(session_pool, limit=limit, operation_deadline=deadline)
            first_ids = {vault.vault_id for vault in first}
            second_ids = {vault.vault_id for vault in second}
            if first_ids != second_ids:
                raise ApexAPIError(f"ApeX ranking membership changed between passes: removed={sorted(first_ids - second_ids)}, added={sorted(second_ids - first_ids)}")
            return second
        except ApexAPIError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                logger.warning("Retrying complete ApeX ranking read (%d/%d): %s", attempt + 1, attempts, exc)
    raise ApexAPIError(f"Could not stabilise ApeX ranking after {attempts} attempts: {last_error}") from last_error


def fetch_vault_history(
    session_pool: ApexSessionPool,
    vault_id: str,
    *,
    operation_timeout: float = APEX_DEFAULT_HISTORY_DEADLINE,
) -> tuple[ApexHistoryPoint, ...]:
    """Fetch all history currently recoverable for one ApeX vault.

    The public endpoint exposes no pagination or completeness metadata. The
    returned timestamps are therefore the recoverable source range, not a claim
    of lifetime completeness.
    """
    if not vault_id:
        raise ValueError("ApeX vault ID is required")
    if operation_timeout <= 0:
        raise ValueError("ApeX history timeout must be positive")
    return session_pool.fetch_json(
        "vault/fund-net-values",
        params={"vaultId": vault_id},
        operation_deadline=time.monotonic() + operation_timeout,
        validator=parse_history,
    )
