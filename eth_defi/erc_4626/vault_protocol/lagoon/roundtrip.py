"""Persisted manual Arbitrum/Ethereum/HyperEVM investment round trip.

This tutorial workflow uses ordinary phase functions and real guarded custody.
Core settlement is asynchronous; uncertain submissions are reconciled, never
replayed. See :doc:`/tutorials/lagoon-multichain` and
https://docs.lagoon.finance/vault/deposit-and-withdraw-flows.
"""

import asyncio
import datetime
import fcntl
import json
import logging
import os
import secrets
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests
from eth_typing import HexAddress
from web3 import Web3
from web3.contract.contract import ContractFunction
from web3.exceptions import TransactionNotFound
from web3.logs import DISCARD

from eth_defi.abi import get_deployed_contract
from eth_defi.cctp.attestation import fetch_attestation
from eth_defi.cctp.constants import CHAIN_ID_TO_CCTP_DOMAIN
from eth_defi.cctp.fees import CCTPFeeQuote, fetch_cctp_fee_quote
from eth_defi.cctp.receive import fetch_message_received, prepare_receive_message
from eth_defi.cctp.transfer import prepare_approve_for_burn, prepare_deposit_for_burn
from eth_defi.cctp.whitelist import CCTPDeployment
from eth_defi.compat import native_datetime_utc_now
from eth_defi.confirmation import Reverted
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonAutomatedDeployment, LagoonConfig, LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.erc_4626.vault_protocol.lagoon.funding import check_multichain_deployer_funding
from eth_defi.erc_4626.vault_protocol.lagoon.vault import AutomatedSafe, LagoonVault
from eth_defi.hotwallet import HotWallet
from eth_defi.hyperliquid.api import fetch_perp_clearinghouse_state, fetch_spot_clearinghouse_state, fetch_user_abstraction_mode, fetch_user_vault_equities
from eth_defi.hyperliquid.block import fetch_using_big_blocks, set_big_blocks
from eth_defi.hyperliquid.constants import HYPERLIQUID_SYSTEM_VAULT_ADDRESSES
from eth_defi.hyperliquid.core_writer import (
    MINIMUM_VAULT_DEPOSIT,
    build_hypercore_approve_deposit_wallet_call,
    build_hypercore_deposit_for_spot_call,
    build_hypercore_deposit_to_spot_call,
    build_hypercore_deposit_to_vault_call,
    build_hypercore_send_asset_to_evm_call,
    build_hypercore_transfer_usd_class_call,
    build_hypercore_withdraw_from_vault_call,
    compute_spot_to_evm_withdrawal_amount,
)
from eth_defi.hyperliquid.evm_escrow import is_account_activated
from eth_defi.hyperliquid.session import HyperliquidSession, create_hyperliquid_session
from eth_defi.hyperliquid.vault import HyperliquidVault, classify_hyperliquid_vault_deposit, fetch_all_vaults
from eth_defi.lighter.activation import activate_lighter_safe, create_activation_report, record_activation
from eth_defi.lighter.api import fetch_lighter_account_index, fetch_lighter_withdrawal_delay, wait_for_lighter_collateral
from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT
from eth_defi.lighter.deployment import LighterDeployment
from eth_defi.lighter.execution import (
    LIGHTER_USDC_ASSET_ID,
    LighterAccountIdentity,
    LighterOperation,
    LighterOperationStage,
    LighterPoolExecutor,
    fetch_execution_account,
    fetch_matching_withdrawal,
    fetch_pool_holdings,
    fetch_pool_state,
    fetch_withdrawals,
    prepare_pool_amount,
    prepare_pool_deposit,
    prepare_pool_redemption,
    prepare_secure_withdrawal,
    wait_for_pool_shares,
)
from eth_defi.lighter.lagoon import broadcast_tx, fetch_lighter_pending_balance
from eth_defi.lighter.sdk import import_lighter_sdk
from eth_defi.lighter.session import LighterSession, create_lighter_session
from eth_defi.lighter.vault import fetch_all_pools
from eth_defi.safe.deployment import SAFE_PROXY_FACTORY_ADDRESS
from eth_defi.token import USDC_NATIVE_TOKEN, TokenDetails

if TYPE_CHECKING:
    from eth_defi.testing.lagoon_roundtrip import RoundtripSimulation

logger = logging.getLogger(__name__)

#: Fixed tutorial topology; only Arbitrum has investor shares.
CHAINS = {"arbitrum": 42161, "ethereum": 1, "hyperliquid": 999}
#: An unwind invocation waits briefly, then saves pending work for a later invocation.
POLL_TIMEOUT = 180
#: Terminal outcomes with all investor shares redeemed.
TERMINAL = {"completed", "redeemed_with_dust", "unfunded"}


class RoundtripPendingError(RuntimeError):
    """A known delay or liquidity constraint requires another manual invocation."""


class UncertainExecutionError(RuntimeError):
    """Saved submission evidence cannot yet establish a safe next mutation."""


def parse_bool(value: str) -> bool:
    """Parse an explicit environment boolean.

    Empty values preserve the default false behaviour.

    :param value: Environment text.
    :return: Validated boolean.
    """
    assert value.lower() in {"", "false", "0", "true", "1", "yes", "no"}, "Invalid boolean"
    return value.lower() in {"true", "1", "yes"}


@dataclass(slots=True)
class RoundtripSettings:
    """Non-secret tutorial settings; saved amounts govern later unwind calls."""

    #: Lifecycle stage requested by the operator.
    action: str = "deploy"
    #: Whether all transaction endpoints are local forks.
    simulate: bool = False
    #: Protected persistent state.
    state_path: Path = field(default_factory=lambda: Path.home() / ".tradingstrategy/lagoon-multichain-core-roundtrip/mainnet/state.json")
    #: Initial accounted Arbitrum subscription.
    subscription: Decimal = Decimal(25)
    #: Target allocation in each selected protocol.
    position: Decimal = Decimal(10)
    #: Fee ceiling, included in the gross CCTP burn.
    max_fee: Decimal = Decimal("0.01")
    #: Maximum aggregate measured cash dust written down before redemption.
    max_residual: Decimal = Decimal("0.01")
    #: Optional explicit normal Hypercore vault.
    hypercore_vault: HexAddress | None = None
    #: Optional explicit Lighter public-pool index.
    lighter_pool: int | None = None

    @property
    def funding_floor(self) -> Decimal:
        """Return the subscription needed by the chosen allocation and allowances."""
        return self.position * 2 + Decimal(3) + self.max_fee * 2

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RoundtripSettings":
        """Read optional overrides without requiring keys for read-only status.

        :param env: Environment mapping, defaulting to the process environment.
        :return: Validated non-secret settings.
        """
        env = os.environ if env is None else env
        simulate = parse_bool(env.get("SIMULATE", "false"))
        mode = "simulation" if simulate else "mainnet"
        settings = cls(
            action=env.get("ACTION", "all" if simulate else "deploy"),
            simulate=simulate,
            state_path=Path(env.get("STATE_PATH", str(Path.home() / ".tradingstrategy/lagoon-multichain-core-roundtrip" / mode / "state.json"))).expanduser(),
            subscription=Decimal(env.get("USDC_AMOUNT", "25")),
            position=Decimal(env.get("POSITION_USDC", "10")),
            max_fee=Decimal(env.get("CCTP_MAX_FEE_USDC", "0.01")),
            max_residual=Decimal(env.get("MAX_RESIDUAL_USDC", "0.01")),
            hypercore_vault=Web3.to_checksum_address(env["HYPERCORE_VAULT"]) if env.get("HYPERCORE_VAULT") else None,
            lighter_pool=int(env["LIGHTER_POOL_INDEX"]) if env.get("LIGHTER_POOL_INDEX") else None,
        )
        assert env.get("NETWORK", "mainnet") == "mainnet", "Only the three mainnets are supported"
        assert settings.action in ({"all"} if simulate else {"deploy", "status", "unwind"}), "Unsupported mode/action"
        assert all(x.is_finite() and x >= 0 for x in (settings.subscription, settings.position, settings.max_fee, settings.max_residual))
        assert settings.max_fee <= 1 and settings.max_residual <= Decimal("0.01"), "Invalid fee/dust ceiling"
        if settings.action in {"deploy", "all"}:
            assert settings.position >= Decimal(5), "Hypercore vault entries require at least 5 USDC"
            assert settings.subscription >= settings.funding_floor, f"Subscription needs at least {settings.funding_floor} USDC"
        return settings


