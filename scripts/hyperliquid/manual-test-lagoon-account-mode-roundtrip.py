"""Manual Lagoon + Hyperliquid account-mode round-trip investigation.

Deploys a fresh Lagoon vault on HyperEVM, funds it through the real Lagoon
deposit flow, then runs the Safe through a direct HyperCore round-trip one
transaction at a time:

1. Safe EVM USDC approval to CoreDepositWallet
2. Safe EVM USDC -> HyperCore spot
3. Safe spot -> Safe perp
4. Safe perp -> Safe spot
5. Safe spot -> Safe EVM
6. Redeem Lagoon shares back to the operator
7. Sweep any residual Safe-side EVM USDC back to the operator

The script prints full operator and Safe state snapshots before the round-trip
and after each leg so unified / standard account-mode differences can be
inspected manually.

Environment variables
---------------------

- ``NETWORK``: ``mainnet`` (default) or ``testnet``
- ``HYPERCORE_WRITER_TEST_PRIVATE_KEY``: Operator private key
- ``USDC_AMOUNT``: Round-trip deposit amount in human units (default: ``1``)
- ``WITHDRAW_USDC_AMOUNT``: Final spot-to-EVM withdrawal amount in human units
  (default: same as ``USDC_AMOUNT``)
- ``ACTIVATION_AMOUNT``: HyperCore activation amount in human units
  (default: ``2`` on mainnet, ``5`` on testnet)
- ``ACTIVATION_TIMEOUT``: Activation wait timeout in seconds
  (default: ``60`` on mainnet, ``180`` on testnet)
- ``EXISTING_VAULT_ADDRESS``: Optional existing Lagoon vault to reuse; when set,
  the script skips deployment
- ``EXISTING_MODULE_ADDRESS``: Optional TradingStrategyModuleV0 for the reused
  vault Safe; when omitted, the script auto-detects it from enabled Safe modules
- ``LOG_LEVEL``: Logging level (default: ``info``)
- ``JSON_RPC_HYPERLIQUID``: HyperEVM mainnet RPC URL
- ``JSON_RPC_HYPERLIQUID_TESTNET``: HyperEVM testnet RPC URL

Usage::

    source .local-test.env && \\
    NETWORK=mainnet USDC_AMOUNT=1 \\
        poetry run python scripts/hyperliquid/manual-test-lagoon-account-mode-roundtrip.py
"""

import logging
import os
import secrets
import time
from dataclasses import dataclass
from decimal import Decimal

import requests
from eth_account import Account
from eth_typing import HexAddress, HexStr
from safe_eth.safe.safe import Safe
from tabulate import tabulate
from web3 import Web3
from web3.contract.contract import ContractFunction
from web3.contract.contract import Contract
from web3.exceptions import ContractLogicError

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner import resolve_trading_strategy_module
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LAGOON_BEACON_PROXY_FACTORIES,
    LagoonConfig,
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
)
from eth_defi.erc_4626.vault_protocol.lagoon.testing import (
    fund_lagoon_vault,
    redeem_vault_shares,
)
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gas import estimate_gas_price
from eth_defi.hotwallet import HotWallet
from eth_defi.hyperliquid.api import (
    HyperliquidSession,
    SpotClearinghouseState,
    UserVaultEquity,
    fetch_perp_clearinghouse_state,
    fetch_spot_clearinghouse_state,
    fetch_user_abstraction_mode,
    fetch_user_vault_equities,
)
from eth_defi.hyperliquid.core_writer import (
    build_hypercore_approve_deposit_wallet_call,
    build_hypercore_deposit_for_spot_call,
    build_hypercore_deposit_to_spot_call,
    build_hypercore_send_asset_to_evm_call,
    build_hypercore_transfer_usd_class_call,
)
from eth_defi.hyperliquid.evm_escrow import is_account_activated, wait_for_evm_escrow_clear
from eth_defi.hyperliquid.session import (
    HYPERLIQUID_API_URL,
    HYPERLIQUID_TESTNET_API_URL,
    create_hyperliquid_session,
)
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.safe.execute import execute_safe_tx
from eth_defi.safe.safe_compat import create_safe_ethereum_client
from eth_defi.token import USDC_NATIVE_TOKEN, TokenDetails, fetch_erc20_details
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

HYPERLIQUID_TESTNET_RPC = "https://rpc.hyperliquid-testnet.xyz/evm"
BALANCE_TOLERANCE = Decimal("0.02")
POLL_INTERVAL = 2.0
BALANCE_TIMEOUT = 60.0
SAFE_GAS_LIMIT = 500_000

#: Default HyperCore vault address per network.
#:
#: Used for Hypercore whitelisting when deploying the Lagoon guard setup.
DEFAULT_HYPERCORE_VAULTS = {
    "mainnet": HexAddress(HexStr("0xdfc24b077bc1425ad1dea75bcb6f8158e10df303")),
    "testnet": HexAddress(HexStr("0xa15099a30bbf2e68942d6f4c43d70d04faeab0a0")),
}


