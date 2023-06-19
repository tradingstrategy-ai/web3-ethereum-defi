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

from eth_defi.deploy import deploy_contract
from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.enzyme.events import fetch_vault_balance_events, Deposit, Redemption, fetch_vault_balances
from eth_defi.enzyme.uniswap_v2 import prepare_swap
from eth_defi.enzyme.vault import Vault
from eth_defi.event_reader.reader import extract_events, Web3EventReader
from eth_defi.trace import assert_transaction_success_with_explanation, TransactionAssertionError
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment


@pytest.fixture
def deployment(
    web3: Web3,
    deployer: HexAddress,
    user_1: HexAddress,
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


@pytest.fixture()
def vault(
    deployment,
    user_1: HexAddress,
    usdc: Contract,
) -> Vault:
    """Create a vault for the tests."""
    comptroller_contract, vault_contract = deployment.create_new_vault(user_1, usdc, fund_name="Cow says Moo", fund_symbol="MOO")

    vault = Vault(vault_contract, comptroller_contract, deployment)
    return vault


@pytest.fixture()
def generic_adapter(
    web3: Web3,
    deployment: EnzymeDeployment,
    vault: Vault,
    deployer: HexAddress,
):
    """Deploy generic adapter contract."""
    generic_adapter = deploy_contract(
        web3,
        f"VaultSpecificGenericAdapter.json",
        deployer,
        deployment.contracts.integration_manager.address,
        vault.address,
    )
    return generic_adapter


def test_read_deposit(
    web3: Web3,
    deployer: HexAddress,
    user_1: HexAddress,
    usdc: Contract,
    vault: Vault,
):
    """Translate Enzyme smart contract events to our internal deposit format."""

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
    usdc.functions.transfer(user_1, 500 * 10**6).transact({"from": deployer})
    usdc.functions.approve(vault.comptroller.address, 500 * 10**6).transact({"from": user_1})
    vault.comptroller.functions.buyShares(500 * 10**6, 1).transact({"from": user_1})

    assert vault.get_total_supply() == 500 * 10**18

    old_end_block = end_block
    end_block = web3.eth.block_number
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
    usdc: Contract,
    vault: Vault,
):
    """Translate Enzyme smart contract events to our internal withdrawal format."""

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
    usdc.functions.transfer(user_1, 500 * 10**6).transact({"from": deployer})
    usdc.functions.approve(vault.comptroller.address, 500 * 10**6).transact({"from": user_1})
    vault.comptroller.functions.buyShares(500 * 10**6, 1).transact({"from": user_1})

    assert vault.get_total_supply() == 500 * 10**18

    # Withdraw half of the shares
    # See ComptrollerLib
    tx_hash = vault.comptroller.functions.redeemSharesInKind(user_1, 250 * 10**18, [], []).transact({"from": user_1})
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
    assert isinstance(withdrawal, Redemption)

    assert withdrawal.receiver == user_1
    assert withdrawal.redeemer == user_1
    assert len(withdrawal.redeemed_assets) == 1
    asset, amount = withdrawal.redeemed_assets[0]
    assert asset.address == usdc.address
    assert asset.convert_to_decimals(amount) == 250

    # Withdraw the rest
    old_end_block = end_block
    end_block = web3.eth.block_number
    tx_hash = vault.comptroller.functions.redeemSharesInKind(user_1, 250 * 10**18, [], []).transact({"from": user_1})
    assert vault.get_total_supply() == 0
    assert_transaction_success_with_explanation(web3, tx_hash)
    balance_events = list(fetch_vault_balance_events(vault, old_end_block, end_block, read_events))
    assert len(balance_events) == 1

    # No more withdrawwals
    tx_hash = vault.comptroller.functions.redeemSharesInKind(user_1, 250 * 10**18, [], []).transact({"from": user_1})
    with pytest.raises(TransactionAssertionError):
        assert_transaction_success_with_explanation(web3, tx_hash)


