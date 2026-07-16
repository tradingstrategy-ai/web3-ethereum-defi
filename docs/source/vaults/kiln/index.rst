Kiln
====

`Kiln <https://www.kiln.fi/>`__ provides on-chain yield infrastructure for institutions, wallets,
custodians and other partners. Its DeFi product is named `OmniVaults
<https://docs.kiln.fi/v1/kiln-products/omnivaults>`__; this is the current name for the contracts
previously described as Kiln DeFi or Kiln Metavault in historical vault data.

OmniVaults deploy a dedicated vault for each selected protocol, asset and chain combination.
Kiln provides the contract infrastructure, while the partner controls the vault-specific
administrative roles, including end-user fee configuration and fee collection. The selected
underlying protocol supplies the yield strategy.

This integration detects OmniVault contracts from their
``additionalRewardsStrategy()`` getter. The Kiln vault adapter reads the current fixed deposit fee
in asset units and the reward fee as a percentage of generated rewards. Kiln collects reward fees
by minting vault shares, while a configured deposit fee is deducted from the deposited asset amount.

Deposits and redemptions use synchronous ERC-4626 ``deposit``, ``withdraw`` and ``redeem`` calls;
Kiln imposes no contract-level request queue, cooldown or withdrawal delay. A redemption may still
be unavailable when the selected underlying protocol lacks available liquidity or the vault is paused.

Links
-----

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/kiln>`__
- `Kiln DeFi <https://www.kiln.fi/defi>`__
- `OmniVault documentation <https://docs.kiln.fi/v1/kiln-products/omnivaults>`__
- `Contract source and deployments <https://docs.kiln.fi/v1/kiln-products/omnivaults/security/source-code>`__
- `Audits and bug bounty <https://docs.kiln.fi/v1/kiln-products/omnivaults/security/audits-and-bug-bounty>`__
- `X <https://x.com/Kiln_finance>`__
