"""Exercise both Upshift withdrawal ABIs through the deployed custody guard."""

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction
from web3.exceptions import ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.upshift.deployment import fetch_upshift_guard_calls
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault
from eth_defi.provider.anvil import fund_erc20_on_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
pytestmark = [pytest.mark.skipif(not JSON_RPC_ETHEREUM, reason="JSON_RPC_ETHEREUM required"), pytest.mark.xdist_group("fork:ethereum:midnight")]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Reuse the canonical shared Ethereum fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


@pytest.fixture(scope="module")
def custody(web3: Web3) -> tuple[Contract, Contract, HexAddress]:
    """Deploy expensive guarded custody once before taking test snapshots."""
    owner, manager = web3.eth.accounts[:2]
    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", owner, manager, libraries=GUARD_LIBRARIES)
    assert_transaction_success_with_explanation(web3, vault.functions.initialiseOwnership(owner).transact({"from": owner}))
    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())
    return vault, guard, manager


@pytest.fixture(autouse=True)
def snapshot(anvil_fork_pool: AnvilForkPool, custody: tuple[Contract, Contract, HexAddress]) -> Iterator[None]:
    """Isolate mutations while preserving the deployed custody contracts."""
    yield from evm_snapshot_revert(anvil_fork_pool.get_launch(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK))


def _perform(web3: Web3, custody: tuple[Contract, Contract, HexAddress], function: ContractFunction) -> HexBytes:
    """Submit the actual manager call through delegated GuardV0 custody."""
    vault, _, manager = custody
    target, data = encode_simple_vault_transaction(function)
    tx = vault.functions.performCall(target, data).transact({"from": manager, "gas": 3_000_000})
    assert_transaction_success_with_explanation(web3, tx)
    return tx


@pytest.mark.parametrize(
    "address,expected_shares,expected_payout,instant,public_batch",
    [
        ("0x74aD2F789Ed583DBd141bbdafC673fE1F033718b", 9_696_832, 9_999_999, False, False),
        ("0x74aD2F789Ed583DBd141bbdafC673fE1F033718b", 9_696_832, 9_999_999, False, True),
        ("0x74aD2F789Ed583DBd141bbdafC673fE1F033718b", 9_696_832, 9_980_000, True, False),
        ("0xE9B725010A9E419412ed67d0fA5f3A5f40159D32", 9_737_856, 9_999_999, False, False),
        ("0x998D7b14c123c1982404562b68edDB057b0477cB", 9_366_802, 9_999_999, False, False),
        ("0x80E1048eDE66ec4c364b4F22C8768fc657FF6A42", 9_463_784, 9_999_999, False, False),
    ],
)
def test_guarded_upshift_round_trip(web3: Web3, custody: tuple[Contract, Contract, HexAddress], address: HexAddress, expected_shares: int, expected_payout: int, instant: bool, public_batch: bool) -> None:
    """Prove manager calls, epoch parsing, guarded claims and exact accounting.

    1. Configure the production guard policy and fund only the depositor.
    2. Deposit through the guard and verify actual minted shares.
    3. Request withdrawal, reload its ticket and advance only fork time.
    4. Analyse the exact payout from a guarded claim or an outsider's public batch.
    """
    # 1. Configure the production guard policy and fund only the depositor.
    simple, guard, _ = custody
    vault = create_vault_instance_autodetect(web3, address)
    assert isinstance(vault, UpshiftVault)
    for function in fetch_upshift_guard_calls(guard, vault, "Upshift integration"):
        assert_transaction_success_with_explanation(web3, function.transact({"from": web3.eth.accounts[0]}))
    manager = vault.get_deposit_manager()
    outsider = web3.eth.accounts[3]
    bad = vault.upshift_contract.functions.deposit(vault.denomination_token.address, 1, outsider) if vault.multi_asset_like else vault.upshift_contract.functions.deposit(1, outsider)
    target, data = encode_simple_vault_transaction(bad)
    with pytest.raises((ContractLogicError, ValueError), match="Receiver not whitelisted"):
        simple.functions.performCall(target, data).call({"from": custody[2]})
    if vault.multi_asset_like:
        bad_asset = vault.upshift_contract.functions.deposit(outsider, 1, simple.address)
        target, data = encode_simple_vault_transaction(bad_asset)
        with pytest.raises((ContractLogicError, ValueError), match="Token not allowed"):
            simple.functions.performCall(target, data).call({"from": custody[2]})
    if not vault.multi_asset_like:
        bad = vault.upshift_contract.functions.requestRedeem(1, simple.address, outsider)
        target, data = encode_simple_vault_transaction(bad)
        with pytest.raises((ContractLogicError, ValueError), match="Owner not whitelisted"):
            simple.functions.performCall(target, data).call({"from": custody[2]})
    asset = vault.denomination_token
    fund_erc20_on_anvil(web3, asset.address, simple.address, asset.convert_to_raw(Decimal(10)))
    _perform(web3, custody, asset.approve(manager.get_deposit_approval_target(), Decimal(10)))
    # 2. Deposit through the guard and verify actual minted shares.
    kwargs = {"accepted_asset": asset.address} if vault.multi_asset_like else {}
    request = manager.create_deposit_request(simple.address, amount=Decimal(10), **kwargs)
    txs = [_perform(web3, custody, function) for function in request.funcs]
    analysis = manager.analyse_deposit(txs[-1], request.parse_deposit_transaction(txs))
    assert analysis.denomination_amount == Decimal(10)
    assert asset.address_lower in {token.address_lower for token in vault.fetch_all_denomination_tokens()}
    assert vault.share_token.fetch_raw_balance_of(simple.address) == expected_shares
    # 3. Request withdrawal, reload its ticket and advance only fork time.
    withdrawal = manager.create_redemption_request(simple.address, raw_shares=expected_shares, instant=instant)
    txs = [_perform(web3, custody, function) for function in withdrawal.funcs]
    ticket = withdrawal.parse_redeem_transaction(txs)
    if not instant and not manager.has_synchronous_redemption():
        saved = manager.serialize_redemption_ticket(ticket)
        with pytest.raises(AssertionError, match="date and epoch"):
            manager.reconstruct_redemption_ticket(saved | {"upshift_claimable_epoch": ticket.claimable_epoch + 86400})
        ticket = manager.reconstruct_redemption_ticket(saved)
        assert ticket.year == 2026
        assert manager.get_redemption_request_status(ticket) is AsyncVaultRequestStatus.pending
        result = manager.force_settle(ticket)
        assert result.status_after is AsyncVaultRequestStatus.claimable
        assert result.synthetic_assets_injected_raw == 0
        if public_batch:
            tx = vault.upshift_contract.functions.processAllClaimsByDate(ticket.year, ticket.month, ticket.day, 1).transact({"from": outsider})
            assert_transaction_success_with_explanation(web3, tx)
        else:
            tx = _perform(web3, custody, manager.finish_redemption(ticket))
        assert manager.get_redemption_request_status(ticket) is AsyncVaultRequestStatus.none
    else:
        tx = txs[-1]
    # 4. Analyse the exact payout from a guarded claim or an outsider's public batch.
    analysis = manager.analyse_redemption(tx, ticket)
    assert analysis.share_count == vault.share_token.convert_to_decimals(expected_shares)
    assert analysis.denomination_amount == asset.convert_to_decimals(expected_payout)
    assert vault.share_token.fetch_raw_balance_of(simple.address) == 0
    assert asset.fetch_raw_balance_of(simple.address) == expected_payout