def test_read_withdrawal_in_kind(
    web3: Web3,
    deployer: HexAddress,
    vault: Vault,
    user_1: HexAddress,
    user_2: HexAddress,
    weth: Contract,
    usdc: Contract,
    deployment: EnzymeDeployment,
    uniswap_v2: UniswapV2Deployment,
    generic_adapter: Contract,
    weth_usdc_pair: Contract,
):
    """Attempt withdrawal of undetlying Enzyme assets.

    - The vault has 2 shareholders

    - The vault swaps some USDC->ETH, so that it holds two tokens

    - See that redemption gives us assets in both tokens
    """

    read_events: Web3EventReader = cast(Web3EventReader, partial(extract_events))

    # User 1 buys into the vault
    #
    # Buy shares for 500 USDC
    usdc.functions.transfer(user_1, 500 * 10**6).transact({"from": deployer})
    usdc.functions.approve(vault.comptroller.address, 500 * 10**6).transact({"from": user_1})
    vault.comptroller.functions.buyShares(500 * 10**6, 1).transact({"from": user_1})

    # User 2 buys into the vault
    #
    # Buy shares for 1000 USDC
    usdc.functions.transfer(user_2, 1000 * 10**6).transact({"from": deployer})
    usdc.functions.approve(vault.comptroller.address, 1000 * 10**6).transact({"from": user_2})
    vault.comptroller.functions.buyShares(1000 * 10**6, 1).transact({"from": user_2})
    assert vault.get_total_supply() == 1500 * 10**18

    # Vault swaps USDC->ETH for both users
    # Buy ETH worth of 200 USD
    prepared_tx = prepare_swap(
        deployment,
        vault,
        uniswap_v2,
        generic_adapter,
        usdc,
        weth,
        200 * 10**6,  # 200 USD
    )

    tx_hash = prepared_tx.transact({"from": user_1})
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert usdc.functions.balanceOf(vault.vault.address).call() == 1300 * 10**6
    assert weth.functions.balanceOf(vault.vault.address).call() == 124500872629987902  # 0.12450087262998791

    # Initiate in-kind withdrawal for the user 2, withdraw all shares
    current_block = web3.eth.block_number
    share_count = vault.get_share_count_for_user(user_2)
    tx_hash = vault.comptroller.functions.redeemSharesInKind(user_2, share_count, [], []).transact({"from": user_2})
    assert_transaction_success_with_explanation(web3, tx_hash)
    end_block = web3.eth.block_number

    # Vault positions have decreased
    assert usdc.functions.balanceOf(vault.vault.address).call() == 433333334
    assert weth.functions.balanceOf(vault.vault.address).call() == 41500290876662634

    # Withdrawal event was fired
    balance_events = list(fetch_vault_balance_events(vault, current_block, end_block, read_events))
    assert len(balance_events) == 1

    withdrawal = balance_events[0]
    assert isinstance(withdrawal, Redemption)

    # Withdraw data is correct for two in-kind assets
    assert withdrawal.receiver == user_2
    assert withdrawal.redeemer == user_2
    assert len(withdrawal.redeemed_assets) == 2
    asset, amount = withdrawal.redeemed_assets[0]
    assert asset.address == usdc.address
    assert asset.convert_to_decimals(amount) == pytest.approx(Decimal("866.666666"))
    asset, amount = withdrawal.redeemed_assets[1]
    assert asset.address == weth.address
    assert asset.convert_to_decimals(amount) == pytest.approx(Decimal("0.083000581753325268"))

    # User 2 got its assets
    assert usdc.functions.balanceOf(user_2).call() == 866666666
    assert weth.functions.balanceOf(user_2).call() == 83000581753325268


def test_read_vault_balances(
    web3: Web3,
    deployer: HexAddress,
    vault: Vault,
    user_1: HexAddress,
    user_2: HexAddress,
    weth: Contract,
    usdc: Contract,
    deployment: EnzymeDeployment,
    uniswap_v2: UniswapV2Deployment,
    generic_adapter: Contract,
    weth_usdc_pair: Contract,
):
    """Read vault live balances."""

    # User 1 buys into the vault
    #
    # Buy shares for 500 USDC
    usdc.functions.transfer(user_1, 500 * 10**6).transact({"from": deployer})
    usdc.functions.approve(vault.comptroller.address, 500 * 10**6).transact({"from": user_1})
    vault.comptroller.functions.buyShares(500 * 10**6, 1).transact({"from": user_1})

    # User 2 buys into the vault
    #
    # Buy shares for 1000 USDC
    usdc.functions.transfer(user_2, 1000 * 10**6).transact({"from": deployer})
    usdc.functions.approve(vault.comptroller.address, 1000 * 10**6).transact({"from": user_2})
    vault.comptroller.functions.buyShares(1000 * 10**6, 1).transact({"from": user_2})
    assert vault.get_total_supply() == 1500 * 10**18

    # Vault swaps USDC->ETH for both users
    # Buy ETH worth of 200 USD
    prepared_tx = prepare_swap(
        deployment,
        vault,
        uniswap_v2,
        generic_adapter,
        usdc,
        weth,
        200 * 10**6,  # 200 USD
    )

    tx_hash = prepared_tx.transact({"from": user_1})
    assert_transaction_success_with_explanation(web3, tx_hash)

    balance_map = {b.token.address: b for b in fetch_vault_balances(vault)}
    assert len(balance_map) == 2
    assert balance_map[usdc.address].balance == 1300
    assert balance_map[weth.address].balance == pytest.approx(Decimal("0.124500872629987902"))
