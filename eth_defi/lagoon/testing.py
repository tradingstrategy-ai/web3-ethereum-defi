"""Lagoon unit test helpers."""

from decimal import Decimal

import pytest
from web3 import Web3

from eth_typing import HexAddress

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.event_reader.conversion import convert_uint256_string_to_int, convert_uin256_to_bytes
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.trace import assert_transaction_success_with_explanation


def fund_lagoon_vault(
    web3: Web3,
    vault_address: HexAddress,
    asset_manager: HexAddress,
    test_account_with_balance: HexAddress,
    trading_strategy_module_address: HexAddress,
    amount=Decimal(500),
    nav=Decimal(0),
):
    """Make sure vault has some starting balance in the unit testing.

    - Used in unit testing to prepare the vault for a test trade to have some capital
    """

    assert vault_address.startswith("0x"), f"Vault address should be an address, got: {vault_address}"
    assert asset_manager.startswith("0x"), f"asset_manager should be an address, got: {asset_manager}"
    assert test_account_with_balance.startswith("0x"), f"test_account_with_balance should be an address, got: {test_account_with_balance}"
    assert trading_strategy_module_address.startswith("0x"), f"trading_strategy_module_address should be an address, got: {trading_strategy_module_address}"

    vault = create_vault_instance(
        web3,
        vault_address,
        features={ERC4626Feature.lagoon_like},
    )
    assert isinstance(vault, LagoonVault), f"Vault is not a Lagoon vault: {vault}"

    vault.trading_strategy_module_address = trading_strategy_module_address

    assert vault.denomination_token.fetch_balance_of(test_account_with_balance) >= amount

    denomination_token = vault.denomination_token
    raw_amount = denomination_token.convert_to_raw(amount)

    # 1. approve
    tx_hash = denomination_token.approve(vault.address, amount).transact({"from": test_account_with_balance})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # 2. put to deposit queue
    deposit_func = vault.request_deposit(test_account_with_balance, raw_amount)
    tx_hash = deposit_func.transact({"from": test_account_with_balance})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # 2.b) deposit waiting in the silo
    assert denomination_token.fetch_balance_of(vault.silo_address) == pytest.approx(amount)

    # 3. update NAV and settle
    tx_hash = vault.post_valuation_and_settle(nav, asset_manager)
    assert_transaction_success_with_explanation(web3, tx_hash)


def force_lagoon_settle(
    vault: LagoonVault,
    asset_manager: HexAddress,
    raw_nav: int = None,
    gas_limit: int = 15_000_000,
):
    """Force settling of the Lagoon vault.

    - Used in the testing to move the vault to the next epoch

    :param asset_manager:
        Spoofed account in Anvil
    """

    assert asset_manager.startswith("0x"), f"asset_manager should be an address, got: {asset_manager}"

    web3 = vault.web3
    balance = web3.eth.get_balance(asset_manager)

    # Top up if needed
    if balance < 10**18:
        tx_hash = web3.eth.send_transaction({"to": asset_manager, "from": web3.eth.accounts[0], "value": 5 * 10**18})
        assert_transaction_success_with_explanation(web3, tx_hash)

    if not raw_nav:
        nav = vault.fetch_nav()
        raw_nav = vault.denomination_token.convert_to_raw(nav)

    tx_hash = vault.vault_contract.functions.updateNewTotalAssets(raw_nav).transact({"from": asset_manager, "gas": gas_limit})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Lagoon security fix
    #     function settleDeposit(uint256 _newTotalAssets) public virtual;
    call = EncodedCall.from_keccak_signature(
        address=vault.address,
        function="settleDeposit()",
        signature=Web3.keccak(text="settleDeposit(uint256)")[0:4],
        data=convert_uin256_to_bytes(raw_nav),
        extra_data=None,
    )
    tx_data = call.transact(
        from_=asset_manager,
        gas_limit=gas_limit,
    )
    tx_hash = web3.eth.send_transaction(tx_data)
    assert_transaction_success_with_explanation(web3, tx_hash)
