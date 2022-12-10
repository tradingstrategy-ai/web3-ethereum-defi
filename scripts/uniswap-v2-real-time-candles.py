"""Show real-time candles of QuickSwap trading pairs."""

import logging
import os
import sys
import time

import pandas as pd
import requests
from eth_defi.event_reader.logresult import LogContext
from eth_defi.price_oracle.oracle import TrustedStablecoinPrice, TrustedStablecoinOracle
from eth_defi.uniswap_v2.pair import fetch_pair_details
from requests.adapters import HTTPAdapter


from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.ohlcv.reorgmon import JSONRPCReorganisationMonitor
from eth_defi.uniswap_v2.ohlcv_producer import UniswapV2OHLCVProducer


def main():
    logger = logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

    json_rpc_url = os.environ["JSON_RPC_POLYGON"]

    session = requests.Session()

    http_adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32)
    web3_factory = TunedWeb3Factory(json_rpc_url, http_adapter)
    web3 = web3_factory(LogContext())
    reorg_mon = JSONRPCReorganisationMonitor(web3)

    # https://tradingstrategy.ai/trading-view/polygon/quickswap/matic-usdc
    matic_usdc = fetch_pair_details(
        web3,
        "0x6e7a5fafcec6bb1e78bae2a1f0b612012bf14827",
        reverse_token_order=True,
    )

    oracles = {
        matic_usdc.get_quote_token(): TrustedStablecoinOracle(),
    }

    pairs = [
        matic_usdc
    ]

    # Set candle width
    candle_size = pd.Timedelta(minutes=1)

    # We display data for the last 3 hours
    window_size = pd.Timedelta(hours=0.5) / candle_size

    # Polygon block time
    block_time = pd.Timedelta(seconds=3)

    initial_blocks_buffered = window_size / block_time

    ohlcv_producer = UniswapV2OHLCVProducer(
        pairs,
        web3_factory,
        oracles,
        reorg_mon,
        candle_size=candle_size,
    )

    ohlcv_producer.

    while True:
        ohlcv_producer.perform_duty_cycle()
        time.sleep(1)


if __name__ == "__main__":
    main()