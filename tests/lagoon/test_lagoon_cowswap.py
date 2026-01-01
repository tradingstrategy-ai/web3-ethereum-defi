"""Lagoon swaps with CowSwap tests."""

import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.cow.constants import COWSWAP_SETTLEMENT
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault
from eth_defi.hotwallet import HotWallet
from eth_defi.erc_4626.vault_protocol.lagoon.cowswap import presign_and_broadcast
from eth_defi.cow.quote import fetch_quote
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, USDC_NATIVE_TOKEN, USDC_WHALE, fetch_erc20_details, BRIDGED_USDC_TOKEN, USDT_NATIVE_TOKEN, WRAPPED_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

CI = os.environ.get("CI") == "true"


@pytest.fixture()
def anvil_arbitrum_fork(request, asset_manager) -> AnvilLaunch:
    """Reset write state between tests"""

    usdc_whale = USDC_WHALE[42161]

    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM,
        fork_block_number=375_216_652,
        unlocked_addresses=[usdc_whale, asset_manager],
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


@pytest.fixture()
def topped_up_asset_manager(web3, asset_manager) -> HexAddress:
    # Topped up with some ETH
    tx_hash = web3.eth.send_transaction(
        {
            "to": asset_manager,
            "from": web3.eth.accounts[0],
            "value": 9 * 10**18,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)
    return asset_manager


@pytest.fixture()
def usdc(web3) -> TokenDetails:
    usdc = fetch_erc20_details(
        web3,
        USDC_NATIVE_TOKEN[42161],
    )
    return usdc


@pytest.fixture
def gains_vault(web3) -> GainsVault:
    """gTrade USDC vault on Arbitrum"""
    vault_address = "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"
    vault = create_vault_instance_autodetect(web3, vault_address)
    assert isinstance(vault, GainsVault)
    return vault


@pytest.fixture()
def new_depositor(web3, usdc) -> HexAddress:
    """User with some USDC ready to deposit.

    - Start with 500 USDC
    """
    new_depositor = web3.eth.accounts[5]
    usdc_holder = USDC_WHALE[42161]
    tx_hash = usdc.transfer(new_depositor, Decimal(500)).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return new_depositor


# Anvil is piece of crap
# ERROR tests/lagoon/test_lagoon_cowswap.py::test_cowswap_quote - AssertionError: Could not read block number from Anvil after the launch anvil: at http://localhost:27496, stdout is 0 bytes, stderr is 209 bytes
@pytest.mark.skipif(CI, reason="Flaky on CI")
def test_lagoon_cowswap(
    web3: Web3,
    usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    deployer_hot_wallet: HotWallet,
    multisig_owners: list[HexAddress],
    new_depositor: HexAddress,
):
    """Perform a USDC->USDC.e swap on Lagoon vault using CowSwap via TradingStrategyModuleV0."""

    #
    # 1. Deploy new Lagoon vault where the target vault is whitelisted on the guard
    #

    chain_id = web3.eth.chain_id
    asset_manager = topped_up_asset_manager
    assert asset_manager.startswith("0x")
    depositor = new_depositor

    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[chain_id],
        name="Example",
        symbol="EXA",
    )

    # All Arbitrum mainstream stablecoins
    assets = [
        BRIDGED_USDC_TOKEN[chain_id],
        USDC_NATIVE_TOKEN[chain_id],
        USDT_NATIVE_TOKEN[chain_id],
    ]

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer_hot_wallet,
        asset_manager=asset_manager,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=2,
        uniswap_v2=None,
        uniswap_v3=None,
        any_asset=False,
        cowswap=True,
        from_the_scratch=False,
        use_forge=True,
        assets=assets,
    )

    # Check CowSwap and all assets are whitelisted
    vault = deploy_info.vault
    trading_strategy_module = vault.trading_strategy_module
    assert trading_strategy_module.functions.isAllowedCowSwap(COWSWAP_SETTLEMENT).call() == True
    assert trading_strategy_module.functions.isAllowedApprovalDestination(COWSWAP_SETTLEMENT).call() == True
    for a in assets:
        assert trading_strategy_module.functions.isAllowedAsset(Web3.to_checksum_address(a)).call() == True
    assert not vault.trading_strategy_module.functions.anyAsset().call()

    #
    # 2. Fund our vault
    #

    # We need to do the initial valuation at value 0
    bound_func = vault.post_new_valuation(Decimal(0))
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Deposit 9.00 USDC into the vault
    usdc_amount = 9 * 10**6
    tx_hash = usdc.contract.functions.approve(vault.address, usdc_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    deposit_func = vault.request_deposit(depositor, usdc_amount)
    tx_hash = deposit_func.transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # We need to do the initial valuation at value 0
    valuation = Decimal(0)
    bound_func = vault.post_new_valuation(valuation)
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Settle deposit queue 9 USDC -> 0 USDC
    settle_func = vault.settle_via_trading_strategy_module(valuation)
    tx_hash = settle_func.transact(
        {
            "from": asset_manager,
            "gas": 1_000_000,
        }
    )
    assert_transaction_success_with_explanation(
        web3,
        tx_hash,
        func=settle_func,
        tracing=True,
    )

    #
    # 3. Perform CowSwap swap USDC -> USDC.e
    #

    # 3.a) approve
    usdc_amount = Decimal(5)
    func = usdc.approve(COWSWAP_SETTLEMENT, usdc_amount)
    moduled_tx = vault.transact_via_trading_strategy_module(func)
    tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash, func=func)

    # 3.b) build presigned tx
    order = presign_and_broadcast(
        asset_manager=topped_up_asset_manager,
        vault=vault,
        buy_token=fetch_erc20_details(web3, BRIDGED_USDC_TOKEN[chain_id]),
        sell_token=fetch_erc20_details(web3, USDC_NATIVE_TOKEN[chain_id]),
        amount_in=usdc_amount,
        min_amount_out=usdc_amount * Decimal(0.99),
    )

    assert order["sellToken"] == "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    assert order["buyToken"] == "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8"
    assert order["receiver"] == vault.safe_address
    assert order["sellAmount"] == 5000000
    assert order["buyAmount"] == 4949999
    assert order["validTo"] > 1
    # assert order["appData"] == b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    assert order["feeAmount"] == 0
    assert order["kind"] == "sell"
    assert order["partiallyFillable"] is False


