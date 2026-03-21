"""Lagoon vault + Hypercore CoreWriter integration test on HyperEVM fork.

- Deploys a full Lagoon vault with TradingStrategyModuleV0 on a HyperEVM Anvil fork
- Sets up Hypercore CoreWriter whitelisting on the guard
- Uses MockCoreWriter and MockCoreDepositWallet via anvil_setCode
  (real CoreWriter precompiles do not work in Anvil forks)
- Executes the full 4-step deposit and 3-step withdrawal flows through the
  TradingStrategyModuleV0 module

Requires JSON_RPC_HYPERLIQUID environment variable.
"""

import logging
import os

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import encode_function_call
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonAutomatedDeployment,
    LagoonConfig,
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
)
from eth_defi.hotwallet import HotWallet
from eth_defi.hyperliquid.core_writer import (
    CORE_DEPOSIT_WALLET,
    CORE_WRITER_ADDRESS,
    SPOT_DEX,
    USDC_TOKEN_INDEX,
    build_hypercore_deposit_multicall,
    build_hypercore_withdraw_multicall,
    encode_spot_send,
    encode_transfer_usd_class,
    encode_vault_deposit,
    encode_vault_withdraw,
)
from eth_defi.hyperliquid.testing import (
    deploy_mock_core_deposit_wallet,
    deploy_mock_core_writer,
)
from eth_defi.provider.anvil import (
    ANVIL_OWNER_1,
    ANVIL_OWNER_2,
    AnvilLaunch,
    AnvilSnapshotState,
    create_anvil_snapshot_state,
    fork_network_anvil,
    fund_erc20_on_anvil,
    reset_anvil_snapshot,
)
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details, TokenDetails
from eth_defi.trace import (
    assert_transaction_success_with_explanation,
    TransactionAssertionError,
)

logger = logging.getLogger(__name__)

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = pytest.mark.skipif(
    not JSON_RPC_HYPERLIQUID,
    reason="JSON_RPC_HYPERLIQUID environment variable required",
)

#: Anvil default account #0 private key
DEPLOYER_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

#: Test vault address (arbitrary, just needs to be whitelisted)
TEST_HYPERCORE_VAULT = HexAddress(HexStr("0x1111111111111111111111111111111111111111"))

#: HyperEVM USDC address
USDC_ADDRESS = USDC_NATIVE_TOKEN[999]


def _perform_call(module: Contract, fn_call, asset_manager: str):
    """Encode and submit a performCall transaction on TradingStrategyModuleV0."""
    target = fn_call.address
    data_payload = encode_function_call(fn_call, fn_call.arguments)
    return module.functions.performCall(
        target,
        data_payload,
    ).transact({"from": asset_manager})


@pytest.fixture(scope="module")
def deployer() -> LocalAccount:
    return Account.from_key(DEPLOYER_PRIVATE_KEY)


