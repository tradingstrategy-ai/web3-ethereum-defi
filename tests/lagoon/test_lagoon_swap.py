"""Lagoon Base mainnet fork swap test.

- View Safe here https://app.safe.global/home?safe=base:0x20415f3Ec0FEA974548184bdD6e67575D128953F

"""
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3


from eth_defi.lagoon.vault import LagoonVault
from eth_defi.token import TokenDetails
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import fetch_deployment
from eth_defi.uniswap_v2.swap import swap_with_slippage_protection
from eth_defi.vault.base import TradingUniverse
from eth_defi.safe.trace import assert_execute_module_success


@pytest.fixture()
def uniswap_v2(web3):
    return fetch_deployment(
        web3,
        factory_address=UNISWAP_V2_DEPLOYMENTS["base"]["factory"],
        router_address=UNISWAP_V2_DEPLOYMENTS["base"]["router"],
        init_code_hash=UNISWAP_V2_DEPLOYMENTS["base"]["init_code_hash"],
    )


def test_lagoon_swap(
    web3: Web3,
    uniswap_v2,
    lagoon_vault: LagoonVault,
    base_weth: TokenDetails,
    base_usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
):
    """Perform a basic swap.

    - The test vault setup has a wildcard access for any transaction without whitelists

    - Perform a swap USDC -> WETH using Uniswap v2 SwapRouter02 on Base

    - For starting balances see test_lagoon_fetch_portfolio

    To run with Tenderly tx inspector:

    .. code-block:: shell

        JSON_RPC_TENDERLY="https://virtual.base.rpc.tenderly.co/XXXXXXXXXX" pytest -k test_lagoon_swap

    """
    vault = lagoon_vault
    asset_manager = topped_up_asset_manager

    # Check we have money for the swap
    amount = int(0.1 * 10**6)  # 10 cents
    assert base_usdc.contract.functions.balanceOf(vault.safe_address).call() >= amount

    # Approve USDC for the swap
    approve_call = base_usdc.contract.functions.approve(uniswap_v2.router.address, amount)
    moduled_tx = vault.transact_through_module(approve_call)
    tx_hash = moduled_tx.transact({"from": asset_manager})
    assert_execute_module_success(web3, tx_hash)

    # Creat a bound contract function instance
    # that presents Uniswap swap from the vault
    swap_call = swap_with_slippage_protection(
        uniswap_v2,
        recipient_address=lagoon_vault.safe_address,
        base_token=base_weth.contract,
        quote_token=base_usdc.contract,
        amount_in=amount,
    )

    moduled_tx = vault.transact_through_module(swap_call)
    tx_hash = moduled_tx.transact({"from": asset_manager})
    assert_execute_module_success(web3, tx_hash)

    # Check that vault balances are updated,
    # from what we have at the starting point at test_lagoon_fetch_portfolio
    universe = TradingUniverse(
        spot_token_addresses={
            base_weth.address,
            base_usdc.address,
        }
    )
    portfolio = vault.fetch_portfolio(universe, web3.eth.block_number)
    assert portfolio.spot_erc20[base_usdc.address] == pytest.approx(Decimal(0.247953))
    assert portfolio.spot_erc20[base_weth.address] > Decimal(10**-16)  # Depends on daily ETH price
