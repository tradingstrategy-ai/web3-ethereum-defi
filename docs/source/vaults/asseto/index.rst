Asseto
======

`Asseto <https://asseto.finance/>`__ issues tokenised investment products. The
initial integration supports the Asseto Orient Arbitrage Token (AoABT) on
HashKey Chain. AoABT is an ERC-20 token, but it is not an ERC-4626 or ERC-7540
vault: its permissioned subscriptions and redemptions are administered through
a separate request-and-claim manager.

The adapter therefore uses :class:`eth_defi.vault.base.VaultBase` directly.
It reads the AoABT token supply and the official Asseto ``Pricer`` contract's
``getLatestPrice()`` value to calculate NAV/share and TVL.

Historical prices
-----------------

For each sampled block, the historical reader obtains:

- ``AoABT.totalSupply()`` for outstanding shares
- ``Pricer.getLatestPrice()`` for the published NAV/share, in base-18 USD

TVL is calculated as ``totalSupply * NAV/share`` in USDT. This applies only to
the supported, hardcoded AoABT deployment; it does not claim coverage of every
Asseto product or chain.

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

The :mod:`eth_defi.tokenised_fund.asseto.offchain_api` module reads descriptions, displayed
TVL/APY and product registry data from the public Asseto web-application API.
It is optional enrichment only: Asseto does not document this as a stable,
versioned developer API, and the on-chain ``Pricer`` remains the source for
valuation in this integration.

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
