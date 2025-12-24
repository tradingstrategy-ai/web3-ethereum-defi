.. meta::
   :title: How to swap tokens on Uniswap and DEXes using Python
   :description: Python token swap tutorial with slippage protection


Swap tokens on Uniswap v2 compatible DEXes
------------------------------------------

This is an simple example script to swap one token to another securely.
It works on any `Uniswap v2 compatible DEX <https://tradingstrategy.ai/glossary/uniswap>`__.
For this particular example, we use PancakeSwap on Binance Smart Chain,
but you can reconfigure the script for any Uniswap v2 compatible protocol
on any `EVM-compatible <https://tradingstrategy.ai/glossary/evm-compatible>`__ blockchain.
The script is set up to swap from BUSD to Binance-custodied ETH.

- First :ref:`Read tutorials section for required Python knowledge, version and how to install related packages <tutorials>`.
- In order to run this example script, you need to have
    - Private key on BNB Smart Chain with BNB balance, `you can generate a private key on a command line using these instructions <https://ethereum.stackexchange.com/a/125699/620>`__.
    - `Binance Smart Chain JSON-RPC node <https://docs.bnbchain.org/docs/rpc>`__. You can use public ones.
    - BUSD balance (you can swap some BNB on BUSD manually by importing your private key to a wallet).
    - Easy way to get few dollars worth of starting tokens is `Transak <https://global.transak.com/>`__. Buy popular tokens with debit card - Transak supports buying tokens natively for many blockchains.
    - Easy way to to manually swap between BUSD/BNB/other native gas token is to import your private key to `Rabby desktop wallet <https://rabby.io/>`__ and use Rabby's built-in swap and trade aggregator function.

This script will

- Sets up a private key with BNB gas money

- Sets up a PancakeSwap instance

- Perform a swap from BUSD (`base token <https://tradingstrategy.ai/glossary/base-token>`__) to
  Binance-custodied ETH (`quote token <https://tradingstrategy.ai/glossary/quote-token>`__) for any amount of tokens you enter

- `Uses slippage protection <https://tradingstrategy.ai/glossary/slippage>`__
  for the swap so that you do not get exploited by `MEV bots <https://tradingstrategy.ai/glossary/mev>`__

- Wait for the transactions to complete and display the reason if the trade failed

To find tokens to trade you can use `Trading Strategy search <https://tradingstrategy.ai/search>`__
or `market data listings <https://tradingstrategy.ai/trading-view>`__.
`For any questions please join to Discord chat <https://tradingstrategy.ai/community>`__.

To run:

.. code-block:: shell

    export JSON_RPC_BINANCE="https://bsc-dataseed.bnbchain.org"
    export PRIVATE_KEY="your private key here"
    python scripts/make-swap-on-pancake.py

Example script
~~~~~~~~~~~~~~

.. literalinclude:: ../../../scripts/make-swap-on-pancake.py
   :language: python
