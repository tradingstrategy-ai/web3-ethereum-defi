"""Test Uniswap v3 price calculation."""

import logging
import os
import shutil
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract

from eth_defi.one_delta.deployment import OneDeltaDeployment
from eth_defi.one_delta.price import (
    OneDeltaPriceHelper,
    estimate_buy_received_amount,
    estimate_sell_received_amount,
)
from eth_defi.provider.anvil import fork_network_anvil, mine
from eth_defi.token import create_token, reset_default_token_cache
from eth_defi.uniswap_v3.utils import get_default_tick_range

# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
pytestmark = pytest.mark.skipif(
    (os.environ.get("JSON_RPC_POLYGON") is None) or (shutil.which("anvil") is None),
    reason="Set JSON_RPC_POLYGON env install anvil command to run these tests",
)

logger = logging.getLogger(__name__)


@pytest.fixture
def anvil_polygon_chain_fork(request, large_usdc_holder) -> str:
    """Create a testable fork of live Polygon.

    Override the same fixture in conftest to be able to use quoter

    :return: JSON-RPC URL for Web3
    """
    mainnet_rpc = os.environ["JSON_RPC_POLYGON"]
    launch = fork_network_anvil(
        mainnet_rpc,
        unlocked_addresses=[large_usdc_holder],
        fork_block_number=51_000_000,
    )
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


def test_price_helper(
    web3,
    hot_wallet,
    large_usdc_holder,
    one_delta_deployment,
    aave_v3_deployment,
    weth: Contract,
    usdc: Contract,
    # dai: Contract,
):
    """Test price helper.

    Since the setup part is fairly slow, we test multiple input/output in the same test

    Based on: https://github.com/Uniswap/v3-sdk/blob/1a74d5f0a31040fec4aeb1f83bba01d7c03f4870/src/entities/trade.test.ts
    """
    price_helper = OneDeltaPriceHelper(one_delta_deployment)

    # test amount out
    for slippage, expected_amount_out in [
        (0, 2233287804),
        (5 * 100, 2126940765),
        (50 * 100, 1488858536),
    ]:
        amount_out = price_helper.get_amount_out(
            1 * 10**18,
            [
                weth.address,
                usdc.address,
            ],
            [3000],
            slippage=slippage,
        )

        assert amount_out == pytest.approx(expected_amount_out)

    # test amount in
    for slippage, expected_amount_in in [
        (0, 2250367818),
        (5 * 100, 2362886208),
        (50 * 100, 3375551727),
    ]:
        amount_in = price_helper.get_amount_in(
            1 * 10**18,
            [
                weth.address,
                usdc.address,
            ],
            [3000],
            slippage=slippage,
        )

        assert amount_in == expected_amount_in


def test_estimate_buy_price_for_cash(
    one_delta_deployment: OneDeltaDeployment,
    weth: Contract,
    usdc: Contract,
):
    """Estimate how much asset we receive for a given cash buy."""

    usdc_amount_to_spend = 3000 * 10**6

    # Estimate how much ETH we will receive for 3000 USDC
    eth_received = estimate_buy_received_amount(
        one_delta_deployment,
        weth.address,
        usdc.address,
        usdc_amount_to_spend,
        3000,
    )

    assert eth_received / 10**18 == pytest.approx(1.3327575201582416)

    # Calculate price of ETH as $ for our purchase
    price = usdc_amount_to_spend * 10 ** (18 - 6) / eth_received
    assert price == pytest.approx(2250.9721045459214)

    # test verbose mode
    eth_received, block_number = estimate_buy_received_amount(
        one_delta_deployment,
        weth.address,
        usdc.address,
        usdc_amount_to_spend,
        3000,
        verbose=True,
    )

    assert eth_received / 10**18 == pytest.approx(1.3327575201582416)
    assert block_number == 51_000_000


def test_estimate_sell_received_cash(
    one_delta_deployment: OneDeltaDeployment,
    weth: Contract,
    usdc: Contract,
):
    """Estimate how much asset we receive for a given cash buy."""

    eth_amount_to_sell = 50 * 10**18

    # How much do we receive for selling 50 ETH
    usdc_received = estimate_sell_received_amount(
        one_delta_deployment,
        weth.address,
        usdc.address,
        eth_amount_to_sell,
        3000,
    )

    assert usdc_received / 10**6 == pytest.approx(105560.633339)

    # Calculate price of ETH as $ for our purchase
    price = usdc_received * 10 ** (18 - 6) / eth_amount_to_sell
    assert price == pytest.approx(2111.21266678)

    # test verbose mode
    usdc_received, block_number = estimate_sell_received_amount(
        one_delta_deployment,
        weth.address,
        usdc.address,
        eth_amount_to_sell,
        3000,
        verbose=True,
    )
    assert usdc_received / 1e6 == pytest.approx(105560.633339)
    assert block_number == 51_000_000