@dataclass(slots=True)
class AccountSnapshot:
    """Capture the current operator or Safe account state."""

    #: Human-readable row label.
    label: str

    #: EVM address being inspected.
    address: HexAddress

    #: Hyperliquid account abstraction mode.
    abstraction_mode: str

    #: Native HYPE balance on HyperEVM.
    evm_hype_balance: Decimal

    #: USDC balance on HyperEVM.
    evm_usdc_balance: Decimal

    #: Total HyperCore spot USDC.
    spot_total_usdc: Decimal

    #: Free HyperCore spot USDC.
    spot_free_usdc: Decimal

    #: HyperCore perp withdrawable balance.
    perp_withdrawable: Decimal

    #: HyperCore perp account value.
    perp_account_value: Decimal

    #: HyperCore EVM escrow USDC.
    evm_escrow_usdc: Decimal

    #: Lagoon share balance held by this address.
    lagoon_share_balance: Decimal

    #: HyperCore vault positions for this address.
    vault_positions: tuple[UserVaultEquity, ...]

    @property
    def vault_equity_total(self) -> Decimal:
        """Return the summed vault equity."""
        return sum((position.equity for position in self.vault_positions), start=Decimal(0))


def _get_spot_usdc_balances(spot_state: SpotClearinghouseState) -> tuple[Decimal, Decimal]:
    """Extract total and free spot USDC from HyperCore spot state."""
    for balance in spot_state.balances:
        if balance.coin == "USDC":
            return balance.total, balance.total - balance.hold
    return Decimal(0), Decimal(0)


def _get_escrow_usdc_balance(spot_state: SpotClearinghouseState) -> Decimal:
    """Extract HyperCore EVM escrow USDC total."""
    for escrow in spot_state.evm_escrows:
        if escrow.coin == "USDC":
            return escrow.total
    return Decimal(0)


def _is_within_tolerance(left: Decimal, right: Decimal) -> bool:
    """Check whether two decimal balances are close enough for diagnostics."""
    return abs(left - right) <= BALANCE_TOLERANCE


def _fetch_user_mode(session: HyperliquidSession, user: str) -> str:
    """Read userAbstraction mode, tolerating missing accounts for diagnostics."""
    try:
        return fetch_user_abstraction_mode(session, user)
    except (requests.RequestException, AssertionError) as exc:
        logger.warning("Could not fetch account mode for %s: %s", user, exc)
        return f"error:{exc.__class__.__name__}"


def _fetch_account_snapshot(
    web3: Web3,
    session: HyperliquidSession,
    usdc: TokenDetails,
    share_token: TokenDetails,
    label: str,
    address: str,
) -> AccountSnapshot:
    """Read the current EVM and HyperCore state for one address."""
    spot_state = fetch_spot_clearinghouse_state(session, user=address)
    perp_state = fetch_perp_clearinghouse_state(session, user=address)
    spot_total_usdc, spot_free_usdc = _get_spot_usdc_balances(spot_state)
    evm_escrow_usdc = _get_escrow_usdc_balance(spot_state)
    vault_positions = tuple(fetch_user_vault_equities(session, user=address))
    return AccountSnapshot(
        label=label,
        address=HexAddress(HexStr(Web3.to_checksum_address(address))),
        abstraction_mode=_fetch_user_mode(session, address),
        evm_hype_balance=Decimal(web3.eth.get_balance(address)) / Decimal(10**18),
        evm_usdc_balance=usdc.fetch_balance_of(address),
        spot_total_usdc=spot_total_usdc,
        spot_free_usdc=spot_free_usdc,
        perp_withdrawable=perp_state.withdrawable,
        perp_account_value=perp_state.margin_summary.account_value,
        evm_escrow_usdc=evm_escrow_usdc,
        lagoon_share_balance=share_token.fetch_balance_of(address),
        vault_positions=vault_positions,
    )


def _print_snapshot(stage: str, snapshots: list[AccountSnapshot]) -> None:
    """Render operator and Safe state as a table."""
    rows = []
    for snapshot in snapshots:
        rows.append(
            [
                snapshot.label,
                snapshot.address,
                snapshot.abstraction_mode,
                f"{snapshot.evm_hype_balance:,.6f}",
                f"{snapshot.evm_usdc_balance:,.6f}",
                f"{snapshot.spot_total_usdc:,.6f}",
                f"{snapshot.spot_free_usdc:,.6f}",
                f"{snapshot.perp_withdrawable:,.6f}",
                f"{snapshot.perp_account_value:,.6f}",
                f"{snapshot.evm_escrow_usdc:,.6f}",
                f"{snapshot.lagoon_share_balance:,.6f}",
                f"{snapshot.vault_equity_total:,.6f}",
                len(snapshot.vault_positions),
            ]
        )

    print(f"\n{stage}")
    print(
        tabulate(
            rows,
            headers=[
                "Account",
                "Address",
                "Mode",
                "EVM HYPE",
                "EVM USDC",
                "Spot total",
                "Spot free",
                "Perp withdrawable",
                "Perp account",
                "Escrow",
                "Lagoon shares",
                "Vault equity",
                "Vaults",
            ],
            tablefmt="simple",
        )
    )

    for snapshot in snapshots:
        if not snapshot.vault_positions:
            continue
        vault_rows = [
            [
                position.vault_address,
                f"{position.equity:,.6f}",
                position.locked_until.isoformat(),
            ]
            for position in snapshot.vault_positions
        ]
        print(f"\n{snapshot.label} HyperCore vault positions:")
        print(tabulate(vault_rows, headers=["Vault", "Equity", "Locked until"], tablefmt="simple"))


