"""Sequential vault-test execution and result recording.

This module contains no Typer option handling and no initial deployment setup.
It takes an already constructed :class:`VaultTestRuntime` and processes one
explicit vault specification at a time.  Keeping the state machine here makes
the CLI entry point small and makes deposit/redemption continuation rules
readable without wading through bootstrap code.
"""

import datetime
import logging
import signal
import uuid
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from eth_defi.compat import native_datetime_utc_now
from eth_defi.vault.base import VaultSpec
from tradingstrategy.chain import ChainId
from tradingstrategy.candle import Candle
from tradingstrategy.timebucket import TimeBucket

from tradeexecutor.cli.bootstrap import (
    check_universe_chains_have_gas,
    check_universe_chains_have_rpc,
    check_universe_contracts_resolve,
)
from tradeexecutor.cli.testtrade import perform_test_trade
from eth_defi.cli.vault_trade.state import (
    close_simulated_positions,
    capture_vault_test_error,
    classify_vault_test_failure,
    create_vault_test_diagnostic_pair,
    get_latest_vault_position,
    get_vault_test_status,
    get_vault_trade_position,
    merge_simulated_attempt,
    record_attempt_result,
    redact_vault_test_error_text,
    stamp_position_vault_test_attempt,
)
from eth_defi.cli.vault_trade.setup import VaultTestRuntime
from eth_defi.cli.vault_trade.simulation import (
    SIMULATED_VAULT_ATTEMPT_TIMEOUT,
    SimulatedVaultAttemptTimeout,
    is_simulated_infrastructure_failure,
    queue_simulated_infrastructure_retry,
    raise_simulated_vault_attempt_timeout,
    restore_simulated_snapshots,
    take_simulated_snapshots,
)
from eth_defi.cli.vault_trade.tui import VaultTestAction
from tradeexecutor.ethereum.routing_state import OutOfBalance
from tradeexecutor.state.identifier import TradingPairIdentifier
from tradeexecutor.state.position import TradingPosition
from tradeexecutor.state.state import State
from tradeexecutor.state.trade import TradeFlag, TradeStatus
from tradeexecutor.strategy.execution_context import ExecutionContext, ExecutionMode
from tradeexecutor.strategy.execution_model import ExecutionHaltableIssue
from tradeexecutor.strategy.generic.generic_pricing_model import GenericPricing
from tradeexecutor.strategy.generic.generic_valuation import GenericValuation
from tradeexecutor.strategy.trading_strategy_universe import (
    TradingStrategyUniverse,
    load_partial_data,
)
from tradeexecutor.strategy.universe_model import UniverseOptions
from tradeexecutor.strategy.valuation import revalue_state

logger = logging.getLogger(__name__)


def build_vault_test_universe(
    *,
    client,
    vault_universe,
    vault_spec: VaultSpec,
    reserve_asset,
    primary_chain_id: ChainId,
    execution_context: ExecutionContext,
) -> TradingStrategyUniverse:
    """Build a fresh one-vault executable universe for an action.

    A failed adapter or data translation is intentionally allowed to escape to
    the caller, which records a diagnostic result and continues the batch.
    """

    # Limit before loading pair data so incomplete adapters fail only their own
    # requested vault and cannot prevent unrelated ids from running.
    selected_vault = vault_universe.limit_to_vaults(
        [(ChainId(vault_spec.chain_id), vault_spec.vault_address)],
        check_all_vaults_found=True,
    )
    dataset = load_partial_data(
        client=client,
        time_bucket=TimeBucket.d1,
        pairs=[],
        execution_context=execution_context,
        universe_options=UniverseOptions(history_period=datetime.timedelta(days=1)),
        liquidity=False,
        vaults=selected_vault,
        vault_history_source="none",
        check_all_vaults_found=True,
    )
    if dataset.candles is None or dataset.candles.empty:
        # Vault-only live universes do not download OHLCV data, but the normal
        # universe constructor still expects the canonical empty candle schema.
        dataset.candles = Candle.to_dataframe()
    return TradingStrategyUniverse.create_from_dataset(
        dataset,
        reserve_asset=reserve_asset,
        primary_chain=primary_chain_id,
        auto_generate_cctp_bridges=vault_spec.chain_id != primary_chain_id.value,
    )


class SimulatedAttemptAlarm:
    """Own the SIGALRM lifecycle for one simulated vault attempt.

    The timeout covers RPC and adapter work only.  Call :meth:`disarm` before
    mutating the persisted state so an alarm cannot interrupt an atomic state
    write after the external operation has already returned.
    """

    def __init__(self):
        """Create an inactive alarm that does not yet alter signal handling."""

        self.previous_handler = None
        self.installed = False
        self.armed = False

    def arm(self) -> None:
        """Install the timeout handler and start the per-vault wall clock."""

        self.previous_handler = signal.signal(
            signal.SIGALRM,
            raise_simulated_vault_attempt_timeout,
        )
        self.installed = True
        signal.alarm(SIMULATED_VAULT_ATTEMPT_TIMEOUT)
        self.armed = True

    def disarm(self) -> None:
        """Cancel the pending timeout while retaining the previous handler."""

        if self.armed:
            signal.alarm(0)
            self.armed = False

    def close(self) -> None:
        """Cancel the alarm and restore the process-wide signal handler."""

        self.disarm()
        if self.installed:
            signal.signal(signal.SIGALRM, self.previous_handler)
            self.installed = False


