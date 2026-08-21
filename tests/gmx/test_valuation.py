"""Tests for :mod:`eth_defi.gmx.valuation`.

One integration test, on the same real Lagoon-Safe Anvil fork already used by
``tests/gmx/lagoon/test_gmx_close_pnl_token.py``: drives a long through profit
and close, and asserts ``fetch_gmx_total_equity()`` -- the actual production
NAV function -- correctly reflects mixed stable/non-stable reserves plus an
open position before the close, and captures the realised profit after it.
This exercises both defects fixed here in the one scenario that actually
matters, rather than each in isolation against synthetic setups (see
``docs/claude-plans/2026-08-20-gmx-pnl-token-nav-leak.md``):

- **Defect A (reserves).** ``fetch_gmx_total_equity()`` used to assert every
  reserve token was a stablecoin, making it impossible to value a Safe that
  had accumulated WETH (e.g. from GMX paying profitable-long PnL in the
  market's long token) or native ETH.
- **Defect B (position value).** Position value used to be computed by hand
  as ``collateral + naive PnL``, ignoring borrowing fees, funding fees,
  position fees and price impact.

A second, standalone test covers the one failure mode a happy-path
integration test cannot: an unpriceable reserve token must raise loudly
rather than silently contribute zero to NAV.
"""

import logging
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.gmx.valuation import fetch_gmx_total_equity
from eth_defi.token import create_token, fetch_erc20_details
from tests.gmx.fork_helpers import fetch_on_chain_oracle_prices, setup_mock_oracle
from tests.gmx.lagoon.test_gmx_close_pnl_token import (
    _PRICE_MOVE_FRACTION,
    _SIZE_DELTA_USD,
    _close_position,
    _open_long_and_get_position,
    lagoon_gmx_fork_env,  # noqa: F401 -- pytest fixture, referenced by name below
)
from tests.gmx.lagoon.test_gmx_lagoon_integration import USDC_ARBITRUM, WETH_ARBITRUM, LagoonGMXForkEnv

logger = logging.getLogger(__name__)


def test_fetch_gmx_total_equity_end_to_end(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """NAV correctly counts mixed reserves and an open position, then captures a profitable close.

    Funds the Safe with USDC (stable) and a WETH dust balance (non-stable --
    exactly what a pre-fix leaked profit would have left behind, and exactly
    what the pre-fix blanket assert would have crashed on), opens a real
    leveraged long through the Safe, and checks NAV twice:

    1. While the position is open, with mixed reserves: stable reserves at
       face value, WETH and native ETH priced via the GMX oracle rather than
       asserted away, and the open position's net-of-fees value all counted.
    2. After forcing the position into profit and closing it fully: NAV must
       have captured most of the realised profit, not silently lost it --
       the exact regression this PR fixes. Before the fix, this assertion
       would fail: the profit landed as native ETH, invisible to a
       stablecoin-only reserves total.
    """
    env = lagoon_gmx_fork_env
    web3 = env.web3
    safe_address = env.vault.safe_address
    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)
    weth = fetch_erc20_details(web3, WETH_ARBITRUM)

    position = _open_long_and_get_position(env)

    # === Check 1: NAV while the position is open, with mixed reserves ===
    safe_usdc_balance = usdc.fetch_balance_of(safe_address)
    safe_weth_balance = weth.fetch_balance_of(safe_address)
    assert safe_weth_balance > 0, "Lagoon fork env should fund the Safe with WETH dust -- see _create_lagoon_gmx_fork_env"

    equity_while_open = fetch_gmx_total_equity(
        web3=web3,
        account=safe_address,
        reserve_tokens=[usdc, weth],
        chain="arbitrum",
        include_native_eth=True,
    )
    logger.info(
        "NAV while open: stable_reserves=%s non_stable_reserves=%s positions=%s total=%s",
        equity_while_open.stable_reserves,
        equity_while_open.non_stable_reserves,
        equity_while_open.positions,
        equity_while_open.get_total(),
    )

    assert equity_while_open.stable_reserves == safe_usdc_balance, "Stablecoin reserve must be summed at face value"
    assert equity_while_open.non_stable_reserves > 0, "WETH dust and native ETH must be priced via the oracle, not asserted away"
    assert equity_while_open.positions > 0, "The open long must contribute a positive net-of-fees position value"
    assert equity_while_open.get_total() == equity_while_open.reserves + equity_while_open.positions

    # === Check 2: NAV must capture the profit after a real close ===
    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(web3)
    new_eth_price = int(current_eth_price * (1 + _PRICE_MOVE_FRACTION))
    setup_mock_oracle(web3, eth_price_usd=new_eth_price, usdc_price_usd=current_usdc_price)
    expected_profit_usd = Decimal(str(_SIZE_DELTA_USD * _PRICE_MOVE_FRACTION))

    _close_position(env, position, is_long=True)

    equity_after_close = fetch_gmx_total_equity(
        web3=web3,
        account=safe_address,
        reserve_tokens=[usdc, weth],
        chain="arbitrum",
        include_native_eth=True,
    )
    logger.info(
        "NAV after close: total=%s (was %s while open; expected profit ~$%s)",
        equity_after_close.get_total(),
        equity_while_open.get_total(),
        expected_profit_usd,
    )

    assert equity_after_close.positions == Decimal(0), "No open positions should remain after a full close"
    assert equity_after_close.get_total() > equity_while_open.get_total() + expected_profit_usd * Decimal("0.5"), f"NAV went from {equity_while_open.get_total()} to {equity_after_close.get_total()} across a profitable close (~${expected_profit_usd} expected) -- PnL is not being counted in NAV, exactly the regression this PR fixes"


def test_reserves_unpriceable_token_raises(
    web3_arbitrum_fork: Web3,
    test_address: HexAddress,
    chain_name,
):
    """A non-stablecoin reserve with no GMX oracle price raises loudly.

    The one failure mode the happy-path integration test above cannot cover:
    an unpriceable balance must be a loud error, not a legitimate zero.
    """
    if chain_name != "arbitrum":
        pytest.skip("Reserve pricing test only targets Arbitrum")

    mock_token_contract = create_token(
        web3_arbitrum_fork,
        deployer=test_address,
        name="Definitely Not Priced By GMX",
        symbol="NOPRICE",
        supply=1_000 * 10**18,
    )
    mock_token = fetch_erc20_details(web3_arbitrum_fork, mock_token_contract.address)
    assert not mock_token.is_stablecoin_like()

    with pytest.raises(ValueError, match="No oracle price available"):
        fetch_gmx_total_equity(
            web3=web3_arbitrum_fork,
            account=test_address,
            reserve_tokens=[mock_token],
            block_identifier="latest",
            chain="arbitrum",
            include_native_eth=False,
        )
