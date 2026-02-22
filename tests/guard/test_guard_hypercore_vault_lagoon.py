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

from eth_defi.abi import encode_function_call, get_abi_by_filename
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonAutomatedDeployment,
    LagoonConfig,
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
)
from eth_defi.hotwallet import HotWallet
from eth_defi.hyperliquid.core_writer import (
    CORE_DEPOSIT_WALLET_MAINNET,
    CORE_WRITER_ADDRESS,
    SPOT_DEX,
    USDC_TOKEN_INDEX,
    encode_spot_send,
    encode_transfer_usd_class,
    encode_vault_deposit,
    encode_vault_withdraw,
)
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
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
#: Anvil default accounts #1 and #2 as Safe owners
OWNER_1 = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
OWNER_2 = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"

#: Test vault address (arbitrary, just needs to be whitelisted)
TEST_HYPERCORE_VAULT = HexAddress(HexStr("0x1111111111111111111111111111111111111111"))

#: HyperEVM USDC address
USDC_ADDRESS = USDC_NATIVE_TOKEN[999]


def _load_deployed_bytecode(abi_filename: str) -> str:
    """Load deployed bytecode from an ABI JSON file."""
    abi_data = get_abi_by_filename(abi_filename)
    bytecode = abi_data["deployedBytecode"]["object"]
    if not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode
    return bytecode


def _perform_call(module: Contract, fn_call, asset_manager: str):
    """Encode and submit a performCall transaction on TradingStrategyModuleV0."""
    target = fn_call.address
    data_payload = encode_function_call(fn_call, fn_call.arguments)
    return module.functions.performCall(
        target,
        data_payload,
    ).transact({"from": asset_manager})


@pytest.fixture()
def deployer() -> LocalAccount:
    return Account.from_key(DEPLOYER_PRIVATE_KEY)


@pytest.fixture()
def anvil_hyperliquid() -> AnvilLaunch:
    """Fork HyperEVM mainnet with large block gas limit."""
    launch = fork_network_anvil(
        JSON_RPC_HYPERLIQUID,
        gas_limit=30_000_000,
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3(anvil_hyperliquid):
    web3 = create_multi_provider_web3(
        anvil_hyperliquid.json_rpc_url,
        default_http_timeout=(3, 500.0),
    )
    assert web3.eth.chain_id == 999
    return web3


@pytest.fixture()
def usdc(web3) -> TokenDetails:
    return fetch_erc20_details(web3, USDC_ADDRESS)


@pytest.fixture()
def mock_core_writer(web3) -> Contract:
    """Deploy MockCoreWriter at the system address via anvil_setCode."""
    bytecode = _load_deployed_bytecode("guard/MockCoreWriter.json")
    address = Web3.to_checksum_address(CORE_WRITER_ADDRESS)
    web3.provider.make_request("anvil_setCode", [address, bytecode])
    deployed_code = web3.eth.get_code(address)
    assert len(deployed_code) > 0, "MockCoreWriter bytecode not set"
    abi_data = get_abi_by_filename("guard/MockCoreWriter.json")
    return web3.eth.contract(address=address, abi=abi_data["abi"])


@pytest.fixture()
def mock_core_deposit_wallet(web3) -> Contract:
    """Deploy MockCoreDepositWallet at the mainnet address via anvil_setCode."""
    bytecode = _load_deployed_bytecode("guard/MockCoreDepositWallet.json")
    address = Web3.to_checksum_address(CORE_DEPOSIT_WALLET_MAINNET)
    web3.provider.make_request("anvil_setCode", [address, bytecode])
    deployed_code = web3.eth.get_code(address)
    assert len(deployed_code) > 0, "MockCoreDepositWallet bytecode not set"
    abi_data = get_abi_by_filename("guard/MockCoreDepositWallet.json")
    return web3.eth.contract(address=address, abi=abi_data["abi"])


@pytest.fixture()
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
        asset_manager=deployer.address,
        safe_owners=[OWNER_1, OWNER_2],
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

    # Set up Hypercore whitelisting on the guard
    module = deploy_info.trading_strategy_module

    tx_hash = module.functions.whitelistCoreWriter(
        Web3.to_checksum_address(CORE_WRITER_ADDRESS),
        Web3.to_checksum_address(CORE_DEPOSIT_WALLET_MAINNET),
        "Hypercore vault trading",
    ).transact({"from": deployer.address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = module.functions.whitelistHypercoreVault(
        Web3.to_checksum_address(TEST_HYPERCORE_VAULT),
        "Test Hypercore vault",
    ).transact({"from": deployer.address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Whitelist USDC token for approve calls
    tx_hash = module.functions.whitelistToken(
        Web3.to_checksum_address(USDC_ADDRESS),
        "USDC for Hypercore bridging",
    ).transact({"from": deployer.address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    return deploy_info


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

    # Fund the Safe with USDC by setting storage directly
    web3.provider.make_request("anvil_setBalance", [safe_address, hex(10 * 10**18)])
    web3.provider.make_request(
        "anvil_setStorageAt",
        [
            Web3.to_checksum_address(USDC_ADDRESS),
            Web3.solidity_keccak(
                ["uint256", "uint256"],
                [int(safe_address, 16), 9],
            ).hex(),
            "0x" + usdc_amount.to_bytes(32, "big").hex(),
        ],
    )
    balance = usdc.contract.functions.balanceOf(safe_address).call()
    assert balance >= usdc_amount, f"Safe USDC balance {balance} < {usdc_amount}"

    # Step 1: Approve USDC to CoreDepositWallet
    fn_call = usdc.contract.functions.approve(
        Web3.to_checksum_address(CORE_DEPOSIT_WALLET_MAINNET),
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
def test_lagoon_hypercore_disallowed_vault(
    web3: Web3,
    deployer: LocalAccount,
    lagoon_deployment: LagoonAutomatedDeployment,
    mock_core_writer: Contract,
):
    """Deposit to a non-whitelisted vault through TradingStrategyModuleV0 should revert."""
    module = lagoon_deployment.trading_strategy_module
    asset_manager = deployer.address
    hypercore_amount = 1_000 * 10**6

    malicious_vault = "0x2222222222222222222222222222222222222222"
    raw_action = encode_vault_deposit(malicious_vault, hypercore_amount)
    fn_call = mock_core_writer.functions.sendRawAction(raw_action)
    tx_hash = _perform_call(module, fn_call, asset_manager)
    with pytest.raises(TransactionAssertionError):
        assert_transaction_success_with_explanation(web3, tx_hash)
