"""Feed gas to hot wallets across multiple chains using LI.FI.

Checks native token gas balances on target chains and bridges
gas from a source chain when any target is running low.

See :py:mod:`eth_defi.lifi.top_up` for the full implementation and
environment variable reference.

Usage:

.. code-block:: shell

    export PRIVATE_KEY=<...>
    export DRY_RUN=true
    export SOURCE_CHAIN=arbitrum
    export TARGET_CHAINS="base, ethereum, monad, hyperliquid, avalanche"
    export MIN_GAS_USD=5
    export TOP_UP_GAS_USD=10
    python scripts/lifi/feed-cross-chain.py

"""

from eth_defi.lifi.top_up import perform_top_up


def main():
    perform_top_up()


if __name__ == "__main__":
    main()
