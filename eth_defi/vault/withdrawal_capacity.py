"""Read-only withdrawal buffer observations and configurable catalogue warnings.

Withdrawal amounts remain denomination assets, never shares. The tiny-TVL
warning uses a separate USD valuation. Unknown capacity stays unknown: idle cash
need not be the complete withdrawal buffer.
"""

import logging
import math
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.types import Percent
from eth_defi.vault.base import VaultBase, VaultSpec

logger = logging.getLogger(__name__)

DEFAULT_CAPACITY_THRESHOLD: Percent = 0.15
#: Capacity checks for vaults meeting the USD 10,000 guideline.
CAPACITY_REFRESH_INTERVAL = timedelta(days=1)
#: Capacity checks for smaller or unvalued vaults.
LOW_TVL_CAPACITY_REFRESH_INTERVAL = timedelta(days=7)
#: Allow the scanner to reach its next hourly tick before expiring a snapshot.
CAPACITY_FRESHNESS_GRACE = timedelta(hours=2)
DEFAULT_MAX_AGE = CAPACITY_REFRESH_INTERVAL + CAPACITY_FRESHNESS_GRACE
#: USD TVL strictly below this amount triggers the tiny-vault warning.
TINY_TVL_THRESHOLD_USD = Decimal("10000")


def configured_capacity_threshold() -> Percent:
    """Read the export threshold once without changing RPC observation policy."""
    return validate_threshold(float(os.environ.get("WITHDRAWAL_CAPACITY_THRESHOLD", str(DEFAULT_CAPACITY_THRESHOLD))))


def validate_threshold(value: Percent) -> Percent:
    """Require a finite Percent float fraction between zero and one."""
    assert isinstance(value, float), f"Capacity threshold must be a Percent float, got {type(value)}"
    if not math.isfinite(value) or not 0 <= value <= 1:
        message = "Capacity threshold must be between zero and one"
        raise ValueError(message)
    return value


@dataclass(frozen=True, slots=True)
class WithdrawalCapacityLimit:
    """Advisory withdrawal buffer, with the original measurement provenance.

    Capacity and TVL are gross denomination assets before withdrawal fees.
    ``None`` is not evidence of either adequate liquidity or a depleted buffer.
    """

    #: Whether the numeric buffer is low or the structural exit warning applies.
    capacity_warning: bool | None
    #: Whether an authoritative deposit or withdrawal pause is active.
    pause_warning: bool | None
    #: Expanded denomination amounts, threshold and observation limitations.
    capacity_reason: str
    #: Minimum immediate withdrawal buffer as a fraction of TVL.
    capacity_threshold: Percent
    #: Gross immediate denomination assets, or unknown.
    current_capacity: Decimal | None
    #: Same-block total denomination assets, or unknown.
    current_tvl: Decimal | None
    #: Whether same-observation USD TVL is below USD 10,000; unknown without a valuation.
    tiny_tvl: bool | None = None
    #: Source measurement and machine-readable pause and warning scope.
    observation: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Apply the same threshold contract to directly constructed assessments."""
        validate_threshold(self.capacity_threshold)

    def to_dict(self) -> dict:
        """Serialize the seven public fields using lossless token amounts."""
        return {"tiny_tvl": self.tiny_tvl, "capacity_warning": self.capacity_warning, "pause_warning": self.pause_warning, "capacity_reason": self.capacity_reason, "capacity_threshold": self.capacity_threshold, "current_capacity": format(self.current_capacity, "f") if self.current_capacity is not None else None, "current_tvl": format(self.current_tvl, "f") if self.current_tvl is not None else None}


def assess_withdrawal_capacity(observation: dict | None, capacity_threshold: Percent = DEFAULT_CAPACITY_THRESHOLD, *, now: datetime | None = None, max_age: timedelta | None = None) -> WithdrawalCapacityLimit:  # ruff: ignore[too-many-locals] - Keep the related warning decisions in one pure assessor.
    """Evaluate a persisted snapshot without RPCs or modifying its contents.

    :param observation: Primitive dictionary from the adapter or ``None`` for an old row.
    :param capacity_threshold: Buffer/TVL fraction; equality does not warn.
    :param now: UTC clock injected by deterministic export tests.
    :param max_age: Explicit age limit, otherwise the observation cadence plus grace.
    :return: Assessment and independently machine-readable pause/warning scope.
    """
    threshold = validate_threshold(capacity_threshold)
    # Keep denomination arithmetic exact while the public fraction uses Percent.
    decimal_threshold = Decimal(str(threshold))
    data = dict(observation or {"status": "unsupported", "reason": "Withdrawal capacity has not been observed"})
    configured_age = os.environ.get("WITHDRAWAL_CAPACITY_MAX_AGE_SECONDS")
    if max_age is None:
        if configured_age:
            max_age = timedelta(seconds=int(configured_age))
        elif data.get("refresh_interval_seconds") == int(LOW_TVL_CAPACITY_REFRESH_INTERVAL.total_seconds()):
            max_age = LOW_TVL_CAPACITY_REFRESH_INTERVAL + CAPACITY_FRESHNESS_GRACE
        else:
            max_age = DEFAULT_MAX_AGE
    if max_age <= timedelta(0):
        message = "Withdrawal capacity maximum age must be positive"
        raise ValueError(message)
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    stamp = data.get("observed_at")
    stale = False
    if stamp:
        try:
            timestamp = datetime.fromisoformat(stamp)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            stale = not timedelta(0) <= now - timestamp <= max_age
        except (ValueError, TypeError):
            stale = True
    elif data.get("status") == "measured":
        stale = True
    if stale:
        data["status"] = "stale"
        data["last_observation"] = {key: data.pop(key) for key in ("current_tvl", "current_tvl_usd", "current_capacity", "capacity_lower_bound", "idle_assets", "usd_valuation_source", "reason") if key in data}
        data["reason"] = "Previous withdrawal capacity observation is stale"

    def amount(key: str) -> Decimal | None:
        value = data.get(key)
        if value is None or isinstance(value, bool):
            return None
        try:
            value = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return value if value.is_finite() and value >= 0 else None

    tvl, capacity = amount("current_tvl"), amount("current_capacity")
    if data.get("status") in {"stale", "failed", "unsupported"}:
        tvl = capacity = None
    deposits, withdrawals = data.get("deposits_paused"), data.get("withdrawals_paused")
    if stale:
        deposits = withdrawals = None
    pause = True if deposits is True or withdrawals is True else False if deposits is False and withdrawals is False else None
    structural = data.get("structural_warning") is True
    measured = None
    if data.get("status") == "measured" and tvl is not None and tvl > 0 and capacity is not None and capacity <= tvl:
        measured = capacity < tvl * decimal_threshold
    usd_tvl = amount("current_tvl_usd")
    # A USD valuation is independent of whether the exit route is supported.
    # It still needs the same source timestamp and freshness guarantees.
    if stale or not stamp or data.get("status") == "failed":
        usd_tvl = None
    tiny_tvl = usd_tvl < TINY_TVL_THRESHOLD_USD if usd_tvl is not None else None
    warning = True if structural else measured
    source = "both" if structural and measured else "structural" if structural else "measured" if measured else None
    data.update(deposits_paused=deposits, withdrawals_paused=withdrawals, capacity_warning_source=source)
    symbol = str(data.get("denomination_symbol") or "denomination tokens").replace("\n", " ")
    reasons = [data.get("reason") or "No authoritative withdrawal capacity measurement"]
    if measured is not None:
        ratio = capacity / tvl
        reasons.append(f"Immediate withdrawal capacity is {capacity:,f} {symbol} against TVL {tvl:,f} {symbol}: {ratio:.2%}; configured threshold {threshold:.2%} ({tvl * decimal_threshold:,f} {symbol})")
    elif stale:
        reasons.append("Observation is stale; current capacity and pause state are unknown")
    else:
        reasons.append("Withdrawal buffer ratio is unknown")
    if structural:
        reasons.append("40acres withdrawals depend on available cash and borrower repayments without a guaranteed completion date")
    if tiny_tvl:
        reasons.append(f"Total vault TVL is {usd_tvl:,f} USD, below the tiny-TVL threshold of {TINY_TVL_THRESHOLD_USD:,f} USD")
    for direction, paused in [("Deposits", deposits), ("Withdrawals", withdrawals)]:
        if paused:
            reasons.append(f"{direction} are administratively paused")
    return WithdrawalCapacityLimit(warning, pause, "; ".join(reasons), threshold, capacity, tvl, tiny_tvl=tiny_tvl, observation=data)


def fetch_withdrawal_capacity_limit(vault: VaultBase, block_identifier: int | str = "latest", capacity_threshold: Percent = DEFAULT_CAPACITY_THRESHOLD) -> WithdrawalCapacityLimit:
    """Resolve a single block and assess the adapter's read-only observation."""
    validate_threshold(capacity_threshold)
    web3 = getattr(vault, "web3", None)
    if web3 is None:
        return assess_withdrawal_capacity(None, capacity_threshold)
    block = web3.eth.get_block(block_identifier)
    number = block["number"]
    data = vault.fetch_withdrawal_capacity_observation(number)
    data.update(chain_id=vault.chain_id, vault_address=vault.address, block_number=number, block_hash=block["hash"].hex(), observed_at=datetime.fromtimestamp(block["timestamp"], UTC).isoformat())
    return assess_withdrawal_capacity(data, capacity_threshold, now=datetime.fromtimestamp(block["timestamp"], UTC))


