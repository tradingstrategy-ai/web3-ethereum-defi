"""Dedicated position state helpers for ``vault-test-trade``.

The command stores real and fork-only diagnostics in a normal executor
``State``.  Interpretation of the special ``TradingPosition.simulated`` field
belongs here and in the vault-test TUI only; general accounting and analytics
must remain unaware of it.
"""

import hashlib
import json
import re
import traceback
import uuid
from copy import deepcopy
from typing import Any

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.compat import native_datetime_utc_now
from eth_defi.vault.base import VaultSpec

from tradeexecutor.state.identifier import (
    AssetIdentifier,
    TradingPairIdentifier,
    TradingPairKind,
)
from tradeexecutor.state.position import TradingPosition
from tradeexecutor.state.state import State
from tradeexecutor.state.trade import TradeStatus


_URL_PATTERN = re.compile(r"(?:https?|wss?)://[^\s'\"<>]+")


VAULT_TEST_ATTEMPT_SCHEMA_VERSION = 2

# Persisted status values deliberately remain strings.  A newer executor might
# write a value an older executor does not recognise; state loading must remain
# lossless in that situation.
VAULT_TEST_RESULTS = {
    "metadata_failed",
    "preflight_failed",
    "transaction_build_failed",
    "gas_estimation_reverted",
    "broadcast_failed",
    "transaction_reverted",
    "receipt_analysis_failed",
    "state_inference_failed",
    "execution_failed",
    "infrastructure_failed",
    "deposit_closed",
    "redemption_unavailable",
    "simulation_unsupported_async",
    # Version 1 diagnostic states used this generic value.  Keep presenting it
    # normally when an older state file is reopened, while new attempts use
    # the more specific values above.
    "failed",
    "success",
    "success_simulated",
}


def redact_vault_test_error_text(value: object) -> str:
    """Make exception text safe to persist and hand to external reporters.

    JSON-RPC client exceptions can include their full provider URL, including an
    API key.  The diagnostic state must be shareable without leaking it.
    """

    return _URL_PATTERN.sub("<redacted-url>", str(value))


def _serialise_exception_chain(error: BaseException) -> list[dict]:
    """Return the causal exception chain without retaining exception objects."""

    chain: list[dict] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(
            {
                "type": f"{current.__class__.__module__}.{current.__class__.__qualname__}",
                "message": redact_vault_test_error_text(current),
                "arguments": [
                    redact_vault_test_error_text(argument) for argument in current.args
                ],
            }
        )
        current = current.__cause__ or current.__context__
    return chain


def _serialise_vault_test_transactions(
    state: State,
    *,
    original_trade_ids: set[int],
) -> list[dict]:
    """Extract persisted transaction diagnostics created by the failed attempt."""

    transactions: list[dict] = []
    for position in state.portfolio.get_all_positions():
        for trade in position.trades.values():
            if trade.trade_id in original_trade_ids:
                continue
            for transaction in trade.blockchain_transactions:
                transactions.append(
                    {
                        "position_id": position.position_id,
                        "trade_id": trade.trade_id,
                        "chain_id": transaction.chain_id,
                        "tx_hash": str(transaction.tx_hash)
                        if transaction.tx_hash
                        else None,
                        "contract_address": transaction.contract_address,
                        "function_selector": transaction.function_selector,
                        "wrapped_target": transaction.wrapped_target,
                        "wrapped_function_selector": transaction.wrapped_function_selector,
                        "nonce": transaction.nonce,
                        "block_number": transaction.block_number,
                        "block_hash": str(transaction.block_hash)
                        if transaction.block_hash
                        else None,
                        "status": transaction.status,
                        "revert_reason": redact_vault_test_error_text(
                            transaction.revert_reason
                        )
                        if transaction.revert_reason
                        else None,
                        "stack_trace": redact_vault_test_error_text(
                            transaction.stack_trace
                        )
                        if transaction.stack_trace
                        else None,
                    }
                )
    return transactions


