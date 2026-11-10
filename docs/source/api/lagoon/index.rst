Lagoon Finance API
------------------

`Lagoon Finance <https://lagoon.finance/>`__ vault protocol integration.

Lagoon is a non-custodial asset management protocol built on ERC-4626.
This module provides tools for interacting with Lagoon vaults, including:

- Vault deployment and configuration
- Deposits and redemptions
- CoW Swap integration for vault trading
- GMX perpetuals integration for vault trading
- Lighter account activation and perpetuals integration
- Offchain metadata fetching

The ERC-4626 adapter can read and scan known Lagoon releases through v0.6 and
the fixed-block-characterised v1.0 compatibility surface. The v1
implementation is not verified, so the adapter uses the official v0.6 ABI only
for calls and storage fields covered by its Base integration test. Vault
deployment remains pinned to the repository's v0.5 artefacts; v1 deployment
and upgrade support are not provided.

Reviewed private deployments remain excluded by default unless their chain and
address are explicitly allowlisted. Kamui's Stable, Balanced and Boosted vaults
are approved through this mechanism. Their v0.6 contracts report whitelisted
deposits onchain, and their asynchronous flow history bypasses the generic
vault-local ``Deposit`` event threshold used to suppress inactive contracts.

Offchain metadata
~~~~~~~~~~~~~~~~~

Known deployments can fetch descriptions from Lagoon's exact-address API even
when omitted from its public catalogue. Detail availability never approves a
private or closed vault for public listing. Public membership and the reviewed
private allowlist remain independent eligibility inputs.

The versioned cache under ``~/.tradingstrategy/cache/lagoon`` preserves historical
payloads in ``lagoon_metadata_chain_{id}_v2.json`` and reads legacy
``lagoon_vaults_chain_{id}.json`` maps without overwriting them. Older releases
can therefore still read their own format on rollback. Active non-empty
details expire daily; empty or closed details expire after seven days and refresh
on demand. A 404 retries after one day; temporary failures use a one-hour delay
or bounded provider ``Retry-After``. A detail HTTP 429 sets a shared chain
deadline so other detail requests defer too. Failed reads retain previous payloads.
Successful empty fields remain visible in the provider cache, but normal metadata
merges preserve already published descriptions and log the retained fields.
Successful empty source fields are logged at INFO; retried failures use WARNING.
A deliberate deletion requires a reviewed address-specific accessor correction
and a new numbered migration; do not just blank the pickle, because a normal
scan may restore provider text. Invalid versioned cache files fail with a
diagnostic instead of discarding history. A zero-byte legacy file is treated
as absent, matching the previous reader.

Cache expiry does not schedule a scan. Default lead-discovery receipts and
successful metadata observations each have a seven-day interval. Under healthy
scheduled chain execution, a conservative upper bound is fourteen days plus the
next chain tick; chain failures, disabled scanning or operator overrides can
extend it. The existing force-refresh path bypasses scanner scheduling, not API
rate-limit deadlines. Price-only cycles do not update these metadata fields.

Migration ``036-lagoon-offchain-metadata.py`` fills only missing descriptions for
22 reviewed deployments; it does not change flags, curator assignments or price
history. Use the numbered migration runner and inspect all pending migrations.

A minimal public API check needs no RPC or credentials::

    source .local-test.env && RUN_LAGOON_LIVE=true timeout 180s poetry run pytest tests/lagoon/test_lagoon_unlisted_metadata.py -v -rA

This check uses temporary cache storage and is guarded because Lagoon blocks
GitHub Actions IPs. A closed product disappearing from the catalogue does not
mean its available historical strategy text should be discarded.

Each description or curator-name accessor makes at most one metadata read per
call. Standalone reads may refresh catalogue or detail data over HTTP when due.
The scanner pins an observation for the whole row, so its accessors share the
same payload and membership result.

Migration ``037-lagoon-strategy-tags.py`` validates classified rows against the
shared adapter priority before collecting tag updates. Missing Lagoon detection,
or detection selecting a native or earlier-priority adapter, fails the batch
before any metadata is committed. Unmapped vaults retain their persisted tags.

Tutorials
~~~~~~~~~

- :ref:`lagoon-cowswap` - Trading via CowSwap from a Lagoon vault
- :ref:`lagoon-gmx` - Trading GMX perpetuals from a Lagoon vault
- :ref:`lagoon-velora` - Velora integration
- :ref:`lagoon-hyperliquid` - Deploying on HyperEVM with Hypercore deposits
- ``scripts/lagoon/lagoon-lighter-example.py`` - Deploying a Lagoon vault with a Lighter API key
- ``scripts/lagoon/deploy-lagoon-lighter-multichain.py`` - Manual multichain deployment with CCTP funding and satellite Lighter activation
- ``scripts/lagoon/lagoon-lighter-trade-example.py`` - Trading an ETH perpetual with the deployed API key

.. autosummary::
   :toctree: _autosummary_lagoon
   :recursive:

   eth_defi.lagoon.vault
   eth_defi.lagoon.deployment
   eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem
   eth_defi.erc_4626.vault_protocol.lagoon.constants
   eth_defi.erc_4626.vault_protocol.lagoon.funding
   eth_defi.erc_4626.vault_protocol.lagoon.offchain_metadata
   eth_defi.erc_4626.vault_protocol.lagoon.tags
   eth_defi.lagoon.cowswap
   eth_defi.lagoon.config
   eth_defi.lagoon.analysis
   eth_defi.lagoon.beacon_proxy
   eth_defi.lagoon.lagoon_compatibility
   eth_defi.lagoon.testing

.. toctree::
   :maxdepth: 1

   lighter
