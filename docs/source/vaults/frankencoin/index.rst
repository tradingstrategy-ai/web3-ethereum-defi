Frankencoin
============

`Frankencoin <https://frankencoin.com/>`__ is an over-collateralised,
oracle-free stablecoin protocol whose ZCHF token tracks the Swiss franc. The
protocol is implemented fully onchain and makes ZCHF available across multiple
EVM networks.

The Frankencoin Savings Vaults are ERC-20 and ERC-4626 wrappers for the
Frankencoin savings module. Users deposit ZCHF and receive svZCHF shares whose
value follows savings module yield. The official `token page
<https://frankencoin.com/token/>`__ lists savings vault deployments on Ethereum,
Base and Gnosis.

Trading Strategy reports Frankencoin savings TVL from the whole savings product,
not only from the ERC-4626 wrapper account. The custom Frankencoin reader sums
ZCHF held by the underlying savings module and the svZCHF wrapper contract,
while keeping the ERC-4626 wrapper exchange rate for share-price history.

Frankencoin shares
~~~~~~~~~~~~~~~~~~

`FCS mechanics <https://docs.frankencoin.com/pool-shares/fcs>`__ describe the
Ethereum equity product at
`0xDb861830D9Ae2d1fCF99fA0cfd3973de382B0B5b
<https://etherscan.io/address/0xDb861830D9Ae2d1fCF99fA0cfd3973de382B0B5b>`__.
The :class:`~eth_defi.erc_4626.vault_protocol.frankencoin.shares.FrankencoinSharesVault`
adapter is classified by this chain and address and labelled with the
Frankencoin protocol and protocol curator. Its strategy tags are
``protocol_equity``, ``lending``, ``rwa`` and ``rwa_lending``. The lending and
RWA tags describe underlying revenue and collateral exposure through equity;
FCS itself holds FPS, while collateral remains in borrowers' positions.

FCS wraps underlying FPS shares one to one. Its ERC-4626 asset is ZCHF.
``totalAssets()`` measures proportional backing equity; ``convertToAssets()``
uses a marginal bonding-curve valuation three times backing per share.
The inherited historical reader preserves both values independently. Price
appreciation reflects capital flows as well as income and losses, and is not a
fixed savings yield or a secondary-market execution price.

ZCHF redemption requires FCS to control more than two thirds of underlying FPS
votes and the wrapper itself to satisfy FPS's average holding-period gate.
There is no fixed personal 90-day FCS wait. A preview can succeed while actual
redemption is disabled. Secondary-market sales and FPS unwrapping have different
conditions. The adapter does not advertise a certified public transaction
manager; conditional exits and minimum-output protection need a complete
guarded lifecycle test before certification.

Underlying revenue
~~~~~~~~~~~~~~~~~~

The protocol earns interest fees on collateralised ZCHF issuance. In the
reviewed newer `position terms <https://docs.frankencoin.com/positions/open>`__,
the annual rate is the global borrowing rate plus a position-specific risk
premium. Interest for the remaining term is charged up front when minting,
and early repayment does not refund it. Rates and accounting timing differ
between contract versions; outstanding debt times a displayed annual rate is
not a measurement of cash revenue collected during a period.

Other equity inflows include position-proposal fees and the portion of minting
module application fees forwarded to equity. The
`reserve accounting <https://docs.frankencoin.com/reserve>`__ shows how
liquidations release assigned minter reserves and settle sale proceeds,
repayment and challenger rewards. Their net result can be a profit or loss.
Ordinary retained minter reserves remain separately attributed until released;
they are not borrowing revenue when first collected.

Equity entry and exit fees leave more backing for the remaining shares. FCS's
additional redemption discount is sent back to the underlying Equity contract.
These holder transaction effects are separate from borrower-generated income.
New share subscriptions are capital contributions and can move the bonding
curve price without representing operating revenue. Secondary-market FCS pool
trading fees accrue to the pool's liquidity providers rather than automatically
to every FCS holder.

The `savings module <https://docs.frankencoin.com/savings>`__ pays interest from
equity, reducing the net income available to FCS. Savings referrals split this
gross expense between savers and referrers; they do not create additional
interest income for FCS. Losses not absorbed by the affected position's assigned
reserve also reduce equity. A useful economic decomposition is borrowing fees
plus proposal fees and net liquidation results, less savings expense, alongside
the separate capital and holder-transaction flows.

The official `collateral catalogue
<https://app.frankencoin.com/monitoring/collateral>`__ includes crypto assets
and tokenised gold. The `position API
<https://api.frankencoin.com/positions/list>`__ was checked on 2026-09-30 and
reported outstanding debt against PAXG and XAUt. The
`PAXG issuer <https://www.paxos.com/pax-gold>`__ confirms its physical gold
backing. This supports indirect RWA lending exposure, without implying that
all protocol debt is RWA-backed or that FCS tracks the gold price.

FCS fee model
~~~~~~~~~~~~~