def _serialise_vault_test_call_context(
    state: State,
    *,
    original_trade_ids: set[int],
) -> list[dict]:
    """Capture unsigned call details for failures without a receipt.

    The state already owns the transaction details.  Keep a compact subset so
    a reporter can reproduce a failed estimate without persisting signed bytes
    or an arbitrarily large calldata blob.
    """

    calls: list[dict] = []
    for position in state.portfolio.get_all_positions():
        for trade in position.trades.values():
            if trade.trade_id in original_trade_ids:
                continue
            for transaction in trade.blockchain_transactions:
                if transaction.block_number is not None:
                    continue
                details = transaction.details or {}
                calldata = details.get("data")
                calls.append(
                    {
                        "position_id": position.position_id,
                        "trade_id": trade.trade_id,
                        "chain_id": transaction.chain_id,
                        "sender": transaction.from_address,
                        "target": transaction.contract_address,
                        "function_selector": transaction.function_selector,
                        "wrapped_target": transaction.wrapped_target,
                        "wrapped_function_selector": transaction.wrapped_function_selector,
                        "value": str(details.get("value", 0)),
                        "gas": details.get("gas"),
                        "gas_price": details.get("gasPrice"),
                        "max_fee_per_gas": details.get("maxFeePerGas"),
                        "max_priority_fee_per_gas": details.get("maxPriorityFeePerGas"),
                        "nonce": transaction.nonce,
                        # This is unsigned ABI calldata, not a signed payload.
                        # Keeping it makes the report independently replayable
                        # with eth-defi at the recorded fork height.
                        "calldata": str(calldata) if calldata else None,
                        "calldata_hash": hashlib.sha256(
                            str(calldata).encode("utf-8")
                        ).hexdigest()
                        if calldata
                        else None,
                    }
                )
    return calls


def _capture_chain_blocks(web3config: Any | None) -> dict[str, dict]:
    """Capture the current block for every configured chain without masking failure."""

    if web3config is None:
        return {}

    blocks: dict[str, dict] = {}
    for chain_id, web3 in web3config.connections.items():
        chain_key = str(getattr(chain_id, "value", chain_id))
        try:
            blocks[chain_key] = {"block_number": int(web3.eth.block_number)}
        except Exception as block_error:
            blocks[chain_key] = {"error": redact_vault_test_error_text(block_error)}
    return blocks


def capture_vault_test_error(
    error: BaseException,
    *,
    state: State,
    original_trade_ids: set[int],
    web3config: Any | None,
    phase: str,
    capture_chain_blocks: bool = True,
) -> dict:
    """Create complete, JSON-safe diagnostics for a failed vault-test attempt.

    The payload intentionally contains both the Python-level error and every
    transaction created during this invocation.  Anvil adds an EVM stack trace
    to reverted :class:`BlockchainTransaction` objects, so preserving it here
    lets external reporters consume the same evidence after the fork is gone.
    """

    exception_chain = _serialise_exception_chain(error)
    call_context = _serialise_vault_test_call_context(
        state,
        original_trade_ids=original_trade_ids,
    )
    transactions = _serialise_vault_test_transactions(
        state,
        original_trade_ids=original_trade_ids,
    )
    return {
        "captured_at": native_datetime_utc_now().isoformat(),
        "phase": phase,
        "exception": exception_chain[0],
        "exception_chain": exception_chain,
        "traceback": redact_vault_test_error_text(
            "".join(traceback.format_exception(type(error), error, error.__traceback__))
        ),
        "chain_blocks": _capture_chain_blocks(web3config)
        if capture_chain_blocks
        else {},
        "transactions": transactions,
        "call_context": call_context,
    }


def classify_vault_test_failure(
    *,
    phase: str,
    error_data: dict,
) -> str:
    """Classify a failure from its lifecycle phase and transaction evidence."""

    if phase == "preflight":
        return "preflight_failed"
    if phase == "state_inference":
        return "state_inference_failed"

    transactions = error_data.get("transactions", [])
    if any(transaction.get("status") is False for transaction in transactions):
        return "transaction_reverted"
    if any(transaction.get("status") is True for transaction in transactions):
        return "receipt_analysis_failed"
    if any(transaction.get("tx_hash") for transaction in transactions):
        return "broadcast_failed"
    if error_data.get("call_context"):
        return "gas_estimation_reverted"
    if phase == "execute":
        return "execution_failed"
    return "transaction_build_failed"


def get_latest_vault_position(
    state: State, vault_spec: VaultSpec
) -> TradingPosition | None:
    """Return the newest diagnostic or traded position for one vault id.

    Metadata matching keeps pre-adapter diagnostic positions discoverable,
    while pair matching retains compatibility with positions created before
    vault-test metadata was stamped.
    """

    vault_id = vault_spec.as_string_id()
    matches: list[TradingPosition] = []
    for position in state.portfolio.get_all_positions():
        attempt = position.other_data.get("vault_test_attempt", {})
        if attempt.get("vault_id") == vault_id:
            matches.append(position)
            continue
        if (
            position.pair.chain_id == vault_spec.chain_id
            and position.pair.pool_address.lower() == vault_spec.vault_address.lower()
        ):
            matches.append(position)
    return max(matches, key=lambda position: position.position_id, default=None)


