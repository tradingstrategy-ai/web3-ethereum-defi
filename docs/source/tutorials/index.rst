Tutorials
=========

Examples how to use web3.py and Web-Ethereum-Defi library.

Examples include

* Reading live trades of different DEXes

* Performing ERC-20 token transfers

* Data research with Jupyter Notebooks and Pandas

Prerequisites
-------------

Make sure you know how to install packages (pip, poetry)
and use Python virtual environments.

To run the scripts you need to be able to understand
how Python packaging works and how to install additional modules.

Install the package with data addons:

.. code-block:: shell

    pip install "web3-ethereum-defi[data]"

Example tutorials
-----------------

.. toctree::
   :maxdepth: 1

   transfer
   multithread-reader
   verify-node-integrity
   live-price
   uniswap-v3-liquidity-analysis
   uniswap-v3-price-analysis
   event-reader
   live-swap-minimal
   pancakeswap-live-minimal
   live-swap
   aave-v3-interest-analysis

`For more examples, browse tests folder on Github <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/tests>`__.
You can also search function names in `the repository <https://github.com/tradingstrategy-ai/web3-ethereum-defi/>`__
using Github search.