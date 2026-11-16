"""Read-only withdrawal buffer observations and configurable catalogue warnings.

Withdrawal amounts remain denomination assets, never shares. The tiny-TVL
warning uses a separate USD valuation. Unknown capacity stays unknown: idle cash
need not be the complete withdrawal buffer.
"""

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from eth_defi.vault.base import VaultBase, VaultSpec

DEFAULT_CAPACITY_THRESHOLD = Decimal("0.15")
DEFAULT_MAX_AGE = timedelta(hours=2)
#: USD TVL strictly below this amount triggers the tiny-vault warning.
TINY_TVL_THRESHOLD_USD = Decimal("10000")


def configured_capacity_threshold() -> Decimal:
    """Read the export threshold once without changing RPC observation policy."""
    return validate_threshold(os.environ.get("WITHDRAWAL_CAPACITY_THRESHOLD", str(DEFAULT_CAPACITY_THRESHOLD)))


def validate_threshold(value: Decimal | str) -> Decimal:
    """Validate a finite fraction without accepting ambiguous binary floats."""
    if isinstance(value, bool) or not isinstance(value, (Decimal, str)):
        message = "Capacity threshold must be a Decimal or decimal string"
        raise ValueError(message)
    result = Decimal(value)
    if not result.is_finite() or not 0 <= result <= 1:
        message = "Capacity threshold must be between zero and one"
        raise ValueError(message)
    return result


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
    capacity_threshold: Decimal
    #: Gross immediate denomination assets, or unknown.
    current_capacity: Decimal | None
    #: Same-block total denomination assets, or unknown.
    current_tvl: Decimal | None
    #: Whether same-observation USD TVL is below USD 10,000; unknown without a valuation.
    tiny_tvl: bool | None = None
    #: Source measurement and machine-readable pause and warning scope.
    observation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize the seven public fields using lossless token amounts."""
        return {"tiny_tvl": self.tiny_tvl, "capacity_warning": self.capacity_warning, "pause_warning": self.pause_warning, "capacity_reason": self.capacity_reason, "capacity_threshold": float(self.capacity_threshold), "current_capacity": format(self.current_capacity, "f") if self.current_capacity is not None else None, "current_tvl": format(self.current_tvl, "f") if self.current_tvl is not None else None}


def assess_withdrawal_capacity(observation: dict | None, capacity_threshold: Decimal | str = DEFAULT_CAPACITY_THRESHOLD, *, now: datetime | None = None, max_age: timedelta = DEFAULT_MAX_AGE) -> WithdrawalCapacityLimit:  # ruff: ignore[too-many-locals] - Keep the related warning decisions in one pure assessor.
    """Evaluate a persisted snapshot without RPCs or modifying its contents.

    :param observation: Primitive dictionary from the adapter or ``None`` for an old row.
    :param capacity_threshold: Buffer/TVL fraction; equality does not warn.
    :param now: UTC clock injected by deterministic export tests.
    :param max_age: Maximum permitted age of a current observation.
    :return: Assessment and independently machine-readable pause/warning scope.
    """
    threshold = validate_threshold(capacity_threshold)
    data = dict(observation or {"status": "unsupported", "reason": "Withdrawal capacity has not been observed"})
    configured_age = os.environ.get("WITHDRAWAL_CAPACITY_MAX_AGE_SECONDS")
    if configured_age is not None and max_age == DEFAULT_MAX_AGE:
        max_age = timedelta(seconds=int(configured_age))
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
        measured = capacity < tvl * threshold
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
        reasons.append(f"Immediate withdrawal capacity is {capacity:,f} {symbol} against TVL {tvl:,f} {symbol}: {ratio:.2%}; configured threshold {threshold:.2%} ({tvl * threshold:,f} {symbol})")
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


def fetch_withdrawal_capacity_limit(vault: VaultBase, block_identifier: int | str = "latest", capacity_threshold: Decimal | str | None = DEFAULT_CAPACITY_THRESHOLD) -> WithdrawalCapacityLimit:
    """Resolve a single block and assess the adapter's read-only observation."""
    capacity_threshold = DEFAULT_CAPACITY_THRESHOLD if capacity_threshold is None else capacity_threshold
    validate_threshold(capacity_threshold)
    web3 = getattr(vault, "web3", None)
    if web3 is None:
        return assess_withdrawal_capacity(None, capacity_threshold)
    block = web3.eth.get_block(block_identifier)
    number = block["number"]
    data = vault.fetch_withdrawal_capacity_observation(number)
    data.update(chain_id=vault.chain_id, vault_address=vault.address, block_number=number, block_hash=block["hash"].hex(), observed_at=datetime.fromtimestamp(block["timestamp"], UTC).isoformat())
    return assess_withdrawal_capacity(data, capacity_threshold, now=datetime.fromtimestamp(block["timestamp"], UTC))


def enrich_withdrawal_capacity(rows: list[dict], database: Any, *, threshold: Decimal | str = DEFAULT_CAPACITY_THRESHOLD, now: datetime | None = None) -> None:
    """Attach fresh assessments after sticky replay without changing existing fields."""

    for row in rows:
        spec = VaultSpec(int(row["chain_id"]), row["address"])
        source = database.rows.get(spec, {})
        result = assess_withdrawal_capacity(source.get("_withdrawal_capacity_observation"), threshold, now=now)
        row["withdrawal_capacity_limit"] = result.to_dict()
        row["withdrawal_capacity_observation"] = result.observation