def get_vault_trade_position(
    state: State,
    vault_spec: VaultSpec,
    *,
    open_only: bool = False,
    simulated: bool | None = None,
    position_ids: set[int] | None = None,
    trade_ids: set[int] | None = None,
) -> TradingPosition | None:
    """Return the latest position that actually traded the selected vault pair.

    ``simulated`` is interpreted only by the vault-test command because its
    dedicated state intentionally contains both fork and real diagnostics.
    ``position_ids`` and ``trade_ids`` constrain lookup to evidence created by
    the current attempt, preventing a later failure from relabelling history.
    """

    matches = [
        position
        for position in state.portfolio.get_all_positions()
        if position.pair.chain_id == vault_spec.chain_id
        and position.pair.pool_address.lower() == vault_spec.vault_address.lower()
        and position.trades
        and (not open_only or position.is_open())
        and (simulated is None or position.simulated is simulated)
        and (position_ids is None or position.position_id in position_ids)
        and (
            trade_ids is None
            or any(trade_id in trade_ids for trade_id in position.trades)
        )
    ]
    return max(matches, key=lambda position: position.position_id, default=None)


def get_vault_test_status(position: TradingPosition | None) -> str:
    """Derive the TUI/table status from metadata and position state."""

    if position is None:
        return "not tested"

    # Explicit terminal results take priority over inferred trade lifecycle
    # state because adapter and infrastructure failures may have no trades.
    attempt = position.other_data.get("vault_test_attempt", {})
    result = attempt.get("result")
    if result:
        if result not in VAULT_TEST_RESULTS:
            # Do not write this presentation normalisation back to state: the
            # original raw value may have been written by a newer executor.
            return "legacy result"
        return result.replace("_", " ")

    phase_status = {
        "bridge_back_pending": "bridge back pending",
        "bridge_out_pending": "bridge out pending",
    }.get(attempt.get("phase"))
    if phase_status:
        return phase_status

    # Pending async requests remain open across command invocations. Direction
    # distinguishes a deposit ticket from a redemption ticket in the same enum.
    pending_trade = next(
        (
            trade
            for trade in reversed(position.trades.values())
            if trade.get_status() == TradeStatus.vault_settlement_pending
        ),
        None,
    )
    if pending_trade is not None:
        direction = pending_trade.other_data.get("vault_direction")
        return "redemption pending" if direction == "redeem" else "deposit pending"
    if position.is_open():
        return "deposited"
    if position.simulated:
        return "success (simulated)"
    if position.is_closed():
        return "success"
    return "failed"


def create_vault_test_diagnostic_pair(
    vault_spec: VaultSpec,
    reserve_asset: AssetIdentifier,
    vault=None,
) -> TradingPairIdentifier:
    """Create a serialisable placeholder pair when a vault adapter cannot load.

    The point of ``vault-test-trade`` is to retain adapter and universe failures,
    including vaults whose on-chain adapter support is incomplete.  A normal
    ``TradingPosition`` still needs a pair, so diagnostics use the downloaded
    vault token metadata when available and safe placeholders otherwise.  This
    pair is never routed or executed.
    """

    # Prefer downloaded share-token metadata, but fall back to deterministic
    # serialisable values when metadata loading was the failure being recorded.
    chain_id = vault_spec.chain_id
    base_address = (
        getattr(vault, "share_token_address", None) or vault_spec.vault_address
    )
    base_symbol = (
        getattr(vault, "share_token_symbol", None)
        or getattr(vault, "token_symbol", None)
        or "UNKNOWN"
    )
    base_decimals = getattr(vault, "share_token_decimals", None)
    if base_decimals is None:
        base_decimals = 18

    # A placeholder pair must live on the target chain even though its reserve
    # metadata originates from the hub executor's denomination asset.
    quote_address = (
        getattr(vault, "denomination_token_address", None) or reserve_asset.address
    )
    quote_symbol = (
        getattr(vault, "denomination_token_symbol", None) or reserve_asset.token_symbol
    )
    quote_decimals = getattr(vault, "denomination_token_decimals", None)
    if quote_decimals is None:
        quote_decimals = reserve_asset.decimals

    base = AssetIdentifier(chain_id, base_address, base_symbol, int(base_decimals))
    quote = AssetIdentifier(chain_id, quote_address, quote_symbol, int(quote_decimals))

    # Derive a stable JSON-safe identifier without colliding with normal small
    # pair ids. The 53-bit mask also keeps it exactly representable in JS UIs.
    internal_id = int.from_bytes(
        hashlib.sha256(
            f"{chain_id}:{vault_spec.vault_address.lower()}".encode("ascii")
        ).digest()[:8],
        "big",
    ) & ((1 << 53) - 1)
    protocol_slug = getattr(vault, "protocol_slug", None) or "unknown"

    return TradingPairIdentifier(
        base=base,
        quote=quote,
        pool_address=vault_spec.vault_address,
        exchange_address=ZERO_ADDRESS_STR,
        internal_id=internal_id,
        fee=0,
        reverse_token_order=False,
        exchange_name=getattr(vault, "name", None) or vault_spec.vault_address,
        kind=TradingPairKind.vault,
        other_data={
            "vault_features": list(getattr(vault, "features", None) or []),
            "vault_protocol": protocol_slug,
        },
    )