# Anvil is piece of crap
# ERROR tests/lagoon/test_lagoon_cowswap.py::test_cowswap_quote - AssertionError: Could not read block number from Anvil after the launch anvil: at http://localhost:27496, stdout is 0 bytes, stderr is 209 bytes
@pytest.mark.skipif(CI, reason="Flaky on CI")
def test_cowswap_quote(
    web3: Web3,
):
    """See we can quote CowSwap order data unpacking correctly."""

    chain_id = web3.eth.chain_id
    weth = fetch_erc20_details(
        web3,
        WRAPPED_NATIVE_TOKEN[chain_id],
    )

    usdce = fetch_erc20_details(web3, BRIDGED_USDC_TOKEN[chain_id])

    amount = Decimal("0.0001")
    quote = fetch_quote(
        from_="0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6",  # Dummy address
        buy_token=usdce,
        sell_token=weth,
        amount_in=amount,
        min_amount_out=amount / 2,
    )

    quoted_data = quote.data
    assert quoted_data["from"].startswith("0x")
    # assert quoted_data["expiration"] == "1970-01-01T00:00:00Z"
    assert quoted_data["id"] is None
    assert quoted_data["verified"] is False
    assert quoted_data["quote"]["sellToken"] == "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
    assert quoted_data["quote"]["buyToken"] == "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"
    assert quoted_data["quote"]["receiver"] is None
    assert int(quoted_data["quote"]["sellAmount"]) > 1
    assert int(quoted_data["quote"]["buyAmount"]) > 1
    assert quoted_data["quote"]["validTo"] > 1
    assert quoted_data["quote"]["appData"] == "0x0000000000000000000000000000000000000000000000000000000000000000"
    assert int(quoted_data["quote"]["feeAmount"]) > 1
    assert quoted_data["quote"]["kind"] == "sell"
    assert quoted_data["quote"]["partiallyFillable"] is False
    assert quoted_data["quote"]["sellTokenBalance"] == "erc20"
    assert quoted_data["quote"]["buyTokenBalance"] == "erc20"
    assert quoted_data["quote"]["signingScheme"] == "presign"

    quote.pformat()

    quote = fetch_quote(
        from_="0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6",  # Dummy address
        buy_token=usdce,
        sell_token=weth,
        amount_in=amount,
        min_amount_out=amount / 2,
        price_quality="verified",
    )
    verified_quoted_data = quote.data
    assert verified_quoted_data["verified"] is True
