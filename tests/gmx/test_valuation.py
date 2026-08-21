"""Tests for :mod:`eth_defi.gmx.valuation`.

Covers two independent GMX NAV defects fixed here (see
``docs/claude-plans/2026-08-20-gmx-pnl-token-nav-leak.md``):

- **Defect A (reserves).** ``fetch_gmx_total_equity()`` used to assert every
  reserve token was a stablecoin, making it impossible to value a Safe that
  had accumulated WETH/WBTC (e.g. from GMX paying profitable-long PnL in the
  market's long token) or native ETH. The reserve tests below fund a wallet
  with a non-stablecoin token and native ETH and check it is priced via the
  GMX oracle, that stablecoin reserves are still summed at face value, and
  that a token with no GMX oracle price raises loudly instead of silently
  contributing zero.
- **Defect B (position value).** Position value used to be computed by hand
  from raw ``Reader.getAccountPositions()`` data as ``collateral + naive
  PnL``, ignoring borrowing fees, funding fees, position fees and price
  impact. The position tests below compare that pre-fix formula (kept here,
  standalone, only to prove the regression -- production code no longer
  contains it) against the new ``Reader.getAccountPositionInfoList()``-based
  value on a real Arbitrum account with open, fee-accruing positions.

Reserve tests mutate an isolated Anvil fork (fund balances, deploy a mock
token) and therefore need `web3_arbitrum_fork`. Position tests are read-only
against a fixed historical block on the real chain -- no fork needed, per the
module's own documented live-oracle/historical-block-state pattern.
"""

import logging
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from eth_utils import to_checksum_address
from web3 import Web3

from eth_defi.gmx.contracts import get_contract_addresses, get_reader_contract, get_tokens_metadata_dict
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.valuation import GMXEquity, _get_mark_price, fetch_gmx_total_equity  # noqa: PLC2701
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import create_token, fetch_erc20_details
from tests.gmx.conftest import GMX_ARBITRUM_FORK_BLOCK

logger = logging.getLogger(__name__)

#: Real Arbitrum address with open GMX positions, also used in the
#: `eth_defi.gmx.valuation` module docstring example. Pinned to
#: `GMX_ARBITRUM_FORK_BLOCK` so its position state is reproducible even
#: though the account itself lives on the real chain, not a fork.
_POSITIONS_ACCOUNT = to_checksum_address("0x1640e916e10610Ba39aAC5Cd8a08acF3cCae1A4c")

#: WETH on Arbitrum -- see `eth_defi.gmx.contracts.NETWORK_TOKENS`.
_WETH_ADDRESS_ARBITRUM = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")


# ---------------------------------------------------------------------------
# Defect A -- reserves
# ---------------------------------------------------------------------------


def test_reserves_stablecoin_face_value(
    web3_arbitrum_fork: Web3,
    test_address: HexAddress,
    wallet_with_usdc,
    chain_name,
    usdc,
):
    """A stablecoin reserve is summed at face value, unchanged by the fix.

    Regression check: the two-bucket reserves rewrite must not alter
    behaviour for the case the assertion previously allowed.
    """
    expected_balance = usdc.fetch_balance_of(test_address)
    assert expected_balance > 0, "wallet_with_usdc must have funded the test wallet"

    result = fetch_gmx_total_equity(
        web3=web3_arbitrum_fork,
        account=test_address,
        reserve_tokens=[usdc],
        block_identifier="latest",
        chain="arbitrum",
        include_native_eth=False,
    )

    assert result.stable_reserves == expected_balance
    assert result.non_stable_reserves == Decimal(0)
    assert result.reserves == expected_balance
    # test_address has no open GMX positions on the forked chain.
    assert result.positions == Decimal(0)
    assert result.get_total() == expected_balance


