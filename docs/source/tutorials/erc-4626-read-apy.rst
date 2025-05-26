.. meta::
   :description: Tutorial to read APY of ERC-4626 vault

.. _read-erc-4626-apy:

ERC-4626: current APY of a vault
================================

Here is a Python example how to estimate the ERC-4626 `APY <https://tradingstrategy.ai/glossary/annual-percentage-yield-apy>`__.

- Reads the most recent APY of given ERC-4626 `vault <https://tradingstrategy.ai/glossary/vault>`__

- The APY is calculated using the price difference of the vault share token

- Only uses EVM JSON-RPC and archive node, no external services needed. Public RPC nodes won't work,
  because they are not archive nodes. Get your Base node JSON-RPC access from `dRPC <https://drpc.io/>`__ or `Ethereumnodes.com <https://ethereumnodes.com/>`__.

- Supported vaults include all ERC-4626, including but not limited to: Morpho, Euler, Lagoon Finance, Superform, IPOR, Yearn, Fluid

Then to run this script:

.. code-block:: shell

    # Get JSON-RPC archive node
    export JSON_RPC_BASE=...
    python scripts/erc-4626/read-live-apy.py

Output looks like:

.. code-block:: plain

    Vault: IPOR USDC Lending Optimizer Base (0x45aa96f0b3188d47a1dafdbefce1db6b37f58216)
    Estimated APY: 5.53%
    Period: 2025-05-19 11:08:31 - 2025-05-26 11:08:31
    Block range: 30,431,782 - 30,734,182
    Share price at begin: 1.042212618930521299088288235 ipUSDCfusion / USDC
    Share price at end: 1.043318675169052799414866657 ipUSDCfusion / USDC
    Share price diff: 0.001106056238531500326578422 ipUSDCfusion / USDC


Further reading

- See :py:ref:`erc-4626` API documentation.
- `For any questions please join to Discord chat <https://tradingstrategy.ai/community>`__.

.. literalinclude:: ../../../scripts/erc-4626/read-live-apy.py
   :language: python
