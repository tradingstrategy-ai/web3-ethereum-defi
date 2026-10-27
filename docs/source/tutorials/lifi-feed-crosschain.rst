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

Configuration and defaults
--------------------------

Load ``PRIVATE_KEY`` and the selected ``JSON_RPC_*`` connections from your private
environment. Never paste a private key into shell history. ``SOURCE_CHAIN``
defaults to ``arbitrum`` and ``TARGET_CHAINS`` to ``ethereum,hyperliquid``.
Fallback RPC URL lists are supported. Optional settings:

- ``MIN_GAS_USD=5``: prepare a gas transfer below this destination balance.
- ``TOP_UP_GAS_USD=20``: source spending in USD per low-gas destination.
- ``SOURCE_TOKEN=native``: pay for gas transfers with native tokens; ``usdc``
  spends source-chain native USDC instead. Source native gas is always needed.
- ``ETHEREUM_USDC_AMOUNT=0``: disabled by default. A positive amount additionally
  spends that many source-chain native USDC to receive native USDC on Ethereum,
  at the same wallet address. This works even when Ethereum gas is sufficient.
- ``DRY_RUN=false``: show balances and quotes, then ask before execution.
  Set ``true`` to make no approvals or transactions.

The explicit USDC amount is **source spending**, not an exact received amount
or a destination balance target. Fees reduce the result. Review the estimated
and minimum received amounts, then check the observed destination balance.
Every live invocation with this option requests another transfer. Inspect the
original transaction/status after a timeout instead of blindly rerunning it.
The source must be a supported non-Ethereum chain with native USDC.

Running the script
------------------

After loading the private environment:

.. code-block:: shell

    # Default gas routes: Arbitrum to Ethereum and HyperEVM, quotes only.
    DRY_RUN=true poetry run python scripts/lifi/feed-cross-chain.py

    # Add an explicit 5-USDC source spend for Ethereum, still quotes only.
    DRY_RUN=true ETHEREUM_USDC_AMOUNT=5 poetry run python scripts/lifi/feed-cross-chain.py

    # Review and confirm the actual transfers interactively.
    ETHEREUM_USDC_AMOUNT=5 poetry run python scripts/lifi/feed-cross-chain.py

    # Only transfer USDC; no gas top-ups.
    TARGET_CHAINS='' ETHEREUM_USDC_AMOUNT=5 poetry run python scripts/lifi/feed-cross-chain.py

For a different gas topology, set e.g. ``TARGET_CHAINS=base,ethereum,hyperliquid``.
The script approves only the required ERC-20 amount to the quote's spender when
allowance is insufficient, following `LI.FI's approval guidance
<https://help.li.fi/hc/en-us/articles/13305031013275-How-can-I-find-get-approve-tx-data-and-check-for-allowance>`_.
It waits for delivery and rejects failed, pending, refunded or partial delivery
as successful funding. The Ethereum USDC path also checks the wallet's observed
USDC increase against the executed quote's minimum.

Preparing the Lagoon deployer
----------------------------

The :doc:`multichain Lagoon tutorial <lagoon-multichain>` requires native gas
on every selected chain. Its deployment-only Lighter example needs just 1 USDC
on Ethereum for activation, with no hub subscription or activation bridge.
The separate three-chain investment example needs 25 USDC
on Arbitrum plus a 1-USDC deployer reserve on each of Ethereum and HyperEVM.
The default gas destinations match that topology. This distribution script's
USDC option targets Ethereum only: prefund HyperEVM USDC separately.

Use the same wallet as ``LAGOON_MULTCHAIN_TEST_PRIVATE_KEY`` by configuring
``PRIVATE_KEY`` in your private environment. These transfers fund the deployer,
not the satellite Safes. Leave enough hub USDC for the subscription and native
gas on every chain for both deployment and later unwind. Satellite reserves
stay outside Lagoon accounting. Simulation supplies these initial balances on
local forks and requires no real funds.

API documentation
-----------------

See :doc:`LI.FI API reference </api/lifi/index>` for the module documentation.

Source code
-----------

.. literalinclude:: ../../../scripts/lifi/feed-cross-chain.py
   :language: python