def create_vault_test_attempt_metadata(
    vault_spec: VaultSpec,
    *,
    simulated: bool,
    attempt_id: str | None = None,
    operation: str | None = None,
    provenance: dict | None = None,
) -> dict:
    """Create JSON-serialisable metadata carried by the authoritative position."""

    return {
        "schema_version": VAULT_TEST_ATTEMPT_SCHEMA_VERSION,
        "attempt_id": attempt_id or uuid.uuid4().hex,
        "vault_id": vault_spec.as_string_id(),
        "simulated": simulated,
        "operation": operation,
        "phase": "created",
        "created_at": native_datetime_utc_now().isoformat(),
        "provenance": provenance or {},
    }


def stamp_position_vault_test_attempt(
    position: TradingPosition,
    vault_spec: VaultSpec,
    *,
    simulated: bool,
    phase: str | None = None,
    result: str | None = None,
    detail: str | None = None,
    attempt_id: str | None = None,
    operation: str | None = None,
    provenance: dict | None = None,
) -> None:
    """Attach vault-test provenance to a specific target or bridge position."""

    position.simulated = simulated
    attempt = position.other_data.setdefault(
        "vault_test_attempt",
        create_vault_test_attempt_metadata(
            vault_spec,
            simulated=simulated,
            attempt_id=attempt_id,
            operation=operation,
            provenance=provenance,
        ),
    )
    attempt.setdefault("schema_version", VAULT_TEST_ATTEMPT_SCHEMA_VERSION)
    # A position may be revisited for redemption or a retry. Its metadata must
    # describe the latest action, not retain the id of its original deposit.
    attempt["attempt_id"] = attempt_id or attempt.get("attempt_id") or uuid.uuid4().hex
    attempt["vault_id"] = vault_spec.as_string_id()
    attempt["simulated"] = simulated
    if operation:
        attempt["operation"] = operation
    if provenance:
        attempt["provenance"] = provenance
    if phase:
        attempt["phase"] = phase
    if result:
        attempt["result"] = result
    if detail:
        attempt["detail"] = detail


def record_attempt_result(
    state: State,
    pair: TradingPairIdentifier,
    vault_spec: VaultSpec,
    *,
    simulated: bool,
    result: str,
    detail: str | None = None,
    error: dict | None = None,
    source_position_id: int | None = None,
    attempt_id: str | None = None,
    operation: str | None = None,
    provenance: dict | None = None,
) -> TradingPosition:
    """Create a closed diagnostic position in the dedicated vault-test state.

    Some failures happen before a transaction or even an adapter can be
    constructed. They still need one normal ``TradingPosition`` so the latest
    result for the vault remains discoverable by the TUI and subsequent runs.
    """

    reserve = state.portfolio.get_default_reserve_position().asset
    now = native_datetime_utc_now()
    position = state.portfolio.open_new_position(
        now,
        pair,
        assumed_price=1.0,
        reserve_currency=reserve,
        reserve_currency_price=1.0,
    )
    position.simulated = simulated

    attempt = create_vault_test_attempt_metadata(
        vault_spec,
        simulated=simulated,
        attempt_id=attempt_id,
        operation=operation,
        provenance=provenance,
    )
    attempt["result"] = result
    if detail:
        attempt["detail"] = detail
    if error:
        attempt["error"] = error
    if source_position_id is not None:
        attempt["source_position_id"] = source_position_id
    position.other_data["vault_test_attempt"] = attempt

    # Diagnostic positions never represent live holdings, so close them at the
    # same timestamp at which they were created.
    state.portfolio.close_position(position, now)
    return position


