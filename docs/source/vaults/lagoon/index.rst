Lagoon Finance API
------------------

`Lagoon Finance <https://lagoon.finance/>`__ integration.

Lagoon provides open, general-purpose, secure vault infrastructure to build and scale onchain
yield products. The platform is designed for asset managers, DAOs, DeFi protocols and market
makers who need flexible vault infrastructure.

Powered by the ERC-7540 standard (asynchronous vaults), Lagoon lets vault operators settle deposit
and redemption requests asynchronously. Whether an investor can request entry or redemption
depends on that vault's access policy; some products use allowlists or KYC/KYB checks. The
infrastructure is built on top of Safe and uses Zodiac modules.

Key features:

- ERC-7540 asynchronous vault standard for managed deposits and withdrawals
- Built on Safe with Zodiac modules for institutional-grade security
- Vault access controls with optional KYC/KYB integration
- CoW Protocol integration for trade execution

Curator and strategy metadata
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Lagoon supplies vault infrastructure for independently managed products, so the protocol name
does not identify a vault's curator or investment mandate. The scanner uses Lagoon's offchain
curator metadata when it is available. For products whose public Lagoon metadata omits the
curator, attribution is maintained only for reviewed contract addresses with supporting sources.
For example, Kamui's `launch announcement <https://www.linkedin.com/posts/kamui-finance_kamui-finance-brings-three-institutional-activity-7508163602609209344-rxYM>`__
identifies Stable, Balanced and Boosted as its first three permissioned Ethereum vaults.

Strategy categories are likewise maintained per vault contract rather than inferred from the
Lagoon protocol. An unclassified Lagoon vault therefore has no strategy tags instead of receiving
a protocol-wide default that could misrepresent its mandate. Each maintained classification records
the exact-address primary sources and decision material in the Lagoon strategy table. Known cases
where unrelated deployments reuse a contract address on different chains are resolved by chain ID
and address together, preventing one vault's mandate from leaking to the other deployment.

Private vault listing
~~~~~~~~~~~~~~~~~~~~~

Lagoon's paginated catalogue omits private deployments whose ``isVisible`` field is false, even
though their exact-address detail endpoint remains available. The scanner includes economically
material private vaults only after a manual plausibility review and records each approved chain and
contract address in an explicit allowlist. Allowlisting removes the dynamic ``unofficial`` flag and
fetches the private vault's available Lagoon metadata without inventing a curator or strategy when
the public record does not disclose one.

Hidden deployments are not admitted automatically. Unreviewed contracts continue to receive the
``unofficial`` flag, and implausible balances, share prices, token metadata or stale deployments are
grounds for exclusion. This keeps the public listing open to credible permissioned products without
treating every discoverable Lagoon contract as endorsed.

Private catalogue visibility is independent of investor admission. A vault can be hidden from
Lagoon's paginated catalogue while its deployed access policy remains permissionless, or it can
require an explicit account whitelist. The scanner derives ``deposit_permission`` from the current
contract policy and does not infer it from ``isVisible`` or from the listing allowlist.

Withdrawal timing metadata
~~~~~~~~~~~~~~~~~~~~~~~~~~

The scanner reads each vault's ``averageSettlement`` field from Lagoon's app
metadata and exports it as ``estimated_settlement`` (in seconds) for
backtesting. It measures an observed or curator-provided settlement cadence,
not the time at which a redemption becomes claimable. Curators can settle
earlier, later, or not at all; Lagoon's ERC-7540 contracts do not make this a
binding deadline. Accordingly, the scanner exports ``withdrawal_delay_type``
as ``delay`` but leaves ``min_withdrawal_period`` and
``max_withdrawal_period`` as ``null``. See :doc:`../withdrawal-period-audit`
for the public export contract.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/vaults/protocols/lagoon-finance>`__
- `Homepage <https://lagoon.finance/>`__
- `App <https://app.lagoon.finance/>`__
- `Documentation <https://docs.lagoon.finance/>`__
- `GitHub <https://github.com/hopperlabsxyz>`__
- `Twitter <https://x.com/lagoon_finance>`__
- `DefiLlama <https://defillama.com/protocol/lagoon>`__

.. autosummary::
   :toctree: _autosummary_lagoon
   :recursive:

   eth_defi.erc_4626.vault_protocol.lagoon.vault
   eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem
   eth_defi.erc_4626.vault_protocol.lagoon.config
   eth_defi.erc_4626.vault_protocol.lagoon.deployment
   eth_defi.erc_4626.vault_protocol.lagoon.analysis
   eth_defi.erc_4626.vault_protocol.lagoon.beacon_proxy
   eth_defi.erc_4626.vault_protocol.lagoon.cowswap
   eth_defi.erc_4626.vault_protocol.lagoon.lagoon_compatibility
   eth_defi.erc_4626.vault_protocol.lagoon.offchain_metadata
   eth_defi.erc_4626.vault_protocol.lagoon.tags
   eth_defi.erc_4626.vault_protocol.lagoon.testing
   eth_defi.erc_4626.vault_protocol.lagoon.velora