@dataclass(slots=True)
class RoundtripState:
    """Versioned recovery evidence, never including the supplied EOA private key."""

    #: State schema version.
    version: int = 1
    #: Execution environment identity.
    simulate: bool = False
    #: Original investor and manager address.
    owner: HexAddress | None = None
    #: Shared deterministic Safe salt.
    salt: int = field(default_factory=lambda: secrets.randbits(64))
    #: Visible overall outcome.
    outcome: str = "preparing"
    #: Frozen financial settings, encoded as decimal strings.
    settings: dict[str, str] = field(default_factory=dict)
    #: Selected targets, fixed before broadcasting.
    targets: dict[str, Any] = field(default_factory=dict)
    #: Redacted per-chain deployment serialisations.
    deployments: dict[str, dict] = field(default_factory=dict)
    #: EVM signed hashes/nonces/receipts, indexed by semantic operation name.
    transactions: dict[str, dict] = field(default_factory=dict)
    #: Per-operation amounts and protocol confirmation observations.
    steps: dict[str, Any] = field(default_factory=dict)
    #: CCTP original burn/message/relay evidence.
    bridges: dict[str, dict] = field(default_factory=dict)
    #: Lighter account/key reference and persisted signed operations.
    lighter: dict[str, Any] = field(default_factory=dict)
    #: Initial and final balances, fees, proceeds and residuals.
    results: dict[str, Any] = field(default_factory=dict)


def save_state(path: Path, state: RoundtripState) -> None:
    """Atomically replace a protected state file after flushing its contents.

    :param path: Run-local file whose directory is already locked.
    :param state: Recovery evidence to persist.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(asdict(state), output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_state(path: Path, *, simulate: bool) -> RoundtripState:
    """Read a private report and reject schema/environment substitutions.

    :param path: Saved run.
    :param simulate: Required environment identity.
    :return: Hydrated state; no transactions are submitted.
    """
    assert path.stat().st_mode & 0o077 == 0, "State file must be private"
    state = RoundtripState(**json.loads(path.read_text()))
    assert state.version == 1 and state.simulate == simulate, "State schema or execution mode mismatch"
    return state


@contextmanager
def lock_run(path: Path) -> Iterator[None]:
    """Prevent concurrent local writers without deleting another run's evidence.

    :param path: State path whose companion lock is held for this invocation.
    :return: Context yielding while the lock is held.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@dataclass(slots=True)
class RoundtripContext:
    """Connections and adapters for one invocation; no generic workflow backend."""

    #: Validated operator settings.
    settings: RoundtripSettings
    #: Persisted financial evidence.
    state: RoundtripState
    #: Three execution connections.
    web3: dict[str, Web3]
    #: One nonce-managed signer per chain, absent in status mode.
    wallets: dict[str, HotWallet]
    #: Native USDC token metadata per chain.
    usdc: dict[str, TokenDetails]
    #: Reconstructed per-chain deployments.
    deployments: dict[str, LagoonAutomatedDeployment] = field(default_factory=dict)
    #: Lighter read transport, replaced only at the local simulation boundary.
    lighter_session: LighterSession = field(default_factory=create_lighter_session)
    #: Public Hypercore read transport.
    hyper_session: HyperliquidSession = field(default_factory=create_hyperliquid_session)
    #: Optional simulation-only Core ledger and API transport.
    simulation: "RoundtripSimulation | None" = None
    #: Optional caller-owned SDK signer.
    signer: Any = None

    def save(self) -> None:
        """Flush the current recovery evidence before allowing another side effect."""
        save_state(self.settings.state_path, self.state)

    def safe(self, chain: str) -> AutomatedSafe:
        """Resolve a main or satellite Safe without a dummy vault contract.

        :param chain: Canonical name.
        :return: Guarded Safe wrapper.
        """
        return self.deployments[chain].vault