def _wait_for_evm_usdc_balance(
    usdc: TokenDetails,
    address: str,
    expected_balance: Decimal,
    timeout: float = BALANCE_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
) -> None:
    """Wait until the EVM USDC balance reaches the expected level."""
    deadline = time.time() + timeout
    while True:
        balance = usdc.fetch_balance_of(address)
        if _is_within_tolerance(balance, expected_balance):
            return
        if time.time() >= deadline:
            raise TimeoutError(f"Timed out waiting for EVM USDC balance {expected_balance} for {address}, last balance was {balance}")
        time.sleep(poll_interval)


def _wait_for_spot_free_balance(
    session: HyperliquidSession,
    user: str,
    expected_balance: Decimal,
    timeout: float = BALANCE_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
) -> None:
    """Wait until the HyperCore free spot USDC balance reaches the expected level."""
    deadline = time.time() + timeout
    while True:
        spot_state = fetch_spot_clearinghouse_state(session, user=user)
        _spot_total, spot_free = _get_spot_usdc_balances(spot_state)
        if _is_within_tolerance(spot_free, expected_balance):
            return
        if time.time() >= deadline:
            raise TimeoutError(f"Timed out waiting for free spot USDC balance {expected_balance} for {user}, last balance was {spot_free}")
        time.sleep(poll_interval)


def _wait_for_perp_withdrawable_balance(
    session: HyperliquidSession,
    user: str,
    expected_balance: Decimal,
    timeout: float = BALANCE_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
) -> None:
    """Wait until the HyperCore perp withdrawable balance reaches the expected level."""
    deadline = time.time() + timeout
    while True:
        perp_state = fetch_perp_clearinghouse_state(session, user=user)
        if _is_within_tolerance(perp_state.withdrawable, expected_balance):
            return
        if time.time() >= deadline:
            raise TimeoutError(f"Timed out waiting for perp withdrawable balance {expected_balance} for {user}, last balance was {perp_state.withdrawable}, account value was {perp_state.margin_summary.account_value}")
        time.sleep(poll_interval)


def _wait_for_activation(
    web3: Web3,
    user: str,
    timeout: float,
    poll_interval: float = POLL_INTERVAL,
) -> None:
    """Wait until the HyperCore account is activated."""
    deadline = time.time() + timeout
    while True:
        if is_account_activated(web3, user=user):
            return
        if time.time() >= deadline:
            raise TimeoutError(f"Timed out waiting for HyperCore activation for {user}")
        time.sleep(poll_interval)


def _broadcast_step(
    web3: Web3,
    deployer: HotWallet,
    bound_func: ContractFunction,
    label: str,
    tx_rows: list[list[str | int]],
    gas_limit: int = SAFE_GAS_LIMIT,
) -> str | None:
    """Broadcast one transaction and record its result."""
    try:
        tx_hash = deployer.transact_and_broadcast_with_contract(bound_func, gas_limit=gas_limit)
        receipt = assert_transaction_success_with_explanation(web3, tx_hash)
        tx_hash_hex = tx_hash.hex()
        tx_rows.append([label, tx_hash_hex, receipt["gasUsed"], "ok"])
        logger.info("%s succeeded: %s", label, tx_hash_hex)
        return tx_hash_hex
    except (TransactionAssertionError, ContractLogicError, ValueError) as exc:
        logger.warning("%s failed: %s", label, exc)
        tx_rows.append([label, "-", "-", f"failed: {exc}"])
        return None


def _compare_expected_delta(
    baseline: AccountSnapshot,
    current: AccountSnapshot,
    phase_name: str,
    expected_evm_delta: Decimal,
    expected_spot_free_delta: Decimal,
    expected_perp_delta: Decimal,
) -> list[str]:
    """Compare current Safe balances against standard-account expectations."""
    issues = []
    actual_evm_delta = current.evm_usdc_balance - baseline.evm_usdc_balance
    actual_spot_free_delta = current.spot_free_usdc - baseline.spot_free_usdc
    actual_perp_delta = current.perp_withdrawable - baseline.perp_withdrawable

    if not _is_within_tolerance(actual_evm_delta, expected_evm_delta):
        issues.append(f"{phase_name}: expected Safe EVM USDC delta {expected_evm_delta}, observed {actual_evm_delta}")
    if not _is_within_tolerance(actual_spot_free_delta, expected_spot_free_delta):
        issues.append(f"{phase_name}: expected Safe spot free delta {expected_spot_free_delta}, observed {actual_spot_free_delta}")
    if not _is_within_tolerance(actual_perp_delta, expected_perp_delta):
        issues.append(f"{phase_name}: expected Safe perp withdrawable delta {expected_perp_delta}, observed {actual_perp_delta}")
    return issues


