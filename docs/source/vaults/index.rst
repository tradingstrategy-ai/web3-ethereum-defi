Vault protocols
===============

This section documents integrations with ERC-4626 compliant vault protocols.

About ERC-4626
--------------

`ERC-4626 <https://eips.ethereum.org/EIPS/eip-4626>`__ is a tokenised vault standard
that provides a common interface for yield-bearing vaults. The standard defines
a consistent API for deposit, withdrawal, and share accounting operations,
making it easier to integrate with different vault implementations.

Vault integrations
------------------

This Python package provides integrations with various ERC-4626 vault protocols,
enabling you to:

- **Discover vaults**: Find vaults and query their features across multiple chains
- **Deposit and redeem**: Automate deposits into vaults and redemptions from your application
- **Query positions**: Check your vault positions and share balances
- **Historical data**: Read share price history, TVL, deposits, and redemptions
- **Protocol-specific features**: Handle unique characteristics like redemption delays, fees, and settlement mechanics

For the core ERC-4626 functionality shared across all vault protocols, see
:py:mod:`eth_defi.erc_4626`.

Supported protocols
-------------------

.. toctree::
   :maxdepth: 1

   aarna/index
   accountable/index
   altura/index
   avant/index
   auto_finance/index
   basevol/index
   brink/index
   cap/index
   centrifuge/index
   csigma/index
   curvance/index
   d2_finance/index
   deltr/index
   dolomite/index
   ethena/index
   euler/index
   eth_strategy/index
   fluid/index
   foxify/index
   frax/index
   gains/index
   gearbox/index
   goat/index
   harvest/index
   hyperdrive_hl/index
   hyperlend/index
   hypurrfi/index
   infinifi/index
   ipor/index
   lagoon/index
   liquidity_royalty/index
   llama-lend/index
   mainstreet/index
   maple/index
   morpho/index
   nashpoint/index
   plutus/index
   renalta/index
   resolv/index
   royco/index
   sentiment/index
   silo/index
   singularity/index
   sky/index
   spark/index
   spectra/index
   summer/index
   superform/index
   teller/index
   term_finance/index
   truefi/index
   umami/index
   untangle/index
   upshift/index
   usdai/index
   usdd/index
   usdx_money/index
   yearn/index
   yieldfi/index
   yieldnest/index
   yo/index
   yuzu_money/index
   zerolend/index
