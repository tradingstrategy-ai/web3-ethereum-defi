Asseto
======

`Asseto <https://asseto.finance/>`__ issues tokenised investment products. The
integration maps every EVM product returned by Asseto's current public registry
on chains supported by the shared vault pipeline. This presently includes
Ethereum, BNB Chain and Avalanche products. Asseto fund shares are ERC-20
tokens, but they are not ERC-4626 or ERC-7540 vaults: their permissioned
subscriptions and redemptions are administered separately.

The backfill discovers product addresses from the registry on every run and
combines on-chain token supply with Asseto's published NAV/share history.
Products remain present in vault metadata even when they currently have zero
supply or Asseto has not published a price history.

Historical prices
-----------------

For each sampled block, the historical reader obtains:

- the product ERC-20 ``totalSupply()`` for outstanding shares
- ``Pricer.getLatestPrice()`` for the hardcoded AoABT deployment, or the
  product's full public daily NAV history for registry-discovered products

TVL is calculated as ``totalSupply * NAV/share``. Registry ``stoken`` products
omit a collateral-token address but publish USD fund-unit values, so they are
exported with a synthetic USD accounting denomination. HKD-denominated products
are converted to USD using the shared historical currency-rate database before
they enter cleaned live-feed history. The backfill stops with an error if any
active supported-chain registry product lacks price history or positive,
USD-compatible current metadata.

Fees
----

The adapter reads the manager's live ``mintFee()`` and ``redemptionFee()``
settings as basis points. Its verified source applies each fee as
``amount * fee / BPS_DENOMINATOR``, where ``BPS_DENOMINATOR`` is 10,000.
AoABT's `underlying fund documentation
<https://asseto.gitbook.io/asseto/products/aoabt/underlying-fund>`__ also
states a 1% annual management fee, a 20% performance fee above a 6% hurdle,
and a redemption fee that falls from 1% during the first year to 0% after the
lock-up. The latter conditions are exposed through ``has_custom_fees()`` rather
than being reduced to a misleading single scalar withdrawal fee.

Limitations
-----------

The deposit manager is deliberately blocked. Asseto subscription and redemption
requests are KYC-gated and follow a bespoke request, price assignment and claim
lifecycle, so the generic vault deposit and redemption APIs cannot safely
operate them. The adapter provides read-only discovery, NAV/share and
historical metrics instead.

Off-chain product metadata
--------------------------

The :mod:`eth_defi.tokenised_fund.asseto.offchain_api` module reads descriptions,
displayed TVL/APY, product registry data and the complete available NAV history
from the public Asseto web-application API. Asseto does not document this as a
stable, versioned developer API, so production runs validate current active
products rather than silently accepting a coverage gap.

Chain coverage
--------------

The live pipeline requires both a project chain mapping and cache-aware
HyperSync timestamp support. Asseto EVM products on HashKey Chain and Pharos
are therefore reported but skipped until those chains are supported. XRPL
products are outside the EVM scope of this library.

Partner roles and curators
--------------------------

The same public application API exposes partner role labels and logo assets.
The adapter's ``fetch_roles()`` method returns ``AssetoRoleInfo`` values and
resolves an organisation only for known official logo assets. It treats an
``Investment Manager`` as the vault curator where present, otherwise an
``Investment Advisor``; generic advisor, legal, custody and administration
roles do not create a curator attribution.

.. autosummary::
   :toctree: _autosummary_asseto
   :recursive:

   eth_defi.tokenised_fund.asseto.vault
   eth_defi.tokenised_fund.asseto.historical
   eth_defi.tokenised_fund.asseto.offchain_api
   eth_defi.tokenised_fund.asseto.backfill
