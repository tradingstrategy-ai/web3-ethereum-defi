"""Check guard against Hypercore CoreWriter deposit and withdrawal calls.

- Check CoreWriter sendRawAction() access rights using SimpleVaultV0
- Uses MockCoreWriter and MockCoreDepositWallet deployed via anvil_setCode
  at the system contract addresses
- Validates vault deposit flow, withdrawal flow, disallowed vault, disallowed
  action, and disallowed spotSend receiver
"""

import os

import pytest
from eth_abi import encode
from eth_typing import HexAddress, HexStr
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import ZERO_ADDRESS, get_abi_by_filename, get_deployed_contract
from eth_defi.deploy import deploy_contract
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
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.token import fetch_erc20_details, TokenDetails
from eth_defi.trace import (
    assert_transaction_success_with_explanation,
    TransactionAssertionError,
)

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")
CI = os.environ.get("CI") == "true"

pytestmark = pytest.mark.skipif(
    JSON_RPC_BASE is None,
    reason="Set JSON_RPC_BASE env",
)

#: Test vault address (arbitrary, just needs to be whitelisted)
TEST_HYPERCORE_VAULT = HexAddress(HexStr("0x1111111111111111111111111111111111111111"))

#: Non-whitelisted vault for negative tests
MALICIOUS_VAULT = HexAddress(HexStr("0x2222222222222222222222222222222222222222"))


def _load_deployed_bytecode(abi_filename: str) -> str:
    """Load deployed bytecode from an ABI JSON file.

    :param abi_filename:
        ABI JSON filename relative to eth_defi/abi/.

    :return:
        Hex-encoded deployed bytecode string (with 0x prefix).
    """
    abi_data = get_abi_by_filename(abi_filename)
    bytecode = abi_data["deployedBytecode"]["object"]
    if not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode
    return bytecode


@pytest.fixture
def large_usdc_holder() -> HexAddress:
    return HexAddress(HexStr("0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A"))


@pytest.fixture
def anvil_base_chain_fork(request, large_usdc_holder) -> AnvilLaunch:
    """Create a testable fork of live Base chain."""
    mainnet_rpc = os.environ["JSON_RPC_BASE"]
    launch = fork_network_anvil(
        mainnet_rpc,
        unlocked_addresses=[large_usdc_holder],
        fork_block_number=30_659_990,
    )
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture
def web3(anvil_base_chain_fork: AnvilLaunch):
    """Create a web3 connector."""
    web3 = create_multi_provider_web3(
        anvil_base_chain_fork.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture
def usdc(web3) -> TokenDetails:
    """Get USDC on Base."""
    return fetch_erc20_details(web3, "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")


@pytest.fixture()
def deployer(web3) -> str:
    return web3.eth.accounts[0]


@pytest.fixture()
def owner(web3) -> str:
    return web3.eth.accounts[1]


@pytest.fixture()
def asset_manager(web3) -> str:
    return web3.eth.accounts[2]


@pytest.fixture()
def third_party(web3) -> str:
    return web3.eth.accounts[3]


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
def hypercore_vault_lib(web3: Web3, deployer: str) -> Contract:
    """Deploy HypercoreVaultLib library contract."""
    return deploy_contract(web3, "guard/HypercoreVaultLib.json", deployer)


@pytest.fixture()
def cowswap_lib(web3: Web3, deployer: str) -> Contract:
    """Deploy CowSwapLib library contract."""
    return deploy_contract(web3, "guard/CowSwapLib.json", deployer)


@pytest.fixture()
def vault(
    web3: Web3,
    usdc: TokenDetails,
    deployer: str,
    owner: str,
    asset_manager: str,
    mock_core_writer: Contract,
    mock_core_deposit_wallet: Contract,
    hypercore_vault_lib: Contract,
    cowswap_lib: Contract,
) -> Contract:
    """Create SimpleVaultV0 with Hypercore whitelisting.

    - Deploys libraries and links them into SimpleVaultV0 bytecode
    - Deploys SimpleVaultV0 (which creates its own GuardV0)
    - Whitelists CoreWriter + CoreDepositWallet on the guard
    - Whitelists a test vault address
    """
    # Deploy SimpleVaultV0 with library linking
    vault = deploy_contract(
        web3,
        "guard/SimpleVaultV0.json",
        deployer,
        asset_manager,
        libraries={
            "HypercoreVaultLib": hypercore_vault_lib.address,
            "CowSwapLib": cowswap_lib.address,
            "GmxLib": ZERO_ADDRESS,
        },
    )

    assert vault.functions.owner().call() == deployer
    tx_hash = vault.functions.initialiseOwnership(owner).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert vault.functions.owner().call() == owner
    assert vault.functions.assetManager().call() == asset_manager

    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())
    assert guard.functions.owner().call() == owner

    # Whitelist CoreWriter + CoreDepositWallet
    tx_hash = guard.functions.whitelistCoreWriter(
        Web3.to_checksum_address(CORE_WRITER_ADDRESS),
        Web3.to_checksum_address(CORE_DEPOSIT_WALLET_MAINNET),
        "Hypercore vault trading",
    ).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Whitelist USDC token (needed for approve calls through the guard)
    tx_hash = guard.functions.whitelistToken(
        usdc.address,
        "USDC for Hypercore bridging",
    ).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Whitelist the test Hypercore vault
    tx_hash = guard.functions.whitelistHypercoreVault(
        Web3.to_checksum_address(TEST_HYPERCORE_VAULT),
        "Test Hypercore vault",
    ).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    return vault