def wait_until(description: str, predicate: Callable[[], bool], timeout: float = POLL_TIMEOUT) -> None:
    """Poll an observable condition with a bounded, logged wait.

    :param description: Safe-to-log progress description.
    :param predicate: Replay-safe read.
    :param timeout: Maximum wait in seconds.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise RoundtripPendingError(description)
        logger.info("Waiting: %s (%.0fs remaining)", description, deadline - time.monotonic())
        time.sleep(5)


def send_transaction(ctx: RoundtripContext, name: str, chain: str, call: ContractFunction) -> str:
    """Record before broadcast, then reconcile this exact EVM transaction on restart.

    :param ctx: Current run.
    :param name: Stable operation identifier; never reused for a different mutation.
    :param chain: Execution chain.
    :param call: Prepared guarded or direct call, used only when no saved submission exists.
    :return: Confirmed original hash.
    """
    web3 = ctx.web3[chain]
    saved = ctx.state.transactions.get(name)
    if saved is None:
        ctx.wallets[chain].sync_nonce(web3)

        def observe(tx_hash: str, nonce: int, _description: str) -> None:
            """Persist the signed identity before the shared sender broadcasts."""
            ctx.state.transactions[name] = {"chain": chain, "hash": tx_hash, "nonce": nonce}
            ctx.save()

        broadcast_tx(web3, ctx.wallets[chain], call, name, gas_limit=3_000_000, tx_observer=observe)
        saved = ctx.state.transactions[name]
    assert saved["chain"] == chain
    try:
        receipt = web3.eth.get_transaction_receipt(saved["hash"])
    except TransactionNotFound as error:
        raise UncertainExecutionError(f"Reconcile {name} {saved['hash']}; no second broadcast is allowed") from error
    assert receipt["status"] == 1, f"Failed transaction {name}: {saved['hash']}"
    saved.update(confirmed=True, gas_used=receipt["gasUsed"], gas_cost_raw=str(receipt["gasUsed"] * receipt["effectiveGasPrice"]))
    ctx.save()
    return saved["hash"]


def fetch_core_snapshot(ctx: RoundtripContext) -> dict[str, Any]:
    """Observe fresh Safe-owned Core balances, holdings and unlock constraints.

    :param ctx: Current run.
    :return: Decimal strings and raw protocol identity, without missing-data defaults.
    """
    if ctx.simulation is not None:
        return ctx.simulation.fetch_core_snapshot()
    safe = ctx.safe("hyperliquid").safe_address
    spot = fetch_spot_clearinghouse_state(ctx.hyper_session, safe)
    perp = fetch_perp_clearinghouse_state(ctx.hyper_session, safe)
    positions = tuple(fetch_user_vault_equities(ctx.hyper_session, safe))
    target = ctx.state.targets["hypercore"]
    assert all(p.vault_address.lower() == target.lower() for p in positions), "Unexpected Core holding"
    held = next((p for p in positions if p.vault_address.lower() == target.lower()), None)
    return {"spot": str(sum((b.total - b.hold for b in spot.balances if b.coin == "USDC"), Decimal(0))), "perp": str(perp.withdrawable), "equity": str(held.equity if held else 0), "locked_until": held.locked_until.isoformat() if held else None, "escrow": str(sum((e.total for e in spot.evm_escrows if e.coin == "USDC"), Decimal(0)))}


def fetch_status(ctx: RoundtripContext) -> dict[str, Any]:
    """Read observed balances without EOA signing or a completed activation report.

    :param ctx: Loaded run, including partial deployments.
    :return: Timestamped observations and explicit unavailable-data messages.
    """
    rows: dict[str, Any] = {"observed_at": native_datetime_utc_now().isoformat(), "outcome": ctx.state.outcome}
    for chain in ctx.deployments:
        rows[chain] = {"safe": ctx.safe(chain).safe_address, "usdc": str(ctx.usdc[chain].fetch_balance_of(ctx.safe(chain).safe_address))}
    if "hyperliquid" in ctx.deployments:
        try:
            rows["core"] = fetch_core_snapshot(ctx)
        except (requests.RequestException, ValueError, KeyError, AssertionError) as error:
            rows["core"] = {"unavailable": type(error).__name__}
    if ctx.state.lighter.get("account_index") is not None:
        try:
            _, equity = fetch_execution_account(ctx.lighter_session, ctx.state.lighter["account_index"])
            rows["lighter"] = {"collateral": str(equity.collateral), "account_equity": str(equity.total_asset_value), "available": str(equity.available_balance), "holdings": [{"pool": h.pool_index, "shares": h.shares, "gross_value": str(h.gross_value), "principal": str(h.principal), "estimated_operator_fee": str(h.estimated_fee)} for h in fetch_pool_holdings(ctx.lighter_session, equity.account_index)]}
        except (requests.RequestException, ValueError, KeyError, AssertionError) as error:
            rows["lighter"] = {"unavailable": type(error).__name__}
    if "lighter" in rows:
        for field_name in ("withdrawal_id", "withdrawal_delay_seconds", "estimated_retry_at"):
            if field_name in ctx.state.lighter:
                rows["lighter"][field_name] = ctx.state.lighter[field_name]
    rows["pending"] = {name: row for name, row in ctx.state.bridges.items() if not row.get("complete")}
    return rows


def select_targets(ctx: RoundtripContext) -> None:
    """Select eligible current targets once, before deployment or capital movement.

    :param ctx: New run with optional overrides.
    """
    if ctx.simulation:
        ctx.state.targets = {"hypercore": ctx.simulation.hypercore_vault, "lighter": ctx.simulation.pool_index}
        return
    candidates = [ctx.settings.hypercore_vault] if ctx.settings.hypercore_vault else [v.vault_address for v in sorted(fetch_all_vaults(ctx.hyper_session), key=lambda v: (-v.tvl, v.vault_address))]
    for address in candidates:
        if address.lower() in {a.lower() for a in HYPERLIQUID_SYSTEM_VAULT_ADDRESSES}:
            continue
        info = HyperliquidVault(ctx.hyper_session, address).fetch_metadata()
        status = classify_hyperliquid_vault_deposit(info.is_closed, info.allow_deposits, info.relationship_type, info.leader_fraction)
        if info.relationship_type == "normal" and status.deposits_open is True and status.max_deposit != 0 and info.leader_fraction is not None:
            ctx.state.targets["hypercore"] = Web3.to_checksum_address(address)
            break
    assert "hypercore" in ctx.state.targets, "No open normal Hypercore user vault with sufficient capacity"
    pools = [ctx.settings.lighter_pool] if ctx.settings.lighter_pool is not None else [p.account_index for p in sorted(fetch_all_pools(ctx.lighter_session), key=lambda p: (-p.total_asset_value, p.account_index))]
    for index in pools:
        pool = fetch_pool_state(ctx.lighter_session, index)
        if not pool.is_llp and pool.status == 0 and pool.equity > 0:
            try:
                prepare_pool_amount(pool, ctx.settings.position, available_collateral=ctx.settings.position)
            except AssertionError:
                continue
            ctx.state.targets["lighter"] = index
            break
    assert "lighter" in ctx.state.targets, "No eligible non-LLP Lighter pool"


def build_configs(ctx: RoundtripContext) -> dict[str, LagoonConfig]:
    """Use native USDC and narrow custody permissions on the fixed three chains.

    :param ctx: Preflighted targets and deterministic Safe parameters.
    :return: One full vault configuration and two satellite configurations.
    """
    return {
        chain: LagoonConfig(
            parameters=LagoonDeploymentParameters(underlying=USDC_NATIVE_TOKEN[chain_id], name="Core round trip manual test", symbol="CORE-TEST", managementRate=0, performanceRate=0),
            safe_owners=[ctx.state.owner],
            safe_threshold=1,
            asset_managers=[ctx.state.owner],
            safe_salt_nonce=ctx.state.salt,
            satellite_chain=chain != "arbitrum",
            assets=[USDC_NATIVE_TOKEN[chain_id]],
            any_asset=False,
            hypercore_vaults=[ctx.state.targets["hypercore"]] if chain == "hyperliquid" else None,
            lighter_deployment=LighterDeployment.create_ethereum() if chain == "ethereum" else None,
            generate_lighter_api_key=False,
            between_contracts_delay_seconds=0 if ctx.settings.simulate else 2,
            cctp_deployment=CCTPDeployment.create_for_chain(chain_id, allowed_destinations=[other for other in CHAINS.values() if other != chain_id]),
        )
        for chain, chain_id in CHAINS.items()
    }


def fetch_quote(ctx: RoundtripContext, source: str, destination: str, net: Decimal, *, max_fee: Decimal | None = None) -> CCTPFeeQuote:
    """Quote a gross-included fee on a fresh or simulated CCTP route.

    :param ctx: Run containing the explicit fee ceiling.
    :param source: Source chain name.
    :param destination: Destination chain name.
    :param net: Minimum net receipt; gross is net plus the configured ceiling.
    :param max_fee: Optional smaller ceiling to drain a tiny complete balance.
    :return: Integer quote, preserving full-balance arithmetic.
    """
    ceiling = ctx.settings.max_fee if max_fee is None else max_fee
    if ctx.simulation:
        return ctx.simulation.fetch_quote(ctx.usdc[source], CHAINS[source], net, ceiling)
    return fetch_cctp_fee_quote(ctx.usdc[source], CHAINS[source], CHAINS[destination], net, ceiling)


def bridge(ctx: RoundtripContext, name: str, source: str, destination: str, net: Decimal | None = None) -> None:
    """Burn once, retain the original message and relay/observe that message only.

    :param ctx: Current run with guarded source and destination Safes.
    :param name: Stable bridge identifier.
    :param source: Chain holding accounted capital.
    :param destination: Chain whose Safe receives net proceeds.
    :param net: Requested net amount, or ``None`` for a complete source-balance burn.
    """
    row = ctx.state.bridges.setdefault(name, {"source": source, "destination": destination})
    if row.get("complete"):
        return
    token, dest_token = ctx.usdc[source], ctx.usdc[destination]
    if name + "_burn" not in ctx.state.transactions:
        balance = token.fetch_balance_of(ctx.safe(source).safe_address)
        if balance == 0:
            row["complete"] = True
            ctx.save()
            return
        ceiling = min(ctx.settings.max_fee, token.convert_to_decimals(token.convert_to_raw(balance / 2))) if net is None else ctx.settings.max_fee
        amount = net if net is not None else balance - ceiling
        # A restart of an already prepared bridge preserves its original gross
        # amount, but an explicit fee override may affect the still-unsigned burn.
        if "amount" in row:
            amount = token.convert_to_decimals(row["amount"]) - ceiling
        try:
            quote = fetch_quote(ctx, source, destination, amount, max_fee=ceiling)
        except AssertionError as error:
            if net is None and token.convert_to_raw(balance) == 1 and balance <= ctx.settings.max_residual and "fee" in str(error).lower():
                row.update(complete=True, dust_usdc=str(balance))
                ctx.save()
                return
            raise RoundtripPendingError(f"{name}: fresh fee/allowance check failed: {error}; set an explicit CCTP_MAX_FEE_USDC override if appropriate") from error
        if net is None:
            assert quote.amount_raw == token.convert_to_raw(balance)
        row.update(amount=quote.amount_raw, max_fee=quote.max_fee_raw, fee_quote=quote.fee_raw, finality=quote.finality, destination_before=dest_token.fetch_raw_balance_of(ctx.safe(destination).safe_address))
        ctx.save()
    approve = prepare_approve_for_burn(ctx.web3[source], row["amount"])
    send_transaction(ctx, name + "_approve", source, ctx.safe(source).transact_via_trading_strategy_module(approve))
    burn = prepare_deposit_for_burn(ctx.web3[source], row["amount"], CHAINS[destination], ctx.safe(destination).safe_address, max_fee=row["max_fee"], min_finality_threshold=row["finality"])
    row["burn_hash"] = send_transaction(ctx, name + "_burn", source, ctx.safe(source).transact_via_trading_strategy_module(burn))
    ctx.save()
    if "message" not in row:
        if ctx.simulation:
            message, attestation = ctx.simulation.attest(ctx, row)
        else:
            result = fetch_attestation(CHAIN_ID_TO_CCTP_DOMAIN[CHAINS[source]], row["burn_hash"], timeout=POLL_TIMEOUT)
            message, attestation = result.message, result.attestation
        row.update(message=message.hex(), attestation=attestation.hex())
        ctx.save()
    message, attestation = bytes.fromhex(row["message"]), bytes.fromhex(row["attestation"])
    # Bind the attested amount and recipient to this exact custody route.
    assert int.from_bytes(message[4:8], "big") == CHAIN_ID_TO_CCTP_DOMAIN[CHAINS[source]]
    assert int.from_bytes(message[8:12], "big") == CHAIN_ID_TO_CCTP_DOMAIN[CHAINS[destination]]
    assert message[184:216][-20:] == bytes.fromhex(ctx.safe(destination).safe_address[2:])
    assert int.from_bytes(message[216:248], "big") == row["amount"]
    if not fetch_message_received(ctx.web3[destination], message):
        try:
            send_transaction(ctx, name + "_receive", destination, prepare_receive_message(ctx.web3[destination], message, attestation))
        except (RuntimeError, Reverted):
            if not fetch_message_received(ctx.web3[destination], message):
                raise
    after = dest_token.fetch_raw_balance_of(ctx.safe(destination).safe_address)
    received = after - row["destination_before"]
    assert row["amount"] - row["max_fee"] <= received <= row["amount"], "Unexpected bridge receipt"
    row.update(complete=True, received_raw=received, actual_fee_raw=row["amount"] - received)
    ctx.save()


def subscribe(ctx: RoundtripContext) -> None:
    """Perform the normal Lagoon subscription with each transaction persisted.

    :param ctx: Deployed, funded investor; calling after a restart reconciles saved stages.
    """
    vault = ctx.safe("arbitrum")
    assert isinstance(vault, LagoonVault)
    token, owner = ctx.usdc["arbitrum"], ctx.state.owner
    amount = token.convert_to_raw(ctx.settings.subscription)
    send_transaction(ctx, "subscription_initial_nav", "arbitrum", vault.post_new_valuation(Decimal(0)))
    send_transaction(ctx, "subscription_approve", "arbitrum", token.approve(vault.address, ctx.settings.subscription))
    send_transaction(ctx, "subscription_request", "arbitrum", vault.request_deposit(owner, amount, check_balance=False, check_allowance=False))
    send_transaction(ctx, "subscription_nav", "arbitrum", vault.post_new_valuation(Decimal(0)))
    send_transaction(ctx, "subscription_settle", "arbitrum", vault.settle_via_trading_strategy_module(Decimal(0)))
    send_transaction(ctx, "subscription_claim", "arbitrum", vault.finalise_deposit(owner, raw_amount=amount))
    assert vault.share_token.fetch_raw_balance_of(owner) > 0
    ctx.state.steps["subscribed"] = True
    ctx.save()


def core_action(ctx: RoundtripContext, name: str, call: ContractFunction, ready: Callable[[dict], bool], amount: Decimal) -> None:
    """Submit one Core action and require an observed protocol effect.

    :param ctx: Run with a single Core writer.
    :param name: Stable stage identifier, also used by the simulation ledger.
    :param call: Guarded EVM call.
    :param ready: Fresh Core-state predicate establishing this transition.
    :param amount: Explicit amount saved before submission.
    """
    if ctx.state.steps.get(name, {}).get("complete"):
        return
    row = ctx.state.steps.setdefault(name, {"amount": str(amount)})
    row.setdefault("before", fetch_core_snapshot(ctx))
    ctx.save()
    send_transaction(ctx, name, "hyperliquid", call)
    if ctx.simulation:
        ctx.simulation.apply_core(ctx, name, amount)
    wait_until(name + " protocol effect", lambda: ready(fetch_core_snapshot(ctx)))
    ctx.state.steps[name]["complete"] = True
    ctx.state.steps[name]["after"] = fetch_core_snapshot(ctx)
    ctx.save()


def invest_hypercore(ctx: RoundtripContext) -> None:
    """Activate, clear the deposit escrow, then invest from the Safe perp account.

    :param ctx: Funded satellite and selected normal user vault.
    """
    safe, token, amount = ctx.safe("hyperliquid"), ctx.usdc["hyperliquid"], ctx.settings.position
    send_transaction(ctx, "core_activation_approve", "hyperliquid", build_hypercore_approve_deposit_wallet_call(safe, token.convert_to_raw(Decimal(2))))
    core_action(ctx, "core_activation", build_hypercore_deposit_for_spot_call(safe, token.convert_to_raw(Decimal(2))), lambda s: Decimal(s["spot"]) > 0, Decimal(2))
    if not ctx.simulation:
        mode = fetch_user_abstraction_mode(ctx.hyper_session, safe.safe_address)
        ctx.state.steps["core_account_mode"] = mode
        ctx.save()
        # userAbstraction uses default/disabled for separate spot and perp
        # balances; "standard" is the UI name, not an API response value.
        assert mode in {"default", "disabled"}, f"This tutorial's spot/perp route requires default/disabled account mode, observed {mode}; recover activation collateral before continuing"
        info = HyperliquidVault(ctx.hyper_session, ctx.state.targets["hypercore"]).fetch_metadata()
        status = classify_hyperliquid_vault_deposit(info.is_closed, info.allow_deposits, info.relationship_type, info.leader_fraction)
        assert status.deposits_open is True and status.max_deposit != 0, "Core target no longer accepts deposits"
    before = Decimal(fetch_core_snapshot(ctx)["spot"])
    send_transaction(ctx, "core_deposit_approve", "hyperliquid", build_hypercore_approve_deposit_wallet_call(safe, token.convert_to_raw(amount)))
    core_action(ctx, "core_deposit", build_hypercore_deposit_to_spot_call(safe, token.convert_to_raw(amount)), lambda s: Decimal(s["escrow"]) == 0 and Decimal(s["spot"]) >= before + amount, amount)
    core_action(ctx, "core_to_perp", build_hypercore_transfer_usd_class_call(safe, token.convert_to_raw(amount), True), lambda s: Decimal(s["perp"]) >= amount, amount)
    core_action(ctx, "core_invest", build_hypercore_deposit_to_vault_call(safe, ctx.state.targets["hypercore"], token.convert_to_raw(amount)), lambda s: Decimal(s["equity"]) > 0, amount)
    ctx.state.results["core_after_invest"] = fetch_core_snapshot(ctx)
    ctx.save()


def lighter_progress(ctx: RoundtripContext, stage: str, **details: Any) -> None:
    """Back up activation secrets and transaction evidence before registration.

    :param ctx: Current run.
    :param stage: Shared activation helper stage.
    :param details: Protected evidence, never logged verbatim.
    """
    report = Path(ctx.state.lighter["key_report"])
    record_activation(report, stage, **details)
    if stage == "lighter_deposit_confirmed" and ctx.simulation:
        ctx.simulation.lighter.credit_deposit(Decimal(1))
    if stage == "lighter_key_saved":
        ctx.state.lighter.update(account_index=details["account_index"], api_key_index=details["api_key_index"])
        if ctx.simulation:
            ctx.simulation.lighter.public_key = details["public_key"]
    if stage == "ethereum_transaction_signed":
        ctx.state.transactions["activation_" + str(details["nonce"])] = {"chain": "ethereum", "hash": details["tx_hash"], "nonce": details["nonce"], "confirmed": False}
    ctx.state.lighter["activation_stage"] = stage
    ctx.save()


def invest_lighter(ctx: RoundtripContext) -> None:
    """Activate the Safe-owned key, credit collateral and mint current raw shares.

    :param ctx: Funded Ethereum satellite with generated key backup location.
    """
    safe, token = ctx.safe("ethereum"), ctx.usdc["ethereum"]
    report = ctx.settings.state_path.parent / "lighter-key.jsonl"
    create_activation_report(report)
    ctx.state.lighter["key_report"] = str(report)
    ctx.save()
    if ctx.simulation:
        ctx.simulation.prepare_lighter(ctx)
    ctx.wallets["ethereum"].sync_nonce(ctx.web3["ethereum"])
    setup = activate_lighter_safe(ctx.web3["ethereum"], ctx.wallets["ethereum"], safe, progress=lambda stage, **details: lighter_progress(ctx, stage, **details), session=ctx.lighter_session)
    ctx.state.lighter.update(account_index=setup.account_index, api_key_index=setup.api_key_index, activated=True)
    ctx.save()
    zk = get_deployed_contract(ctx.web3["ethereum"], "lighter/ZkLighter.json", LIGHTER_L1_CONTRACT)
    send_transaction(ctx, "lighter_collateral_approve", "ethereum", safe.transact_via_trading_strategy_module(token.approve(zk.address, ctx.settings.position)))
    send_transaction(ctx, "lighter_collateral_deposit", "ethereum", safe.transact_via_trading_strategy_module(zk.functions.deposit(safe.safe_address, 3, 0, token.convert_to_raw(ctx.settings.position))))
    if ctx.simulation:
        ctx.simulation.lighter.credit_deposit(ctx.settings.position)
    wait_for_lighter_collateral(ctx.lighter_session, setup.account_index, ctx.settings.position + 1)
    _, before = fetch_execution_account(ctx.lighter_session, setup.account_index)
    ctx.state.lighter["mint_collateral_before"] = str(before.available_balance)
    ctx.save()
    execute_lighter(ctx, "mint")
    _, after = fetch_execution_account(ctx.lighter_session, setup.account_index)
    ctx.state.lighter["mint_collateral_after"] = str(after.available_balance)
    ctx.state.lighter["observed_mint_cost"] = str(before.available_balance - after.available_balance)
    ctx.state.results["lighter_after_invest"] = fetch_status(ctx).get("lighter")
    ctx.save()


def load_lighter_key(ctx: RoundtripContext) -> dict:
    """Read the exact protected key generated for this run, never a global default.

    :param ctx: Current state with a saved key report reference.
    :return: Verified key identity from the original report.
    """
    report = Path(ctx.state.lighter["key_report"])
    assert report.stat().st_mode & 0o077 == 0
    rows = [json.loads(line) for line in report.read_text().splitlines()]
    keys = [row for row in rows if row["stage"] == "lighter_key_saved"]
    assert len(keys) == 1, "Recover the incomplete activation key before API execution"
    key = keys[0]
    assert key["account_index"] == ctx.state.lighter["account_index"]
    return key


@contextmanager
def lighter_executor(ctx: RoundtripContext, name: str) -> Iterator[LighterPoolExecutor]:
    """Bind the optional signer and durable operation recorder for one API stage.

    :param ctx: Account/key owner context.
    :param name: Semantic operation identifier (mint, burn or withdraw).
    :return: Executor with original account and signing-network identity.
    """
    key = load_lighter_key(ctx)
    owned = ctx.signer is None and ctx.simulation is None
    loop = None
    if owned:
        sdk = import_lighter_sdk()
        loop = asyncio.new_event_loop()

        async def construct() -> Any:  # noqa: RUF029 - SDK construction needs a running event loop.
            """Construct the optional SDK on its required event loop."""
            return sdk.SignerClient(url=ctx.lighter_session.api_url, account_index=key["account_index"], api_private_keys={key["api_key_index"]: key["private_key"]})

        signer = loop.run_until_complete(construct())
    else:
        signer = ctx.simulation.lighter if ctx.simulation else ctx.signer
    identity = LighterAccountIdentity(key["account_index"], api_key_index=key["api_key_index"])

    def persist(operation: LighterOperation) -> None:
        """Save signed payload/hash/nonce before the executor submits."""
        ctx.state.lighter.setdefault("operations", {})[name] = operation.as_dict()
        ctx.save()

    executor = LighterPoolExecutor(ctx.lighter_session, identity, signer, persist, submission_session_factory=(lambda: ctx.simulation.lighter) if ctx.simulation else None)
    try:
        yield executor
    finally:
        if owned:
            try:
                loop.run_until_complete(signer.close())
            finally:
                loop.close()


def execute_lighter(ctx: RoundtripContext, name: str, amount: Decimal | None = None) -> None:
    """Prepare once or reconcile an original signed API operation after restart.

    :param ctx: Saved account and key identity.
    :param name: Mint, burn or secure withdrawal stage.
    :param amount: USDC for a secure withdrawal only.
    """
    saved = ctx.state.lighter.get("operations", {}).get(name)
    if saved and saved["stage"] == "confirmed":
        return
    with lighter_executor(ctx, name) as executor:
        if saved:
            operation = LighterOperation.from_dict(saved)
            if operation.stage == LighterOperationStage.rejected:
                raise UncertainExecutionError(f"Lighter {name} was rejected; inspect the saved operation")
            # Even a prepared operation can have been broadcast before a crash.
            executor.reconcile(operation, timeout=POLL_TIMEOUT)
        else:
            if name == "mint":
                operation = prepare_pool_deposit(executor, ctx.state.targets["lighter"], ctx.settings.position)
            elif name == "burn":
                operation = prepare_pool_redemption(executor, ctx.state.targets["lighter"])
            else:
                assert name == "withdraw" and amount is not None
                operation = prepare_secure_withdrawal(executor, ctx.usdc["ethereum"], amount)
            executor.submit(operation)
            executor.reconcile(operation, timeout=POLL_TIMEOUT)
    if name in {"mint", "burn"}:
        wait_for_pool_shares(ctx.lighter_session, ctx.state.lighter["account_index"], ctx.state.targets["lighter"], operation.amount if name == "mint" else 0, timeout=POLL_TIMEOUT)


def fetch_auth(ctx: RoundtripContext) -> str:
    """Generate a short-lived API token for replay-safe withdrawal reads.

    :param ctx: Key-backed account.
    :return: Token, never stored or printed.
    """
    with lighter_executor(ctx, "auth") as executor:
        token, error = executor.signer.create_auth_token_with_expiry(api_key_index=executor.identity.api_key_index)
        assert error is None
        return token


def restore_big_blocks(ctx: RoundtripContext) -> None:
    """Restore the recorded deployer's HyperEVM mode after interrupted deployment.

    :param ctx: Run with its original mode saved before deployment.
    """
    if not ctx.simulation and "big_blocks_before" in ctx.state.steps:
        previous = ctx.state.steps["big_blocks_before"]
        if fetch_using_big_blocks(ctx.web3["hyperliquid"], ctx.state.owner) != previous:
            set_big_blocks(ctx.wallets["hyperliquid"].private_key.hex(), enable=previous, is_mainnet=True)
        ctx.state.steps["big_blocks_restored"] = True
        ctx.save()


def deploy_and_invest(ctx: RoundtripContext) -> None:
    """Deploy all contracts before subscribing once and opening both positions.

    :param ctx: New run with gas and USDC prefunded on every chain.
    """
    assert not ctx.settings.state_path.exists(), "State already exists: inspect/unwind it, or choose a new STATE_PATH"
    assert not (ctx.settings.state_path.parent / "lighter-key.jsonl").exists(), "Lighter key backup already exists; reconcile that run or choose a new state directory before deployment"
    assert ctx.usdc["hyperliquid"].convert_to_raw(ctx.settings.position) >= MINIMUM_VAULT_DEPOSIT
    check_multichain_deployer_funding(ctx.web3, ctx.state.owner, "arbitrum", ctx.settings.subscription)
    assert all(ctx.usdc[c].convert_to_decimals(ctx.usdc[c].convert_to_raw(ctx.settings.subscription)) == ctx.settings.subscription for c in CHAINS)
    if not ctx.simulation:
        assert is_account_activated(ctx.web3["hyperliquid"], ctx.state.owner), "Activate the deployer on Hypercore before deploying any chain: HyperEVM large-block deployment requires a recognised Core account. See scripts/lagoon/lagoon-multichain-core-roundtrip.py prerequisites and eth_defi.hyperliquid.evm_escrow.activate_account_sponsored()."
        import_lighter_sdk()
    for chain, chain_id in CHAINS.items():
        canonical = CCTPDeployment.create_for_chain(chain_id)
        for address in (USDC_NATIVE_TOKEN[chain_id], SAFE_PROXY_FACTORY_ADDRESS, canonical.token_messenger, canonical.message_transmitter):
            assert ctx.web3[chain].eth.get_code(Web3.to_checksum_address(address)), f"Missing required contract on {chain}: {address}"
    assert ctx.web3["ethereum"].eth.get_code(LIGHTER_L1_CONTRACT)
    select_targets(ctx)
    logger.info("Preflight targets: Hypercore %s; Lighter pool %s", ctx.state.targets["hypercore"], ctx.state.targets["lighter"])
    # Quote all four routes before any deployment, then refresh at burn time.
    for source, destination, net in [("arbitrum", "hyperliquid", ctx.settings.position + 2), ("arbitrum", "ethereum", ctx.settings.position + 1), ("ethereum", "arbitrum", ctx.settings.position), ("hyperliquid", "arbitrum", ctx.settings.position)]:
        fetch_quote(ctx, source, destination, net)
    ctx.state.settings = {k: str(getattr(ctx.settings, k)) for k in ("subscription", "position", "max_fee", "max_residual")}
    ctx.state.results["initial_usdc"] = str(ctx.usdc["arbitrum"].fetch_balance_of(ctx.state.owner))
    ctx.state.results["initial_native"] = {c: str(w.eth.get_balance(ctx.state.owner)) for c, w in ctx.web3.items()}
    if not ctx.simulation:
        ctx.state.steps["big_blocks_before"] = fetch_using_big_blocks(ctx.web3["hyperliquid"], ctx.state.owner)
    ctx.save()
    configs = build_configs(ctx)
    try:
        for chain, config in configs.items():
            logger.info("Phase 1: deploy %s (%s)", chain, CHAINS[chain])
            ctx.state.steps["deploy_" + chain] = {"initial_nonce": ctx.web3[chain].eth.get_transaction_count(ctx.state.owner)}
            ctx.save()
            ctx.wallets[chain].sync_nonce(ctx.web3[chain])
            deployment = deploy_automated_lagoon_vault(web3=ctx.web3[chain], deployer=ctx.wallets[chain], config=config)
            ctx.deployments[chain] = deployment
            ctx.state.deployments[chain] = deployment.as_json_friendly_dict()
            ctx.save()
    finally:
        restore_big_blocks(ctx)
    assert len({ctx.safe(c).safe_address.lower() for c in CHAINS}) == 1, "Deterministic Safes differ"
    if ctx.simulation:
        ctx.simulation.install_core(ctx)
    verify_bindings(ctx)
    assert all(ctx.usdc[c].fetch_raw_balance_of(ctx.safe(c).safe_address) == 0 for c in CHAINS), "Fresh Safes have unexpected USDC; reconcile before subscribing"
    logger.info("Phase 2: subscribe %s USDC on Arbitrum", ctx.settings.subscription)
    subscribe(ctx)
    logger.info("Phase 3: fund both satellites")
    bridge(ctx, "out_hyperliquid", "arbitrum", "hyperliquid", ctx.settings.position + 2)
    bridge(ctx, "out_ethereum", "arbitrum", "ethereum", ctx.settings.position + 1)
    logger.info("Phase 4: invest in Hypercore %s", ctx.state.targets["hypercore"])
    invest_hypercore(ctx)
    logger.info("Phase 5: invest in Lighter pool %s", ctx.state.targets["lighter"])
    invest_lighter(ctx)
    ctx.state.outcome = "invested"
    ctx.save()


def verify_bindings(ctx: RoundtripContext) -> None:
    """Validate saved role and custody identities against actual onchain state.

    :param ctx: Loaded or newly deployed contracts.
    """
    for chain, deployment in ctx.deployments.items():
        assert ctx.web3[chain].eth.chain_id == CHAINS[chain]
        safe = ctx.safe(chain)
        assert {a.lower() for a in safe.safe.retrieve_owners()} == {ctx.state.owner.lower()}
        assert safe.safe.retrieve_threshold() == 1
        assert ctx.web3[chain].eth.get_code(safe.trading_strategy_module_address)
        assert safe.trading_strategy_module_address.lower() in {a.lower() for a in safe.safe.retrieve_modules()}, "Saved guard module is disabled"
        assert deployment.asset_managers[0].lower() == ctx.state.owner.lower()
        module = safe.trading_strategy_module
        assert module.functions.isAllowedSender(ctx.state.owner).call()
        assert module.functions.isAllowedAsset(ctx.usdc[chain].address).call()
        assert module.functions.anyAsset().call() is False
        assert module.functions.isAllowedReceiver(safe.safe_address).call()
        for destination in CHAINS:
            if destination != chain:
                assert module.functions.isAllowedCCTPDestination(CHAIN_ID_TO_CCTP_DOMAIN[CHAINS[destination]]).call()


def reconcile_core_entries(ctx: RoundtripContext) -> None:
    """Resolve original entry submissions before spending their possible source funds.

    A receipt with unchanged Core balances remains uncertain. This function
    never retries a Core action; it only recognises an observed completed effect.

    :param ctx: Saved original entry stages and fresh Core reads.
    """
    snapshot = fetch_core_snapshot(ctx)
    for name in ("core_activation", "core_deposit", "core_to_perp", "core_invest"):
        if name not in ctx.state.transactions or ctx.state.steps.get(name, {}).get("complete"):
            continue
        row = ctx.state.steps[name]
        amount = Decimal(row["amount"])
        before = row.get("before", {})
        observed = False
        if name == "core_activation":
            observed = Decimal(snapshot["spot"]) > 0
        elif name == "core_deposit":
            observed = Decimal(snapshot["escrow"]) == 0 and Decimal(snapshot["spot"]) >= Decimal(before["spot"]) + amount
        elif name == "core_to_perp":
            observed = Decimal(snapshot["perp"]) >= Decimal(before["perp"]) + amount
        elif name == "core_invest":
            observed = Decimal(snapshot["equity"]) > Decimal(before["equity"])
        if not observed:
            message = f"{name} Core effect remains uncertain; inspect the original hash and balances before moving funds"
            raise UncertainExecutionError(message)
        row.update(complete=True, after=snapshot)
        ctx.save()


def unwind_hypercore(ctx: RoundtripContext) -> None:
    """Resolve the Core investment before bridging linked USDC back to the satellite.

    :param ctx: Funded or partly invested original run.
    """
    if ctx.state.bridges.get("return_hyperliquid", {}).get("complete"):
        return
    reconcile_core_entries(ctx)
    safe, token = ctx.safe("hyperliquid"), ctx.usdc["hyperliquid"]
    current = fetch_core_snapshot(ctx)
    equity = Decimal(current["equity"])
    if equity:
        if current["locked_until"] and native_datetime_utc_now() < datetime.datetime.fromisoformat(current["locked_until"]):
            raise RoundtripPendingError(f"Hypercore locked until {current['locked_until']} UTC")
        if not ctx.simulation:
            info = HyperliquidVault(ctx.hyper_session, ctx.state.targets["hypercore"]).fetch_info(user=safe.safe_address)
            if info.max_withdrawable < equity:
                message = "Hypercore vault lacks liquidity for a full exit"
                raise RoundtripPendingError(message)
        saved = ctx.state.steps.setdefault("core_exit", {"amount": str(token.convert_to_decimals(token.convert_to_raw(equity)))})
        ctx.save()
        amount = Decimal(saved["amount"])
        core_action(ctx, "core_exit", build_hypercore_withdraw_from_vault_call(safe, ctx.state.targets["hypercore"], token.convert_to_raw(amount)), lambda s: Decimal(s["equity"]) == 0, amount)
    elif "core_invest" in ctx.state.transactions and not ctx.state.steps.get("core_invest", {}).get("complete"):
        message = "Core investment submission remains unresolved; do not withdraw its source funds"
        raise UncertainExecutionError(message)
    current = fetch_core_snapshot(ctx)
    perp = Decimal(current["perp"])
    if perp or "core_to_spot" in ctx.state.transactions:
        row = ctx.state.steps.setdefault("core_to_spot", {"amount": str(perp)})
        amount = Decimal(row["amount"])
        core_action(ctx, "core_to_spot", build_hypercore_transfer_usd_class_call(safe, token.convert_to_raw(amount), False), lambda s: Decimal(s["perp"]) == 0, amount)
    current = fetch_core_snapshot(ctx)
    assert Decimal(current["escrow"]) == 0, "Resolve the existing Core escrow before returning funds"
    spot = Decimal(current["spot"])
    if spot > ctx.settings.max_residual or "core_return" in ctx.state.transactions:
        amount = compute_spot_to_evm_withdrawal_amount(spot, spot)
        amount = token.convert_to_decimals(token.convert_to_raw(amount))
        row = ctx.state.steps.setdefault("core_return", {"amount": str(amount), "evm_before": str(token.fetch_balance_of(safe.safe_address))})
        amount = Decimal(row["amount"])
        if amount <= 0:
            message = "Core spot cannot cover its return bridge fee"
            raise RoundtripPendingError(message)
        core_action(ctx, "core_return", build_hypercore_send_asset_to_evm_call(safe, token.convert_to_raw(amount)), lambda _s: token.fetch_balance_of(safe.safe_address) >= Decimal(row["evm_before"]) + amount, amount)
    bridge(ctx, "return_hyperliquid", "hyperliquid", "arbitrum")


def unwind_lighter(ctx: RoundtripContext) -> None:  # noqa: PLR0914 - Keep the sequential withdrawal evidence visible.
    """Burn shares, request secure withdrawal, claim the exact amount and return it.

    :param ctx: Original Ethereum satellite and persisted API operations.
    """
    if ctx.state.bridges.get("return_ethereum", {}).get("complete"):
        return
    safe, token = ctx.safe("ethereum"), ctx.usdc["ethereum"]
    index = ctx.state.lighter.get("account_index")
    if index is None:
        index = fetch_lighter_account_index(ctx.lighter_session, safe.safe_address)
    if index is None:
        if ctx.state.lighter.get("key_report"):
            message = "Lighter activation deposit is not indexed yet; retain the original deposit"
            raise RoundtripPendingError(message)
        bridge(ctx, "return_ethereum", "ethereum", "arbitrum")
        return
    ctx.state.lighter["account_index"] = index
    ctx.save()
    if not ctx.state.lighter.get("activated"):
        if "lighter_claim" in ctx.state.transactions:
            claim_lighter(ctx, Decimal(ctx.state.steps["lighter_claim_amount"]))
            bridge(ctx, "return_ethereum", "ethereum", "arbitrum")
            return
        # Existing L1 recovery can withdraw without a registered API key.
        account, equity = fetch_execution_account(ctx.lighter_session, index)
        assert not account["shares"] and not account["positions"], "Recover the existing activation key before unwinding positions"
        zk = get_deployed_contract(ctx.web3["ethereum"], "lighter/ZkLighter.json", LIGHTER_L1_CONTRACT)
        if equity.available_balance >= 1:
            row = ctx.state.steps.setdefault("lighter_recovery", {"amount": str(equity.available_balance)})
            send_transaction(ctx, "lighter_recovery_request", "ethereum", safe.transact_via_trading_strategy_module(zk.functions.withdraw(3, 0, token.convert_to_raw(Decimal(row["amount"])))))
        pending = fetch_lighter_pending_balance(ctx.web3["ethereum"], owner=safe.safe_address, usdc=token)
        if not pending:
            message = "Activation recovery withdrawal awaiting L1 settlement"
            raise RoundtripPendingError(message)
        claim_lighter(ctx, pending)
        bridge(ctx, "return_ethereum", "ethereum", "arbitrum")
        return
    # Resolve mint/burn originals before preparing any later mutation.
    for name in ("mint", "burn"):
        op = ctx.state.lighter.get("operations", {}).get(name)
        if op and op["stage"] != "confirmed":
            execute_lighter(ctx, name)
    holdings = tuple(fetch_pool_holdings(ctx.lighter_session, index))
    assert all(h.pool_index == ctx.state.targets["lighter"] for h in holdings), "Unexpected pool holding"
    if any(h.shares for h in holdings):
        pool = fetch_pool_state(ctx.lighter_session, ctx.state.targets["lighter"])
        if sum((h.gross_value for h in holdings), Decimal(0)) > pool.available_balance:
            message = "Lighter pool lacks liquidity for a full exit"
            raise RoundtripPendingError(message)
        execute_lighter(ctx, "burn")
    account, equity = fetch_execution_account(ctx.lighter_session, index)
    assert not any(row["shares_amount"] for row in account["shares"]) and not account["positions"]
    if "withdrawal_baseline" not in ctx.state.lighter:
        history = tuple(fetch_withdrawals(ctx.lighter_session, index, fetch_auth(ctx)))
        assert not any(r["status"] in {"pending", "claimable"} for r in history), "Resolve pre-existing withdrawals"
        delay = fetch_lighter_withdrawal_delay(ctx.lighter_session)
        ctx.state.lighter["withdrawal_delay_seconds"] = delay
        ctx.state.lighter["estimated_retry_at"] = (native_datetime_utc_now() + datetime.timedelta(seconds=delay)).isoformat()
        logger.info("Secure withdrawal current delay estimate: %s seconds (not a claimability guarantee)", delay)
        ctx.state.lighter["withdrawal_safe_before"] = token.fetch_raw_balance_of(safe.safe_address)
        ctx.state.lighter["withdrawal_baseline"] = [str(row["id"]) for row in history]
        ctx.state.lighter["withdrawal_amount"] = str(token.convert_to_decimals(token.convert_to_raw(equity.available_balance)))
        ctx.save()
    amount = Decimal(ctx.state.lighter["withdrawal_amount"])
    if amount < 1:
        if equity.available_balance:
            message = "Lighter collateral is below secure-withdrawal minimum"
            raise RoundtripPendingError(message)
        bridge(ctx, "return_ethereum", "ethereum", "arbitrum")
        return
    execute_lighter(ctx, "withdraw", amount)
    if "withdrawal_id" not in ctx.state.lighter:
        rows = [row for row in fetch_withdrawals(ctx.lighter_session, index, fetch_auth(ctx)) if str(row["id"]) not in ctx.state.lighter["withdrawal_baseline"]]
        if not rows:
            message = "Confirmed secure withdrawal is not indexed yet"
            raise RoundtripPendingError(message)
        assert len(rows) == 1 and rows[0]["type"] == "secure" and int(rows[0]["asset_id"]) == LIGHTER_USDC_ASSET_ID, "Ambiguous withdrawal history"
        ctx.state.lighter["withdrawal_id"] = str(rows[0]["id"])
        ctx.save()
    row = fetch_matching_withdrawal(ctx.lighter_session, index, ctx.state.lighter["withdrawal_id"], fetch_auth(ctx))
    assert row is not None
    if "lighter_claim" not in ctx.state.transactions:
        if row["status"] == "completed":
            claimed = token.convert_to_raw(Decimal(row["amount"]))
            assert 0 < Decimal(row["amount"]) <= amount
            baseline = ctx.state.lighter["withdrawal_safe_before"]
            assert token.fetch_raw_balance_of(safe.safe_address) - baseline == claimed, "External claim does not match saved Safe balance evidence"
            assert fetch_lighter_pending_balance(ctx.web3["ethereum"], owner=safe.safe_address, usdc=token) == 0
            ctx.state.steps["lighter_external_claim"] = {"withdrawal_id": row["id"], "amount": row["amount"]}
            ctx.save()
            bridge(ctx, "return_ethereum", "ethereum", "arbitrum")
            return
        if row["status"] != "claimable":
            raise RoundtripPendingError(f"Secure withdrawal {row['id']} is {row['status']}")
        assert 0 < Decimal(row["amount"]) <= amount
        claim_lighter(ctx, Decimal(row["amount"]))
    else:
        claim_lighter(ctx, Decimal(ctx.state.steps["lighter_claim_amount"]))
    bridge(ctx, "return_ethereum", "ethereum", "arbitrum")


def fetch_received_usdc(ctx: RoundtripContext, chain: str, tx_hash: str, recipient: HexAddress) -> Decimal:
    """Read actual USDC proceeds from a confirmed transaction's transfer logs.

    Wallet balance changes can include unrelated transfers between invocations.
    Receipt logs bind these proceeds to the saved claim instead.

    :param ctx: Run with native USDC metadata.
    :param chain: Execution chain.
    :param tx_hash: Original confirmed claim hash.
    :param recipient: Expected recipient of the claim.
    :return: Human-readable USDC credited by this transaction.
    """
    token = ctx.usdc[chain]
    receipt = ctx.web3[chain].eth.get_transaction_receipt(tx_hash)
    transfers = token.contract.events.Transfer().process_receipt(receipt, errors=DISCARD)
    credited = sum(row["args"]["value"] for row in transfers if row["address"].lower() == token.address.lower() and row["args"]["to"].lower() == recipient.lower())
    return token.convert_to_decimals(credited)


def claim_lighter(ctx: RoundtripContext, amount: Decimal) -> None:
    """Claim actual settled USDC through the existing guarded withdrawal selector.

    :param ctx: Ethereum custody context.
    :param amount: Actual claimable amount, not the original withdrawal estimate.
    """
    ctx.state.steps.setdefault("lighter_claim_amount", str(amount))
    ctx.save()
    zk = get_deployed_contract(ctx.web3["ethereum"], "lighter/ZkLighter.json", LIGHTER_L1_CONTRACT)
    safe = ctx.safe("ethereum")
    token = ctx.usdc["ethereum"]
    tx_hash = send_transaction(ctx, "lighter_claim", "ethereum", safe.transact_via_trading_strategy_module(zk.functions.withdrawPendingBalance(safe.safe_address, 3, token.convert_to_raw(amount))))
    credited = fetch_received_usdc(ctx, "ethereum", tx_hash, safe.safe_address)
    assert credited == amount, "Claim receipt does not establish the actual Safe credit"
    ctx.state.steps["lighter_claim_credited_raw"] = token.convert_to_raw(credited)
    ctx.save()


def redeem(ctx: RoundtripContext) -> None:
    """Redeem through Lagoon only after external holdings and receivables resolve.

    :param ctx: Hub with the actual recovered cash balance.
    """
    vault = ctx.safe("arbitrum")
    owner, token = ctx.state.owner, ctx.usdc["arbitrum"]
    assert isinstance(vault, LagoonVault)
    if "redemption" not in ctx.state.steps:
        shares = vault.share_token.fetch_raw_balance_of(owner)
        assert shares > 0, "Recover pending Lagoon shares/requests before final redemption"
        assert vault.share_token.contract.functions.totalSupply().call() == shares, "Unexpected other shareholder"
        assert vault.get_flow_manager().fetch_pending_deposit("latest") == 0
        assert token.fetch_raw_balance_of(vault.silo_address) == 0
        ctx.state.steps["redemption"] = {"shares": shares, "nav": str(token.fetch_balance_of(vault.safe_address)), "wallet_before": str(token.fetch_balance_of(owner))}
        ctx.save()
    row = ctx.state.steps["redemption"]
    send_transaction(ctx, "redemption_approve", "arbitrum", vault.share_token.approve(vault.address, vault.share_token.convert_to_decimals(row["shares"])))
    send_transaction(ctx, "redemption_request", "arbitrum", vault.request_redeem(owner, row["shares"], check_enough_token=False))
    nav = Decimal(row["nav"])
    send_transaction(ctx, "redemption_nav", "arbitrum", vault.post_new_valuation(nav))
    send_transaction(ctx, "redemption_settle", "arbitrum", vault.settle_via_trading_strategy_module(nav))
    claim_hash = send_transaction(ctx, "redemption_claim", "arbitrum", vault.finalise_redeem(owner, raw_amount=row["shares"]))
    assert vault.share_token.fetch_raw_balance_of(owner) == 0
    assert vault.vault_contract.functions.maxRedeem(owner).call() == 0
    assert vault.get_flow_manager().fetch_pending_redemption("latest") == 0
    assert token.fetch_raw_balance_of(vault.safe_address) == 0
    assert token.fetch_raw_balance_of(vault.address) == 0 and token.fetch_raw_balance_of(vault.silo_address) == 0
    claimed = fetch_received_usdc(ctx, "arbitrum", claim_hash, owner)
    ctx.state.results.update(claimed_usdc=str(claimed), final_usdc=str(token.fetch_balance_of(owner)), final_native={c: str(w.eth.get_balance(owner)) for c, w in ctx.web3.items()})
    ctx.state.outcome = "redeemed_with_dust" if Decimal(ctx.state.results["residual_usdc"]) else "completed"
    ctx.save()


def unwind_and_redeem(ctx: RoundtripContext) -> None:
    """Reconcile independent exit legs and return all recoverable capital to the investor.

    :param ctx: Saved run; repeated terminal invocation submits nothing.
    """
    if ctx.state.outcome in TERMINAL:
        return
    assert all(w.address.lower() == ctx.state.owner.lower() for w in ctx.wallets.values()), "Wrong deployer key"
    verify_bindings(ctx)
    restore_big_blocks(ctx)
    assert all(ctx.web3[c].eth.get_balance(ctx.state.owner) > 0 for c in CHAINS), "Refill unwind gas"
    if not ctx.deployments or "subscription_request" not in ctx.state.transactions:
        ctx.state.outcome = "unfunded"
        ctx.save()
        return
    if not ctx.state.steps.get("subscribed"):
        subscribe(ctx)
    for name, row in ctx.state.bridges.copy().items():
        if not row.get("complete") and name.startswith("out_"):
            bridge(ctx, name, row["source"], row["destination"])
    logger.info("Phase 6: close positions and settle protocol withdrawals")
    delayed, uncertain = [], []
    for name, function in [("hyperliquid", unwind_hypercore), ("ethereum", unwind_lighter)]:
        if name not in ctx.deployments:
            continue
        try:
            function(ctx)
        except (RoundtripPendingError, TimeoutError) as error:
            logger.warning("%s pending: %s", name, error)
            delayed.append(str(error))
        except UncertainExecutionError as error:
            uncertain.append(f"{name}: {error}")
    if uncertain:
        ctx.state.outcome = "uncertain"
        ctx.save()
        raise UncertainExecutionError("; ".join(uncertain))
    if delayed:
        ctx.state.outcome = "pending"
        ctx.save()
        raise RoundtripPendingError("; ".join(delayed))
    logger.info("Phase 7: confirm satellite returns and reconcile residuals")
    core = fetch_core_snapshot(ctx)
    assert Decimal(core["equity"]) == 0 and Decimal(core["perp"]) == 0 and Decimal(core["escrow"]) == 0
    residual = Decimal(core["spot"]) + sum((ctx.usdc[c].fetch_balance_of(ctx.safe(c).safe_address) for c in ("ethereum", "hyperliquid")), Decimal(0))
    if ctx.state.lighter.get("account_index") is not None:
        account, equity = fetch_execution_account(ctx.lighter_session, ctx.state.lighter["account_index"])
        assert not account["positions"] and not any(r["shares_amount"] for r in account["shares"])
        assert equity.available_balance == 0 and fetch_lighter_pending_balance(ctx.web3["ethereum"], owner=ctx.safe("ethereum").safe_address, usdc=ctx.usdc["ethereum"]) == 0
    assert all(row.get("complete") for row in ctx.state.bridges.values()), "Unsettled CCTP receivable"
    if residual > ctx.settings.max_residual:
        raise RoundtripPendingError(f"Residual {residual} USDC exceeds disclosed dust budget")
    ctx.state.results["residual_usdc"] = str(residual)
    ctx.state.results["bridge_fees_usdc"] = str(sum((ctx.usdc[row["source"]].convert_to_decimals(row.get("actual_fee_raw", 0)) for row in ctx.state.bridges.values()), Decimal(0)))
    ctx.save()
    logger.info("Phase 8: redeem recovered cash through Lagoon (disclosed cash dust %s)", residual)
    redeem(ctx)