def _collect_stage_snapshots(
    web3: Web3,
    session: HyperliquidSession,
    usdc: TokenDetails,
    share_token: TokenDetails,
    operator_address: str,
    safe_address: str,
) -> tuple[AccountSnapshot, AccountSnapshot]:
    """Read operator and Safe snapshots together."""
    operator_snapshot = _fetch_account_snapshot(
        web3=web3,
        session=session,
        usdc=usdc,
        share_token=share_token,
        label="Operator",
        address=operator_address,
    )
    safe_snapshot = _fetch_account_snapshot(
        web3=web3,
        session=session,
        usdc=usdc,
        share_token=share_token,
        label="Safe",
        address=safe_address,
    )
    return operator_snapshot, safe_snapshot


def _sweep_safe_usdc_to_operator(
    lagoon_vault: LagoonVault,
    deployer: HotWallet,
) -> str | None:
    """Transfer any residual Safe-side EVM USDC back to the operator."""
    web3 = lagoon_vault.web3
    safe_balance = lagoon_vault.underlying_token.fetch_balance_of(lagoon_vault.safe_address)
    if safe_balance <= 0:
        return None

    raw_amount = lagoon_vault.underlying_token.convert_to_raw(safe_balance)
    transfer_data = lagoon_vault.underlying_token.contract.functions.transfer(
        deployer.address,
        raw_amount,
    ).build_transaction({"from": lagoon_vault.safe_address})["data"]

    ethereum_client = create_safe_ethereum_client(web3)
    safe = Safe(lagoon_vault.safe_address, ethereum_client)
    safe_tx = safe.build_multisig_tx(
        lagoon_vault.underlying_token.address,
        0,
        bytes.fromhex(transfer_data[2:]),
    )
    safe_tx.sign(deployer.private_key.hex())

    deployer.sync_nonce(web3)
    tx_hash, _tx = execute_safe_tx(
        safe_tx,
        tx_sender_private_key=deployer.private_key.hex(),
        tx_gas=100_000,
        tx_nonce=deployer.allocate_nonce(),
        gas_fee=estimate_gas_price(web3),
    )
    assert_transaction_success_with_explanation(web3, tx_hash)
    return tx_hash.hex()


def _load_existing_lagoon_vault(
    web3: Web3,
    vault_address: HexAddress,
    module_address: HexAddress | None,
) -> tuple[LagoonVault, Contract]:
    """Load an existing Lagoon vault and its enabled TradingStrategyModuleV0."""
    vault = create_vault_instance(
        web3,
        vault_address,
        features={ERC4626Feature.lagoon_like},
        default_block_identifier="latest",
        require_denomination_token=True,
    )
    if not isinstance(vault, LagoonVault):
        raise RuntimeError(f"Existing vault is not a Lagoon vault: {vault_address}")

    resolved_module_address = module_address or resolve_trading_strategy_module(web3, vault.safe_address)
    if resolved_module_address is None:
        raise RuntimeError(f"Could not resolve TradingStrategyModuleV0 for Safe {vault.safe_address}")

    vault.trading_strategy_module_address = resolved_module_address
    return vault, vault.trading_strategy_module