@pytest.mark.skipif(not os.environ.get("HYPERSYNC_API_KEY"), reason="HYPERSYNC_API_KEY required")
def test_upshift_historical_payout_recovery(web3: Web3) -> None:
    """Recover a real historical payout through the actual Hypersync provider.

    1. Reconstruct the known mainnet request from its actual receipt.
    2. Find its completed payout and validate date, shares and USDC transfer.
    """
    # 1. Reconstruct the known mainnet request from its actual receipt.
    # Anvil does not expose arbitrary historical transaction receipts.
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    vault = create_vault_instance_autodetect(web3, "0x74aD2F789Ed583DBd141bbdafC673fE1F033718b")
    manager = vault.get_deposit_manager()
    owner = Web3.to_checksum_address("0xf76663a0876fd57bbae65291f291df2b4acbe33d")
    request = manager.create_redemption_request_for_analysis(owner, 982_352_046)
    ticket = request.parse_redeem_transaction([HexBytes("0x40efc71d6d4aef920bbadd167a46598adfab13284cc23975f5f271667f4e7901")])
    # 2. Find its completed payout and validate date, shares and USDC transfer.
    tx = manager.fetch_completed_redemption_tx_hash(ticket)
    assert tx == HexBytes("0x295dd9ff252902841dde27bd87b75d8f49ff29f6c07a9b663e0172ce67271e28")
    assert manager.analyse_redemption(tx, ticket).denomination_amount == Decimal("1010.322429")


def test_upshift_outside_addition_halts(web3: Web3, custody: tuple[Contract, Contract, HexAddress]) -> None:
    """Refuse outside shares added to the custody receiver/date obligation.

    1. Deposit and request through delegated guarded custody.
    2. Let an independent holder add a real small request for that receiver.
    3. Halt status and valuation without attempting an aggregate payout split.
    """
    # 1. Deposit and request through delegated guarded custody.
    simple, guard, _ = custody
    vault = create_vault_instance_autodetect(web3, "0x74aD2F789Ed583DBd141bbdafC673fE1F033718b")
    manager = vault.get_deposit_manager()
    for function in fetch_upshift_guard_calls(guard, vault, "Upshift aggregate refusal"):
        assert_transaction_success_with_explanation(web3, function.transact({"from": web3.eth.accounts[0]}))
    token = vault.denomination_token
    fund_erc20_on_anvil(web3, token.address, simple.address, token.convert_to_raw(Decimal(10)))
    request = manager.create_deposit_request(simple.address, amount=Decimal(10), accepted_asset=token.address)
    for function in request.funcs:
        _perform(web3, custody, function)
    request = manager.create_redemption_request(simple.address, raw_shares=vault.share_token.fetch_raw_balance_of(simple.address))
    hashes = [_perform(web3, custody, function) for function in request.funcs]
    ticket = request.parse_redeem_transaction(hashes)
    # 2. Let an independent holder add a real small request for that receiver.
    outsider = web3.eth.accounts[3]
    fund_erc20_on_anvil(web3, token.address, outsider, token.convert_to_raw(Decimal(1)))
    manager.create_deposit_request(outsider, amount=Decimal(1), accepted_asset=token.address).broadcast()
    assert_transaction_success_with_explanation(web3, vault.share_token.approve(vault.address, vault.share_token.convert_to_decimals(1)).transact({"from": outsider}))
    assert_transaction_success_with_explanation(web3, vault.upshift_contract.functions.requestRedeem(1, simple.address).transact({"from": outsider}))
    # 3. Halt status and valuation without attempting an aggregate payout split.
    with pytest.raises(AssertionError, match="aggregate"):
        manager.get_redemption_request_status(ticket)
    with pytest.raises(AssertionError, match="aggregate"):
        manager.fetch_pending_redemption_value(ticket)