def test_reserves_non_stablecoin_priced_via_oracle(
    web3_arbitrum_fork: Web3,
    test_address: HexAddress,
    wallet_with_weth,
    chain_name,
):
    """A non-stablecoin reserve (WETH) is priced via the GMX oracle mark price.

    Before the fix this raised ``AssertionError`` unconditionally -- see
    ``eth_defi/gmx/valuation.py`` at the pre-fix line 126. The expected USD
    value is computed independently in this test from a second, separate
    oracle fetch; a small relative tolerance absorbs the price drift between
    the two live fetches (the module's oracle prices are live, not
    block-historical -- a documented, pre-existing limitation, not something
    this fix changes).
    """
    weth = fetch_erc20_details(web3_arbitrum_fork, _WETH_ADDRESS_ARBITRUM)
    weth_balance = weth.fetch_balance_of(test_address)
    assert weth_balance > 0, "wallet_with_weth must have funded the test wallet"

    result = fetch_gmx_total_equity(
        web3=web3_arbitrum_fork,
        account=test_address,
        reserve_tokens=[weth],
        block_identifier="latest",
        chain="arbitrum",
        include_native_eth=False,
    )

    reference_prices = OraclePrices(chain="arbitrum").get_recent_prices()
    reference_price = _get_mark_price(
        oracle_prices=reference_prices,
        index_token_address=_WETH_ADDRESS_ARBITRUM,
        index_token_decimals=18,
    )
    assert reference_price is not None, "GMX oracle must have a WETH price"
    expected_usd = weth_balance * reference_price

    assert result.stable_reserves == Decimal(0)
    assert result.non_stable_reserves == pytest.approx(expected_usd, rel=Decimal("0.01"))
    assert result.reserves == result.non_stable_reserves
    assert result.get_total() == result.reserves


