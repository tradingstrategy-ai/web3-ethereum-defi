"""Guarded Veranta lifecycle and metadata for both reviewed Base implementations."""

import datetime
import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from eth_account import Account
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction
from web3.exceptions import ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.veranta.constants import VERANTA_VAULT_ADDRESS
from eth_defi.erc_4626.vault_protocol.veranta.deposit_redeem import VerantaDepositManager
from eth_defi.erc_4626.vault_protocol.veranta.vault import VerantaVault
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil, set_balance
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import BASE_MIDNIGHT_BLOCK
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission, VaultFlowUnavailable
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.repairs.veranta import refresh_veranta_metadata

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")
pytestmark = [pytest.mark.skipif(not JSON_RPC_BASE, reason="JSON_RPC_BASE required")]

#: The current implementation is not deployed at the canonical fork block.
#: Keep one additional fixed pooled fork to prove the live generation's cap
#: and complete lifecycle; generic Guard rejection checks stay canonical.
VERANTA_CURRENT_IMPLEMENTATION_BLOCK = 53_496_246


@pytest.fixture(
    scope="module",
    params=[
        pytest.param(BASE_MIDNIGHT_BLOCK, marks=pytest.mark.xdist_group("fork:base:midnight"), id="canonical"),
        pytest.param(VERANTA_CURRENT_IMPLEMENTATION_BLOCK, marks=pytest.mark.xdist_group("fork:base:veranta-current"), id="current-implementation"),
    ],
)
def fork_block(request: pytest.FixtureRequest) -> int:
    """Select the two source-reviewed implementation generations.

    :param request: Parametrised canonical or post-upgrade fixed block.
    :return: Immutable pooled fork block.
    """
    return request.param


def broadcast(web3: Web3, wallet: HotWallet, func: ContractFunction) -> HexBytes:
    """Send one signed call and require a successful mined receipt.

    :param web3: Isolated Anvil connection.
    :param wallet: Funded signing account with a synchronised nonce.
    :param func: Bound call to execute.
    :return: Successful transaction hash.
    """
    signed = wallet.sign_bound_call_with_new_nonce(func, {"gas": 3_000_000}, web3=web3, fill_gas_price=True)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)
    return tx_hash


def perform(web3: Web3, custody: Contract, wallet: HotWallet, func: ContractFunction) -> HexBytes:
    """Execute a manager call through actual guarded custody.

    :param web3: Anvil connection.
    :param custody: SimpleVaultV0 with a separate asset manager.
    :param wallet: Asset manager, distinct from governance.
    :param func: Manager-generated approval, deposit or redemption.
    :return: Successful transaction hash.
    """
    target, data = encode_simple_vault_transaction(func)
    return broadcast(web3, wallet, custody.functions.performCall(target, data))


