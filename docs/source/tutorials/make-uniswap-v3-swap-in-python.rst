.. meta::
   :title: How to swap tokens on Uniswap v3 using Python
   :description: Python Uniswap 3 token swap tutorial

Swap tokens on Uniswap v3
-------------------------

This is an simple example script to swap one token to another.
It works on any `Uniswap v3 compatible DEX <https://tradingstrategy.ai/glossary/uniswap>`__.
For this particular example, we use Uniswap v3 on Polygon,
but you can reconfigure the script for any  `EVM-compatible <https://tradingstrategy.ai/glossary/evm-compatible>`__
blockchain.

- :ref:`Read tutorials section for required Python knowledge, version and how to install related packages <tutorials>`

How to use

- Create a private key. `You can generate a private key on a command line using these instructions <https://ethereum.stackexchange.com/a/125699/620>`__.
  Store this private key safely e.g. in your password manager.

- Import the private key into a cryptocurrency wallet. We recommend `Rabby <https://rabby.io/>`__.

- Get MATIC (for gas gees) and USDC (for the trade) into the wallet.
  Note that Polygon has two different USDC flavours, native (USDC) and bridged (USDC.e).
  We use native USDC in this script. The easiest way is to buy MATIC in a centralised
  exchange and swap a bit it to USDC in Rabby internal swap function or uniswap.org.

- Configure environment variables and run this script

- The script will make you a swap, swapping 1 USDC for WETH on Uniswap v3

To run:

.. code-block:: shell

    export JSON_RPC_POLYGON="https://polygon-rpc.com"
    export PRIVATE_KEY="your private key here"
    python scripts/make-swap-on-uniwap-v3.py

.. note ::

    Polygon is notoriously low quality what comes to broadcasting transactions and confirming them.
    If you get errors like `Transaction confirmation failed` and `ValueError: {'code': -32000, 'message': 'replacement transaction underpriced'}`
    it usually means that Polygon mempool is broken. In this case, try to run the script on Uniswap v3
    deployment on some other blockchain.

Example script
~~~~~~~~~~~~~~

.. literalinclude:: ../../../scripts/make-swap-on-uniswap-v3.py
   :language: python