def export_vault_test_report(
    state: State,
    rows: list[dict],
) -> dict:
    """Build a compact external report without copying unrelated state history."""

    results = []
    for row in rows:
        vault_id = row["vault id"]
        matches = [
            candidate
            for candidate in state.portfolio.get_all_positions()
            if candidate.other_data.get("vault_test_attempt", {}).get("vault_id")
            == vault_id
        ]
        position = max(
            matches, key=lambda candidate: candidate.position_id, default=None
        )
        attempt = position.other_data.get("vault_test_attempt", {}) if position else {}
        results.append(
            {
                "vault_id": vault_id,
                "position_id": position.position_id if position else None,
                "row": row,
                "attempt": attempt,
            }
        )
    return {
        "schema_version": VAULT_TEST_ATTEMPT_SCHEMA_VERSION,
        "run": _get_latest_vault_test_run(state),
        "results": results,
    }


def _get_latest_vault_test_run(state: State) -> dict:
    """Read the latest run record even when another key exists in a newer cycle."""

    for cycle in sorted(state.other_data.data, reverse=True):
        run = state.other_data.data[cycle].get("vault_test_run")
        if run is not None:
            return run
    return {}


def write_vault_test_report(path, state: State, rows: list[dict]) -> None:
    """Write a machine-readable vault-test report with stable JSON key ordering."""

    path.write_text(
        json.dumps(export_vault_test_report(state, rows), indent=2, sort_keys=True)
    )


def close_simulated_positions(
    state: State,
    *,
    vault_spec: VaultSpec,
    position_ids: set[int],
    result: str | None = None,
    phase: str | None = None,
    attempt_id: str | None = None,
    operation: str | None = None,
    provenance: dict | None = None,
) -> None:
    """Close all newly-created fork positions and stamp their vault-test role.

    Both target-vault and temporary CCTP bridge positions are closed because the
    Anvil snapshot is about to be reverted. Only the target position receives
    the user-facing vault result metadata.
    """

    now = native_datetime_utc_now()
    vault_id = vault_spec.as_string_id()

    # A full instant round trip is already closed by perform_test_trade(), while
    # deposit-only and failed-redemption positions remain open. Process both
    # collections through the same stamping helper and close only when needed.
    positions = [
        position
        for position in (
            *state.portfolio.open_positions.values(),
            *state.portfolio.closed_positions.values(),
        )
        if position.position_id in position_ids
    ]
    for position in positions:
        position.simulated = True
        if position.pair.pool_address.lower() == vault_spec.vault_address.lower():
            attempt = position.other_data.setdefault("vault_test_attempt", {})
            attempt.setdefault("schema_version", VAULT_TEST_ATTEMPT_SCHEMA_VERSION)
            attempt.setdefault("attempt_id", attempt_id or uuid.uuid4().hex)
            attempt.setdefault("vault_id", vault_id)
            attempt["simulated"] = True
            if operation:
                attempt["operation"] = operation
            if phase:
                attempt["phase"] = phase
            if provenance:
                attempt["provenance"] = provenance
            if result:
                attempt["result"] = result
        if position.is_open():
            state.portfolio.close_position(position, now)


def merge_simulated_attempt(
    *,
    source_state: State,
    target_state: State,
    original_position_ids: set[int],
    original_trade_ids: set[int],
) -> list[TradingPosition]:
    """Copy only fork-created closed diagnostics to the persisted state.

    The caller executes against a deep copy. This makes it impossible to write
    fork-derived balance, valuation or settlement changes for an existing live
    position into the normal state file.
    """

    # Ignore all pre-existing positions from the copied state. Importing only
    # newly allocated, explicitly simulated positions prevents fork balances or
    # lifecycle changes from overwriting real history.
    imported: list[TradingPosition] = []
    for position in source_state.portfolio.closed_positions.values():
        if position.position_id in original_position_ids or not position.simulated:
            continue
        copied = deepcopy(position)
        target_state.portfolio.closed_positions[copied.position_id] = copied
        imported.append(copied)

    if imported:
        # Carry the id counters forward so the next real or simulated attempt
        # cannot reuse identifiers present in the merged diagnostics.
        target_state.portfolio.next_position_id = max(
            target_state.portfolio.next_position_id,
            max(position.position_id for position in imported) + 1,
        )
        max_trade_id = max(
            (
                trade.trade_id
                for position in imported
                for trade in position.trades.values()
                if trade.trade_id not in original_trade_ids
            ),
            default=target_state.portfolio.next_trade_id - 1,
        )
        target_state.portfolio.next_trade_id = max(
            target_state.portfolio.next_trade_id,
            max_trade_id + 1,
        )

    return imported
