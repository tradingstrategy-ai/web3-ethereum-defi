"""GMX account valuation.

Calculate the total equity of a GMX trading account at a specific block,
combining wallet token reserves (including native ETH) with the net value
of open perpetual positions.

- Reserves are read onchain at the requested ``block_identifier``.
  Stablecoin reserve tokens (checked via
  :meth:`~eth_defi.token.TokenDetails.is_stablecoin_like`) are summed at
  face value. Any other reserve token -- and native ETH, when
  ``include_native_eth`` is set -- is converted to USD via the live GMX
  oracle mark price. This is not limited to USDC-collateralised accounts:
  a Safe that has accumulated WETH/WBTC (e.g. from a GMX long position
  paying out PnL in the market's long token) or native ETH is priced
  rather than rejected.
- Position data is read onchain at the requested ``block_identifier``
  via ``Reader.getAccountPositionInfoList()``. Its ``positionValueInUsd``
  is already net of borrowing fees, funding fees, position fees and price
  impact -- it is not a hand-rolled ``collateral + naive PnL`` figure.
- Oracle prices use the live GMX signed-prices API and therefore reflect
  *current* market prices, not prices at the historical block. This
  applies equally to reserve and position pricing, and is a known
  limitation for historical valuation -- see `GMX oracle documentation
  <https://docs.gmx.io/docs/trading/v2/#oracles>`__.

.. code-block:: python

    import os
    from eth_defi.provider.multi_provider import create_multi_provider_web3
    from eth_defi.token import fetch_erc20_details
    from eth_defi.gmx.valuation import fetch_gmx_total_equity

    web3 = create_multi_provider_web3(os.environ["JSON_RPC_ARBITRUM"])
    usdc = fetch_erc20_details(web3, "0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

    # Account 0x1640... holds USDC reserves and several GMX positions
    # (mixed long/short) at block 401_729_535.
    result = fetch_gmx_total_equity(
        web3=web3,
        account="0x1640e916e10610Ba39aAC5Cd8a08acF3cCae1A4c",
        reserve_tokens=[usdc],
        block_identifier=401_729_535,
    )
    # result.stable_reserves     = USDC only, deterministic at this block
    # result.non_stable_reserves = the account's native ETH balance, priced live
    #                               (include_native_eth defaults to True)
    # result.reserves            = result.stable_reserves + result.non_stable_reserves
    # result.positions           = net of fees, oracle-price dependent
    # result.get_total() = result.reserves + result.positions
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from eth_utils import to_checksum_address
from web3 import Web3
from web3.contract import Contract

from eth_defi.gmx.constants import PRECISION
from eth_defi.gmx.contracts import (
    NETWORK_TOKENS,
    get_contract_addresses,
    get_reader_contract,
)
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.token import TokenDetails

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GMXEquity:
    """Equity breakdown for a GMX trading account.

    See :py:func:`fetch_gmx_total_equity`.
    """

    #: Sum of all reserves in USD -- ``stable_reserves + non_stable_reserves``.
    reserves: Decimal

    #: Sum of open GMX position values in USD, net of borrowing fees,
    #: funding fees, position fees and price impact.
    positions: Decimal

    #: Portion of ``reserves`` summed at face value because the token
    #: looks like a USD-pegged stablecoin.
    stable_reserves: Decimal

    #: Portion of ``reserves`` converted to USD via the GMX oracle mark
    #: price -- non-stablecoin reserve tokens, plus native ETH when
    #: ``include_native_eth`` was set.
    non_stable_reserves: Decimal

    def get_total(self) -> Decimal:
        """Total equity = reserves + positions."""
        return self.reserves + self.positions


def fetch_gmx_total_equity(
    web3: Web3,
    account: HexAddress | str,
    reserve_tokens: list[TokenDetails],
    *,
    block_identifier: BlockIdentifier = "latest",
    chain: str = "arbitrum",
    include_native_eth: bool = True,
) -> GMXEquity:
    """Calculate the total equity of a GMX trading account.

    Returns a :class:`GMXEquity` dataclass with separate ``reserves`` and
    ``positions`` subtotals (and a stable/non-stable breakdown of
    ``reserves`` for observability). Call :meth:`GMXEquity.get_total` for
    the combined figure.

    Reserve token balances are summed directly -- the caller controls
    which tokens to include. Stablecoin reserves are summed at face
    value; any other reserve token, and native ETH when
    ``include_native_eth`` is set, is converted to USD via the live GMX
    oracle mark price (see the module docstring).

    - Reserve balances and position data are read onchain at the given
      ``block_identifier`` (requires an archive node for historical blocks).
    - Oracle prices are fetched from the live GMX signed-prices API and
      therefore reflect *current* market prices, not prices at the
      historical block.

    :param web3:
        Web3 connection. Must point to an archive node when querying
        historical blocks.

    :param account:
        Wallet address that holds the reserves and GMX positions.

    :param reserve_tokens:
        List of ``TokenDetails`` whose ``balanceOf(account)`` should be
        included in the reserve total. Any ERC-20 is accepted.
        Stablecoins (checked via
        :meth:`~eth_defi.token.TokenDetails.is_stablecoin_like`) are
        summed at face value; other tokens are converted to USD via the
        live GMX oracle mark price, and a :exc:`ValueError` is raised if
        the oracle has no price for one -- an unpriceable reserve must
        not silently contribute zero.

    :param block_identifier:
        Block number (or ``"latest"``) at which to read onchain state.

    :param chain:
        GMX chain name. Reserve valuation works on any chain GMX supports.
        Position valuation additionally requires a known GMX
        ``ReferralStorage`` address -- currently only ``"arbitrum"`` is
        confirmed (see :data:`_REFERRAL_STORAGE_ADDRESSES`); an account with
        open positions on an unconfirmed chain raises :exc:`ValueError`.

    :param include_native_eth:
        If ``True`` (the default), add the account's native ETH balance
        to the reserve total, priced via the chain's wrapped-native-token
        oracle price. Native ETH is not an ERC-20, so it cannot be passed
        via ``reserve_tokens``. The balance is counted in full -- callers
        that need to reserve part of it as a gas float must account for
        that themselves; this function does not guess a gas reserve.

    :return:
        :class:`GMXEquity` with reserves and positions subtotals.
    """
    account = Web3.to_checksum_address(account)

    # Oracle prices are shared between the reserves loop (non-stablecoin
    # and native ETH pricing) and the GMX position valuation below, so
    # fetch them once.
    oracle_prices = OraclePrices(chain=chain).get_recent_prices()

    # 1. Reserve balances
    stable_reserves_total = Decimal(0)
    non_stable_reserves_total = Decimal(0)
    for token in reserve_tokens:
        balance = token.fetch_balance_of(account, block_identifier=block_identifier)
        if token.is_stablecoin_like():
            stable_reserves_total += balance
            logger.info(
                "Stablecoin reserve %s balance for %s: %s",
                token.symbol,
                account,
                balance,
            )
        else:
            mark_price = _get_mark_price(
                oracle_prices=oracle_prices,
                index_token_address=token.address,
                index_token_decimals=token.decimals,
            )
            if mark_price is None:
                raise ValueError(f"No oracle price available for non-stablecoin reserve token {token.symbol} ({token.address}); cannot value this reserve.")
            usd_value = balance * mark_price
            non_stable_reserves_total += usd_value
            logger.info(
                "Non-stablecoin reserve %s balance for %s: %s (%s USD at mark price %s)",
                token.symbol,
                account,
                balance,
                usd_value,
                mark_price,
            )

    if include_native_eth:
        native_balance_wei = web3.eth.get_balance(account, block_identifier=block_identifier)
        native_balance = Decimal(native_balance_wei) / Decimal(10**18)
        native_token_address = _native_wrapped_token_address(chain)
        mark_price = _get_mark_price(
            oracle_prices=oracle_prices,
            index_token_address=native_token_address,
            index_token_decimals=18,
        )
        if mark_price is None:
            raise ValueError(f"No oracle price available for the native token on chain {chain!r}; cannot value the native ETH balance.")
        native_usd = native_balance * mark_price
        non_stable_reserves_total += native_usd
        logger.info(
            "Native token balance for %s: %s (%s USD at mark price %s)",
            account,
            native_balance,
            native_usd,
            mark_price,
        )

    reserves_total = stable_reserves_total + non_stable_reserves_total

    # 2. GMX positions
    positions_total = _fetch_gmx_positions_value(
        web3=web3,
        account=account,
        block_identifier=block_identifier,
        chain=chain,
        oracle_prices=oracle_prices,
    )

    logger.info(
        "Total equity for %s: stable_reserves=%s, non_stable_reserves=%s, positions=%s, total=%s",
        account,
        stable_reserves_total,
        non_stable_reserves_total,
        positions_total,
        reserves_total + positions_total,
    )
    return GMXEquity(
        reserves=reserves_total,
        positions=positions_total,
        stable_reserves=stable_reserves_total,
        non_stable_reserves=non_stable_reserves_total,
    )


def _native_wrapped_token_address(chain: str) -> str:
    """Resolve the wrapped-native-token address used to price native ETH/AVAX.

    GMX has no oracle feed keyed by "native ETH" -- native ETH and AVAX
    trade 1:1 with their wrapped ERC-20, so the wrapped token's oracle
    price is used. Mirrors the per-chain lookup in
    :meth:`eth_defi.gmx.gas_monitor.GMXGasMonitor.get_native_token_price_usd`.

    :raises ValueError:
        If no native wrapped token address is configured for ``chain``.
    """
    chain_tokens = NETWORK_TOKENS.get(chain, {})
    if chain in {"avalanche", "avalanche_fuji"}:
        address = chain_tokens.get("WAVAX") or chain_tokens.get("AVAX")
    else:
        address = chain_tokens.get("WETH") or chain_tokens.get("ETH")

    if address is None:
        raise ValueError(f"No native wrapped token address configured for chain {chain!r}")

    return address


#: GMX ``ReferralStorage`` contract address, required by
#: ``Reader.getAccountPositionInfoList()``. Only Arbitrum is confirmed --
#: this mirrors the same hardcoded address used by
#: :func:`eth_defi.gmx.core.liquidation.get_liquidation_price` for the same
#: contract call. It is not part of :func:`~eth_defi.gmx.contracts.get_contract_addresses`
#: because that release-pinned table has no ``referralstorage`` entry.
_REFERRAL_STORAGE_ADDRESSES: dict[str, str] = {
    "arbitrum": to_checksum_address("0xe6fab3F0c7199b0d34d7FbE83394fc0e0D06e99d"),
}


def _get_referral_storage_address(chain: str) -> str:
    """Look up the GMX ``ReferralStorage`` address for ``chain``.

    :raises ValueError:
        If ``chain`` has no confirmed ``ReferralStorage`` address.
    """
    address = _REFERRAL_STORAGE_ADDRESSES.get(chain)
    if address is None:
        raise ValueError(f"No known GMX ReferralStorage address for chain {chain!r}; cannot value open GMX positions on this chain. Supported: {sorted(_REFERRAL_STORAGE_ADDRESSES)}")
    return address


#: wstETH GMX market has historically used a zero index-token address
#: onchain; the real wstETH token must be substituted so its oracle price
#: can be looked up. As of writing, this market's onchain index token is
#: WETH (not zero), so this branch is not currently exercised by live
#: mainnet state -- kept as a safety net since the Reader-based valuation
#: path has not been verified against an actual open wstETH position. See
#: :class:`~eth_defi.gmx.core.markets.Markets` for the canonical handling.
_WSTETH_MARKET = to_checksum_address("0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5")
_WSTETH_TOKEN = to_checksum_address("0x5979D7b546E38E414F7E9822514be443A4800529")
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _fetch_market_token_addresses(
    reader: Contract,
    datastore_address: str,
    block_identifier: BlockIdentifier,
) -> dict[str, tuple[str, str, str]]:
    """Build a market_address -> (index_token, long_token, short_token) mapping.

    Calls ``Reader.getMarkets()`` directly, needed to build the
    ``MarketUtils.MarketPrices[]`` argument that
    ``Reader.getAccountPositionInfoList()`` requires per market.

    Markets with a zero onchain index token are swap-only liquidity pools
    (no perp trading), except the wstETH special case -- see
    :data:`_WSTETH_MARKET`. Swap-only markets are excluded since they
    cannot host a leveraged position.

    :return:
        Dict mapping checksummed market address to a checksummed
        ``(index_token, long_token, short_token)`` tuple.
    """
    raw_markets = reader.functions.getMarkets(datastore_address, 0, 1000).call(block_identifier=block_identifier)

    market_tokens: dict[str, tuple[str, str, str]] = {}
    for raw_market in raw_markets:
        market_addr = to_checksum_address(raw_market[0])
        index_token = to_checksum_address(raw_market[1])
        long_token = to_checksum_address(raw_market[2])
        short_token = to_checksum_address(raw_market[3])

        if index_token == _ZERO_ADDRESS:
            if market_addr == _WSTETH_MARKET:
                index_token = _WSTETH_TOKEN
            else:
                continue

        market_tokens[market_addr] = (index_token, long_token, short_token)

    return market_tokens


def _oracle_price_tuple(oracle_prices: dict, token_address: str) -> tuple[int, int]:
    """Look up a token's ``(min, max)`` GMX oracle price as raw integers.

    Unlike :func:`_get_mark_price`, this returns the raw, GMX-precision
    ``(min, max)`` pair required by the ``Price.Props`` struct that
    ``Reader.getAccountPositionInfoList()`` takes as an input argument --
    it is not converted to a human-readable mark price.

    :raises ValueError:
        If ``oracle_prices`` has no usable price for ``token_address``.
    """
    for addr, data in oracle_prices.items():
        if addr.lower() == token_address.lower():
            if "maxPriceFull" not in data or "minPriceFull" not in data:
                break
            return int(data["minPriceFull"]), int(data["maxPriceFull"])

    raise ValueError(f"No oracle price available for token {token_address}; cannot value open GMX position.")


def _build_market_prices(tokens: tuple[str, str, str], oracle_prices: dict) -> tuple:
    """Build one ``MarketUtils.MarketPrices`` tuple for ``Reader.getAccountPositionInfoList()``.

    :param tokens:
        ``(index_token, long_token, short_token)`` addresses for a single market.

    :raises ValueError:
        If any of the three tokens has no oracle price.
    """
    index_token, long_token, short_token = tokens
    return (
        _oracle_price_tuple(oracle_prices, index_token),
        _oracle_price_tuple(oracle_prices, long_token),
        _oracle_price_tuple(oracle_prices, short_token),
    )


def _fetch_gmx_positions_value(
    web3: Web3,
    account: HexAddress,
    block_identifier: BlockIdentifier,
    chain: str,
    oracle_prices: dict,
) -> Decimal:
    """Read all open GMX positions and sum their net USD value.

    Uses ``Reader.getAccountPositionInfoList()``, whose
    ``positionValueInUsd`` is already net of borrowing fees, funding
    fees, position fees and price impact -- see the `GMX v2 Reader
    contract <https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/reader/Reader.sol>`__.
    This replaces a hand-rolled ``collateral + (mark - entry) * size``
    calculation that ignored all of those costs.

    :param oracle_prices:
        Live GMX signed prices, keyed by token address, as returned by
        :meth:`~eth_defi.gmx.core.oracle.OraclePrices.get_recent_prices`.
        Shared with the caller's reserves valuation so it is only fetched once.

    :return:
        Sum of all position values (USD, net of fees and price impact).
    """
    reader = get_reader_contract(web3, chain)
    addresses = get_contract_addresses(chain)

    # Cheap discovery call: which markets does this account actually have
    # positions in? Avoids building oracle price tuples for every GMX
    # market when the account only holds a handful of positions, and lets
    # us bail out early when there are none.
    raw_positions = reader.functions.getAccountPositions(addresses.datastore, account, 0, 100).call(block_identifier=block_identifier)

    if not raw_positions:
        logger.info("No open GMX positions for %s", account)
        return Decimal(0)

    market_addresses = sorted({to_checksum_address(raw_position[0][1]) for raw_position in raw_positions})
    market_tokens = _fetch_market_token_addresses(reader, addresses.datastore, block_identifier)

    # Every open position's market must resolve to its token triple, or the
    # Reader.getAccountPositionInfoList() call below silently mis-prices (or
    # under-counts) that position. Fail loudly here rather than let a missing
    # market -- e.g. one excluded by _fetch_market_token_addresses()'s zero
    # index-token filter, or outside its getMarkets() pagination window --
    # produce an understated NAV that nothing downstream would notice.
    unresolved = [market_address for market_address in market_addresses if market_address not in market_tokens]
    if unresolved:
        raise ValueError(f"Cannot value open GMX position(s): market(s) {unresolved} for account {account} were not found by _fetch_market_token_addresses() (chain={chain!r}). This position cannot be safely valued -- investigate before trusting this account's NAV.")

    market_prices = [_build_market_prices(market_tokens[market_address], oracle_prices) for market_address in market_addresses]

    referral_storage = _get_referral_storage_address(chain)

    raw_position_infos = reader.functions.getAccountPositionInfoList(
        addresses.datastore,
        referral_storage,
        account,
        market_addresses,
        market_prices,
        _ZERO_ADDRESS,  # uiFeeReceiver -- no UI fee for a read-only valuation
        0,
        100,
    ).call(block_identifier=block_identifier)

    positions_total = Decimal(0)
    for position_info in raw_position_infos:
        # PositionInfo tuple layout (see eth_defi/abi/gmx/Reader.json):
        # (positionKey, position, fees, executionPriceResult, basePnlUsd,
        #  uncappedBasePnlUsd, pnlAfterPriceImpactUsd, positionValueInUsd)
        position = position_info[1]
        market_address = to_checksum_address(position[0][1])
        is_long = position[2][0]
        position_value = Decimal(position_info[7]) / Decimal(10**PRECISION)

        logger.info(
            "Position market=%s is_long=%s value=%s",
            market_address,
            is_long,
            position_value,
        )
        positions_total += position_value

    return positions_total


def _get_mark_price(
    oracle_prices: dict,
    index_token_address: str,
    index_token_decimals: int,
) -> Decimal | None:
    """Get the current mark price for a token from GMX oracle data.

    :return:
        Mark price as :class:`~decimal.Decimal`, or ``None`` if unavailable.
    """
    # Case-insensitive lookup
    price_data = None
    for addr, data in oracle_prices.items():
        if addr.lower() == index_token_address.lower():
            price_data = data
            break

    if price_data is None:
        return None

    if "maxPriceFull" not in price_data or "minPriceFull" not in price_data:
        return None

    # Mid price from oracle min/max
    mid_price = (Decimal(price_data["maxPriceFull"]) + Decimal(price_data["minPriceFull"])) / Decimal(2)

    # Convert from 30-decimal precision, adjusting for token decimals
    mark_price = mid_price / Decimal(10 ** (PRECISION - index_token_decimals))

    return mark_price
