.. _uniswap-v3-swap:

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

Example output
~~~~~~~~~~~~~~

Here is what the output should look like

.. code-block:: text

    Connected to blockchain, chain id is 137. the latest block is 56,006,351
    Using Uniwap v3 compatible router at 0xE592427A0AEce92De3Edee1F18E0157C05861564
    Your address is 0xB53afDBd66c88418a723fc2961ddC7f6b1313D2b
    Your have 0 WETH
    Your have 1.358455 USDC
    Your have 17.346547483969616 for gas fees
    How many USDC tokens you wish to swap to WETH? 1
    Confirm swap amount 1 USDC to WETH
    Ok [y/n]?y
    Broadcasted transactions 0xda4a1e46079368fe85e68ebfc74b6bfd0a13214bd652d61e582e7e572be31fd0, 0xc2bdbc7742303d26716b5e49c07c279c034f285ca20fa62ee1f59a3ee01a2166, now waiting 2.5 minutes for it to be included in a new block
    View your transactions confirming at https://polygonscan/address/0xB53afDBd66c88418a723fc2961ddC7f6b1313D2b
    All ok!
    After swap, you have 0.000322448755681374 WETH
    After swap, you have 0.358455 USDC
    After swap, you have 17.346547483969616 native token left


Example script
~~~~~~~~~~~~~~

.. literalinclude:: ../../../scripts/make-swap-on-uniswap-v3.py
   :language: python