@pytest.fixture(scope="module")
def anvil_hyperliquid() -> AnvilLaunch:
    """Fork HyperEVM mainnet with large block gas limit.

    Uses an explicit fork block a few blocks behind the tip to avoid
    transient "Unknown block" errors from the HyperEVM RPC.
    """
    # HyperEVM RPC sporadically returns "Unknown block" for the chain tip.
    # Pin the fork to a slightly older block to work around this.
    rpc_url = JSON_RPC_HYPERLIQUID.split()[0] if " " in JSON_RPC_HYPERLIQUID else JSON_RPC_HYPERLIQUID
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.block_tip_latency = 4
    fork_block = get_almost_latest_block_number(w3)

    launch = fork_network_anvil(
        JSON_RPC_HYPERLIQUID,
        gas_limit=30_000_000,
        fork_block_number=fork_block,
        archive=False,
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture(scope="module")
def web3(anvil_hyperliquid):
    web3 = create_multi_provider_web3(
        anvil_hyperliquid.json_rpc_url,
        default_http_timeout=(3, 500.0),
    )
    assert web3.eth.chain_id == 999
    return web3


@pytest.fixture(scope="module")
def usdc(web3) -> TokenDetails:
    return fetch_erc20_details(web3, USDC_ADDRESS)


@pytest.fixture(scope="module")
def mock_core_writer(web3) -> Contract:
    """Deploy MockCoreWriter at the system address via anvil_setCode."""
    return deploy_mock_core_writer(web3)


@pytest.fixture(scope="module")
def mock_core_deposit_wallet(web3) -> Contract:
    """Deploy MockCoreDepositWallet at the mainnet address via anvil_setCode."""
    return deploy_mock_core_deposit_wallet(web3)


@pytest.fixture(scope="module")
def lagoon_deployment(
    web3,
    deployer,
    mock_core_writer,
    mock_core_deposit_wallet,
) -> LagoonAutomatedDeployment:
    """Deploy a Lagoon vault on HyperEVM fork with Hypercore whitelisting."""
    web3.provider.make_request("anvil_setBalance", [deployer.address, hex(100 * 10**18)])

    wallet = HotWallet(deployer)
    wallet.sync_nonce(web3)

    config = LagoonConfig(
        parameters=LagoonDeploymentParameters(
            underlying=USDC_ADDRESS,
            name="HyperEVM Hypercore Test Vault",
            symbol="HHTV",
        ),
        asset_manager=None,
        asset_managers=[deployer.address],
        safe_owners=[ANVIL_OWNER_1, ANVIL_OWNER_2],
        safe_threshold=2,
        any_asset=True,
        safe_salt_nonce=42,
    )

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=wallet,
        config=config,
    )

    assert deploy_info.vault is not None
    assert deploy_info.safe is not None
    assert deploy_info.trading_strategy_module is not None

    # Set up Hypercore whitelisting on the guard.
    # After deploy_automated_lagoon_vault(), ownership has been transferred
    # to the Safe, so we impersonate the Safe to call onlyGuardOwner functions.
    module = deploy_info.trading_strategy_module
    safe_address = deploy_info.safe.address

    web3.provider.make_request("anvil_impersonateAccount", [safe_address])
    web3.provider.make_request("anvil_setBalance", [safe_address, hex(10 * 10**18)])

    tx_hash = module.functions.whitelistCoreWriter(
        Web3.to_checksum_address(CORE_WRITER_ADDRESS),
        Web3.to_checksum_address(CORE_DEPOSIT_WALLET[999]),
        "Hypercore vault trading",
    ).transact({"from": safe_address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = module.functions.whitelistHypercoreVault(
        Web3.to_checksum_address(TEST_HYPERCORE_VAULT),
        "Test Hypercore vault",
    ).transact({"from": safe_address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Whitelist USDC token for approve calls
    tx_hash = module.functions.whitelistToken(
        Web3.to_checksum_address(USDC_ADDRESS),
        "USDC for Hypercore bridging",
    ).transact({"from": safe_address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    web3.provider.make_request("anvil_stopImpersonatingAccount", [safe_address])

    return deploy_info


@pytest.fixture(scope="module")
def hypercore_lagoon_state(
    web3: Web3,
    lagoon_deployment: LagoonAutomatedDeployment,
) -> AnvilSnapshotState:
    """Save a post-deployment checkpoint so later tests can reuse the Hypercore Lagoon setup."""

    return create_anvil_snapshot_state(web3)


@pytest.fixture(autouse=True)
def restore_hypercore_lagoon_state(
    web3: Web3,
    hypercore_lagoon_state: AnvilSnapshotState,
) -> None:
    """Restore the shared HyperEVM fork back to the deployed Hypercore Lagoon baseline before each test."""

    reset_anvil_snapshot(web3, hypercore_lagoon_state)


@pytest.mark.timeout(600)
def test_lagoon_hypercore_vault_deposit(
    web3: Web3,
    deployer: LocalAccount,
    lagoon_deployment: LagoonAutomatedDeployment,
    mock_core_writer: Contract,
    mock_core_deposit_wallet: Contract,
    usdc: TokenDetails,
):
    """Execute the full 4-step Hypercore deposit flow through TradingStrategyModuleV0.

    1. Approve USDC to CoreDepositWallet
    2. CoreDepositWallet.deposit(amount, SPOT_DEX)
    3. CoreWriter.sendRawAction(transferUsdClass(amount, true))
    4. CoreWriter.sendRawAction(vaultTransfer(vault, true, amount))
    """
    module = lagoon_deployment.trading_strategy_module
    safe_address = lagoon_deployment.safe.address
    asset_manager = deployer.address
    usdc_amount = 10_000 * 10**6  # 10k USDC

    # Fund the Safe with USDC
    web3.provider.make_request("anvil_setBalance", [safe_address, hex(10 * 10**18)])
    fund_erc20_on_anvil(web3, USDC_ADDRESS, safe_address, usdc_amount)
    balance = usdc.contract.functions.balanceOf(safe_address).call()
    assert balance >= usdc_amount, f"Safe USDC balance {balance} < {usdc_amount}"

    # Step 1: Approve USDC to CoreDepositWallet
    fn_call = usdc.contract.functions.approve(
        Web3.to_checksum_address(CORE_DEPOSIT_WALLET[999]),
        usdc_amount,
    )
    tx_hash = _perform_call(module, fn_call, asset_manager)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Step 2: CoreDepositWallet.deposit(amount, SPOT_DEX)
    fn_call = mock_core_deposit_wallet.functions.deposit(usdc_amount, SPOT_DEX)
    tx_hash = _perform_call(module, fn_call, asset_manager)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Verify mock recorded the deposit
    assert mock_core_deposit_wallet.functions.getDepositCount().call() == 1
    sender, amount, dex = mock_core_deposit_wallet.functions.getDeposit(0).call()
    assert sender == safe_address
    assert amount == usdc_amount
    assert dex == SPOT_DEX

    # Step 3: CoreWriter.sendRawAction(transferUsdClass(amount, true))
    hypercore_amount = 10_000 * 10**6
    raw_action = encode_transfer_usd_class(hypercore_amount, to_perp=True)
    fn_call = mock_core_writer.functions.sendRawAction(raw_action)
    tx_hash = _perform_call(module, fn_call, asset_manager)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Step 4: CoreWriter.sendRawAction(vaultTransfer(vault, true, amount))
    raw_action = encode_vault_deposit(TEST_HYPERCORE_VAULT, hypercore_amount)
    fn_call = mock_core_writer.functions.sendRawAction(raw_action)
    tx_hash = _perform_call(module, fn_call, asset_manager)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Verify mock recorded both CoreWriter actions
    assert mock_core_writer.functions.getActionCount().call() == 2

    # Check transferUsdClass (action ID 7)
    sender, version, action_id, params = mock_core_writer.functions.getAction(0).call()
    assert sender == safe_address
    assert version == 1
    assert action_id == 7

    # Check vaultTransfer deposit (action ID 2)
    sender, version, action_id, params = mock_core_writer.functions.getAction(1).call()
    assert sender == safe_address
    assert version == 1
    assert action_id == 2


@pytest.mark.timeout(600)
def test_lagoon_hypercore_deposit_for_activation(
    web3: Web3,
    deployer: LocalAccount,
    lagoon_deployment: LagoonAutomatedDeployment,
    mock_core_deposit_wallet: Contract,
    usdc: TokenDetails,
):
    """Execute depositFor() for account activation through TradingStrategyModuleV0.

    depositFor(safe, amount, SPOT_DEX) is used to activate a Safe's HyperCore
    account before the first deposit. New HyperCore accounts require >1 USDC
    (1 USDC account creation fee, deposits <=1 USDC fail silently).
    """
    module = lagoon_deployment.trading_strategy_module
    safe_address = lagoon_deployment.safe.address
    asset_manager = deployer.address
    activation_amount = 5 * 10**6  # 5 USDC

    # Fund the Safe with USDC for activation
    web3.provider.make_request("anvil_setBalance", [safe_address, hex(10 * 10**18)])
    fund_erc20_on_anvil(web3, USDC_ADDRESS, safe_address, activation_amount)
    balance = usdc.contract.functions.balanceOf(safe_address).call()
    assert balance >= activation_amount

    # Step 1: Approve USDC to CoreDepositWallet
    fn_call = usdc.contract.functions.approve(
        Web3.to_checksum_address(CORE_DEPOSIT_WALLET[999]),
        activation_amount,
    )
    tx_hash = _perform_call(module, fn_call, asset_manager)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Step 2: depositFor(safe, amount, SPOT_DEX) — recipient is the Safe itself
    fn_call = mock_core_deposit_wallet.functions.depositFor(
        safe_address,
        activation_amount,
        SPOT_DEX,
    )
    tx_hash = _perform_call(module, fn_call, asset_manager)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Verify mock recorded the depositFor
    assert mock_core_deposit_wallet.functions.getDepositCount().call() == 1
    sender, amount, dex = mock_core_deposit_wallet.functions.getDeposit(0).call()
    assert sender == safe_address
    assert amount == activation_amount
    assert dex == SPOT_DEX


@pytest.mark.timeout(600)
def test_lagoon_hypercore_deposit_for_wrong_recipient(
    web3: Web3,
    deployer: LocalAccount,
    lagoon_deployment: LagoonAutomatedDeployment,
    mock_core_deposit_wallet: Contract,
):
    """depositFor() with a recipient other than the Safe should revert.

    The guard checks that the depositFor recipient is an allowed receiver.
    A third-party address that hasn't been whitelisted will be rejected.
    """
    module = lagoon_deployment.trading_strategy_module
    asset_manager = deployer.address
    third_party = "0x4444444444444444444444444444444444444444"

    fn_call = mock_core_deposit_wallet.functions.depositFor(
        third_party,
        5 * 10**6,
        SPOT_DEX,
    )
    tx_hash = _perform_call(module, fn_call, asset_manager)
    with pytest.raises(TransactionAssertionError):
        assert_transaction_success_with_explanation(web3, tx_hash)


@pytest.mark.timeout(600)
def test_lagoon_hypercore_any_asset_allows_non_whitelisted_vault(
    web3: Web3,
    deployer: LocalAccount,
    mock_core_writer: Contract,
):
    """With anyAsset enabled, Lagoon keeps HypercoreWriter but skips per-vault whitelisting.

    1. Deploy Lagoon with ``any_asset=True`` and an explicit Hypercore vault list.
    2. Verify HypercoreWriter/CoreDepositWallet permissions are installed.
    3. Verify the listed Hypercore vault was not individually whitelisted.
    4. Verify deposit to a different Hypercore vault still succeeds.
    """
    web3.provider.make_request("anvil_setBalance", [deployer.address, hex(100 * 10**18)])

    wallet = HotWallet(deployer)
    wallet.sync_nonce(web3)

    listed_vault = Web3.to_checksum_address(TEST_HYPERCORE_VAULT)
    hypercore_amount = 1_000 * 10**6
    non_whitelisted_vault = "0x2222222222222222222222222222222222222222"

    config = LagoonConfig(
        parameters=LagoonDeploymentParameters(
            underlying=USDC_ADDRESS,
            name="HyperEVM Hypercore AnyAsset Vault",
            symbol="HHAV",
        ),
        asset_manager=None,
        asset_managers=[deployer.address],
        safe_owners=[ANVIL_OWNER_1, ANVIL_OWNER_2],
        safe_threshold=2,
        any_asset=True,
        hypercore_vaults=[listed_vault],
        safe_salt_nonce=43,
    )

    # 1. Deploy Lagoon with any_asset=True and an explicit Hypercore vault list.
    start_block = web3.eth.block_number
    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=wallet,
        config=config,
    )
    module = deploy_info.trading_strategy_module
    end_block = web3.eth.block_number

    # 2. Verify HypercoreWriter/CoreDepositWallet permissions are installed.
    assert module.functions.isAllowedApprovalDestination(
        Web3.to_checksum_address(CORE_DEPOSIT_WALLET[999]),
    ).call()

    # 3. Verify the listed Hypercore vault was not individually whitelisted.
    hypercore_vault_approved_logs = web3.eth.get_logs(
        {
            "fromBlock": start_block + 1,
            "toBlock": end_block,
            "address": module.address,
            "topics": [Web3.keccak(text="HypercoreVaultApproved(address,string)").hex()],
        }
    )
    assert hypercore_vault_approved_logs == []

    # 4. Verify deposit to a different Hypercore vault still succeeds.
    raw_action = encode_vault_deposit(non_whitelisted_vault, hypercore_amount)
    fn_call = mock_core_writer.functions.sendRawAction(raw_action)
    tx_hash = _perform_call(module, fn_call, deployer.address)
    assert_transaction_success_with_explanation(web3, tx_hash)


@pytest.mark.timeout(600)
def test_lagoon_hypercore_deposit_multicall(
    web3: Web3,
    deployer: LocalAccount,
    lagoon_deployment: LagoonAutomatedDeployment,
    mock_core_writer: Contract,
    mock_core_deposit_wallet: Contract,
    usdc: TokenDetails,
):
    """Execute the full 4-step Hypercore deposit as a single multicall transaction.

    Uses build_hypercore_deposit_multicall() to batch all 4 steps into one
    EVM transaction via TradingStrategyModuleV0.multicall().
    """
    vault = lagoon_deployment.vault
    safe_address = lagoon_deployment.safe.address
    asset_manager = deployer.address
    usdc_amount = 10_000 * 10**6  # 10k USDC

    # Fund the Safe with USDC
    web3.provider.make_request("anvil_setBalance", [safe_address, hex(10 * 10**18)])
    fund_erc20_on_anvil(web3, USDC_ADDRESS, safe_address, usdc_amount)
    balance = usdc.contract.functions.balanceOf(safe_address).call()
    assert balance >= usdc_amount

    hypercore_amount = 10_000 * 10**6

    # Build and execute the multicall in a single transaction
    fn = build_hypercore_deposit_multicall(
        lagoon_vault=vault,
        evm_usdc_amount=usdc_amount,
        hypercore_usdc_amount=hypercore_amount,
        vault_address=TEST_HYPERCORE_VAULT,
    )
    tx_hash = fn.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Verify mock recorded the CDW deposit
    assert mock_core_deposit_wallet.functions.getDepositCount().call() == 1
    sender, amount, dex = mock_core_deposit_wallet.functions.getDeposit(0).call()
    assert sender == safe_address
    assert amount == usdc_amount
    assert dex == SPOT_DEX

    # Verify mock recorded both CoreWriter actions
    assert mock_core_writer.functions.getActionCount().call() == 2

    # Check transferUsdClass (action ID 7)
    sender, version, action_id, params = mock_core_writer.functions.getAction(0).call()
    assert sender == safe_address
    assert version == 1
    assert action_id == 7

    # Check vaultTransfer deposit (action ID 2)
    sender, version, action_id, params = mock_core_writer.functions.getAction(1).call()
    assert sender == safe_address
    assert version == 1
    assert action_id == 2