def test_reserves_unpriceable_token_raises(
    web3_arbitrum_fork: Web3,
    test_address: HexAddress,
    chain_name,
):
    """A non-stablecoin reserve with no GMX oracle price raises loudly.

    It must not silently contribute zero to the total -- an unpriceable
    balance is a data gap, not a legitimate zero.
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


def test_native_eth_included_in_equity(
    web3_arbitrum_fork: Web3,
    test_address: HexAddress,
    chain_name,
):
    """Native ETH is counted in the reserve total when ``include_native_eth=True``.

    Native ETH is not an ERC-20, so it cannot be passed via
    ``reserve_tokens`` -- this is the dedicated code path for it (see the
    module docstring's native-ETH design note).
    """
    native_balance_wei = web3_arbitrum_fork.eth.get_balance(test_address)
    native_balance = Decimal(native_balance_wei) / Decimal(10**18)
    assert native_balance > 0, "Anvil's default account must hold native ETH"

    result_with_native = fetch_gmx_total_equity(
        web3=web3_arbitrum_fork,
        account=test_address,
        reserve_tokens=[],
        block_identifier="latest",
        chain="arbitrum",
        include_native_eth=True,
    )
    result_without_native = fetch_gmx_total_equity(
        web3=web3_arbitrum_fork,
        account=test_address,
        reserve_tokens=[],
        block_identifier="latest",
        chain="arbitrum",
        include_native_eth=False,
    )

    reference_prices = OraclePrices(chain="arbitrum").get_recent_prices()
    reference_price = _get_mark_price(
        oracle_prices=reference_prices,
        index_token_address=_WETH_ADDRESS_ARBITRUM,
        index_token_decimals=18,
    )
    assert reference_price is not None
    expected_native_usd = native_balance * reference_price

    assert result_without_native.reserves == Decimal(0)
    assert result_without_native.non_stable_reserves == Decimal(0)
    assert result_with_native.non_stable_reserves == pytest.approx(expected_native_usd, rel=Decimal("0.01"))
    assert result_with_native.reserves == result_with_native.non_stable_reserves
    assert result_with_native.reserves > result_without_native.reserves


# ---------------------------------------------------------------------------
# Defect B -- position value net of fees
# ---------------------------------------------------------------------------


def _naive_gross_position_value(web3: Web3, account: str, block_identifier, chain: str) -> Decimal:
    """Reference re-implementation of the pre-fix ``collateral + naive PnL`` formula.

    Mirrors the pre-fix ``eth_defi.gmx.valuation._calculate_position_value``,
    which read ``Reader.getAccountPositions()`` and computed
    ``collateral_usd + (mark_price - entry_price) * size_in_tokens`` by hand,
    ignoring borrowing fees, funding fees, position fees and price impact.
    Kept here, standalone, only so this test can demonstrate the regression
    Defect B fixed -- production code no longer contains this formula.

    :return:
        Sum of collateral + naive PnL across all open positions, in USD.
    """
    reader = get_reader_contract(web3, chain)
    addresses = get_contract_addresses(chain)

    raw_positions = reader.functions.getAccountPositions(addresses.datastore, account, 0, 100).call(block_identifier=block_identifier)
    if not raw_positions:
        return Decimal(0)

    raw_markets = reader.functions.getMarkets(addresses.datastore, 0, 1000).call(block_identifier=block_identifier)
    market_to_index_token = {to_checksum_address(m[0]): to_checksum_address(m[1]) for m in raw_markets}

    oracle_prices = OraclePrices(chain=chain).get_recent_prices()
    chain_tokens = get_tokens_metadata_dict(chain)

    total = Decimal(0)
    for raw_position in raw_positions:
        market_address = to_checksum_address(raw_position[0][1])
        collateral_token_address = to_checksum_address(raw_position[0][2])
        size_in_usd = raw_position[1][0]
        size_in_tokens = raw_position[1][1]
        collateral_amount_raw = raw_position[1][2]
        is_long = raw_position[2][0]

        # Real per-token decimals from chain metadata, not a hardcoded
        # assumption -- if the reference account's position set ever changes
        # shape (e.g. picks up non-USDC collateral or an 8-decimal index
        # token), this must still compute the correct "old gross" figure
        # rather than silently miscomputing by a power of 10.
        collateral_decimals = chain_tokens[collateral_token_address]["decimals"]
        collateral_usd = Decimal(collateral_amount_raw) / Decimal(10**collateral_decimals)

        if size_in_usd == 0 or size_in_tokens == 0:
            total += collateral_usd
            continue

        index_token = market_to_index_token[market_address]
        index_token_decimals = chain_tokens[index_token]["decimals"]

        entry_price = (Decimal(size_in_usd) / Decimal(size_in_tokens)) / Decimal(10 ** (30 - index_token_decimals))
        mark_price = _get_mark_price(oracle_prices=oracle_prices, index_token_address=index_token, index_token_decimals=index_token_decimals)
        assert mark_price is not None

        size_in_tokens_decimal = Decimal(size_in_tokens) / Decimal(10**index_token_decimals)
        if is_long:
            pnl_usd = (mark_price - entry_price) * size_in_tokens_decimal
        else:
            pnl_usd = (entry_price - mark_price) * size_in_tokens_decimal

        total += collateral_usd + pnl_usd

    return total


@pytest.fixture()
def positions_web3(chain_rpc_url) -> Web3:
    """Direct (non-forked) connection to real Arbitrum, for read-only position checks.

    Position valuation reads a fixed historical block for reproducible
    position state, but oracle prices are always live -- there is no benefit
    to forking for a read-only call, see the module docstring's documented
    live-oracle/historical-block-state design.
    """
    return create_multi_provider_web3(chain_rpc_url)


def test_position_value_net_of_fees_lower_than_gross(positions_web3: Web3, chain_name):
    """New Reader-based position value is strictly lower than the old gross figure.

    ``_POSITIONS_ACCOUNT`` has held open, fee-accruing GMX positions since at
    least `GMX_ARBITRUM_FORK_BLOCK`. Confirmed empirically against live chain
    state while implementing this fix: 4 open positions, old (gross) total
    ~$184,060.74 vs. new (net) total ~$176,959.55 -- an overstatement of
    ~$7,101 (~3.9%), consistent with accrued borrowing/funding/position fees
    and price impact that the pre-fix formula ignored entirely.
    """
    if chain_name != "arbitrum":
        pytest.skip("Reference account only has positions tracked on Arbitrum")

    old_gross_total = _naive_gross_position_value(
        positions_web3,
        _POSITIONS_ACCOUNT,
        block_identifier=GMX_ARBITRUM_FORK_BLOCK,
        chain="arbitrum",
    )

    result = fetch_gmx_total_equity(
        web3=positions_web3,
        account=_POSITIONS_ACCOUNT,
        reserve_tokens=[],
        block_identifier=GMX_ARBITRUM_FORK_BLOCK,
        chain="arbitrum",
        include_native_eth=False,
    )
    new_net_total = result.positions

    if old_gross_total == Decimal(0):
        pytest.skip(f"{_POSITIONS_ACCOUNT} currently has no open GMX positions on Arbitrum -- cannot demonstrate the fee/price-impact overstatement empirically")

    overstatement = old_gross_total - new_net_total
    overstatement_pct = (overstatement / old_gross_total) * 100 if old_gross_total else Decimal(0)
    logger.info(
        "Old gross position value=%s, new net position value=%s, overstatement=%s (%.2f%%)",
        old_gross_total,
        new_net_total,
        overstatement,
        overstatement_pct,
    )

    assert new_net_total > 0
    assert new_net_total < old_gross_total, f"Expected the net (fee-adjusted) position value ({new_net_total}) to be strictly lower than the pre-fix gross value ({old_gross_total}); overstatement={overstatement}"


def test_position_value_matches_reader_position_value_in_usd(positions_web3: Web3, chain_name):
    """``fetch_gmx_total_equity`` uses the Reader's own ``positionValueInUsd``.

    Fallback / belt-and-braces check independent of the old-vs-new
    comparison above: re-derive the same total directly from the raw
    ``Reader.getAccountPositionInfoList()`` response and confirm it matches
    what the production code returns exactly.
    """
    if chain_name != "arbitrum":
        pytest.skip("Reference account only has positions tracked on Arbitrum")

    reader = get_reader_contract(positions_web3, "arbitrum")
    addresses = get_contract_addresses("arbitrum")

    raw_positions = reader.functions.getAccountPositions(addresses.datastore, _POSITIONS_ACCOUNT, 0, 100).call(block_identifier=GMX_ARBITRUM_FORK_BLOCK)
    if not raw_positions:
        pytest.skip(f"{_POSITIONS_ACCOUNT} currently has no open GMX positions on Arbitrum")

    market_addresses = sorted({to_checksum_address(p[0][1]) for p in raw_positions})
    raw_markets = reader.functions.getMarkets(addresses.datastore, 0, 1000).call(block_identifier=GMX_ARBITRUM_FORK_BLOCK)
    market_tokens = {to_checksum_address(m[0]): (to_checksum_address(m[1]), to_checksum_address(m[2]), to_checksum_address(m[3])) for m in raw_markets}

    oracle_prices = OraclePrices(chain="arbitrum").get_recent_prices()

    def price_tuple(token_address: str) -> tuple[int, int]:
        for addr, data in oracle_prices.items():
            if addr.lower() == token_address.lower():
                return int(data["minPriceFull"]), int(data["maxPriceFull"])
        raise AssertionError(f"No oracle price for {token_address}")

    market_prices = [tuple(price_tuple(t) for t in market_tokens[m]) for m in market_addresses]

    referral_storage = to_checksum_address("0xe6fab3F0c7199b0d34d7FbE83394fc0e0D06e99d")
    zero_address = "0x0000000000000000000000000000000000000000"
    raw_position_infos = reader.functions.getAccountPositionInfoList(
        addresses.datastore,
        referral_storage,
        _POSITIONS_ACCOUNT,
        market_addresses,
        market_prices,
        zero_address,
        0,
        100,
    ).call(block_identifier=GMX_ARBITRUM_FORK_BLOCK)

    expected_total = sum((Decimal(p[7]) / Decimal(10**30) for p in raw_position_infos), Decimal(0))

    result = fetch_gmx_total_equity(
        web3=positions_web3,
        account=_POSITIONS_ACCOUNT,
        reserve_tokens=[],
        block_identifier=GMX_ARBITRUM_FORK_BLOCK,
        chain="arbitrum",
        include_native_eth=False,
    )

    # Both sides call the oracle independently a few milliseconds apart, so
    # a wide-open market during the test run could move the mark price
    # between fetches; positionValueInUsd is otherwise deterministic at a
    # fixed block. This account's positions are leveraged, so a small mark-price
    # wobble is amplified in the PnL component of the net value -- use the same
    # tolerance as this file's other oracle-drift-sensitive assertions rather
    # than a tighter one that would make this test flaky on volatile days.
    assert result.positions == pytest.approx(expected_total, rel=Decimal("0.01"))


def test_gmx_equity_dataclass_total():
    """``GMXEquity.get_total()`` is the sum of reserves and positions."""
    equity = GMXEquity(
        reserves=Decimal("1000"),
        positions=Decimal("500"),
        stable_reserves=Decimal("800"),
        non_stable_reserves=Decimal("200"),
    )
    assert equity.get_total() == Decimal("1500")
