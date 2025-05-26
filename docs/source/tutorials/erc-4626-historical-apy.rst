.. meta::
   :description: Calculate the historical APY of ERC-4626 vault

.. _historical-erc-4626-apy:

ERC-4626: historical APY of a vault
===================================

Here is a Python example how to estimate the ERC-4626 `APY <https://tradingstrategy.ai/glossary/annual-percentage-yield-apy>`__.

- Takes a time range and estimates the APY for it

- The APY is calculated using the price difference of the vault share token

- Uses `FindBlock <https://www.findblock.xyz/>`__ to convert timestamps to the historical block range.

- Uses EVM JSON-RPC and archive node, no external services needed. Public RPC nodes won't work,
  because they are not archive nodes. Get your Base node JSON-RPC access from `dRPC <https://drpc.io/>`__ or `Ethereumnodes.com <https://ethereumnodes.com/>`__.

Then to run this script:

.. code-block:: shell

    # Get JSON-RPC archive node
    export JSON_RPC_BASE=...
    python scripts/erc-4626/read-historical-apy.py

Output looks like:

.. code-block:: plain

    Vault: IPOR USDC Lending Optimizer Base (0x45aa96f0b3188d47a1dafdbefce1db6b37f58216)
    Chain: Base
    Estimated APY: 5.32%
    Period: 2025-02-28 23:59:59 - 2025-04-30 23:59:59 (61 days)
    Block range: 26,998,926 - 29,634,126
    Share price at begin: 1.030367113833284958570710981 ipUSDCfusion / USDC
    Share price at end: 1.039527386826594071396200349 ipUSDCfusion / USDC
    Share price diff: 0.009160272993309112825489368 ipUSDCfusion / USDC

Further reading

- See :py:ref:`erc-4626` API documentation.
- `For any questions please join to Discord chat <https://tradingstrategy.ai/community>`__.

.. literalinclude:: ../../../scripts/erc-4626/read-historical-apy.py
   :language: python
