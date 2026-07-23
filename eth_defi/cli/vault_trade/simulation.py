"""Disposable Anvil infrastructure for ``vault-test-trade --auto-simulated``."""

import datetime
import json
import logging
import tempfile
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.provider.anvil import (
    ArchiveNodeRequired,
    RPCRequestError,
    make_anvil_custom_rpc_request,
)
from eth_defi.vault.base import VaultSpec
from requests.exceptions import RequestException
from tradingstrategy.chain import ChainId
from web3.exceptions import (
    CannotHandleRequest,
    MultipleFailedRequests,
    ProviderConnectionError,
    RequestTimedOut,
)

from tradeexecutor.cli.bootstrap import (
    create_execution_and_sync_model,
    create_web3_config,
)
from eth_defi.cli.vault_trade.core import deploy_simulated_lagoon_multichain
from tradeexecutor.ethereum.token import translate_token_details
from tradeexecutor.strategy.execution_model import AssetManagementMode

logger = logging.getLogger(__name__)


SIMULATED_VAULT_ATTEMPT_TIMEOUT = 60
SIMULATED_VAULT_INFRASTRUCTURE_RESTARTS = 1


SIMULATED_INFRASTRUCTURE_EXCEPTIONS = (
    ArchiveNodeRequired,
    BrokenPipeError,
    CannotHandleRequest,
    ConnectionRefusedError,
    ConnectionResetError,
    MultipleFailedRequests,
    ProbablyNodeHasNoBlock,
    ProviderConnectionError,
    RequestException,
    RequestTimedOut,
    RPCRequestError,
    TimeoutError,
)


class SimulatedVaultAttemptTimeout(BaseException):
    """A fork-only vault attempt exceeded its diagnostic time budget.

    This intentionally bypasses broad ``except Exception`` blocks in third-party
    vault adapters. The command catches it at the outer per-vault boundary.
    """


@dataclass(slots=True)
class SimulatedVaultRuntime:
    """One disposable generation of the multichain Anvil simulation.

    All fields belong to the same fork generation and must be replaced
    together.  ``temporary_deployment_dir`` owns the generated deployment
    artefact consumed by normal Lagoon execution-model bootstrap.
    """

    generation: int
    web3config: Any
    deployment: Any
    deployment_file: Path
    execution_model: Any
    sync_model: Any
    reserve_asset: Any
    temporary_deployment_dir: tempfile.TemporaryDirectory
    #: Immutable upstream heights immediately after Anvil forks start, before
    #: the test Lagoon topology deploys any contracts.
    fork_blocks: dict[str, int] = field(default_factory=dict)

    def close(self) -> None:
        """Hard-stop all forks and remove this generation's artefact."""

        try:
            try:
                self.web3config.close(log_level=logging.ERROR, block_timeout=5)
            except Exception:
                logger.exception(
                    "One or more Anvil processes did not close cleanly for simulation generation %d",
                    self.generation,
                )
        finally:
            self.temporary_deployment_dir.cleanup()


def is_simulated_infrastructure_failure(error: BaseException) -> bool:
    """Check if a failed simulated attempt needs a fresh Anvil generation.

    Only transport/process failures qualify.  Reverts and adapter errors are
    vault results and must be persisted without an automatic retry.
    """

    if isinstance(error, SimulatedVaultAttemptTimeout):
        return True
    if isinstance(error, SIMULATED_INFRASTRUCTURE_EXCEPTIONS):
        return True

    message = str(error).lower()
    if any(
        clue in message
        for clue in (
            "anvil did not start",
            "could not read block number from anvil",
            "could not restore simulated",
            "failed to create genesis",
            "rpc smoke test failed",
        )
    ):
        return True

    # Only follow explicit exception chaining.  ``__context__`` merely means
    # this exception was raised while another was being handled: treating that
    # as causation can turn a deterministic adapter failure into an Anvil retry.
    nested = error.__cause__
    return (
        nested is not None
        and nested is not error
        and is_simulated_infrastructure_failure(nested)
    )


def queue_simulated_infrastructure_retry(
    spec: VaultSpec,
    pending_specs: deque,
    restart_counts: dict[str, int],
) -> bool:
    """Queue one clean rerun of a vault after replacing all Anvil forks.

    :return:
        ``True`` when the id was put back at the front of the sequential queue,
        or ``False`` after its single infrastructure retry was already used.
    """

    vault_id = spec.as_string_id()
    if restart_counts[vault_id] >= SIMULATED_VAULT_INFRASTRUCTURE_RESTARTS:
        return False
    restart_counts[vault_id] += 1
    pending_specs.appendleft(spec)
    return True


def raise_simulated_vault_attempt_timeout(signum, frame) -> None:
    """Interrupt a stuck fork-only adapter call so the next vault can run."""

    raise SimulatedVaultAttemptTimeout(
        f"Simulated vault attempt exceeded {SIMULATED_VAULT_ATTEMPT_TIMEOUT} seconds"
    )


