"""Versioned in-process evidence from GMX's pre-sign gas refusal branches.

Only the synchronous ``GMX.create_order`` preflight and the Freqtrade wrapper's
pre-delegation cooldown create this evidence. A generic gas exception, later RPC
failure or serialised dictionary is never an instance of these proof types.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Literal

from ccxt import InsufficientFunds


@dataclass(frozen=True, slots=True)
class GasRefusalProof:
    """Carry the refusing branch, identity and actual retry deadline.

    ``raw_result`` retains the rejected response before stock can record an order.
    This is in-process evidence; durable retry deadlines belong to the consumer's
    later objective ledger.
    """

    version: int
    branch: Literal["ccxt_critical_return", "ccxt_critical_raise", "freqtrade_pair_cooldown"]
    phase: str
    chain_id: int
    payer: str
    pair: str
    observed_at: datetime
    signed: bool
    broadcast: bool
    raw_result: dict | None = None
    failure_count: int = 0
    retry_not_before: datetime | None = None


class GMXPreflightGasError(InsufficientFunds):
    """CCXT refusal raised exclusively by the create-order critical preflight."""

    def __init__(self, message: str, proof: GasRefusalProof) -> None:
        """Attach structured evidence before any signing or submission."""
        super().__init__(message)
        self.proof = proof


class GasRejectedOrder(dict):
    """CCXT-shaped rejected result with a typed, out-of-band branch proof."""

    def __init__(self, result: dict, proof: GasRefusalProof) -> None:
        """Keep the original response fields separate from the proof."""
        super().__init__(result)
        self.proof = proof


def is_explicit_zero(value: object) -> bool:
    """Accept only present, finite, numeric and non-boolean zero.

    Strings, missing values and false booleans do not attest executed quantity.
    """
    if type(value) not in {int, float, Decimal}:
        return False
    try:
        return value == 0 and isfinite(value)
    except (InvalidOperation, ValueError, OverflowError):
        return False


def is_gas_rejected_result(result: object) -> bool:
    """Validate the terminal gas response without inferring missing fields.

    A transaction, child order or execution evidence contradicts no acceptance.
    """
    if not isinstance(result, dict) or result.get("status") != "rejected":
        return False
    info = result.get("info")
    if not isinstance(info, dict) or info.get("error") != "insufficient_gas":
        return False
    return (
        is_explicit_zero(result.get("filled"))
        and not result.get("trades")
        and not any(
            result.get(key) or info.get(key)
            for key in (
                "tx_hash",
                "txHash",
                "transactionHash",
                "transaction_hash",
                "transactions",
                "order_key",
                "orderKey",
                "order_keys",
                "pending",
                "pending_order",
                "pending_orders",
                "child_orders",
                "child_order",
                "childOrderIds",
                "orders",
                "stopLoss",
                "takeProfit",
            )
        )
    )
