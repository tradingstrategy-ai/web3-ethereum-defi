.. meta::
   :description: GMX v2 swap tutorial

.. _gmx-swap:

GMX: swap tokens
================

Here is a Python example how to swap tokens on `GMX <https://gmx.io/>`__.

GMX is a `decentralised exchange <https://tradingstrategy.ai/glossary/decentralised-exchange>`__ for
`perpetual futures <https://tradingstrategy.ai/glossary/perpetual-future>`__.

- The swap takes place on GMX Arbitrum instance

- You can swap between GMX collateral tokens

- This is the simplest possible GMX code example for getting started - it does not use any leverage
  or advanced GMX features

- The example does not use external APIs, only raw Arbitrum JSON-RPC API

- Takes a time range and estimates the APY for it

Then to run this script:

.. code-block:: shell

    # Get JSON-RPC archive node
    export JSON_RPC_BASE=...
    python scripts/gmx/swap.py

Output looks like:

.. code-block:: none

    TODO

Further reading

- :ref:`gmx`
- `GMX swap documentation <https://docs.gmx.io/docs/trading/v2#swaps>`__


.. literalinclude:: ../../../scripts/gmx/swap.py
   :language: python