@dataclass(slots=True)
class VaultAttempt:
    """Prepared per-vault objects shared by action selection and execution."""

    spec: VaultSpec
    vault: Any
    universe: Any
    pair: TradingPairIdentifier
    routing_model: Any
    pricing_model: GenericPricing
    valuation_model: GenericValuation
    routing_state: Any
    previous: TradingPosition | None
    open_trade_position: TradingPosition | None
    bridge_position: TradingPosition | None


@dataclass(slots=True)
class VaultAttemptContext:
    """Immutable attempt identity plus mutable lifecycle phase for diagnostics."""

    attempt_id: str
    original_position_ids: set[int]
    original_trade_ids: set[int]
    provenance: dict
    phase: str = "preflight"
    operation: str | None = None


def should_leave_deposit_open(
    *,
    operation: str,
    is_async: bool,
    redemption_available: bool,
    manual: bool,
) -> bool:
    """Decide whether a deposit action must stop before redemption.

    Manual deposits are intentionally single-action.  Automatic deposits may
    complete a round trip only when the vault is synchronous and currently
    permits redemption.
    """

    return operation == "deposit" and (manual or is_async or not redemption_available)


def get_bridge_conflict(
    bridge_position: TradingPosition | None,
    vault_spec: VaultSpec,
) -> str | None:
    """Explain why a shared per-chain bridge cannot serve this vault yet.

    A chain has one bridge position, so another vault must not overwrite its
    attempt metadata or consume capital already bridged for the owning vault.
    """

    if bridge_position is None:
        return None

    attempt = bridge_position.other_data.get("vault_test_attempt", {})
    owner_vault_id = attempt.get("vault_id")
    in_transit = next(
        (
            trade
            for trade in bridge_position.trades.values()
            if trade.get_status() == TradeStatus.cctp_in_transit
        ),
        None,
    )
    if in_transit is not None:
        return (
            f"CCTP transfer for {owner_vault_id or 'another vault'} is still in transit"
        )

    phase = attempt.get("phase")
    if (
        owner_vault_id
        and owner_vault_id != vault_spec.as_string_id()
        and phase in {"bridge_out_pending", "bridge_back_pending"}
    ):
        return f"Bridge capital belongs to pending vault {owner_vault_id}"

    return None


