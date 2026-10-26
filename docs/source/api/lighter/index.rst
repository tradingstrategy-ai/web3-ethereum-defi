Lighter API
-----------

`Lighter <https://lighter.xyz/>`__ decentralised perpetuals exchange integration.

This package contains two Lighter integration surfaces.

The public vault-data surface extracts Lighter pool data via public endpoints,
mirroring the :doc:`Hyperliquid integration <../hyperliquid/index>` and
:doc:`GRVT integration <../grvt/index>`:

- Pool listing via the public ``/api/v1/publicPoolsMetadata`` endpoint
- Per-pool share price history from ``/api/v1/account``
- DuckDB storage for daily metrics
- Bridge into the ERC-4626 pipeline (VaultDatabase pickle + uncleaned Parquet)

No authentication is required for this vault-data path -- all data comes from
public endpoints.

Pool fees and investor returns
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Lighter's operator performance fee is **externalised**: a participant pays a
percentage of positive profits upon withdrawal. The pool share-price series is
gross of this investor fee. This follows the
`official public-pool documentation <https://docs.lighter.xyz/trading/public-pools>`__
and is corroborated by the public-pool worked example in the independent
`Cipher Intelligence guide <https://cipher-intelligence.io/blog/lighter-dex-lp-staking-guide/>`__.

The pipeline preserves the operator fee in both ``gross_fees`` and
``net_fees`` and deducts it once from positive investment-period profits before
annualising. A loss incurs no performance fee. A 20% gross return with a 30%
operator fee therefore produces a 14% investor net return.

Raw and cleaned Parquet files carry the fee as a fraction in
``performance_fee`` (``0.3`` means 30%), joined by deployment and account index.
These exports apply the current metadata fee to each period; they do not
reconstruct historical fee changes or individual deposit and withdrawal lots.
An unknown operator fee remains null and suppresses investor net returns
instead of reporting gross returns as net.
The protocol-operated LLP has a zero operator fee. Exchange trading and
network withdrawal charges are distinct from this pool-level performance fee.

The scheduled Lighter scan refreshes cached fee metadata automatically through
its normal metadata merge. For an immediate correction without waiting for the
next scan, refresh metadata from the existing Lighter DuckDB before JSON
regeneration. Pass affected vault IDs to
:py:func:`eth_defi.vault.top_vaults_json.main` as ``force_vault_ids`` to replace
cached or sticky metrics immediately. See the pipeline README below for the
isolated live export check and regeneration procedure.

Guarded custody
~~~~~~~~~~~~~~~

The guarded Lagoon/Safe surface supports the manual mainnet custody lifecycle:

- Guard whitelisting for Lighter ``deposit`` / ``withdraw`` /
  ``withdrawPendingBalance`` L1 calls
- Safe-owned ``changePubKey`` API-key registration
- Lagoon-vault helper for the minimum USDC activation deposit
- Account valuation via public Lighter account NAV fields
- Deployment-time trading API-key generation without an SDK dependency;
  the separate trading tutorial uses Lighter's official SDK for order signing
- Generic auth-token refresh and retry for authenticated, idempotent SDK calls
- Sub-account creation and key registration for a Safe-owned master account,
  same-owner USDC transfers, and the key-free L1 ``withdraw`` path that
  drains any owned account to the Safe

Tutorials
~~~~~~~~~

- :doc:`Lighter: benchmark pools </tutorials/lighter-vault-benchmark>` - Benchmark Lighter pool performance, equity curves, and rolling returns
- ``scripts/lagoon/lagoon-lighter-example.py`` - Deploy and activate a Lagoon vault with a Lighter API key
- ``scripts/lagoon/lagoon-lighter-trade-example.py`` - Deploy, open and close a small ETH perpetual, and read NAV

For architecture details, API endpoint reference, DuckDB schema, and
fee model documentation, see
`README-lighter-vaults.md <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/scripts/lighter/README-lighter-vaults.md>`__.

.. autosummary::
   :toctree: _autosummary_lighter
   :recursive:

   eth_defi.lighter.vault
   eth_defi.lighter.valuation
   eth_defi.lighter.daily_metrics
   eth_defi.lighter.vault_data_export
   eth_defi.lighter.session
   eth_defi.lighter.constants
   eth_defi.lighter.api
   eth_defi.lighter.sdk
   eth_defi.lighter.api_key
   eth_defi.lighter.deployment
   eth_defi.lighter.lagoon
   eth_defi.lighter.pubkey
   eth_defi.lighter.sub_account
   eth_defi.lighter.transfer
   eth_defi.lighter.testing

Guard integration
~~~~~~~~~~~~~~~~~~

For depositing into and withdrawing from Lighter through an asset-managed
Gnosis Safe governed by ``GuardV0`` / ``TradingStrategyModuleV0``, see
:py:mod:`eth_defi.lighter.deployment` and the
`README-lighter-guard.md <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/eth_defi/lighter/README-lighter-guard.md>`__
architecture and security notes.
