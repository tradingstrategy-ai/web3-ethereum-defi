Wildcat
=======

`Wildcat <https://wildcat.finance/>`__ is a protocol for mostly
undercollateralised lending markets. Each market lends one asset to one
KYB-verified borrower, such as a market maker or trading firm, which sets
the interest rate, reserve ratio, withdrawal batch duration and lender access
rules. The borrower can change the rate within the protocol's limits. See the `protocol documentation <https://docs.wildcat.finance/>`__,
the `version 2 contracts <https://github.com/wildcat-finance/v2-protocol>`__,
`audits <https://docs.wildcat.finance/security-measures/code-security-reviews>`__,
`fees <https://docs.wildcat.finance/using-wildcat/protocol-usage-fees>`__,
the `DefiLlama page <https://defillama.com/protocol/wildcat-protocol>`__ and
`Twitter <https://x.com/wildcatfi>`__.

Wildcat markets are not ERC-4626 vaults. The market contract is the lender's
rebasing ERC-20 token, and the read-only adapter uses its ``scaleFactor`` as
the share price. Version 2 markets on Ethereum are discovered from the
``WildcatArchController`` registry. Deposit and redemption support is not
implemented: withdrawals are queued into batches and most markets admit only
approved lenders.

See ``eth_defi/erc_4626/vault_protocol/wildcat/README-Wildcat.md`` for the
value mapping, delinquency handling and onboarding.

.. autosummary::
   :toctree: _autosummary_wildcat
   :recursive:

   eth_defi.erc_4626.vault_protocol.wildcat.vault
   eth_defi.erc_4626.vault_protocol.wildcat.state
   eth_defi.erc_4626.vault_protocol.wildcat.historical
   eth_defi.erc_4626.vault_protocol.wildcat.discovery
   eth_defi.erc_4626.vault_protocol.wildcat.offchain_metadata