@pytest.fixture(scope="module")
def anvil_fork(anvil_fork_pool: AnvilForkPool, fork_block: int) -> AnvilLaunch:
    """Reuse each fixed Base process for read and transaction evidence.

    :param anvil_fork_pool: Session-owned pool.
    :param fork_block: Selected canonical or post-upgrade block.
    :return: Shared fixed-block launch.
    """
    return anvil_fork_pool.get_launch(JSON_RPC_BASE, fork_block)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool, fork_block: int) -> Web3:
    """Connect to the selected shared fixed-block Base fork.

    :param anvil_fork_pool: Session-owned pool.
    :param fork_block: Selected canonical or post-upgrade block.
    :return: Web3 for the selected fixed block.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_BASE, fork_block)


@pytest.fixture(autouse=True)
def snapshot(anvil_fork: AnvilLaunch) -> Iterator[None]:
    """Isolate every mutation without restarting the fork.

    :param anvil_fork: Pooled launch to restore.
    :return: One test scope followed by snapshot restoration.
    """
    yield from evm_snapshot_revert(anvil_fork)


@pytest.fixture
def vault(web3: Web3) -> VerantaVault:
    """Require the public factory to select the Veranta adapter.

    :param web3: Fixed-block fork.
    :return: Autodetected Veranta vault.
    """
    result = create_vault_instance_autodetect(web3, VERANTA_VAULT_ADDRESS)
    assert isinstance(result, VerantaVault)
    return result


def test_veranta_metadata(vault: VerantaVault, fork_block: int) -> None:
    """Check fixed-block fee scaling, admission and protocol identity.

    :param vault: Publicly constructed adapter.
    :param fork_block: Selected implementation generation.
    :return: None; assertions check the verified deployment.
    """
    assert vault.features == {ERC4626Feature.veranta_like, ERC4626Feature.gains_tranche_like}
    assert vault.get_protocol_name() == "Veranta"
    assert vault.get_risk() is VaultTechnicalRisk.severe
    assert vault.name == "Avantis USDC Vault"
    assert vault.symbol == "avUSDC"
    assert vault.denomination_token.address.lower() == "0x833589fcd6edb6e08f4c7c32d4f71b54bdA02913".lower()
    fees = vault.get_fee_data(fork_block)
    assert fees.fee_mode is VaultFeeMode.externalised
    assert fees.withdraw == pytest.approx(0.005 if fork_block == BASE_MIDNIGHT_BLOCK else 0.015)
    assert fees.deposit == 0
    assert fees.management is None
    assert fees.performance is None
    assert fetch_deposit_permission(vault) is VaultDepositPermission.permissionless
    assert vault.get_strategy_tags() == {StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.perpetual_futures}
    assert vault.get_estimated_lock_up().total_seconds() == 0
    assert vault.get_deposit_manager_capability().as_initial_public_schema() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "synchronous",
        "redemption_flow": "synchronous",
    }


def test_veranta_metadata_repair(web3: Web3, fork_block: int) -> None:
    """Exercise the production repair against each real contract generation.

    Offline runner tests cover persistence; this test proves that the repair's
    pinned asset, implementation and fee reads work together on Base state.

    :param web3: Snapshot-isolated fixed-block Base fork.
    :param fork_block: Selected implementation generation.
    :return: None; the real repair refreshes an isolated catalogue row.
    """
    spec = VaultSpec(8453, VERANTA_VAULT_ADDRESS)
    timestamp = datetime.datetime(2026, 1, 1)
    detection = ERC4262VaultDetection(8453, VERANTA_VAULT_ADDRESS, 1, timestamp, {ERC4626Feature.gains_tranche_like}, timestamp, 1, 1)
    database = VaultDatabase(rows={spec: {"Protocol": "<protocol not yet identified>", "_detection_data": detection}})
    report = refresh_veranta_metadata(database, web3)
    assert report["changed_rows"] == 1
    assert report["read_block"] == fork_block
    assert database.rows[spec]["Protocol"] == "Veranta"
    assert database.rows[spec]["Withdraw fee"] == pytest.approx(0.005 if fork_block == BASE_MIDNIGHT_BLOCK else 0.015)
    assert database.rows[spec]["_deposit_permission"] == "permissionless"
    assert refresh_veranta_metadata(database, web3)["changed_rows"] == 0


def test_veranta_guarded_lifecycle(web3: Web3, vault: VerantaVault, fork_block: int) -> None:
    """Prove guarded approval, deposit and full fee-adjusted redemption.

    Includes receiver, owner and approval redirection refusals and preflight
    capacity failures without broadcasting a transaction.

    :param web3: Snapshot-isolated Base fork.
    :param vault: Autodetected adapter.
    :param fork_block: Selected implementation generation.
    :return: None; actual balances and receipt analysis prove both directions.
    """
    governance, manager_wallet = HotWallet(Account.create()), HotWallet(Account.create())
    for wallet in (governance, manager_wallet):
        set_balance(web3, wallet.address, Web3.to_wei(10, "ether"))
        wallet.sync_nonce(web3)
    custody = deploy_contract(web3, "guard/SimpleVaultV0.json", governance, manager_wallet.address, libraries=GUARD_LIBRARIES)
    broadcast(web3, governance, custody.functions.initialiseOwnership(governance.address))
    guard = get_deployed_contract(web3, "guard/GuardV0.json", custody.functions.guard().call())
    broadcast(web3, governance, guard.functions.whitelistERC4626(vault.address, "Veranta"))
    manager = vault.get_deposit_manager()
    assert isinstance(manager, VerantaDepositManager)
    raw_amount = vault.denomination_token.convert_to_raw(Decimal(10))
    fund_erc20_on_anvil(web3, vault.denomination_token.address, custody.address, raw_amount)
    expected_shares = vault.vault_contract.functions.previewDeposit(raw_amount).call()
    assert manager.estimate_deposit(custody.address, Decimal(10)) == vault.share_token.convert_to_decimals(expected_shares)
    approval = vault.denomination_token.contract.functions.approve(manager.get_deposit_approval_target(), raw_amount)
    perform(web3, custody, manager_wallet, approval)
    request = manager.create_deposit_request(owner=custody.address, raw_amount=raw_amount)
    deposit_hash = perform(web3, custody, manager_wallet, request.funcs[0])
    analysis = manager.analyse_deposit(deposit_hash, request.parse_deposit_transaction([deposit_hash]))
    assert analysis.denomination_amount == Decimal(10)
    raw_shares = vault.share_token.fetch_raw_balance_of(custody.address)
    assert raw_shares == expected_shares
    assert analysis.share_count == vault.share_token.convert_to_decimals(expected_shares)
    assert vault.denomination_token.fetch_raw_balance_of(custody.address) == 0
    assert manager.force_settle(None).settlement_required is False

    before_nonce = web3.eth.get_transaction_count(manager_wallet.address)
    cap = manager.fetch_depositable_raw_assets(custody.address)
    assert cap == max(0, vault.vault_contract.functions.depositCap().call() - vault.vault_contract.functions.totalAssets().call() - 1)
    assert cap < 2**128
    with pytest.raises(VaultFlowUnavailable):
        manager.create_deposit_request(owner=custody.address, raw_amount=cap + 1, check_enough_token=False)
    expected_cap_error = "DEPOSIT_CAP_BREACHED" if fork_block == BASE_MIDNIGHT_BLOCK else "ERC4626: deposit more than max"
    with pytest.raises((ContractLogicError, ExtraValueError), match=expected_cap_error):
        vault.vault_contract.functions.deposit(cap + 1, custody.address).call({"from": custody.address})

    # Protocol governance can block withdrawals without imposing an identity gate.
    vm_address = vault.vault_contract.functions.vaultManager().call()
    gov_selector = Web3.keccak(text="gov()")[:4]
    protocol_governance = Web3.to_checksum_address(web3.eth.call({"to": vm_address, "data": gov_selector})[-20:])
    web3.provider.make_request("anvil_impersonateAccount", [protocol_governance])
    set_balance(web3, protocol_governance, Web3.to_wei(1, "ether"))
    original_threshold = vault.vault_contract.functions.withdrawThreshold().call()
    tx = vault.vault_contract.functions.setWithdrawThreshold(1).transact({"from": protocol_governance, "gas": 200_000})
    assert_transaction_success_with_explanation(web3, tx)
    with pytest.raises(VaultFlowUnavailable):
        manager.create_redemption_request(owner=custody.address, raw_shares=raw_shares)
    with pytest.raises(VaultFlowUnavailable) as simulation_failure:
        manager.create_redemption_request(owner=custody.address, raw_shares=raw_shares, check_max_redeem=False)
    assert simulation_failure.value.preflight_result == "redemption_call_reverted"
    assert simulation_failure.value.error_selector == Web3.keccak(text="Error(string)")[:4]
    tx = vault.vault_contract.functions.setWithdrawThreshold(original_threshold).transact({"from": protocol_governance, "gas": 200_000})
    assert_transaction_success_with_explanation(web3, tx)
    assert web3.eth.get_transaction_count(manager_wallet.address) == before_nonce

    # Prove GuardV0's own refusals on the canonical fork, with both would-be
    # deposit and alternate-owner redemption otherwise funded and approved.
    if fork_block == BASE_MIDNIGHT_BLOCK:
        attacker = web3.eth.accounts[9]
        fund_erc20_on_anvil(web3, vault.denomination_token.address, custody.address, raw_amount)
        perform(web3, custody, manager_wallet, approval)
        fund_erc20_on_anvil(web3, vault.denomination_token.address, attacker, raw_amount)
        tx = vault.denomination_token.contract.functions.approve(vault.address, raw_amount).transact({"from": attacker, "gas": 200_000})
        assert_transaction_success_with_explanation(web3, tx)
        tx = vault.vault_contract.functions.deposit(raw_amount, attacker).transact({"from": attacker, "gas": 2_000_000})
        assert_transaction_success_with_explanation(web3, tx)
        attacker_shares = vault.share_token.fetch_raw_balance_of(attacker)
        tx = vault.share_token.contract.functions.approve(custody.address, attacker_shares).transact({"from": attacker, "gas": 200_000})
        assert_transaction_success_with_explanation(web3, tx)
        invalid = [
            (vault.vault_contract.functions.deposit(raw_amount, attacker), "Receiver not whitelisted"),
            (vault.vault_contract.functions.redeem(raw_shares, attacker, custody.address), "Receiver not whitelisted"),
            (vault.vault_contract.functions.redeem(attacker_shares, custody.address, attacker), "Owner not whitelisted"),
            (vault.denomination_token.contract.functions.approve(attacker, raw_amount), "Approve address not allowed"),
        ]
        for call, reason in invalid:
            target, data = encode_simple_vault_transaction(call)
            with pytest.raises((ContractLogicError, ExtraValueError), match=reason):
                custody.functions.performCall(target, data).call({"from": manager_wallet.address})

    reserve_before_redemption = vault.denomination_token.fetch_raw_balance_of(custody.address)
    expected_assets = vault.vault_contract.functions.previewRedeem(raw_shares).call()
    assert manager.estimate_redeem(custody.address, vault.share_token.convert_to_decimals(raw_shares)) == vault.denomination_token.convert_to_decimals(expected_assets)
    gross_assets = vault.vault_contract.functions.convertToAssets(raw_shares).call()
    assert expected_assets < gross_assets
    assert gross_assets / expected_assets - 1 == pytest.approx(vault.get_withdraw_fee("latest"), rel=1e-3)
    redemption = manager.create_redemption_request(owner=custody.address, raw_shares=raw_shares)
    redeem_hash = perform(web3, custody, manager_wallet, redemption.funcs[0])
    result = manager.analyse_redemption(redeem_hash, redemption.parse_redeem_transaction([redeem_hash]))
    assert result.denomination_amount == vault.denomination_token.convert_to_decimals(expected_assets)
    assert vault.denomination_token.fetch_raw_balance_of(custody.address) == reserve_before_redemption + expected_assets
    assert vault.share_token.fetch_raw_balance_of(custody.address) == 0
    rebuilt = manager.create_redemption_request_for_analysis(custody.address, raw_shares)
    assert rebuilt.shares == vault.share_token.convert_to_decimals(raw_shares)
    assert rebuilt.raw_shares == raw_shares
    rebuilt_result = manager.analyse_redemption(redeem_hash, rebuilt.parse_redeem_transaction([redeem_hash]))
    assert rebuilt_result.denomination_amount == result.denomination_amount
    assert rebuilt_result.share_count == result.share_count
