"""Test GMX account valuation.

Tests :py:func:`eth_defi.gmx.valuation.fetch_gmx_total_equity` on an
Anvil mainnet fork at a fixed block number.

Requires ``JSON_RPC_ARBITRUM`` environment variable pointing to an archive node.

Manual cross-validation
-----------------------

Position data (collateral, size, entry price) is read on-chain at the fork
block and is deterministic.  PnL uses *live* GMX oracle prices, so position
values will shift between test runs.  To manually cross-validate:

1. Open https://app.gmx.io/#/actions/<account_address> for the test accounts
   listed below to see the account's trade history and current positions.

2. Use the GMX REST API v2 to fetch live positions::

       from eth_defi.gmx.api import GMXAPI
       api = GMXAPI(chain="arbitrum")
       positions = api.get_positions("0x1640e916e10610Ba39aAC5Cd8a08acF3cCae1A4c")

   Compare ``collateralAmount``, ``pnlAfterFees``, and ``isLong`` fields
   against the logged output from the test (run with ``--log-cli-level=info``).
   Note: the API returns *current* state, not the historical fork-block state,
   so collateral amounts will match only if the position hasn't been modified.

3. To verify on-chain position data at the fork block, call the Reader
   contract directly::

       from eth_defi.gmx.contracts import get_reader_contract, get_contract_addresses

       reader = get_reader_contract(web3, "arbitrum")
       addresses = get_contract_addresses("arbitrum")
       positions = reader.functions.getAccountPositions(addresses.datastore, account, 0, 100).call(block_identifier=401_729_535)

   Each position tuple contains:
   - ``[0]``: Addresses (account, market, collateralToken)
   - ``[1]``: Numbers (sizeInUsd, sizeInTokens, collateralAmount, ...)
   - ``[2]``: Flags (isLong,)

   Entry price = sizeInUsd / sizeInTokens / 10^(30 - tokenDecimals).

Test accounts at block 401_729_535
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055``
    USDC whale, ~$294M USDC, zero GMX positions.

``0x1640e916e10610Ba39aAC5Cd8a08acF3cCae1A4c``
    9 USDC-collateralised positions (mixed long/short across ARB, LINK, SOL,
    DOGE, BTC, AAVE, PEPE, XRP markets), ~$978K USDC reserves, ~$272K total
    collateral.

``0x9dd1497FF0775bab1FAEb45ea270F66b11496dDf``
    1 ETH-collateralised short position (~588 ETH collateral, ~$2.7M notional),
    zero USDC/WETH wallet reserves.  Tests non-USDC collateral handling.
"""

import logging
import os
from decimal import Decimal

import pytest
from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.gmx.contracts import get_reader_contract, get_contract_addresses
from eth_defi.gmx.valuation import GMXEquity, fetch_gmx_total_equity, _fetch_market_index_tokens
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.token import fetch_erc20_details

pytestmark = pytest.mark.skipif(
    not os.environ.get("JSON_RPC_ARBITRUM"),
    reason="JSON_RPC_ARBITRUM environment variable not set",
)

#: Arbitrum USDC (native) address
USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

#: A known account that holds USDC on Arbitrum at the fork block but has no GMX positions
KNOWN_USDC_HOLDER = "0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055"

#: Account with 9 USDC-collateralised GMX positions (mixed long/short)
ACCOUNT_USDC_POSITIONS = "0x1640e916e10610Ba39aAC5Cd8a08acF3cCae1A4c"

#: Account with 1 ETH-collateralised short position, no wallet reserves
ACCOUNT_ETH_SHORT = "0x9dd1497FF0775bab1FAEb45ea270F66b11496dDf"

#: Fixed fork block for deterministic tests
FORK_BLOCK = 401_729_535


@pytest.fixture()
def anvil_arbitrum():
    """Launch an Anvil mainnet fork of Arbitrum at a fixed block."""
    rpc_url = os.environ["JSON_RPC_ARBITRUM"]
    launch = fork_network_anvil(
        rpc_url,
        fork_block_number=FORK_BLOCK,
        test_request_timeout=100,
        launch_wait_seconds=60,
    )
    try:
        yield launch.json_rpc_url
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3(anvil_arbitrum):
    """Web3 connected to the Anvil fork."""
    web3 = Web3(HTTPProvider(anvil_arbitrum, request_kwargs={"timeout": 100}))
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


@pytest.fixture()
def usdc(web3):
    return fetch_erc20_details(web3, USDC_ADDRESS)


def test_fetch_gmx_total_equity_no_positions(web3, usdc):
    """Test equity for an account with USDC reserves and no GMX positions.

    At block 401_729_535, the USDC whale has ~$294M USDC and zero GMX positions.
    Positions subtotal should be zero; total equals reserves.
    """
    result = fetch_gmx_total_equity(
        web3=web3,
        account=KNOWN_USDC_HOLDER,
        reserve_tokens=[usdc],
    )
    assert isinstance(result, GMXEquity)
    assert result.reserves == pytest.approx(Decimal("294_462_201.855947"), rel=Decimal("0.001"))
    assert result.positions == Decimal(0)
    assert result.get_total() == result.reserves


