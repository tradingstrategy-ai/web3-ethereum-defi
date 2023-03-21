"""Read Enzyme vault events.

- Deposits

- Withdrawals
"""
from functools import partial
from typing import cast
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.enzyme.events import fetch_vault_balance_events, Deposit, Withdrawal
from eth_defi.enzyme.vault import Vault
from eth_defi.event_reader.reader import extract_events, Web3EventReader
from eth_defi.trace import assert_transaction_success_with_explanation, TransactionAssertionError
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment


@pytest.fixture
def deployment(
        web3: Web3,
        deployer: HexAddress,
        user_1: HexAddress,
        user_2,
        weth: Contract,
        mln: Contract,
        usdc: Contract,
        weth_usd_mock_chainlink_aggregator: Contract,
        usdc_usd_mock_chainlink_aggregator: Contract,
) -> EnzymeDeployment:
    """Create Enzyme deployment that supports WETH and USDC tokens"""

    deployment = EnzymeDeployment.deploy_core(
        web3,
        deployer,
        mln,
        weth,
    )

    deployment.add_primitive(
        usdc,
        usdc_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )

    deployment.add_primitive(
        weth,
        weth_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )

    return deployment


def test_read_deposit(
        web3: Web3,
        deployer: HexAddress,
        user_1: HexAddress,
        user_2,
        user_3,
        weth: Contract,
        mln: Contract,
        usdc: Contract,
        weth_usd_mock_chainlink_aggregator: Contract,
        usdc_usd_mock_chainlink_aggregator: Contract,
        deployment: EnzymeDeployment,
        uniswap_v2: UniswapV2Deployment,
        weth_usdc_pair: Contract,
):
    """Translate Enzyme smart contract events to our internal deposit format."""

    comptroller_contract, vault_contract = deployment.create_new_vault(
        user_1,
        usdc,
        fund_name="Cow says Moo",
        fund_symbol="MOO"
    )

    vault = Vault(vault_contract, comptroller_contract)

    read_events: Web3EventReader = cast(Web3EventReader, partial(extract_events))

    # After deployment we see zero deposit events
    start_block = 1
    end_block = web3.eth.block_number
    balance_events = list(fetch_vault_balance_events(vault, start_block, end_block, read_events))
    assert len(balance_events) == 0

    # User 2 buys into the vault
    # See Shares.sol
    #
    # Buy shares for 500 USDC, receive min share
    usdc.functions.transfer(user_1, 500 * 10 ** 6).transact({"from": deployer})
    usdc.functions.approve(vault.comptroller.address, 500*10**6).transact({"from": user_1})
    vault.comptroller.functions.buyShares(500*10**6, 1).transact({"from": user_1})

    assert vault.get_total_supply() == 500 * 10**18

    old_end_block = end_block
    end_block = web3.eth.block_number
    print("Reading range", old_end_block, end_block)
    balance_events = list(fetch_vault_balance_events(vault, old_end_block, end_block, read_events))

    # Check the deposit event was correctly read
    assert len(balance_events) == 1
    deposit = balance_events[0]
    assert isinstance(deposit, Deposit)
    assert deposit.denomination_token.address == usdc.address
    assert deposit.denomination_token.decimals == 6
    assert deposit.receiver == user_1
    assert deposit.investment_amount == Decimal(500)
    assert deposit.shares_issued == Decimal(500)


def test_read_withdrawal(
        web3: Web3,
        deployer: HexAddress,
        user_1: HexAddress,
        user_2,
        user_3,
        weth: Contract,
        mln: Contract,
        usdc: Contract,
        weth_usd_mock_chainlink_aggregator: Contract,
        usdc_usd_mock_chainlink_aggregator: Contract,
        deployment: EnzymeDeployment,
        uniswap_v2: UniswapV2Deployment,
        weth_usdc_pair: Contract,
):
    """Translate Enzyme smart contract events to our internal withdrawal format."""

    comptroller_contract, vault_contract = deployment.create_new_vault(
        user_1,
        usdc,
        fund_name="Cow says Moo",
        fund_symbol="MOO"
    )

    vault = Vault(vault_contract, comptroller_contract)

    read_events: Web3EventReader = cast(Web3EventReader, partial(extract_events))

    # After deployment we see zero deposit events
    start_block = 1
    end_block = web3.eth.block_number
    balance_events = list(fetch_vault_balance_events(vault, start_block, end_block, read_events))
    assert len(balance_events) == 0

    # User 2 buys into the vault
    # See Shares.sol
    #
    # Buy shares for 500 USDC, receive min share
    usdc.functions.transfer(user_1, 500 * 10 ** 6).transact({"from": deployer})
    usdc.functions.approve(vault.comptroller.address, 500*10**6).transact({"from": user_1})
    vault.comptroller.functions.buyShares(500*10**6, 1).transact({"from": user_1})

    assert vault.get_total_supply() == 500 * 10**18

    # Withdraw half of the shares
    # See ComptrollerLib
    tx_hash = vault.comptroller.functions.redeemSharesInKind(user_1, 250*10**18, [], []).transact({"from": user_1})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert vault.get_total_supply() == 250 * 10**18

    old_end_block = end_block
    end_block = web3.eth.block_number
    balance_events = list(fetch_vault_balance_events(vault, old_end_block, end_block, read_events))

    # Check the deposit event was correctly read
    assert len(balance_events) == 2
    deposit = balance_events[0]
    withdrawal = balance_events[1]

    assert isinstance(deposit, Deposit)
    assert isinstance(withdrawal, Withdrawal)

    assert withdrawal.receiver == user_1
    assert withdrawal.redeemer == user_1
    assert len(withdrawal.redeemed_assets) == 1
    asset, amount = withdrawal.redeemed_assets[0]
    assert asset.address == usdc.address
    assert asset.convert_to_decimals(amount) == 250

    # Withdraw the rest
    old_end_block = end_block
    end_block = web3.eth.block_number
    tx_hash = vault.comptroller.functions.redeemSharesInKind(user_1, 250*10**18, [], []).transact({"from": user_1})
    assert vault.get_total_supply() == 0
    assert_transaction_success_with_explanation(web3, tx_hash)
    balance_events = list(fetch_vault_balance_events(vault, old_end_block, end_block, read_events))
    assert len(balance_events) == 1

    # No more withdrawwals
    tx_hash = vault.comptroller.functions.redeemSharesInKind(user_1, 250*10**18, [], []).transact({"from": user_1})
    with pytest.raises(TransactionAssertionError):
        assert_transaction_success_with_explanation(web3, tx_hash)

