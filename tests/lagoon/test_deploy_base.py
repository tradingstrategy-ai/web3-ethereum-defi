"""Deploy a new Lagoon vault on Base."""
import pytest
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.lagoon.deployment import LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.token import TokenDetails, USDC_NATIVE_TOKEN
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import fetch_deployment
from eth_defi.vault.base import TradingUniverse


@pytest.fixture()
def uniswap_v2(web3):
    return fetch_deployment(
        web3,
        factory_address=UNISWAP_V2_DEPLOYMENTS["base"]["factory"],
        router_address=UNISWAP_V2_DEPLOYMENTS["base"]["router"],
        init_code_hash=UNISWAP_V2_DEPLOYMENTS["base"]["init_code_hash"],
    )


@pytest.fixture()
def deployer_local_account(web3) -> LocalAccount:
    hot_wallet = HotWallet.create_for_testing(web3)
    return hot_wallet.account


@pytest.fixture()
def multisig_owners(web3) -> list[HexAddress]:
    return [web3.eth.accounts[2], web3.eth.accounts[3], web3.eth.accounts[4]]


def test_lagoon_deploy_base_guarded_any_token(
    web3: Web3,
    uniswap_v2,
    base_weth: TokenDetails,
    base_usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    usdc_holder: HexAddress,
    deployer_local_account,
    multisig_owners: list[str],
):
    """Deploy a new automated Lagoon vault

    Full e2e test to deploy a new Lagoon vault and do automated trades on it.

    1. Deploy a new Lagoon vault
    2. After deployment, perform a basic swap
    3. Revalue the vault now holding USDC and WETH

    To run with Tenderly tx inspector:

    .. code-block:: shell

        JSON_RPC_TENDERLY="https://virtual.base.rpc.tenderly.co/XXXXXXXXXX" pytest -k test_lagoon_deploy_base_guarded_any_token

    """

    chain_id = web3.eth.chain_id
    deployer = deployer_local_account
    asset_manager = topped_up_asset_manager

    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[chain_id],
        name="Example",
        symbol="EXA",
    )

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer,
        asset_manager=asset_manager,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=2,
        uniswap_v2=uniswap_v2,
        uniswap_v3=None,
        any_token=True,
    )

    # We look correctly initialised, and
    # Safe it set to take the ownership
    assert deploy_info.chain_id == 8453
    assert deploy_info.vault.safe.retrieve_owners() == multisig_owners
    assert deploy_info.trading_strategy_module.functions.owner() == deploy_info.vault.safe.address

    # Top up USDC

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