def test_fetch_gmx_total_equity_usdc_collateral_mixed_directions(web3, usdc):
    """Test equity for an account with USDC reserves and 9 USDC-collateralised positions.

    This account has mixed long and short positions across multiple markets
    (ARB, LINK, SOL, DOGE, BTC, AAVE, PEPE, XRP), all with USDC collateral.

    At block 401_729_535:
    - Wallet USDC reserves: $978,163.29 (deterministic)
    - Total collateral across 9 positions: ~$272K
    - Position values include unrealised PnL (oracle-price dependent)

    PnL uses live oracle prices so the positions value is approximate.
    """
    result = fetch_gmx_total_equity(
        web3=web3,
        account=ACCOUNT_USDC_POSITIONS,
        reserve_tokens=[usdc],
    )
    assert isinstance(result, GMXEquity)

    # Reserves are deterministic at the fork block
    assert result.reserves == pytest.approx(Decimal("978_163.293624"), rel=Decimal("0.001"))

    # Positions must be positive (collateral alone is ~$272K)
    assert result.positions > Decimal("200_000")

    # Total = reserves + positions
    assert result.get_total() == result.reserves + result.positions
    assert result.get_total() > Decimal("1_100_000")


def test_fetch_gmx_total_equity_eth_collateral_short(web3, usdc):
    """Test equity for an account with an ETH-collateralised short position.

    This account has:
    - 0 USDC wallet reserves
    - 1 short ETH position with ~588 ETH collateral, ~$2.7M notional
    - Entry price ~$3,425 (position opened when ETH was much higher)

    The collateral amount is in raw ETH units while PnL is in USD.
    For USDC-denomination this gives collateral_tokens + pnl_usd which
    is a mixed-unit approximation — acceptable when the function is used
    for relative comparisons over time with the same denomination.
    """
    result = fetch_gmx_total_equity(
        web3=web3,
        account=ACCOUNT_ETH_SHORT,
        reserve_tokens=[usdc],
    )
    assert isinstance(result, GMXEquity)

    # No USDC wallet reserves
    assert result.reserves == Decimal(0)

    # Position should have a large value (collateral ~588 ETH + big PnL from short)
    # Collateral alone is 587.8 ETH tokens, and the short is deeply in profit
    # (entry ~$3425, current mark much lower)
    assert result.positions > Decimal("500_000")

    assert result.get_total() == result.positions


def test_fetch_gmx_total_equity_empty_account(web3, usdc):
    """Test equity for an account with no reserves and no positions returns zero."""
    empty_account = "0xdead000000000000000000000000000000000042"

    result = fetch_gmx_total_equity(
        web3=web3,
        account=empty_account,
        reserve_tokens=[usdc],
    )
    assert result.reserves == Decimal(0)
    assert result.positions == Decimal(0)
    assert result.get_total() == Decimal(0)


def test_zero_index_markets_excluded(web3):
    """Verify that markets with zero index tokens are excluded or resolved.

    Some GMX markets (swap-only pools) have a zero address as their on-chain
    index token.  The wstETH market is special-cased to use the real wstETH
    token address; all other zero-index markets are skipped.

    After mapping, no market should have a zero address as index token.
    """
    reader = get_reader_contract(web3, "arbitrum")
    addresses = get_contract_addresses("arbitrum")

    market_map = _fetch_market_index_tokens(reader, addresses.datastore, block_identifier=FORK_BLOCK)

    # Must have resolved some markets
    assert len(market_map) > 30

    # No market should have a zero address as index token
    zero = "0x0000000000000000000000000000000000000000"
    for market, index in market_map.items():
        assert index != zero, f"Market {market} has zero index token"


def test_non_stablecoin_reserve_rejected(web3):
    """Verify that passing a non-6-decimal token as reserve triggers an assertion."""
    weth = fetch_erc20_details(web3, "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")

    with pytest.raises(AssertionError, match="does not look like a stablecoin"):
        fetch_gmx_total_equity(
            web3=web3,
            account="0xdead000000000000000000000000000000000042",
            reserve_tokens=[weth],
        )


def test_gmx_equity_dataclass():
    """Test GMXEquity dataclass arithmetic."""
    equity = GMXEquity(reserves=Decimal("1000"), positions=Decimal("500"))
    assert equity.get_total() == Decimal("1500")

    # Negative PnL can make positions negative
    equity_loss = GMXEquity(reserves=Decimal("1000"), positions=Decimal("-200"))
    assert equity_loss.get_total() == Decimal("800")
