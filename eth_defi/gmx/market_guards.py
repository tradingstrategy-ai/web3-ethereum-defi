"""On-chain guards applied to loaded GMX markets.

:func:`filter_zero_open_interest_cap_markets` drops perpetual markets whose
``MAX_OPEN_INTEREST`` is 0 on both sides.  That is how GMX retires a pool
(ZEC/HYPE/XRP/NEAR in 2026-11): the market stays listed with live open
interest, ``isListed`` and ``IS_MARKET_DISABLED`` do not change, and every
open is keeper-cancelled with ``MaxOpenInterestExceeded(maxAllowed: 0)`` after
the execution fee is paid.  The DataStore holds the caps for every market, so
this check works for every loading path (GraphQL, REST, RPC, disk cache),
unlike the GraphQL-only ``maxOpenInterestLong``/``Short`` fields.
"""

import logging

from web3 import Web3

from eth_defi.event_reader.multicall_batcher import get_multicall_contract
from eth_defi.gmx.contracts import get_datastore_contract
from eth_defi.gmx.keys import max_open_interest_key

logger = logging.getLogger(__name__)


def _market_token(market: dict) -> str:
    info = market.get("info", {})
    return info.get("market_token") or info.get("marketToken") or ""


def filter_zero_open_interest_cap_markets(web3: Web3, chain: str, markets: dict[str, dict]) -> dict[str, dict]:
    """Drop markets whose long and short ``MAX_OPEN_INTEREST`` are both 0.

    One Multicall3 ``aggregate3`` round-trip with two ``DataStore.getUint`` calls
    per market.  A market is dropped only when both reads succeed and both are
    0; a failed read keeps it (cannot tell), and any error returns the input
    unchanged, matching the disabled-market filter's fail-open behaviour.

    Only CCXT perpetual markets are passed in (swap-only markets, which also
    have zero caps, are never part of the CCXT markets dict).

    :param web3: Synchronous Web3 connection.
    :param chain: Chain name for the DataStore address.
    :param markets: CCXT markets keyed by unified symbol.
    :return: Filtered markets dict.
    """
    try:
        datastore = get_datastore_contract(web3, chain)
        multicall = get_multicall_contract(web3)

        batch: list[tuple[str, bytes, bytes]] = []
        for symbol, market in markets.items():
            token = _market_token(market)
            if not token:
                continue
            token = Web3.to_checksum_address(token)
            long_call = bytes.fromhex(datastore.encode_abi(abi_element_identifier="getUint", args=[max_open_interest_key(token, True)])[2:])
            short_call = bytes.fromhex(datastore.encode_abi(abi_element_identifier="getUint", args=[max_open_interest_key(token, False)])[2:])
            batch.append((symbol, long_call, short_call))

        if not batch:
            return markets

        calls = []
        for _symbol, long_call, short_call in batch:
            calls.append((datastore.address, True, long_call))
            calls.append((datastore.address, True, short_call))
        results = multicall.functions.aggregate3(calls).call()

        drop: set[str] = set()
        for index, (symbol, _long_call, _short_call) in enumerate(batch):
            (ok_long, data_long), (ok_short, data_short) = results[2 * index], results[2 * index + 1]
            if not (ok_long and ok_short and data_long and data_short):
                continue
            if int.from_bytes(data_long, "big") == 0 and int.from_bytes(data_short, "big") == 0:
                drop.add(symbol)
                logger.warning(
                    "Market %s (%s) has maxOpenInterest 0 on both sides (retired pool) — excluding from active markets",
                    symbol,
                    _market_token(markets[symbol]),
                )

        logger.info("DataStore open-interest-cap filter checked %d markets, removed %d", len(batch), len(drop))
        return {symbol: market for symbol, market in markets.items() if symbol not in drop}
    except Exception as e:  # noqa: BLE001 - never lose all markets on a guard failure
        logger.warning("filter_zero_open_interest_cap_markets failed, returning unfiltered markets: %s", e)
        return markets