FCS has no recurring management or performance fee. The underlying equity curve
applies a 0.3% entry fee and a nominal 0.3% share-based redemption fee. FCS adds
a size- and activity-dependent redemption discount, so ``has_custom_fees()`` is
true. These transaction costs are externalised relative to the reference price.
The savings product's referral fee does not apply to FCS.

FCS backfill
~~~~~~~~~~~~

``scripts/erc-4626/backfill-frankencoin-shares.py`` repairs or seeds only FCS
metadata and optionally reads hourly price history from deployment block
25,852,506. Existing discovery counts and chain cursors are preserved. New leads
use targeted Hypersync Deposit, Withdraw, Wrapped and Unwrapped events; wrapping
is configuration activity, not a new ZCHF deposit. The common Parquet writer is
restricted to the FCS address and requested half-open block range. It never
receives the scheduled reader-state mapping.

Applied runs require an existing metadata database; a missing path fails before
provider access. Dry runs can prepare a new lead and default to metadata only,
retaining reviewable output copies:

.. code-block:: shell

    source .local-test.env && DRY_RUN=true \
      poetry run python scripts/erc-4626/backfill-frankencoin-shares.py

    source .local-test.env && DRY_RUN=true FRANKENCOIN_SHARES_SCAN_PRICES=true \
      poetry run python scripts/erc-4626/backfill-frankencoin-shares.py

Historical prices require the preserved dense Ethereum cache at
``~/.tradingstrategy/block-timestamp/1-timestamps.duckdb``. Restore or prepopulate
it before running the price backfill. The script verifies dense coverage of
the requested range, including its end boundary, before reading prices.
``START_BLOCK`` and ``END_BLOCK`` can
restrict a retry or verification scan; the end is exclusive. No new Parquet
columns are required. Metadata preparation, empty-output detection and staged
price writing finish before publishing applied outputs. Missing or non-finite
price, backing or supply observations abort publication; genuine zero backing
or supply before the first FCS issuance is retained.

For production, first inspect the host Compose configuration, deploy the code
and stop the persistent scanner. The one-shot maintenance command overrides
the image entrypoint and uses the mounted production state:

.. code-block:: shell

    source ~/vault-scanner/vault-rpc.env && (cd ~/vault-scanner/web3-ethereum-defi && docker compose stop vault-scanner-looped)
    source ~/vault-scanner/vault-rpc.env && (cd ~/vault-scanner/web3-ethereum-defi && docker compose run --rm --entrypoint /bin/bash vault-scanner-oneshot -lc 'DRY_RUN=true FRANKENCOIN_SHARES_SCAN_PRICES=false python scripts/erc-4626/backfill-frankencoin-shares.py')
    source ~/vault-scanner/vault-rpc.env && (cd ~/vault-scanner/web3-ethereum-defi && docker compose run --rm --entrypoint /bin/bash vault-scanner-oneshot -lc 'DRY_RUN=false FRANKENCOIN_SHARES_SCAN_PRICES=false python scripts/erc-4626/backfill-frankencoin-shares.py')
    source ~/vault-scanner/vault-rpc.env && (cd ~/vault-scanner/web3-ethereum-defi && docker compose up -d vault-scanner-looped)

Inspect the metadata dry run before applying. To include historical prices,
verify dense timestamp coverage and repeat both maintenance commands with
``FRANKENCOIN_SHARES_SCAN_PRICES=true``; optionally restrict the range with
``START_BLOCK`` and ``END_BLOCK``. Applied metadata has a sibling backup;
price writes are staged beside their destination for atomic replacement. Run the normal cleaning and export
pipeline afterwards to publish labels, strategy tags and history.

Savings fee model
~~~~~~~~~~~~~~~~~

Frankencoin Savings Vaults do not expose protocol-wide management, performance,
deposit, or withdrawal fees. Savings yield is reflected in the svZCHF share
price. The underlying savings contracts support an optional per-account referral
fee: when a user configures a referrer, up to 25% of earned interest can be
deducted and paid to that referrer.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/vaults/protocols/frankencoin>`__
- `Homepage <https://frankencoin.com/>`__
- `Token and savings vault page <https://frankencoin.com/token/>`__
- `Documentation <https://docs.frankencoin.com/>`__
- `GitHub <https://github.com/Frankencoin-ZCHF/Frankencoin>`__
- `Twitter <https://x.com/frankencoinzchf>`__
- `DeFiLlama <https://defillama.com/protocol/frankencoin>`__
- `Ethereum savings vault <https://etherscan.io/token/0xE5F130253fF137f9917C0107659A4c5262abf6b0>`__
- `Ethereum legacy savings vault <https://etherscan.io/token/0x637F00cAb9665cB07d91bfB9c6f3fa8faBFEF8BC>`__
- `Base savings vault <https://basescan.org/address/0xa09EBdf8A01b9ef04149319D64F83b9C01a5b585>`__
- `Gnosis savings vault <https://gnosisscan.io/token/0x6165946250dd04740ab1409217e95a4f38374fe9>`__

API
~~~

See :doc:`../../api/frankencoin/index`.
