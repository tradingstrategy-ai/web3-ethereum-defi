"""Nova Markets exact-address detection and guarded epoch lifecycle.

The Conviction Pool was deployed after the canonical July Arbitrum block.
Use the fixed, source-verified 2026-10-27 23:08 snapshot instead, shared through
the Anvil pool with a matching RPC-cache seed and mutation isolation.
"""

import datetime
import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance_autodetect, detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import GainsDepositManager
from eth_defi.erc_4626.vault_protocol.nova_markets.constants import NOVA_POOL_ADDRESS, NOVA_POOL_APP
from eth_defi.erc_4626.vault_protocol.nova_markets.vault import NovaMarketsVault
from eth_defi.provider.anvil import fund_erc20_on_anvil
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus
from eth_defi.vault.fee import FeeData, VaultFeeMode

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
NOVA_FORK_BLOCK = 518_876_926
EXPECTED_DEPOSIT_SHARES = 94_934_580
EXPECTED_TOTAL_ASSETS = 206_502_017_790
EXPECTED_TOTAL_SUPPLY = 194_515_028_053
EXPECTED_REDEEMED_AMOUNT = 99_999_999
#: Exact implementation whose executable was reproduced from reviewed source.
REVIEWED_IMPLEMENTATION = "0xa17deCd03257B84BF3724C0425268c02f344dB33"
REVIEWED_RUNTIME_HASH = "0c471a563ecf2eca1efcf04f58b7daa873099b35b6973210932eaf445e1f0fcf"

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed"),
    pytest.mark.xdist_group("fork:arbitrum:nova-518876926"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Use the shared fixed Nova fork and the warmed RPC cache."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, NOVA_FORK_BLOCK)


@pytest.fixture
def isolated_nova_fork(anvil_fork_pool: AnvilForkPool) -> Iterator[None]:
    """Revert mutations on the current pooled launch after the lifecycle test."""
    launch = anvil_fork_pool.get_launch(JSON_RPC_ARBITRUM, NOVA_FORK_BLOCK)
    yield from evm_snapshot_revert(launch)


def test_nova_live_metadata(web3: Web3) -> None:
    """Real contract reads select Nova alone and return fixed-block metadata."""
    features = detect_vault_features(web3, NOVA_POOL_ADDRESS)
    assert features == {ERC4626Feature.nova_markets_like}
    assert ERC4626Feature.gains_like not in features
    vault = create_vault_instance_autodetect(web3, NOVA_POOL_ADDRESS)
    assert isinstance(vault, NovaMarketsVault)
    assert vault.get_protocol_name() == "Nova Markets"
    assert vault.name == "Nova Conviction Pool"
    assert vault.symbol == "nUSDC"
    assert vault.denomination_token.symbol == "USDC"
    assert vault.get_link() == NOVA_POOL_APP
    implementation_slot = int.from_bytes(Web3.keccak(text="eip1967.proxy.implementation"), "big") - 1
    implementation = web3.eth.get_storage_at(vault.address, implementation_slot)[-20:]
    assert Web3.to_checksum_address(implementation.hex()) == REVIEWED_IMPLEMENTATION
    assert Web3.keccak(web3.eth.get_code(REVIEWED_IMPLEMENTATION)).hex() == REVIEWED_RUNTIME_HASH
    assert vault.get_fee_data() == FeeData(fee_mode=VaultFeeMode.feeless, management=0.0, performance=0.0, deposit=0.0, withdraw=0.0, protocol=0.0)
    assert vault.vault_contract.functions.totalAssets().call() == EXPECTED_TOTAL_ASSETS
    assert vault.vault_contract.functions.totalSupply().call() == EXPECTED_TOTAL_SUPPLY
    period = vault.get_withdrawal_period()
    assert period.min_period == datetime.timedelta(days=3)
    assert period.max_period == datetime.timedelta(days=9)


@pytest.mark.usefixtures("isolated_nova_fork")
def test_guarded_nova_deposit_and_epoch_redemption(web3: Web3) -> None:  # noqa: PLR0914 - complete guarded lifecycle
    """Exercise approve, deposit, request, epoch settlement and claim via GuardV0."""
    vault = create_vault_instance_autodetect(web3, NOVA_POOL_ADDRESS)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, GainsDepositManager)
    assert vault.get_deposit_manager_capability().as_initial_public_schema() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "synchronous",
        "redemption_flow": "asynchronous",
        "supports_anvil_settlement": True,
    }
    deployer = HexAddress(web3.eth.accounts[0])
    asset_manager = HexAddress(web3.eth.accounts[1])
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)
    assert_transaction_success_with_explanation(web3, simple_vault.functions.initialiseOwnership(deployer).transact({"from": deployer}))
    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    assert_transaction_success_with_explanation(web3, guard.functions.whitelistERC4626(vault.address, "Nova Conviction Pool").transact({"from": deployer}))
    raw_amount = vault.denomination_token.convert_to_raw(Decimal(100))
    fund_erc20_on_anvil(web3, vault.denomination_token.address, simple_vault.address, raw_amount)

    approval = vault.denomination_token.contract.functions.approve(manager.get_deposit_approval_target(), raw_amount)
    target, data = encode_simple_vault_transaction(approval)
    approval_tx = simple_vault.functions.performCall(target, data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, approval_tx)
    deposit_request = manager.create_deposit_request(owner=simple_vault.address, raw_amount=raw_amount)
    target, data = encode_simple_vault_transaction(deposit_request.funcs[0])
    deposit_tx = simple_vault.functions.performCall(target, data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, deposit_tx)
    assert vault.share_token.fetch_raw_balance_of(simple_vault.address) == EXPECTED_DEPOSIT_SHARES

    request = manager.create_redemption_request(owner=simple_vault.address, raw_shares=EXPECTED_DEPOSIT_SHARES)
    target, data = encode_simple_vault_transaction(request.funcs[0])
    request_tx = simple_vault.functions.performCall(target, data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, request_tx)
    ticket = request.parse_redeem_transaction([request_tx])
    assert manager.get_redemption_request_status(ticket) == AsyncVaultRequestStatus.pending
    settlement = manager.force_settle(ticket)
    assert settlement.status_after == AsyncVaultRequestStatus.claimable
    target, data = encode_simple_vault_transaction(manager.finish_redemption(ticket))
    claim_tx = simple_vault.functions.performCall(target, data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, claim_tx)
    assert vault.share_token.fetch_raw_balance_of(simple_vault.address) == 0
    assert vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == EXPECTED_REDEEMED_AMOUNT