@dataclass(slots=True)
class VaultTestBatchRunner:
    """Run explicit vault ids sequentially and persist one row per outcome."""

    runtime: VaultTestRuntime
    client: Any
    vault_universe: Any
    state: State
    store: Any
    vault_specs: list[VaultSpec]
    amount: Decimal
    max_slippage: float
    auto_simulated: bool
    rerun: bool
    settle_async_on_anvil: bool = False
    manual_action: VaultTestAction | None = None

    rows: list[dict] = field(default_factory=list, init=False)
    pending_specs: deque = field(init=False)
    infrastructure_restart_counts: dict[str, int] = field(
        default_factory=lambda: defaultdict(int),
        init=False,
    )
    restart_requested: BaseException | None = field(default=None, init=False)
    current_attempt: VaultAttemptContext | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Preserve the caller's vault order in a mutable work queue."""

        self.pending_specs = deque(self.vault_specs)

    @property
    def mode(self) -> str:
        """Human-readable execution mode used in the output table."""

        return "simulated" if self.auto_simulated else "real"

    def run(self) -> list[dict]:
        """Process the requested vaults and return tabulate-compatible rows.

        Infrastructure retries are inserted at the front of the queue so the
        affected vault is retried immediately on the replacement forks.  A
        no-cash result is the only ordinary outcome that stops the batch.
        """

        while self.pending_specs:
            if self.restart_requested is not None:
                self.runtime.replace_simulation(self.restart_requested)
                self.restart_requested = None

            spec = self.pending_specs.popleft()
            vault = self.vault_universe.get_by_vault_spec(
                (spec.chain_id, spec.vault_address)
            )

            # A requested id missing from the latest universe is itself a useful
            # diagnostic and must not abort later ids.
            if vault is None:
                self._record_missing_vault(spec)
                continue

            if self._run_vault(spec, vault):
                break

        return self.rows

    def _run_vault(self, spec: VaultSpec, vault: Any) -> bool:
        """Run one guarded attempt.

        :return:
            ``True`` only when the caller must stop the complete batch because
            no deposit cash remains.  All other failures are recorded and the
            next vault is allowed to run.
        """

        alarm = SimulatedAttemptAlarm()
        fork_snapshots = {}
        infrastructure_failure = None
        pair = None
        previous = get_latest_vault_position(self.state, spec)
        stop_batch = False
        original_trade_ids = {
            trade.trade_id for trade in self.state.portfolio.get_all_trades()
        }
        self.current_attempt = VaultAttemptContext(
            attempt_id=uuid.uuid4().hex,
            original_position_ids={
                position.position_id
                for position in self.state.portfolio.get_all_positions()
            },
            original_trade_ids=original_trade_ids,
            provenance=self.runtime.get_provenance(),
        )

        try:
            if self.auto_simulated:
                alarm.arm()
                fork_snapshots = take_simulated_snapshots(
                    self.runtime.web3config,
                    self.runtime.deployment,
                    spec,
                )

            attempt = self._prepare_attempt(spec, vault, previous)
            pair = attempt.pair
            self.current_attempt.phase = "execute"
            stop_batch = self._process_attempt(attempt, alarm)
        except SimulatedVaultAttemptTimeout as e:
            infrastructure_failure = e
            alarm.disarm()
            self._handle_infrastructure_failure(
                e,
                spec,
                vault,
                pair,
                previous,
                original_trade_ids=original_trade_ids,
                phase=self.current_attempt.phase,
            )
        except ExecutionHaltableIssue as e:
            alarm.disarm()
            if self.auto_simulated and is_simulated_infrastructure_failure(e):
                infrastructure_failure = e
                self._handle_infrastructure_failure(
                    e,
                    spec,
                    vault,
                    pair,
                    previous,
                    original_trade_ids=original_trade_ids,
                    phase=self.current_attempt.phase,
                )
            elif not self._record_in_transit_halt(spec, vault):
                self._record_failure(
                    spec,
                    vault,
                    pair,
                    previous,
                    e,
                    original_trade_ids=original_trade_ids,
                    phase=self.current_attempt.phase,
                    halted=True,
                )
        except OutOfBalance as e:
            alarm.disarm()
            self._record_failure(
                spec,
                vault,
                pair,
                previous,
                e,
                detail=f"Insufficient cash: {e}",
                original_trade_ids=original_trade_ids,
                phase=self.current_attempt.phase,
            )
            stop_batch = True
        except Exception as e:
            alarm.disarm()
            if self.auto_simulated and is_simulated_infrastructure_failure(e):
                infrastructure_failure = e
                self._handle_infrastructure_failure(
                    e,
                    spec,
                    vault,
                    pair,
                    previous,
                    original_trade_ids=original_trade_ids,
                    phase=self.current_attempt.phase,
                )
            else:
                self._record_failure(
                    spec,
                    vault,
                    pair,
                    previous,
                    e,
                    original_trade_ids=original_trade_ids,
                    phase=self.current_attempt.phase,
                )
        finally:
            alarm.close()
            if fork_snapshots and infrastructure_failure is None:
                self._restore_simulated_attempt(spec, fork_snapshots)
            self.current_attempt = None

        return stop_batch

    def _prepare_attempt(
        self,
        spec: VaultSpec,
        vault: Any,
        previous: TradingPosition | None,
    ) -> VaultAttempt:
        """Build and preflight the minimal one-vault trading universe."""

        deployment = self.runtime.deployment
        if (
            spec.chain_id != deployment.primary_chain_id.value
            and ChainId(spec.chain_id) not in deployment.satellite_modules
        ):
            raise RuntimeError(
                f"Vault {spec.as_string_id()} is on a satellite chain without a deployed Lagoon module"
            )

        # Unlike perform-test-trade, this command has no strategy file.  Each
        # selected vault is translated into a fresh executable universe here.
        universe = build_vault_test_universe(
            client=self.client,
            vault_universe=self.vault_universe,
            vault_spec=spec,
            reserve_asset=self.runtime.reserve_asset,
            primary_chain_id=deployment.primary_chain_id,
            execution_context=ExecutionContext(mode=ExecutionMode.preflight_check),
        )
        pair = universe.get_pair_by_smart_contract(spec.vault_address)

        # Perform all connectivity, gas and contract checks before constructing
        # any irreversible CCTP burn or vault transaction.
        check_universe_chains_have_rpc(self.runtime.web3config, universe)
        wallet_address = (
            self.runtime.execution_model.tx_builder.get_gas_wallet_address()
        )
        min_gas = getattr(self.runtime.execution_model, "min_balance_threshold", 0)
        check_universe_chains_have_gas(
            self.runtime.web3config,
            universe,
            wallet_address,
            min_gas,
        )
        check_universe_contracts_resolve(
            self.runtime.web3config,
            universe,
            self.runtime.execution_model,
        )

        routing_model = self.runtime.execution_model.create_default_routing_model(
            universe
        )
        pricing_model = GenericPricing(routing_model.pair_configurator)
        valuation_model = GenericValuation(routing_model.pair_configurator)
        routing_state = routing_model.create_routing_state(
            universe,
            self.runtime.execution_model.get_routing_state_details(),
        )

        open_trade_position = get_vault_trade_position(
            self.state,
            spec,
            open_only=True,
            simulated=False,
        )
        bridge_position = self.state.portfolio.get_bridge_position_for_chain(
            spec.chain_id
        )
        return VaultAttempt(
            spec=spec,
            vault=vault,
            universe=universe,
            pair=pair,
            routing_model=routing_model,
            pricing_model=pricing_model,
            valuation_model=valuation_model,
            routing_state=routing_state,
            previous=previous,
            open_trade_position=open_trade_position,
            bridge_position=bridge_position,
        )

    def _process_attempt(
        self,
        attempt: VaultAttempt,
        alarm: SimulatedAttemptAlarm,
    ) -> bool:
        """Choose the next lifecycle action, validate availability and execute it."""

        operation = self._choose_operation(attempt, alarm)
        if operation is None:
            return False

        assert self.current_attempt is not None
        self.current_attempt.operation = operation

        pair = attempt.pair
        spec = attempt.spec

        # The default async simulation verifies requestDeposit only. The explicit
        # Anvil option invokes eth-defi's supported settlement helper instead.
        if (
            self.auto_simulated
            and pair.is_async_vault()
            and operation == "redeem"
            and not self.settle_async_on_anvil
        ):
            self._record_terminal_result(
                attempt,
                alarm,
                result="simulation_unsupported_async",
                detail="Simulation never requests async redemption",
                source_position_id=(
                    attempt.open_trade_position.position_id
                    if attempt.open_trade_position
                    else None
                ),
            )
            return False

        # Deposit/redemption window checks are diagnostic outcomes, not command
        # failures, and automatic mode continues with the next explicit id.
        if (
            operation == "deposit"
            and attempt.pricing_model.can_deposit(native_datetime_utc_now(), pair)
            is False
        ):
            self._record_terminal_result(
                attempt,
                alarm,
                result="deposit_closed",
            )
            return False
        if (
            operation == "redeem"
            and attempt.pricing_model.can_redeem(native_datetime_utc_now(), pair)
            is False
        ):
            alarm.disarm()
            self._append_result(
                attempt.vault, spec, "Redemption is not currently available"
            )
            return False

        if operation == "deposit" and not self._is_resuming_bridge_out(attempt):
            cash = self.state.portfolio.get_default_reserve_position().get_value()
            if cash <= 0:
                alarm.disarm()
                self._append_result(
                    attempt.vault, spec, "No cash remains for another deposit"
                )
                return True

        # A pre-deposit live redemption quote sees zero shares.  The pair-level
        # venue gate is the correct indication for same-run instant redemption.
        if operation == "deposit":
            redemption_available = pair.can_redeem()
        else:
            redemption_available = attempt.pricing_model.can_redeem(
                native_datetime_utc_now(),
                pair,
            )

        if self.auto_simulated:
            self._execute_simulated(attempt, operation, redemption_available, alarm)
        else:
            self._execute_real(attempt, operation, redemption_available)

        self._append_result(attempt.vault, spec)
        return False

    def _choose_operation(
        self,
        attempt: VaultAttempt,
        alarm: SimulatedAttemptAlarm,
    ) -> str | None:
        """Select manual or automatic lifecycle dispatch for a prepared vault.

        Returning ``None`` means the attempt was deliberately skipped and its
        row (or terminal completion) has already been recorded.
        """

        spec = attempt.spec
        bridge_position = attempt.bridge_position
        bridge_conflict = get_bridge_conflict(bridge_position, spec)
        if bridge_conflict:
            return self._skip_attempt(
                attempt,
                alarm,
                bridge_conflict,
            )

        if self.manual_action is not None:
            return self._choose_manual_operation(attempt, alarm)
        return self._choose_automatic_operation(attempt, alarm)

    def _choose_manual_operation(
        self,
        attempt: VaultAttempt,
        alarm: SimulatedAttemptAlarm,
    ) -> str | None:
        """Validate the action explicitly selected in the Textual interface."""

        assert self.manual_action is not None
        operation = self.manual_action.action
        open_position = attempt.open_trade_position

        if operation == "deposit" and open_position is not None:
            return self._skip_attempt(
                attempt,
                alarm,
                "Deposit is already open; select it for redemption",
            )
        if operation == "redeem" and open_position is None:
            return self._skip_attempt(
                attempt,
                alarm,
                "No open deposit is available for redemption",
            )
        if operation == "redeem" and get_vault_test_status(open_position) in {
            "deposit pending",
            "redemption pending",
        }:
            return self._skip_attempt(
                attempt,
                alarm,
                "Pending request must settle before redemption",
            )
        return operation

    def _choose_automatic_operation(
        self,
        attempt: VaultAttempt,
        alarm: SimulatedAttemptAlarm,
    ) -> str | None:
        """Derive the next action from the latest persisted lifecycle phase."""

        spec = attempt.spec
        previous = attempt.previous
        open_position = attempt.open_trade_position
        bridge_position = attempt.bridge_position
        previous_status = get_vault_test_status(previous)
        previous_attempt = (
            previous.other_data.get("vault_test_attempt", {}) if previous else {}
        )

        # Automatic mode never resubmits a pending vault request.  Startup
        # settlement resolution will advance it on a later invocation.
        if open_position is not None and get_vault_test_status(open_position) in {
            "deposit pending",
            "redemption pending",
        }:
            return self._skip_attempt(
                attempt,
                alarm,
                "Pending request is not retried automatically",
            )
        if previous_status in {"deposit pending", "redemption pending"}:
            return self._skip_attempt(
                attempt,
                alarm,
                "Pending request is not retried automatically",
            )

        phase = previous_attempt.get("phase")
        if phase == "bridge_out_pending":
            return "deposit"
        if phase == "bridge_back_pending":
            if bridge_position is not None:
                return "redeem"

            # No bridge position means the already-settled bridge-back completed
            # the lifecycle between command invocations.
            alarm.disarm()
            stamp_position_vault_test_attempt(
                previous,
                spec,
                simulated=False,
                phase="complete",
                result="success",
                attempt_id=self.current_attempt.attempt_id
                if self.current_attempt
                else None,
                operation=self.current_attempt.operation
                if self.current_attempt
                else None,
                provenance=self.current_attempt.provenance
                if self.current_attempt
                else None,
            )
            self.store.sync(self.state)
            self._append_result(attempt.vault, spec)
            return None
        if open_position is not None:
            return "redeem"
        if previous_attempt.get("result") and not self.rerun:
            return self._skip_attempt(
                attempt,
                alarm,
                "Existing terminal result; use --rerun to retest",
            )
        if phase == "redemption_requested" and bridge_position is not None:
            return "redeem"
        if previous is None or self.rerun:
            return "deposit"

        return self._skip_attempt(
            attempt,
            alarm,
            "Existing terminal result; use --rerun to retest",
        )

    def _skip_attempt(
        self,
        attempt: VaultAttempt,
        alarm: SimulatedAttemptAlarm,
        detail: str,
    ) -> None:
        """Disarm simulation timeout and append an explanatory skipped row."""

        alarm.disarm()
        self._append_result(attempt.vault, attempt.spec, detail)
        return None

    def _execute_real(
        self,
        attempt: VaultAttempt,
        operation: str,
        redemption_available: bool,
    ) -> None:
        """Execute and persist one action against the deployed Lagoon executor."""

        revalue_state(
            self.state,
            native_datetime_utc_now(),
            attempt.valuation_model,
        )
        perform_test_trade(
            web3=self.runtime.web3config.get_default(),
            execution_model=self.runtime.execution_model,
            pricing_model=attempt.pricing_model,
            sync_model=self.runtime.sync_model,
            state=self.state,
            universe=attempt.universe,
            routing_model=attempt.routing_model,
            routing_state=attempt.routing_state,
            max_slippage=self.max_slippage,
            amount=self.amount,
            pair=attempt.pair,
            buy_only=should_leave_deposit_open(
                operation=operation,
                is_async=attempt.pair.is_async_vault(),
                redemption_available=redemption_available,
                manual=self.manual_action is not None,
            ),
            close_only=operation == "redeem",
            web3config=self.runtime.web3config,
            test_short=False,
        )

        assert self.current_attempt is not None
        self.current_attempt.phase = "state_inference"
        target_position = get_vault_trade_position(
            self.state,
            attempt.spec,
            simulated=False,
            trade_ids={
                trade.trade_id
                for trade in self.state.portfolio.get_all_trades()
                if trade.trade_id not in self.current_attempt.original_trade_ids
            },
        )
        if target_position is None:
            raise RuntimeError(
                "Test trade completed without creating a target-vault position or trade"
            )
        self._stamp_real_lifecycle(attempt, operation)
        self.store.sync(self.state)

    def _execute_simulated(
        self,
        attempt: VaultAttempt,
        operation: str,
        redemption_available: bool,
        alarm: SimulatedAttemptAlarm,
    ) -> None:
        """Execute on a state copy and merge only closed diagnostic positions."""

        original_position_ids = {
            position.position_id
            for position in self.state.portfolio.get_all_positions()
        }
        original_trade_ids = {
            trade.trade_id for trade in self.state.portfolio.get_all_trades()
        }

        # RPC effects are reverted after the attempt.  A deep-copied state keeps
        # simulated balances, valuations and settlement changes equally isolated.
        fork_state = deepcopy(self.state)
        is_async = attempt.pair.is_async_vault()
        complete_async_lifecycle = is_async and self.settle_async_on_anvil
        try:
            perform_test_trade(
                web3=self.runtime.web3config.get_default(),
                execution_model=self.runtime.execution_model,
                pricing_model=attempt.pricing_model,
                sync_model=self.runtime.sync_model,
                state=fork_state,
                universe=attempt.universe,
                routing_model=attempt.routing_model,
                routing_state=attempt.routing_state,
                max_slippage=self.max_slippage,
                amount=self.amount,
                pair=attempt.pair,
                buy_only=should_leave_deposit_open(
                    operation=operation,
                    is_async=is_async and not complete_async_lifecycle,
                    redemption_available=redemption_available,
                    manual=False,
                ),
                close_only=operation == "redeem",
                web3config=self.runtime.web3config,
                trade_flags={TradeFlag.simulated},
                test_short=False,
                force_async_settlement_on_anvil=complete_async_lifecycle,
            )
        except Exception as error:
            # The outer handler records the result on the persistent state. Keep
            # the disposable fork state attached long enough to extract its
            # transaction-level revert trace before the snapshot is restored.
            try:
                error.vault_test_failure_state = fork_state
            except (AttributeError, TypeError):
                # A few third-party exception classes disallow arbitrary
                # attributes. Preserve their original failure rather than
                # replacing it with a diagnostic bookkeeping error.
                pass
            raise

        # From here onwards only in-memory/state-store work remains.  Do not let
        # the external-operation timeout interrupt the persistent JSON write.
        alarm.disarm()
        created_position_ids = {
            position.position_id
            for position in fork_state.portfolio.get_all_positions()
            if position.position_id not in original_position_ids
        }
        created_target_position_ids = {
            position.position_id
            for position in fork_state.portfolio.get_all_positions()
            if position.position_id in created_position_ids
            and position.pair.pool_address.lower() == attempt.spec.vault_address.lower()
        }
        close_simulated_positions(
            fork_state,
            vault_spec=attempt.spec,
            position_ids=created_position_ids,
            result=(
                "simulation_unsupported_async"
                if is_async and not complete_async_lifecycle
                else "redemption_unavailable"
                if not redemption_available
                else None
            ),
            phase=(
                "complete"
                if not is_async or complete_async_lifecycle
                else "deposit_requested"
            ),
            attempt_id=self.current_attempt.attempt_id
            if self.current_attempt
            else None,
            operation=operation,
            provenance=self.current_attempt.provenance
            if self.current_attempt
            else None,
        )
        merge_simulated_attempt(
            source_state=fork_state,
            target_state=self.state,
            original_position_ids=original_position_ids,
            original_trade_ids=original_trade_ids,
        )

        # Adapter flows that return without creating a target position still
        # need an authoritative terminal row in the dedicated state.
        if not created_target_position_ids:
            record_attempt_result(
                self.state,
                attempt.pair,
                attempt.spec,
                simulated=True,
                result="state_inference_failed",
                detail=(
                    "Test trade returned without creating a target-vault position or trade"
                ),
                source_position_id=(
                    attempt.previous.position_id if attempt.previous else None
                ),
                attempt_id=self.current_attempt.attempt_id
                if self.current_attempt
                else None,
                operation=operation,
                provenance=self.current_attempt.provenance
                if self.current_attempt
                else None,
            )
        self.store.sync(self.state)

    def _stamp_real_lifecycle(self, attempt: VaultAttempt, operation: str) -> None:
        """Persist the resumable phase reached by a real test trade."""

        target_position = get_vault_trade_position(
            self.state,
            attempt.spec,
            simulated=False,
            trade_ids={
                trade.trade_id
                for trade in self.state.portfolio.get_all_trades()
                if trade.trade_id not in self.current_attempt.original_trade_ids
            },
        )
        bridge_position = self.state.portfolio.get_bridge_position_for_chain(
            attempt.spec.chain_id
        )
        in_transit_trade = self._find_in_transit_trade(bridge_position)

        if in_transit_trade is not None:
            phase = (
                "bridge_back_pending"
                if in_transit_trade.is_sell()
                else "bridge_out_pending"
            )
            stamp_position_vault_test_attempt(
                bridge_position,
                attempt.spec,
                simulated=False,
                phase=phase,
                attempt_id=self.current_attempt.attempt_id
                if self.current_attempt
                else None,
                operation=operation,
                provenance=self.current_attempt.provenance
                if self.current_attempt
                else None,
            )
            if phase == "bridge_back_pending" and target_position is not None:
                stamp_position_vault_test_attempt(
                    target_position,
                    attempt.spec,
                    simulated=False,
                    phase=phase,
                    attempt_id=self.current_attempt.attempt_id
                    if self.current_attempt
                    else None,
                    operation=operation,
                    provenance=self.current_attempt.provenance
                    if self.current_attempt
                    else None,
                )
            return

        if target_position is not None:
            phase = (
                "deposit_requested"
                if operation == "deposit"
                else "redemption_requested"
            )
            stamp_position_vault_test_attempt(
                target_position,
                attempt.spec,
                simulated=False,
                phase=phase,
                attempt_id=self.current_attempt.attempt_id
                if self.current_attempt
                else None,
                operation=operation,
                provenance=self.current_attempt.provenance
                if self.current_attempt
                else None,
            )

    def _record_in_transit_halt(self, spec: VaultSpec, vault: Any) -> bool:
        """Record a resumable CCTP halt instead of treating it as a failure."""

        bridge_position = self.state.portfolio.get_bridge_position_for_chain(
            spec.chain_id
        )
        in_transit_trade = self._find_in_transit_trade(bridge_position)
        if in_transit_trade is None:
            return False

        phase = (
            "bridge_back_pending"
            if in_transit_trade.is_sell()
            else "bridge_out_pending"
        )
        stamp_position_vault_test_attempt(
            bridge_position,
            spec,
            simulated=False,
            phase=phase,
            attempt_id=self.current_attempt.attempt_id
            if self.current_attempt
            else None,
            operation=self.current_attempt.operation if self.current_attempt else None,
            provenance=self.current_attempt.provenance
            if self.current_attempt
            else None,
        )
        if phase == "bridge_back_pending":
            target_position = get_vault_trade_position(
                self.state,
                spec,
                simulated=False,
            )
            if target_position is not None:
                stamp_position_vault_test_attempt(
                    target_position,
                    spec,
                    simulated=False,
                    phase=phase,
                    attempt_id=self.current_attempt.attempt_id
                    if self.current_attempt
                    else None,
                    operation=self.current_attempt.operation
                    if self.current_attempt
                    else None,
                    provenance=self.current_attempt.provenance
                    if self.current_attempt
                    else None,
                )

        self.store.sync(self.state)
        self._append_result(vault, spec, "CCTP transfer is in transit")
        return True

    def _handle_infrastructure_failure(
        self,
        error: BaseException,
        spec: VaultSpec,
        vault: Any,
        pair: TradingPairIdentifier | None,
        previous: TradingPosition | None,
        *,
        original_trade_ids: set[int],
        phase: str,
    ) -> None:
        """Queue one clean rerun or record a repeated Anvil failure."""

        self.restart_requested = error
        if queue_simulated_infrastructure_retry(
            spec,
            self.pending_specs,
            self.infrastructure_restart_counts,
        ):
            logger.warning(
                "Vault simulation infrastructure failed for %s; rerunning with a new Anvil generation: %s",
                spec.as_string_id(),
                error,
            )
            return

        logger.error(
            "Vault simulation infrastructure failed again for %s after Anvil replacement",
            spec.as_string_id(),
        )
        diagnostic_pair = pair or create_vault_test_diagnostic_pair(
            spec,
            self.runtime.reserve_asset,
            vault,
        )
        detail = redact_vault_test_error_text(
            f"Anvil infrastructure failed after replacement: {error}"
        )
        error_data = capture_vault_test_error(
            error,
            state=getattr(error, "vault_test_failure_state", self.state),
            original_trade_ids=original_trade_ids,
            web3config=self.runtime.web3config,
            phase=phase,
            capture_chain_blocks=False,
        )
        record_attempt_result(
            self.state,
            diagnostic_pair,
            spec,
            simulated=True,
            result="infrastructure_failed",
            detail=detail,
            error=error_data,
            source_position_id=previous.position_id if previous else None,
            attempt_id=self.current_attempt.attempt_id
            if self.current_attempt
            else None,
            operation=self.current_attempt.operation if self.current_attempt else None,
            provenance=self.current_attempt.provenance
            if self.current_attempt
            else None,
        )
        self.store.sync(self.state)
        self._append_result(vault, spec, detail)

    def _record_failure(
        self,
        spec: VaultSpec,
        vault: Any,
        pair: TradingPairIdentifier | None,
        previous: TradingPosition | None,
        error: Exception,
        *,
        detail: str | None = None,
        original_trade_ids: set[int],
        phase: str,
        halted: bool = False,
    ) -> None:
        """Persist one protocol, adapter or balance failure and its output row."""

        if halted:
            logger.exception("Vault test halted for %s", spec.as_string_id())
        else:
            logger.exception("Vault test failed for %s", spec.as_string_id())

        diagnostic_pair = pair or create_vault_test_diagnostic_pair(
            spec,
            self.runtime.reserve_asset,
            vault,
        )
        detail = redact_vault_test_error_text(detail or error)
        error_state = getattr(error, "vault_test_failure_state", self.state)
        error_data = capture_vault_test_error(
            error,
            state=error_state,
            original_trade_ids=original_trade_ids,
            web3config=self.runtime.web3config,
            phase=phase,
        )
        result = classify_vault_test_failure(
            phase=phase,
            error_data=error_data,
        )
        source_position_id = self._attach_failure_to_attempt_position(
            spec=spec,
            error_state=error_state,
            result=result,
            detail=detail,
            error_data=error_data,
            previous=previous,
        )
        if source_position_id is not None:
            self.store.sync(self.state)
            self._append_result(vault, spec, detail)
            return
        record_attempt_result(
            self.state,
            diagnostic_pair,
            spec,
            simulated=self.auto_simulated,
            result=result,
            detail=detail,
            error=error_data,
            source_position_id=previous.position_id if previous else None,
            attempt_id=self.current_attempt.attempt_id
            if self.current_attempt
            else None,
            operation=self.current_attempt.operation if self.current_attempt else None,
            provenance=self.current_attempt.provenance
            if self.current_attempt
            else None,
        )
        self.store.sync(self.state)
        self._append_result(vault, spec, detail)

    def _attach_failure_to_attempt_position(
        self,
        *,
        spec: VaultSpec,
        error_state: State,
        result: str,
        detail: str,
        error_data: dict,
        previous: TradingPosition | None,
    ) -> int | None:
        """Attach failure evidence to the actual target position when present."""

        assert self.current_attempt is not None
        if self.auto_simulated:
            if error_state is self.state:
                # A simulated preflight failure has no disposable fork position.
                # Never relabel a real or historical simulated position as this
                # attempt; create a fresh diagnostic result instead.
                return None
            position_ids = {
                position.position_id
                for position in error_state.portfolio.get_all_positions()
                if position.position_id
                not in self.current_attempt.original_position_ids
            }
            close_simulated_positions(
                error_state,
                vault_spec=spec,
                position_ids=position_ids,
                result=result,
                phase=self.current_attempt.phase,
                attempt_id=self.current_attempt.attempt_id,
                operation=self.current_attempt.operation,
                provenance=self.current_attempt.provenance,
            )
            merge_simulated_attempt(
                source_state=error_state,
                target_state=self.state,
                original_position_ids=self.current_attempt.original_position_ids,
                original_trade_ids=self.current_attempt.original_trade_ids,
            )
            target_position = get_vault_trade_position(
                self.state,
                spec,
                simulated=True,
                position_ids=position_ids,
            )
        else:
            target_position = get_vault_trade_position(
                self.state,
                spec,
                simulated=False,
                trade_ids={
                    trade.trade_id
                    for trade in self.state.portfolio.get_all_trades()
                    if trade.trade_id not in self.current_attempt.original_trade_ids
                },
            )

        if target_position is None:
            return None

        stamp_position_vault_test_attempt(
            target_position,
            spec,
            simulated=self.auto_simulated,
            phase=self.current_attempt.phase,
            result=result,
            detail=detail,
            attempt_id=self.current_attempt.attempt_id,
            operation=self.current_attempt.operation,
            provenance=self.current_attempt.provenance,
        )
        target_position.other_data["vault_test_attempt"]["error"] = error_data
        if previous is not None:
            target_position.other_data["vault_test_attempt"]["previous_position_id"] = (
                previous.position_id
            )
        return target_position.position_id

    def _record_terminal_result(
        self,
        attempt: VaultAttempt,
        alarm: SimulatedAttemptAlarm,
        *,
        result: str,
        detail: str | None = None,
        source_position_id: int | None = None,
    ) -> None:
        """Persist a non-exception terminal diagnostic for one prepared vault."""

        alarm.disarm()
        record_attempt_result(
            self.state,
            attempt.pair,
            attempt.spec,
            simulated=self.auto_simulated,
            result=result,
            detail=detail,
            source_position_id=source_position_id,
            attempt_id=self.current_attempt.attempt_id
            if self.current_attempt
            else None,
            operation=self.current_attempt.operation if self.current_attempt else None,
            provenance=self.current_attempt.provenance
            if self.current_attempt
            else None,
        )
        self.store.sync(self.state)
        self._append_result(attempt.vault, attempt.spec, detail)

    def _record_missing_vault(self, spec: VaultSpec) -> None:
        """Persist a requested vault id absent from the downloaded universe."""

        detail = "Vault not in downloaded universe"
        pair = create_vault_test_diagnostic_pair(spec, self.runtime.reserve_asset)
        record_attempt_result(
            self.state,
            pair,
            spec,
            simulated=self.auto_simulated,
            result="metadata_failed",
            detail=detail,
            attempt_id=uuid.uuid4().hex,
            provenance=self.runtime.get_provenance(),
        )
        self.store.sync(self.state)
        self._append_result(None, spec, detail)

    def _restore_simulated_attempt(
        self,
        spec: VaultSpec,
        fork_snapshots: dict,
    ) -> None:
        """Revert fork mutations and request replacement if restoration fails."""

        try:
            restore_simulated_snapshots(self.runtime.web3config, fork_snapshots)

            # The next attempt begins on the source chain.  Generic destination
            # routing creates and synchronises its own chain-specific wallet.
            hot_wallet = self.runtime.execution_model.tx_builder.hot_wallet
            hot_wallet.current_nonce = None
            hot_wallet.sync_nonce(self.runtime.web3config.get_default())
        except BaseException as cleanup_error:
            if self.auto_simulated and is_simulated_infrastructure_failure(
                cleanup_error
            ):
                self.restart_requested = cleanup_error
                logger.warning(
                    "Discarding Anvil generation after snapshot restoration failed for %s: %s",
                    spec.as_string_id(),
                    cleanup_error,
                )
            else:
                raise

    def _is_resuming_bridge_out(self, attempt: VaultAttempt) -> bool:
        """Return true when a settled CCTP bridge should fund the deposit."""

        if attempt.previous is None or attempt.bridge_position is None:
            return False
        phase = attempt.previous.other_data.get("vault_test_attempt", {}).get("phase")
        return (
            phase == "bridge_out_pending"
            and attempt.bridge_position.get_available_bridge_capital() > 0
        )

    @staticmethod
    def _find_in_transit_trade(
        bridge_position: TradingPosition | None,
    ):
        """Return the first unfinished CCTP trade on a bridge position."""

        if bridge_position is None:
            return None
        return next(
            (
                trade
                for trade in bridge_position.trades.values()
                if trade.get_status() == TradeStatus.cctp_in_transit
            ),
            None,
        )

    def _append_result(
        self,
        vault: Any,
        spec: VaultSpec,
        detail: str | None = None,
    ) -> None:
        """Append one compact row using the latest position as authority."""

        position = get_latest_vault_position(self.state, spec)
        attempt = position.other_data.get("vault_test_attempt", {}) if position else {}
        detail = detail or attempt.get("detail")
        if detail:
            detail = " ".join(str(detail).split())
            if len(detail) > 160:
                detail = detail[:157] + "..."

        self.rows.append(
            {
                "vault id": spec.as_string_id(),
                "vault": getattr(vault, "name", spec.vault_address),
                "chain": getattr(
                    getattr(vault, "chain_id", None),
                    "get_name",
                    lambda: str(spec.chain_id),
                )(),
                "protocol": getattr(vault, "protocol_name", "unknown"),
                "mode": self.mode,
                "status": get_vault_test_status(position),
                "operation": attempt.get("operation"),
                "phase": attempt.get("phase"),
                "result": attempt.get("result"),
                "attempt": attempt.get("attempt_id"),
                "position": position.position_id if position else None,
                "detail": detail,
            }
        )
