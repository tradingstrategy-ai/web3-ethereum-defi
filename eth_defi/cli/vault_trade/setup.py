"""Bootstrap helpers for the standalone vault test command.

The Typer command should only translate command-line options into application
objects.  This module owns the heavier setup work: downloading vault metadata,
creating either the real Lagoon executor or an ephemeral simulation executor,
loading its dedicated state, and resolving work left pending by an earlier
real invocation.
"""

import datetime
import logging
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from eth_defi.compat import native_datetime_utc_now
from eth_defi.vault.base import VaultSpec
from tradingstrategy.client import Client

from tradeexecutor.cli.bootstrap import (
    create_execution_and_sync_model,
    create_state_store,
    create_web3_config,
    prepare_cache_and_token_cache,
    resolve_deployment_file,
)
from eth_defi.cli.vault_trade.core import (
    LagoonDeployment,
    SIMULATED_LAGOON_PRIVATE_KEY,
    filter_rpc_kwargs_for_vault_specs,
    load_lagoon_deployment,
)
from eth_defi.cli.vault_trade.simulation import (
    SimulatedVaultRuntime,
    start_simulated_vault_runtime_with_replacement,
)
from eth_defi.cli.vault_trade.tui import (
    VaultChoice,
    VaultTestAction,
    display_vault_test_trade_ui,
)
from tradeexecutor.ethereum.cctp.retry import check_and_retry_cctp_in_transit
from tradeexecutor.ethereum.token import translate_token_details
from tradeexecutor.ethereum.web3config import Web3Config
from tradeexecutor.state.identifier import AssetIdentifier
from tradeexecutor.state.state import State
from tradeexecutor.strategy.execution_model import AssetManagementMode, ExecutionModel
from tradeexecutor.strategy.sync_model import SyncModel
from tradeexecutor.strategy.trading_strategy_universe import (
    load_vault_universe_with_metadata,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VaultTestData:
    """Downloaded data and caches shared by all vault attempts in one command."""

    #: Live Trading Strategy client used for per-vault partial-universe loads.
    client: Client

    #: Complete metadata universe used by the TUI and vault-id lookup.
    vault_universe: Any

    #: Persistent token metadata cache passed to execution-model construction.
    token_cache: Any


@dataclass(slots=True)
class VaultTestRuntime:
    """Mutable blockchain runtime used by a sequential vault-test batch.

    Real mode keeps these objects for the command lifetime.  Simulated mode may
    replace all of them together after an infrastructure failure; keeping them
    in one owner prevents callers from retaining a Web3 or execution model from
    an Anvil generation that has already been torn down.
    """

    web3config: Web3Config
    deployment: LagoonDeployment
    execution_model: ExecutionModel
    sync_model: SyncModel
    reserve_asset: AssetIdentifier
    simulated_runtime: SimulatedVaultRuntime | None = None
    simulated_runtime_kwargs: dict | None = None
    provenance: dict = field(default_factory=dict)

    @property
    def is_simulated(self) -> bool:
        """Return true when this runtime owns disposable Anvil forks."""

        return self.simulated_runtime is not None

    def replace_simulation(self, failure: BaseException) -> None:
        """Replace the complete multichain Anvil generation after a failure.

        The old generation is always closed before the replacement starts.  A
        reserve-asset assertion protects the already-loaded state from being
        reused with a deployment funded in a different denomination token.
        """

        assert self.simulated_runtime is not None
        assert self.simulated_runtime_kwargs is not None

        failed_generation = self.simulated_runtime.generation
        logger.warning(
            "Replacing simulated vault runtime generation %d after infrastructure failure: %s",
            failed_generation,
            failure,
        )

        # No object belonging to a failed Anvil generation may survive into the
        # next vault attempt.
        self.simulated_runtime.close()
        replacement = start_simulated_vault_runtime_with_replacement(
            generation=failed_generation + 1,
            **self.simulated_runtime_kwargs,
        )
        assert replacement.reserve_asset == self.reserve_asset, (
            f"Simulation reserve changed across Anvil generations: "
            f"{self.reserve_asset} != {replacement.reserve_asset}"
        )

        self.simulated_runtime = replacement
        self.web3config = replacement.web3config
        self.deployment = replacement.deployment
        self.execution_model = replacement.execution_model
        self.sync_model = replacement.sync_model

    def close(self) -> None:
        """Release RPC connections without masking an earlier command failure."""

        if self.simulated_runtime is not None:
            self.simulated_runtime.close()
            return

        try:
            self.web3config.close()
        except Exception:
            logger.exception("One or more Web3 connections did not close cleanly")

    def get_provenance(self) -> dict:
        """Return JSON-safe immutable inputs needed to reproduce an attempt."""

        provenance = dict(self.provenance or {})
        deployment = {
            "primary_chain_id": self.deployment.primary_chain_id.value,
            "vault_address": self.deployment.vault_address,
            "module_address": self.deployment.module_address,
            "satellite_modules": {
                str(chain_id.value): address
                for chain_id, address in self.deployment.satellite_modules.items()
            },
        }
        provenance["lagoon_deployment"] = deployment
        if self.simulated_runtime is not None:
            provenance["anvil_generation"] = self.simulated_runtime.generation
            provenance["fork_blocks"] = self.simulated_runtime.fork_blocks
        return provenance


def _read_git_commit(path: Path) -> str | None:
    """Read a local Git revision without making provenance mandatory."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip() or None


def _create_vault_test_provenance(
    *,
    mode: str,
    web3config: Web3Config,
    amount: Decimal,
    max_slippage: float,
) -> dict:
    """Capture source revisions and initial chain heights for a tester run."""

    repository_root = Path(__file__).resolve().parents[2]
    eth_defi_root = repository_root / "deps" / "web3-ethereum-defi"
    initial_chain_blocks = {}
    for chain_id, web3 in web3config.connections.items():
        try:
            initial_chain_blocks[str(chain_id.value)] = int(web3.eth.block_number)
        except Exception:
            initial_chain_blocks[str(chain_id.value)] = {
                "error": "Could not capture initial chain block",
            }
    return {
        "schema_version": 2,
        "execution_mode": mode,
        "amount": str(amount),
        "max_slippage": max_slippage,
        "run_started_at": native_datetime_utc_now().isoformat(),
        "trade_executor_commit": _read_git_commit(repository_root),
        "eth_defi_commit": _read_git_commit(eth_defi_root),
        "initial_chain_blocks": initial_chain_blocks,
    }


def load_vault_test_data(
    *,
    executor_id: str,
    cache_path: Path | None,
    trading_strategy_api_key: str | None,
    unit_testing: bool,
) -> VaultTestData:
    """Download the complete vault universe without loading a strategy module.

    :raise RuntimeError:
        If no Trading Strategy API key was supplied.  Vault discovery is an
        essential input even when individual adapters later fail to construct.
    """

    if not trading_strategy_api_key:
        raise RuntimeError(
            "TRADING_STRATEGY_API_KEY is required to download the vault universe"
        )

    # Use the normal executor cache layout so repeated manual invocations do not
    # redownload vault and token metadata unnecessarily.
    resolved_cache_path, token_cache = prepare_cache_and_token_cache(
        executor_id,
        cache_path,
        unit_testing=unit_testing,
    )
    client = Client.create_live_client(
        trading_strategy_api_key,
        cache_path=resolved_cache_path,
        settings_path=None,
    )
    vault_universe = load_vault_universe_with_metadata(client)
    return VaultTestData(client, vault_universe, token_cache)


def create_vault_test_runtime(
    *,
    executor_id: str,
    state_file: Path,
    rpc_kwargs: dict,
    private_key: str | None,
    asset_management_mode: AssetManagementMode,
    min_gas_balance: float | None,
    max_slippage: float,
    confirmation_block_count: int,
    confirmation_timeout: int,
    unit_testing: bool,
    amount: Decimal,
    vault_specs: list[VaultSpec],
    data: VaultTestData,
    auto_simulated: bool,
) -> VaultTestRuntime:
    """Dispatch runtime construction based on the requested execution mode."""

    if auto_simulated:
        return _create_simulated_vault_test_runtime(
            executor_id=executor_id,
            rpc_kwargs=rpc_kwargs,
            private_key=private_key,
            asset_management_mode=asset_management_mode,
            min_gas_balance=min_gas_balance,
            max_slippage=max_slippage,
            confirmation_block_count=confirmation_block_count,
            confirmation_timeout=confirmation_timeout,
            unit_testing=unit_testing,
            amount=amount,
            vault_specs=vault_specs,
            data=data,
        )
    return _create_real_vault_test_runtime(
        executor_id=executor_id,
        state_file=state_file,
        rpc_kwargs=rpc_kwargs,
        private_key=private_key,
        asset_management_mode=asset_management_mode,
        min_gas_balance=min_gas_balance,
        max_slippage=max_slippage,
        confirmation_block_count=confirmation_block_count,
        confirmation_timeout=confirmation_timeout,
        unit_testing=unit_testing,
        amount=amount,
        data=data,
    )


def _create_simulated_vault_test_runtime(
    *,
    executor_id: str,
    rpc_kwargs: dict,
    private_key: str | None,
    asset_management_mode: AssetManagementMode,
    min_gas_balance: float | None,
    max_slippage: float,
    confirmation_block_count: int,
    confirmation_timeout: int,
    unit_testing: bool,
    amount: Decimal,
    vault_specs: list[VaultSpec],
    data: VaultTestData,
) -> VaultTestRuntime:
    """Create the first disposable multichain Anvil generation."""

    # Fork only explicitly requested chains.  This shortens startup and avoids
    # unrelated RPC failures aborting a diagnostic batch.
    filtered_rpc_kwargs = filter_rpc_kwargs_for_vault_specs(rpc_kwargs, vault_specs)
    simulated_runtime_kwargs = {
        "executor_id": executor_id,
        "rpc_kwargs": filtered_rpc_kwargs,
        "unit_testing": unit_testing,
        "vault_specs": vault_specs,
        "vault_universe": data.vault_universe,
        "private_key": private_key or SIMULATED_LAGOON_PRIVATE_KEY,
        "amount": amount,
        "asset_management_mode": asset_management_mode,
        "confirmation_timeout": confirmation_timeout,
        "confirmation_block_count": confirmation_block_count,
        "min_gas_balance": min_gas_balance,
        "max_slippage": max_slippage,
        "token_cache": data.token_cache,
    }
    simulated_runtime = start_simulated_vault_runtime_with_replacement(
        generation=1,
        **simulated_runtime_kwargs,
    )
    return VaultTestRuntime(
        web3config=simulated_runtime.web3config,
        deployment=simulated_runtime.deployment,
        execution_model=simulated_runtime.execution_model,
        sync_model=simulated_runtime.sync_model,
        reserve_asset=simulated_runtime.reserve_asset,
        simulated_runtime=simulated_runtime,
        simulated_runtime_kwargs=simulated_runtime_kwargs,
        provenance=_create_vault_test_provenance(
            mode="auto_simulated",
            web3config=simulated_runtime.web3config,
            amount=amount,
            max_slippage=max_slippage,
        ),
    )


def _create_real_vault_test_runtime(
    *,
    executor_id: str,
    state_file: Path,
    rpc_kwargs: dict,
    private_key: str | None,
    asset_management_mode: AssetManagementMode,
    min_gas_balance: float | None,
    max_slippage: float,
    confirmation_block_count: int,
    confirmation_timeout: int,
    unit_testing: bool,
    amount: Decimal,
    data: VaultTestData,
) -> VaultTestRuntime:
    """Create execution objects from the mandatory state-sibling deployment."""

    # Real and manual modes must use exactly the topology emitted by
    # lagoon-deploy-vault next to this executor's state file.
    deployment_file = resolve_deployment_file(executor_id, state_file)
    deployment = load_lagoon_deployment(deployment_file)
    web3config = create_web3_config(
        **rpc_kwargs,
        unit_testing=unit_testing,
        simulate=False,
    )
    if not web3config.has_any_connection():
        raise RuntimeError("vault-test-trade requires JSON-RPC connections")
    web3config.set_default_chain(deployment.primary_chain_id)
    web3config.check_default_chain_id()

    try:
        execution_model, sync_model, _, _ = create_execution_and_sync_model(
            asset_management_mode=asset_management_mode,
            private_key=private_key,
            web3config=web3config,
            confirmation_timeout=datetime.timedelta(seconds=confirmation_timeout),
            confirmation_block_count=confirmation_block_count,
            min_gas_balance=min_gas_balance,
            max_slippage=max_slippage,
            vault_address=deployment.vault_address,
            vault_adapter_address=deployment.module_address,
            vault_payment_forwarder_address=None,
            token_cache=data.token_cache,
            deployment_file=deployment_file,
        )
    except BaseException:
        # Do not leak providers if model construction rejects the deployment
        # artefact or signing configuration.
        try:
            web3config.close()
        except Exception:
            logger.exception("Could not cleanly close a failed real vault-test setup")
        raise

    reserve_asset = translate_token_details(sync_model.vault.denomination_token)
    return VaultTestRuntime(
        web3config=web3config,
        deployment=deployment,
        execution_model=execution_model,
        sync_model=sync_model,
        reserve_asset=reserve_asset,
        provenance=_create_vault_test_provenance(
            mode="real",
            web3config=web3config,
            amount=amount,
            max_slippage=max_slippage,
        ),
    )


def load_vault_test_state(
    *,
    state_file: Path,
    state_name: str,
    runtime: VaultTestRuntime,
) -> tuple[State, Any]:
    """Load the dedicated state or initialise it from the Lagoon reserve."""

    store = create_state_store(state_file)
    if not store.is_pristine():
        state = store.load()
        state.other_data.save(
            state.other_data.get_latest_stored_cycle(),
            "vault_test_run",
            runtime.get_provenance(),
        )
        store.sync(state)
        return state, store

    # A pristine state must first learn its reserve asset and current Lagoon
    # treasury balance before any test position can be created.
    state = store.create(state_name)
    runtime.sync_model.sync_initial(
        state,
        reserve_asset=runtime.reserve_asset,
        reserve_token_price=1.0,
    )
    runtime.sync_model.sync_treasury(
        native_datetime_utc_now(),
        state,
        [runtime.reserve_asset],
    )
    state.other_data.save(0, "vault_test_run", runtime.get_provenance())
    store.sync(state)
    return state, store


def resolve_pending_real_actions(
    *,
    runtime: VaultTestRuntime,
    state: State,
    store: Any,
) -> None:
    """Advance settled CCTP and async-vault work from earlier invocations.

    Retry helpers may append signed transactions even when the external request
    is not fully resolved.  The state is therefore always persisted afterwards
    so a later run cannot reuse the transaction nonce.
    """

    runtime.execution_model.initialize()
    resolved_bridges = check_and_retry_cctp_in_transit(
        state=state,
        execution_model=runtime.execution_model,
        web3config=runtime.web3config,
    )
    resolved_vaults = runtime.execution_model.resolve_pending_vault_settlements(
        state=state,
        ts=native_datetime_utc_now(),
    )
    if resolved_bridges or resolved_vaults:
        logger.info(
            "Resolved %d CCTP transfer(s) and %d vault settlement(s) before vault testing",
            len(resolved_bridges),
            len(resolved_vaults),
        )
    store.sync(state)


def choose_manual_vault_action(
    *,
    vault_universe: Any,
    state: State,
) -> VaultTestAction | None:
    """Show the Textual interface and return one operator-selected action."""

    # The TUI accepts a small display model instead of depending on the full
    # Trading Strategy vault-universe object.
    choices = [
        VaultChoice(
            vault_spec=VaultSpec(vault.chain_id.value, vault.vault_address),
            name=vault.name or vault.vault_address,
            chain=vault.chain_id.get_name(),
            protocol=vault.protocol_name or "unknown",
        )
        for vault in vault_universe.iterate_vaults()
    ]
    choices.sort(
        key=lambda choice: (choice.name.lower(), choice.vault_spec.as_string_id())
    )
    return display_vault_test_trade_ui(choices=choices, state=state)