def start_simulated_vault_runtime(  # noqa: PLR0917
    *,
    generation: int,
    executor_id: str,
    rpc_kwargs: dict,
    unit_testing: bool,
    vault_specs: list[VaultSpec],
    vault_universe,
    private_key: str,
    amount: Decimal,
    asset_management_mode: AssetManagementMode,
    confirmation_timeout: int,
    confirmation_block_count: int,
    min_gas_balance: float | None,
    max_slippage: float,
    token_cache,
) -> SimulatedVaultRuntime:
    """Create a complete disposable Anvil and Lagoon simulation generation.

    Setup uses normal command bootstrap after writing an ephemeral deployment
    artefact.  Any failure closes every Anvil already started for this
    generation before propagating to the bounded replacement loop.
    """

    web3config = None
    temporary_deployment_dir = None
    try:
        # Web3Config launches one local Anvil proxy for every selected upstream
        # RPC.  Local RPC retries are disabled; a dead process is replaced by the
        # generation-level retry outside this function.
        primary_chain_id = ChainId(vault_specs[0].chain_id)
        web3config = create_web3_config(
            **rpc_kwargs,
            unit_testing=unit_testing,
            simulate=True,
            simulate_http_timeout=(3.0, 40.0),
        )
        if not web3config.has_any_connection():
            raise RuntimeError("vault-test-trade requires JSON-RPC connections")
        web3config.set_default_chain(primary_chain_id)
        web3config.check_default_chain_id()
        fork_blocks = {
            str(chain_id.value): int(web3.eth.block_number)
            for chain_id, web3 in web3config.connections.items()
        }

        # Deploy the same hub/satellite Lagoon contracts used by integration
        # tests before constructing trade-executor models around them.
        deployment, artifact = deploy_simulated_lagoon_multichain(
            web3config=web3config,
            vault_specs=vault_specs,
            vault_universe=vault_universe,
            private_key=private_key,
            amount=amount,
        )
        # Normal bootstrap consumes a deployment file, so write the ephemeral
        # topology in its standard JSON shape rather than adding a special path.
        artifact["simulation_generation"] = generation
        temporary_deployment_dir = tempfile.TemporaryDirectory(
            prefix=f"vault-test-trade-generation-{generation}-"
        )
        deployment_file = (
            Path(temporary_deployment_dir.name) / f"{executor_id}.deployment.json"
        )
        deployment_file.write_text(json.dumps(artifact, indent=2))

        # Reuse production Lagoon transaction builders and sync models.  This is
        # what makes simulation diagnose real adapter/routing compatibility.
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
            token_cache=token_cache,
            deployment_file=deployment_file,
        )
        reserve_asset = translate_token_details(sync_model.vault.denomination_token)
        logger.info(
            "Started simulated vault runtime generation %d using Anvil processes %s",
            generation,
            {
                chain_id.name: anvil.process.pid
                for chain_id, anvil in web3config.anvils.items()
            },
        )
        return SimulatedVaultRuntime(
            generation=generation,
            web3config=web3config,
            deployment=deployment,
            deployment_file=deployment_file,
            execution_model=execution_model,
            sync_model=sync_model,
            reserve_asset=reserve_asset,
            temporary_deployment_dir=temporary_deployment_dir,
            fork_blocks=fork_blocks,
        )
    except BaseException:
        # Multichain setup can fail after earlier forks and contracts exist.
        # Always tear down the partial generation before retrying from scratch.
        if web3config is not None:
            try:
                web3config.close(log_level=logging.ERROR, block_timeout=5)
            except Exception:
                logger.exception(
                    "Could not fully clean up a failed simulated vault runtime"
                )
        if temporary_deployment_dir is not None:
            temporary_deployment_dir.cleanup()
        raise


def start_simulated_vault_runtime_with_replacement(**kwargs) -> SimulatedVaultRuntime:
    """Start a generation with one bounded whole-generation replacement.

    Deterministic deployment, adapter and contract errors escape immediately.
    Only failures classified as infrastructure consume the replacement budget.
    """

    generation = kwargs.pop("generation")
    last_error = None
    for offset in range(SIMULATED_VAULT_INFRASTRUCTURE_RESTARTS + 1):
        current_generation = generation + offset
        try:
            return start_simulated_vault_runtime(
                generation=current_generation,
                **kwargs,
            )
        except BaseException as e:
            if not is_simulated_infrastructure_failure(e):
                raise
            last_error = e
            logger.warning(
                "Discarding failed simulated vault runtime generation %d: %s",
                current_generation,
                e,
            )

    assert last_error is not None
    raise last_error


def take_simulated_snapshots(
    web3config, deployment, spec: VaultSpec
) -> dict[ChainId, str]:
    """Snapshot only chains that the selected vault attempt can mutate.

    A home-chain vault touches one fork.  A satellite vault touches the hub for
    CCTP and its destination fork for the vault transaction.
    """

    affected_chains = {
        deployment.primary_chain_id,
        ChainId(spec.chain_id),
    }
    return {
        chain_id: make_anvil_custom_rpc_request(
            web3config.get_connection(chain_id),
            "evm_snapshot",
        )
        for chain_id in affected_chains
    }


def restore_simulated_snapshots(web3config, fork_snapshots: dict[ChainId, str]) -> None:
    """Restore and health-check a still-responsive Anvil generation.

    ``evm_revert`` returning false means the snapshot is unusable.  A following
    block-number request catches a process that accepted the revert but became
    unresponsive immediately afterwards.
    """

    for chain_id, snapshot in fork_snapshots.items():
        web3 = web3config.get_connection(chain_id)
        reverted = make_anvil_custom_rpc_request(web3, "evm_revert", [snapshot])
        if reverted is not True:
            raise RPCRequestError(
                f"Could not restore simulated {chain_id.name} fork snapshot {snapshot}"
            )
        make_anvil_custom_rpc_request(web3, "eth_blockNumber")