def main() -> None:
    """Run the manual Lagoon / Hyperliquid account-mode round-trip."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    network = os.environ.get("NETWORK", "mainnet").lower()
    assert network in {"mainnet", "testnet"}, f"NETWORK must be 'mainnet' or 'testnet', got {network!r}"

    if network == "mainnet":
        json_rpc_url = os.environ.get("JSON_RPC_HYPERLIQUID")
        assert json_rpc_url, "JSON_RPC_HYPERLIQUID environment variable required for mainnet"
        api_url = HYPERLIQUID_API_URL
        activation_amount_human = Decimal(os.environ.get("ACTIVATION_AMOUNT", "2"))
        activation_timeout = float(os.environ.get("ACTIVATION_TIMEOUT", "60"))
    else:
        json_rpc_url = os.environ.get("JSON_RPC_HYPERLIQUID_TESTNET", HYPERLIQUID_TESTNET_RPC)
        api_url = HYPERLIQUID_TESTNET_API_URL
        activation_amount_human = Decimal(os.environ.get("ACTIVATION_AMOUNT", "5"))
        activation_timeout = float(os.environ.get("ACTIVATION_TIMEOUT", "180"))

    private_key = os.environ.get("HYPERCORE_WRITER_TEST_PRIVATE_KEY")
    assert private_key, "HYPERCORE_WRITER_TEST_PRIVATE_KEY environment variable required"

    roundtrip_amount_human = Decimal(os.environ.get("USDC_AMOUNT", "1"))
    withdraw_amount_human = Decimal(os.environ.get("WITHDRAW_USDC_AMOUNT", str(roundtrip_amount_human)))
    existing_vault_address = os.environ.get("EXISTING_VAULT_ADDRESS")
    existing_module_address = os.environ.get("EXISTING_MODULE_ADDRESS")
    total_safe_funding_human = roundtrip_amount_human if existing_vault_address else roundtrip_amount_human + activation_amount_human
    tx_rows: list[list[str | int]] = []
    issues: list[str] = []

    web3 = create_multi_provider_web3(json_rpc_url, default_http_timeout=(3, 500.0))
    session = create_hyperliquid_session(api_url=api_url)

    deployer_account = Account.from_key(private_key)
    deployer = HotWallet(deployer_account)
    deployer.sync_nonce(web3)

    usdc = fetch_erc20_details(web3, USDC_NATIVE_TOKEN[web3.eth.chain_id])
    roundtrip_amount_raw = usdc.convert_to_raw(roundtrip_amount_human)
    withdraw_amount_raw = usdc.convert_to_raw(withdraw_amount_human)
    activation_amount_raw = usdc.convert_to_raw(activation_amount_human)

    operator_hype_balance = Decimal(web3.eth.get_balance(deployer.address)) / Decimal(10**18)
    operator_usdc_balance = usdc.fetch_balance_of(deployer.address)
    assert operator_hype_balance >= Decimal("0.1"), f"Operator {deployer.address} needs at least 0.1 HYPE for deployment and diagnostics, has {operator_hype_balance}"
    assert operator_usdc_balance >= total_safe_funding_human, f"Operator {deployer.address} needs at least {total_safe_funding_human} USDC, has {operator_usdc_balance}"
    assert roundtrip_amount_human > 0, "USDC_AMOUNT must be positive"
    assert withdraw_amount_human > 0, "WITHDRAW_USDC_AMOUNT must be positive"
    assert withdraw_amount_human <= roundtrip_amount_human, f"WITHDRAW_USDC_AMOUNT {withdraw_amount_human} exceeds USDC_AMOUNT {roundtrip_amount_human}"

    logger.info("Connected to chain %d, block %d", web3.eth.chain_id, web3.eth.block_number)
    logger.info("Operator: %s", deployer.address)
    logger.info("Round-trip deposit amount: %s USDC", roundtrip_amount_human)
    logger.info("Round-trip withdraw amount: %s USDC", withdraw_amount_human)
    logger.info("Activation amount: %s USDC", activation_amount_human)

    if existing_vault_address:
        lagoon_vault, module = _load_existing_lagoon_vault(
            web3=web3,
            vault_address=Web3.to_checksum_address(existing_vault_address),
            module_address=Web3.to_checksum_address(existing_module_address) if existing_module_address else None,
        )
        share_token = lagoon_vault.share_token
        deployment_label = "Reused deployment"
    else:
        from_the_scratch = web3.eth.chain_id not in LAGOON_BEACON_PROXY_FACTORIES
        assert not (from_the_scratch and network == "mainnet"), "Mainnet HyperEVM should use the deployed Lagoon factory"

        config = LagoonConfig(
            parameters=LagoonDeploymentParameters(
                underlying=usdc.address,
                name="Hyperliquid account mode manual test",
                symbol="HLMODE",
            ),
            asset_manager=None,
            asset_managers=[deployer.address],
            safe_owners=[deployer.address],
            safe_threshold=1,
            any_asset=False,
            hypercore_vaults=[DEFAULT_HYPERCORE_VAULTS[network]],
            safe_salt_nonce=secrets.randbelow(1001) if not from_the_scratch else None,
            from_the_scratch=from_the_scratch,
            use_forge=from_the_scratch,
            between_contracts_delay_seconds=8.0,
        )

        deploy_info = deploy_automated_lagoon_vault(
            web3=web3,
            deployer=deployer,
            config=config,
        )
        lagoon_vault = deploy_info.vault
        module = deploy_info.trading_strategy_module
        share_token = lagoon_vault.share_token
        deployment_label = "Deployment"

    print(f"\n{deployment_label}")
    print(
        tabulate(
            [
                ["Vault", lagoon_vault.vault_address],
                ["Safe", lagoon_vault.safe_address],
                ["Module", module.address],
                ["Network", network],
                ["Chain ID", web3.eth.chain_id],
            ],
            tablefmt="simple",
        )
    )

    deployer.sync_nonce(web3)
    fund_lagoon_vault(
        web3=web3,
        vault_address=lagoon_vault.vault_address,
        asset_manager=deployer.address,
        test_account_with_balance=deployer.address,
        trading_strategy_module_address=module.address,
        amount=total_safe_funding_human,
        nav=Decimal(0),
        hot_wallet=deployer,
    )

    operator_snapshot, safe_snapshot = _collect_stage_snapshots(
        web3=web3,
        session=session,
        usdc=usdc,
        share_token=share_token,
        operator_address=deployer.address,
        safe_address=lagoon_vault.safe_address,
    )
    _print_snapshot("Baseline snapshot before activation", [operator_snapshot, safe_snapshot])

    if not is_account_activated(web3, user=lagoon_vault.safe_address):
        activation_approve_tx = _broadcast_step(
            web3,
            deployer,
            build_hypercore_approve_deposit_wallet_call(lagoon_vault, activation_amount_raw),
            "Activation approve",
            tx_rows,
        )
        activation_deposit_tx = _broadcast_step(
            web3,
            deployer,
            build_hypercore_deposit_for_spot_call(
                lagoon_vault,
                activation_amount_raw,
                destination=lagoon_vault.safe_address,
            ),
            "Activation depositFor",
            tx_rows,
        )
        if activation_approve_tx is None or activation_deposit_tx is None:
            message = "Could not activate the Safe on HyperCore"
            raise RuntimeError(message)
        _wait_for_activation(web3, lagoon_vault.safe_address, timeout=activation_timeout)
        wait_for_evm_escrow_clear(
            session,
            user=lagoon_vault.safe_address,
            timeout=activation_timeout,
            poll_interval=POLL_INTERVAL,
        )

        operator_snapshot, safe_snapshot = _collect_stage_snapshots(
            web3=web3,
            session=session,
            usdc=usdc,
            share_token=share_token,
            operator_address=deployer.address,
            safe_address=lagoon_vault.safe_address,
        )
        _print_snapshot("Snapshot after activation", [operator_snapshot, safe_snapshot])

    roundtrip_baseline = safe_snapshot
    if safe_snapshot.evm_usdc_balance < roundtrip_amount_human:
        issues.append(f"Safe EVM USDC balance {safe_snapshot.evm_usdc_balance} is below round-trip amount {roundtrip_amount_human}")

    approve_tx = _broadcast_step(
        web3,
        deployer,
        build_hypercore_approve_deposit_wallet_call(lagoon_vault, roundtrip_amount_raw),
        "Round-trip approve",
        tx_rows,
    )
    if approve_tx is None:
        issues.append("Round-trip approve failed; skipping HyperCore legs")

    phase1_safe_snapshot = roundtrip_baseline
    if approve_tx is not None:
        phase1_tx = _broadcast_step(
            web3,
            deployer,
            build_hypercore_deposit_to_spot_call(lagoon_vault, roundtrip_amount_raw),
            "Phase 1: EVM -> spot",
            tx_rows,
        )
        if phase1_tx is not None:
            try:
                wait_for_evm_escrow_clear(session, user=lagoon_vault.safe_address, timeout=BALANCE_TIMEOUT, poll_interval=POLL_INTERVAL)
                _wait_for_evm_usdc_balance(
                    usdc,
                    lagoon_vault.safe_address,
                    roundtrip_baseline.evm_usdc_balance - roundtrip_amount_human,
                )
                _wait_for_spot_free_balance(
                    session,
                    lagoon_vault.safe_address,
                    roundtrip_baseline.spot_free_usdc + roundtrip_amount_human,
                )
            except TimeoutError as exc:
                issues.append(f"Phase 1 observation failed: {exc}")

            operator_snapshot, phase1_safe_snapshot = _collect_stage_snapshots(
                web3=web3,
                session=session,
                usdc=usdc,
                share_token=share_token,
                operator_address=deployer.address,
                safe_address=lagoon_vault.safe_address,
            )
            _print_snapshot("Snapshot after phase 1", [operator_snapshot, phase1_safe_snapshot])
            issues.extend(
                _compare_expected_delta(
                    roundtrip_baseline,
                    phase1_safe_snapshot,
                    "Phase 1",
                    expected_evm_delta=-roundtrip_amount_human,
                    expected_spot_free_delta=roundtrip_amount_human,
                    expected_perp_delta=Decimal(0),
                )
            )

    current_safe_snapshot = phase1_safe_snapshot

    if current_safe_snapshot.spot_free_usdc >= roundtrip_baseline.spot_free_usdc + roundtrip_amount_human - BALANCE_TOLERANCE:
        phase2_tx = _broadcast_step(
            web3,
            deployer,
            build_hypercore_transfer_usd_class_call(lagoon_vault, roundtrip_amount_raw, to_perp=True),
            "Phase 2: spot -> perp",
            tx_rows,
        )
        if phase2_tx is not None:
            try:
                _wait_for_spot_free_balance(
                    session,
                    lagoon_vault.safe_address,
                    roundtrip_baseline.spot_free_usdc,
                )
                _wait_for_perp_withdrawable_balance(
                    session,
                    lagoon_vault.safe_address,
                    roundtrip_baseline.perp_withdrawable + roundtrip_amount_human,
                )
            except TimeoutError as exc:
                issues.append(f"Phase 2 observation failed: {exc}")

            operator_snapshot, current_safe_snapshot = _collect_stage_snapshots(
                web3=web3,
                session=session,
                usdc=usdc,
                share_token=share_token,
                operator_address=deployer.address,
                safe_address=lagoon_vault.safe_address,
            )
            _print_snapshot("Snapshot after phase 2", [operator_snapshot, current_safe_snapshot])
            issues.extend(
                _compare_expected_delta(
                    roundtrip_baseline,
                    current_safe_snapshot,
                    "Phase 2",
                    expected_evm_delta=-roundtrip_amount_human,
                    expected_spot_free_delta=Decimal(0),
                    expected_perp_delta=roundtrip_amount_human,
                )
            )
    else:
        issues.append("Skipping phase 2 because the Safe does not appear to hold the round-trip amount in spot")

    if current_safe_snapshot.perp_withdrawable >= roundtrip_baseline.perp_withdrawable + roundtrip_amount_human - BALANCE_TOLERANCE:
        phase3_tx = _broadcast_step(
            web3,
            deployer,
            build_hypercore_transfer_usd_class_call(lagoon_vault, roundtrip_amount_raw, to_perp=False),
            "Phase 3: perp -> spot",
            tx_rows,
        )
        if phase3_tx is not None:
            try:
                _wait_for_perp_withdrawable_balance(
                    session,
                    lagoon_vault.safe_address,
                    roundtrip_baseline.perp_withdrawable,
                )
                _wait_for_spot_free_balance(
                    session,
                    lagoon_vault.safe_address,
                    roundtrip_baseline.spot_free_usdc + roundtrip_amount_human,
                )
            except TimeoutError as exc:
                issues.append(f"Phase 3 observation failed: {exc}")

            operator_snapshot, current_safe_snapshot = _collect_stage_snapshots(
                web3=web3,
                session=session,
                usdc=usdc,
                share_token=share_token,
                operator_address=deployer.address,
                safe_address=lagoon_vault.safe_address,
            )
            _print_snapshot("Snapshot after phase 3", [operator_snapshot, current_safe_snapshot])
            issues.extend(
                _compare_expected_delta(
                    roundtrip_baseline,
                    current_safe_snapshot,
                    "Phase 3",
                    expected_evm_delta=-roundtrip_amount_human,
                    expected_spot_free_delta=roundtrip_amount_human,
                    expected_perp_delta=Decimal(0),
                )
            )
    elif current_safe_snapshot.spot_free_usdc < roundtrip_baseline.spot_free_usdc + roundtrip_amount_human - BALANCE_TOLERANCE:
        issues.append("Skipping phase 3 because the Safe does not appear to hold the round-trip amount in perp")

    if current_safe_snapshot.spot_free_usdc >= roundtrip_baseline.spot_free_usdc + roundtrip_amount_human - BALANCE_TOLERANCE:
        phase4_tx = _broadcast_step(
            web3,
            deployer,
            build_hypercore_send_asset_to_evm_call(lagoon_vault, withdraw_amount_raw),
            "Phase 4: spot -> EVM",
            tx_rows,
        )
        if phase4_tx is not None:
            try:
                _wait_for_evm_usdc_balance(
                    usdc,
                    lagoon_vault.safe_address,
                    roundtrip_baseline.evm_usdc_balance - roundtrip_amount_human + withdraw_amount_human,
                )
                _wait_for_spot_free_balance(
                    session,
                    lagoon_vault.safe_address,
                    roundtrip_baseline.spot_free_usdc + roundtrip_amount_human - withdraw_amount_human,
                )
                _wait_for_perp_withdrawable_balance(
                    session,
                    lagoon_vault.safe_address,
                    roundtrip_baseline.perp_withdrawable,
                )
            except TimeoutError as exc:
                issues.append(f"Phase 4 observation failed: {exc}")

            operator_snapshot, current_safe_snapshot = _collect_stage_snapshots(
                web3=web3,
                session=session,
                usdc=usdc,
                share_token=share_token,
                operator_address=deployer.address,
                safe_address=lagoon_vault.safe_address,
            )
            _print_snapshot("Snapshot after phase 4", [operator_snapshot, current_safe_snapshot])
            issues.extend(
                _compare_expected_delta(
                    roundtrip_baseline,
                    current_safe_snapshot,
                    "Phase 4",
                    expected_evm_delta=withdraw_amount_human - roundtrip_amount_human,
                    expected_spot_free_delta=roundtrip_amount_human - withdraw_amount_human,
                    expected_perp_delta=Decimal(0),
                )
            )
    else:
        issues.append("Skipping phase 4 because the Safe does not appear to hold the round-trip amount in spot")

    if existing_vault_address or withdraw_amount_human != roundtrip_amount_human:
        if existing_vault_address:
            issues.append("Skipping redeem and sweep because an existing vault was reused")
        else:
            issues.append("Skipping redeem and sweep because deposit and withdraw amounts differ")

        safe_snapshot = current_safe_snapshot
        print("\nTransactions")
        print(tabulate(tx_rows, headers=["Step", "TX hash", "Gas used", "Status"], tablefmt="simple"))

        print("\nSummary")
        print(
            tabulate(
                [
                    ["Operator", deployer.address],
                    ["Vault", lagoon_vault.vault_address],
                    ["Safe", lagoon_vault.safe_address],
                    ["Module", module.address],
                    ["Round-trip deposit amount", f"{roundtrip_amount_human:,.6f} USDC"],
                    ["Round-trip withdraw amount", f"{withdraw_amount_human:,.6f} USDC"],
                    ["Activation amount", f"{activation_amount_human:,.6f} USDC"],
                    ["Final operator USDC", f"{operator_snapshot.evm_usdc_balance:,.6f}"],
                    ["Final Safe USDC", f"{safe_snapshot.evm_usdc_balance:,.6f}"],
                    ["Final operator shares", f"{operator_snapshot.lagoon_share_balance:,.6f}"],
                    ["Final Safe spot", f"{safe_snapshot.spot_total_usdc:,.6f}"],
                    ["Final Safe perp", f"{safe_snapshot.perp_withdrawable:,.6f}"],
                ],
                tablefmt="simple",
            )
        )

        if issues:
            print("\nObserved issues")
            for issue in issues:
                print(f"- {issue}")
        return

    share_balance_before_redeem = share_token.fetch_balance_of(deployer.address)
    if share_balance_before_redeem <= 0:
        message = "Operator does not hold Lagoon shares to redeem"
        raise RuntimeError(message)

    deployer.sync_nonce(web3)
    redeem_vault_shares(
        web3=web3,
        vault_address=lagoon_vault.vault_address,
        redeemer=deployer.address,
        hot_wallet=deployer,
    )

    safe_usdc_balance = usdc.fetch_balance_of(lagoon_vault.safe_address)
    if safe_usdc_balance <= 0:
        message = "Safe holds no EVM USDC for redemption settlement"
        raise RuntimeError(message)

    deployer.sync_nonce(web3)
    tx_hash = deployer.transact_and_broadcast_with_contract(
        lagoon_vault.post_new_valuation(safe_usdc_balance),
        gas_limit=1_000_000,
    )
    receipt = assert_transaction_success_with_explanation(web3, tx_hash)
    tx_rows.append(["Redeem: post valuation", tx_hash.hex(), receipt["gasUsed"], "ok"])

    deployer.sync_nonce(web3)
    tx_hash = deployer.transact_and_broadcast_with_contract(
        lagoon_vault.settle_via_trading_strategy_module(safe_usdc_balance),
        gas_limit=1_000_000,
    )
    receipt = assert_transaction_success_with_explanation(web3, tx_hash)
    tx_rows.append(["Redeem: settle", tx_hash.hex(), receipt["gasUsed"], "ok"])

    deployer.sync_nonce(web3)
    tx_hash = deployer.transact_and_broadcast_with_contract(
        lagoon_vault.finalise_redeem(deployer.address),
        gas_limit=1_000_000,
    )
    receipt = assert_transaction_success_with_explanation(web3, tx_hash)
    tx_rows.append(["Redeem: finalise", tx_hash.hex(), receipt["gasUsed"], "ok"])

    sweep_tx_hash = _sweep_safe_usdc_to_operator(lagoon_vault, deployer)
    if sweep_tx_hash is not None:
        tx_rows.append(["Sweep Safe USDC", sweep_tx_hash, "-", "ok"])

    operator_snapshot, safe_snapshot = _collect_stage_snapshots(
        web3=web3,
        session=session,
        usdc=usdc,
        share_token=share_token,
        operator_address=deployer.address,
        safe_address=lagoon_vault.safe_address,
    )
    _print_snapshot("Final snapshot after redeem and sweep", [operator_snapshot, safe_snapshot])

    leftover_rows = []
    if safe_snapshot.evm_escrow_usdc > 0:
        leftover_rows.append(["Safe EVM escrow", f"{safe_snapshot.evm_escrow_usdc:,.6f} USDC"])
    if safe_snapshot.spot_total_usdc > BALANCE_TOLERANCE:
        leftover_rows.append(["Safe spot total", f"{safe_snapshot.spot_total_usdc:,.6f} USDC"])
    if safe_snapshot.perp_withdrawable > BALANCE_TOLERANCE:
        leftover_rows.append(["Safe perp withdrawable", f"{safe_snapshot.perp_withdrawable:,.6f} USDC"])
    if safe_snapshot.vault_positions:
        leftover_rows.append(["Safe HyperCore vault equity", f"{safe_snapshot.vault_equity_total:,.6f} USDC"])

    print("\nTransactions")
    print(tabulate(tx_rows, headers=["Step", "TX hash", "Gas used", "Status"], tablefmt="simple"))

    print("\nSummary")
    print(
        tabulate(
            [
                ["Operator", deployer.address],
                ["Vault", lagoon_vault.vault_address],
                ["Safe", lagoon_vault.safe_address],
                ["Module", module.address],
                ["Round-trip amount", f"{roundtrip_amount_human:,.6f} USDC"],
                ["Activation amount", f"{activation_amount_human:,.6f} USDC"],
                ["Final operator USDC", f"{operator_snapshot.evm_usdc_balance:,.6f}"],
                ["Final Safe USDC", f"{safe_snapshot.evm_usdc_balance:,.6f}"],
                ["Final operator shares", f"{operator_snapshot.lagoon_share_balance:,.6f}"],
                ["Final Safe spot", f"{safe_snapshot.spot_total_usdc:,.6f}"],
                ["Final Safe perp", f"{safe_snapshot.perp_withdrawable:,.6f}"],
            ],
            tablefmt="simple",
        )
    )

    if issues:
        print("\nObserved issues")
        for issue in issues:
            print(f"- {issue}")

    if leftover_rows:
        print("\nLeft-over HyperCore balances")
        print(tabulate(leftover_rows, headers=["Location", "Amount"], tablefmt="simple"))


if __name__ == "__main__":
    main()
