.. meta::
   :description: Tutorial for cross-chain gas feeding with LI.FI

.. _lifi-feed-crosschain:

Cross-chain gas feeding with LI.FI
===================================

Here is a Python example showing how to keep hot wallets funded with gas across
multiple EVM chains using `LI.FI <https://li.fi>`__.

Overview
--------

- Checks native token gas balances on target chains
- Fetches USD prices from the LI.FI token API
- Identifies chains running low on gas (below a configurable USD threshold)
- Fetches bridge quotes from LI.FI to bridge native tokens from a source chain
- Optionally executes bridge transactions with human approval

Chain names are resolved using our internal names from :py:data:`eth_defi.chain.CHAIN_NAMES`
(e.g. "ethereum", "arbitrum", "base"), converted to numeric chain IDs, and passed
to the LI.FI API which also uses numeric chain IDs.

Prerequisites
-------------

- Native tokens on your source chain (e.g. ETH on Arbitrum)
- JSON-RPC endpoints for all chains involved (``JSON_RPC_*`` environment variables)
- Optional: a `LI.FI API key <https://portal.li.fi>`__ for higher rate limits

Environment variables
---------------------

.. code-block:: shell

    # Hot wallet private key
    export PRIVATE_KEY=0x...
    # Source chain (where funds are bridged from)
    export SOURCE_CHAIN=arbitrum
    # Target chains (comma-separated, where gas is needed)
    export TARGET_CHAINS=base,polygon,ethereum
    # Minimum gas balance in USD before triggering a top-up
    export MIN_GAS_USD=5
    # Amount to bridge in USD when topping up
    export TOP_UP_GAS_USD=20
    # Optional: LI.FI API key
    export LIFI_API_KEY=...
    # Optional: set to true to only show quotes without executing
    export DRY_RUN=true

Running the example
-------------------

.. code-block:: shell

    source .local-test.env && \
    poetry run python scripts/lifi/feed-cross-chain.py

The script will display a balance table and proposed swaps, then ask for
confirmation before executing. Set ``DRY_RUN=true`` to skip execution entirely.

API documentation
-----------------

See :doc:`LI.FI API reference </api/lifi/index>` for the module documentation.

Source code
-----------

.. literalinclude:: ../../../scripts/lifi/feed-cross-chain.py
   :language: python
