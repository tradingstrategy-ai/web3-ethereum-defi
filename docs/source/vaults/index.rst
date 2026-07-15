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

Deposit manager capability metadata
-----------------------------------

Each newly scanned vault record in the public metrics JSON contains a nullable
``deposit_manager`` object. A non-null value means this library has an explicit
two-way deposit and redemption manager for that vault shape; its
``deposit_flow`` and ``redemption_flow`` values are either ``synchronous`` or
``asynchronous``. Asynchronous flows require callers to persist the returned
ticket and submit the later claim operation.

This is adapter metadata, not live availability. Before submitting a request,
consumers must use current chain state and handle a live preflight or
transaction revert, then handle the declared flow type. The field does not
assert that an account is permissioned, funded, within a vault cap, or able to
obtain redemption liquidity.

Vault settlement event scanning
-------------------------------

The production vault scanner stores sparse asynchronous settlement events in
``vault-settlements.duckdb``. Supported Lagoon and D2 Finance vaults are scanned
as part of each successful EVM chain cycle, not as a separate all-vault
post-processing pass. The event reader queries all supported vault addresses on
the chain as one batch, chunked by block range for the JSON-RPC fallback, and
then filters the returned logs back to each vault's incremental block range.
The production chain loop uses the just-completed chain scan's end block and
cached vault metadata to select settlement ranges, avoiding an extra raw price
parquet read for each chain.
The DuckDB database also stores per-vault scan watermarks, so successful empty
event scans do not cause the same historical block range to be queried again.
During price post-processing, the cleaner reads ``vault-settlements.duckdb``
and annotates the cleaned price DataFrame with ``vault_settlement_at``. The
raw price parquet remains settlement-free; settlement markers are derived from
the sparse event database after row-level cleaning has reduced the price frame.
A failed settlement read is logged and shown in
the scanner dashboard, but it does not abort the rest of the scanner cycle or
price cleaning. If one vault cannot be prepared or decoded, the scanner skips
that vault and still stores settlement events for the other vaults in the same
chain batch.

Supported protocols
-------------------

.. toctree::
   :maxdepth: 1

   aave/index
   forty_acres/index
   aarna/index
   aera/index
   accountable/index
   altura/index
   atoma/index
   avant/index
   auto_finance/index
   basevol/index
   brink/index
   cap/index
   centrifuge/index
   crystalclear/index
   csigma/index
   curvance/index
   d2_finance/index
   deltr/index
   dolomite/index
   domination/index
   ember/index
   ethena/index
   euler/index
   eth_strategy/index
   fluid/index
   forgeyields/index
   foxify/index
   frankencoin/index
   frax/index
   gains/index
   gearbox/index
   goat/index
   harvest/index
   hyperdrive_hl/index
   hyperlend/index
   hyperliquid/index
   hypurrfi/index
   infinifi/index
   inverse_finance/index
   ipor/index
   kiln/index
   kiloex/index
   kinexys/index
   lagoon/index
   liquid_royalty/index
   llama-lend/index
   mainstreet/index
   maple/index
   mellow/index
   maseer-one/index
   midas/index
   vault_street/index
   morpho/index
   nashpoint/index
   plutus/index
   renalta/index
   resolv/index
   royco/index
   sbold/index
   secured_finance/index
   sentiment/index
   silo/index
   singularity/index
   sky/index
   spark/index
   spectra/index
   summer/index
   superform/index
   t3tris/index
   teller/index
   term_finance/index
   threejane/index
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