@pytest.fixture()
def guard(web3: Web3, vault: Contract) -> Contract:
    return get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())


@pytest.fixture()
def vault_with_balance(web3, vault, usdc: TokenDetails, large_usdc_holder) -> Contract:
    """SimpleVaultV0 with some USDC balance for testing."""
    tx_hash = usdc.contract.functions.transfer(
        vault.address,
        500_000 * 10**6,
    ).transact({"from": large_usdc_holder})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return vault


@pytest.mark.skipif(CI, reason="Flaky on CI due to Anvil fork block range errors")
def test_guard_hypercore_vault_deposit(
    web3: Web3,
    asset_manager: str,
    vault_with_balance: Contract,
    mock_core_writer: Contract,
    mock_core_deposit_wallet: Contract,
    usdc: TokenDetails,
):
    """Execute the full 4-step Hypercore vault deposit flow through the guard.

    1. Approve USDC to CoreDepositWallet
    2. CoreDepositWallet.deposit(amount, SPOT_DEX)
    3. CoreWriter.sendRawAction(transferUsdClass(amount, true))
    4. CoreWriter.sendRawAction(vaultTransfer(vault, true, amount))
    """
    vault = vault_with_balance
    usdc_amount = 10_000 * 10**6  # 10k USDC

    # Step 1: Approve USDC to CoreDepositWallet
    fn_call = usdc.contract.functions.approve(
        Web3.to_checksum_address(CORE_DEPOSIT_WALLET_MAINNET),
        usdc_amount,
    )
    target, call_data = encode_simple_vault_transaction(fn_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Step 2: CoreDepositWallet.deposit(amount, SPOT_DEX)
    fn_call = mock_core_deposit_wallet.functions.deposit(usdc_amount, SPOT_DEX)
    target, call_data = encode_simple_vault_transaction(fn_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Verify mock recorded the deposit
    assert mock_core_deposit_wallet.functions.getDepositCount().call() == 1
    sender, amount, dex = mock_core_deposit_wallet.functions.getDeposit(0).call()
    assert sender == vault.address
    assert amount == usdc_amount
    assert dex == SPOT_DEX

    # Step 3: CoreWriter.sendRawAction(transferUsdClass(amount, true))
    # HyperCore uses different decimal (uint64), use a smaller number for the raw action
    hypercore_amount = 10_000 * 10**6  # 10k in HyperCore wei
    raw_action = encode_transfer_usd_class(hypercore_amount, to_perp=True)
    fn_call = mock_core_writer.functions.sendRawAction(raw_action)
    target, call_data = encode_simple_vault_transaction(fn_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Step 4: CoreWriter.sendRawAction(vaultTransfer(vault, true, amount))
    raw_action = encode_vault_deposit(TEST_HYPERCORE_VAULT, hypercore_amount)
    fn_call = mock_core_writer.functions.sendRawAction(raw_action)
    target, call_data = encode_simple_vault_transaction(fn_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Verify mock recorded both CoreWriter actions
    assert mock_core_writer.functions.getActionCount().call() == 2

    # Check transferUsdClass action (action ID 7)
    sender, version, action_id, params = mock_core_writer.functions.getAction(0).call()
    assert sender == vault.address
    assert version == 1
    assert action_id == 7

    # Check vaultTransfer action (action ID 2)
    sender, version, action_id, params = mock_core_writer.functions.getAction(1).call()
    assert sender == vault.address
    assert version == 1
    assert action_id == 2


@pytest.mark.skipif(CI, reason="Flaky on CI due to Anvil fork block range errors")
def test_guard_hypercore_vault_withdraw(
    web3: Web3,
    asset_manager: str,
    vault_with_balance: Contract,
    mock_core_writer: Contract,
    usdc: TokenDetails,
):
    """Execute the 3-step Hypercore vault withdrawal flow through the guard.

    1. CoreWriter.sendRawAction(vaultTransfer(vault, false, amount))
    2. CoreWriter.sendRawAction(transferUsdClass(amount, false))
    3. CoreWriter.sendRawAction(spotSend(safe, USDC_TOKEN, amount))
    """
    vault = vault_with_balance
    hypercore_amount = 5_000 * 10**6

    # Step 1: Withdraw from vault
    raw_action = encode_vault_withdraw(TEST_HYPERCORE_VAULT, hypercore_amount)
    fn_call = mock_core_writer.functions.sendRawAction(raw_action)
    target, call_data = encode_simple_vault_transaction(fn_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Step 2: Move USDC perp -> spot
    raw_action = encode_transfer_usd_class(hypercore_amount, to_perp=False)
    fn_call = mock_core_writer.functions.sendRawAction(raw_action)
    target, call_data = encode_simple_vault_transaction(fn_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Step 3: Bridge USDC back to EVM (spotSend to vault/Safe address)
    raw_action = encode_spot_send(vault.address, USDC_TOKEN_INDEX, hypercore_amount)
    fn_call = mock_core_writer.functions.sendRawAction(raw_action)
    target, call_data = encode_simple_vault_transaction(fn_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Verify all 3 actions recorded
    assert mock_core_writer.functions.getActionCount().call() == 3

    # Check vaultTransfer withdraw (action ID 2)
    sender, version, action_id, params = mock_core_writer.functions.getAction(0).call()
    assert sender == vault.address
    assert version == 1
    assert action_id == 2

    # Check transferUsdClass (action ID 7)
    _, _, action_id, _ = mock_core_writer.functions.getAction(1).call()
    assert action_id == 7

    # Check spotSend (action ID 6)
    _, _, action_id, _ = mock_core_writer.functions.getAction(2).call()
    assert action_id == 6


@pytest.mark.skipif(CI, reason="Flaky on CI due to Anvil fork block range errors")
def test_guard_hypercore_disallowed_vault(
    web3: Web3,
    asset_manager: str,
    vault_with_balance: Contract,
    mock_core_writer: Contract,
):
    """Deposit to a non-whitelisted vault address should revert."""
    vault = vault_with_balance
    hypercore_amount = 1_000 * 10**6

    # Try to deposit to MALICIOUS_VAULT (not whitelisted)
    raw_action = encode_vault_deposit(MALICIOUS_VAULT, hypercore_amount)
    fn_call = mock_core_writer.functions.sendRawAction(raw_action)
    target, call_data = encode_simple_vault_transaction(fn_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    with pytest.raises(TransactionAssertionError):
        assert_transaction_success_with_explanation(web3, tx_hash)


@pytest.mark.skipif(CI, reason="Flaky on CI due to Anvil fork block range errors")
def test_guard_hypercore_disallowed_action(
    web3: Web3,
    asset_manager: str,
    vault_with_balance: Contract,
    mock_core_writer: Contract,
):
    """Send a disallowed CoreWriter action ID (e.g. limitOrder = 1) should revert."""
    vault = vault_with_balance

    # Build a raw action with action ID 1 (limitOrder, not whitelisted)
    # Format: version(1 byte) + actionId(3 bytes big-endian) + params
    action_id = 1
    version = (1).to_bytes(1, "big")
    action_id_bytes = action_id.to_bytes(3, "big")
    # Minimal params (doesn't matter, should fail before parsing)
    fake_params = encode(["uint64"], [1000])
    raw_action = version + action_id_bytes + fake_params

    fn_call = mock_core_writer.functions.sendRawAction(raw_action)
    target, call_data = encode_simple_vault_transaction(fn_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    with pytest.raises(TransactionAssertionError):
        assert_transaction_success_with_explanation(web3, tx_hash)


@pytest.mark.skipif(CI, reason="Flaky on CI due to Anvil fork block range errors")
def test_guard_hypercore_disallowed_spot_send_receiver(
    web3: Web3,
    asset_manager: str,
    vault_with_balance: Contract,
    mock_core_writer: Contract,
    third_party: str,
):
    """spotSend to a non-allowed receiver should revert.

    The vault (Safe) address is an allowed receiver by default,
    but a random third party address should be rejected.
    """
    vault = vault_with_balance
    hypercore_amount = 1_000 * 10**6

    # spotSend to third_party (not in allowedReceivers)
    raw_action = encode_spot_send(third_party, USDC_TOKEN_INDEX, hypercore_amount)
    fn_call = mock_core_writer.functions.sendRawAction(raw_action)
    target, call_data = encode_simple_vault_transaction(fn_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    with pytest.raises(TransactionAssertionError):
        assert_transaction_success_with_explanation(web3, tx_hash)
