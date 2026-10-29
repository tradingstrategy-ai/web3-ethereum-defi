Tori Finance
------------

`Tori Finance <https://tori.finance/>`__ issues trUSD, a dollar-tracking token,
and strUSD, the receipt for staking it. The native Ethereum staking vault is
operated by Tori. Its `strategy documentation <https://docs.tori.finance/strategy/overview>`__
describes short-duration money-market positions, spot/futures arbitrage and
calendar spreads. Returns are variable, and backing includes positions held
with external custodians.

The `staking terms <https://docs.tori.finance/products/strusd>`__ describe
immediate deposits and, as at 29 October 2026, a seven-day cooldown before a
holder can claim trUSD. Staking and unstaking have no separate charge, but Tori
retains an unspecified share of trading income before distributing rewards.
Creating or redeeming trUSD through the issuer is a separate service with its
own eligibility rules and fees.

The `RockawayX-curated ecosystem vault <https://docs.tori.finance/resources/pre-deposit>`__
uses Upshift infrastructure and issues etrUSD. It is a separate product from
native strUSD staking.

Integration
~~~~~~~~~~~

The Ethereum deployment is classified by chain and address using
``ERC4626Feature.tori_like`` and read through
:py:class:`eth_defi.erc_4626.vault_protocol.tori.vault.ToriVault`.
The protocol and protocol-operated curator use slug ``tori``. Social posts
resolve to the existing ``trusd`` feeder. Other chains' bridged strUSD tokens
are ERC-20 representations and are not covered as native staking vaults.

The reader uses standard ERC-4626 accounting and reads ``cooldownDuration()``
for withdrawal timing. A zero duration is reported as instant; RPC failures
propagate. No public transaction-manager capability is advertised because the
cooldown and claim lifecycle is not implemented by this integration.

Management, deposit and withdrawal fees are zero. The retained income share
is unknown, but fees are internalised: share-price returns already reflect
Tori's income retention. The technical risk assessment is High, reflecting
upgradeable contracts, privileged loss reporting and dependencies on issuer
valuation and reporting. Public matched source and audits do not remove those
dependencies. This is separate from the risk of trading losses or a depeg.
The maintained strategy tags are ``multistrategy``, ``delta_neutral``,
``arbitrage`` and ``carry_trade``.

Contract and security references
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The native proxy is
`0x280839980a7eD0D7717F64125fE241012E5F5815 <https://etherscan.io/address/0x280839980a7eD0D7717F64125fE241012E5F5815#code>`__,
with trUSD denomination token ``0xd0580192E98eA6CEB9c7b6191Ed2E27560911697``.
The `official contract inventory <https://docs.tori.finance/resources/contracts>`__
lists these separately from bridged tokens. The
`published Solidity source <https://github.com/Tori-Finance/tori-contracts>`__
documents rewards vesting, operator controls, loss reporting and cooldowns.
See the `audit inventory <https://docs.tori.finance/security/audits>`__,
`privileged roles <https://docs.tori.finance/security/roles>`__ and
`risk disclosures <https://docs.tori.finance/resources/risks>`__ for their
respective scopes and limitations.

See :doc:`the API reference </api/tori/index>` and
:doc:`deployment instructions <deployment>` for the targeted metadata repair.
See :doc:`risk and fees across Tori exposures <risk-and-fees>` for the reviewed
third-party products and their separate charges.

.. toctree::
   :hidden:

   deployment
   risk-and-fees