def enrich_withdrawal_capacity(rows: list[dict], database: Any, *, threshold: Percent = DEFAULT_CAPACITY_THRESHOLD, now: datetime | None = None) -> None:
    """Attach fresh assessments without changing non-capacity export fields.

    Legacy records may carry only the combined vault ``id``. Resolve that
    identity without adding fields to the record; unresolvable identities get
    an unknown assessment instead of interrupting catalogue publication.

    :param rows: Export records enriched in place, including retained legacy rows.
    :param database: Catalogue supplying the latest persisted observations.
    :param threshold: Minimum withdrawal buffer as a fraction of TVL.
    :param now: Optional UTC clock for observation freshness.
    :return: ``None`` after updating capacity fields on each record.
    """

    validate_threshold(threshold)
    for row in rows:
        spec = None
        try:
            if row.get("chain_id") is not None and row.get("address") is not None:
                spec = VaultSpec(int(row["chain_id"]), row["address"])
            elif isinstance(row.get("id"), str):
                spec = VaultSpec.parse_string(row["id"])
        except (AssertionError, TypeError, ValueError, OverflowError) as error:
            # Retained export rows must not prevent publication when their
            # identity cannot be matched to a current catalogue observation.
            logger.warning("Cannot resolve withdrawal capacity export identity (%s); exporting unknown capacity", type(error).__name__)
        source = database.rows.get(spec, {}) if spec is not None else {}
        observation = dict(source.get("_withdrawal_capacity_observation") or {})
        features = source.get("features")
        if features is None:
            features = getattr(source.get("_detection_data"), "features", ())
        if ERC4626Feature.forty_acres_like in (features or ()):
            observation["structural_warning"] = True
            observation.setdefault("status", "unsupported")
        result = assess_withdrawal_capacity(observation or None, threshold, now=now)
        row["withdrawal_capacity_limit"] = result.to_dict()
        row["withdrawal_capacity_observation"] = result.observation
