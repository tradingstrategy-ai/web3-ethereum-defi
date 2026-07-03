Upshift protocol API
--------------------

Upshift democratises institutional-grade DeFi yield strategies through non-custodial vaults
built on August infrastructure. The platform handles three main product offerings:

**Upshift Lend**: An on-chain lending platform providing overcollateralised positions
with institutional borrowers, emphasising KYC/KYB verification and real yield opportunities.

**Upshift DeFi Yield**: Access to yield strategies curated and executed by some of the
best-performing DeFi funds, offering institutional-level risk management previously
unavailable to retail participants.

**Vault-as-a-Service (VaaS)**: A streamlined solution for ecosystems and protocols
looking to offer tailored vault opportunities, utilising the ERC-4626 standard alongside
August smart contracts.

The vaults operate as non-custodial structures, meaning users maintain control over their assets.
The implementation leverages the ERC-4626 Vault Standard, a widely-adopted Ethereum standard
for tokenised vaults.

Trading Strategy supports both observed Upshift vault families:

* TokenizedAccount ERC-4626 vaults where the vault contract itself exposes share-token metadata,
  ``totalAssets()`` and ``convertToAssets()``.
* Upshift ``multiAssetVault`` proxies where vault accounting is exposed through
  ``getSharePrice()`` and ``getTotalAssets()``, while ``lpTokenAddress()`` points to the ERC-20
  share token.

The multi-asset implementation is documented through the verified
`shared implementation contract <https://etherscan.io/address/0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2#code>`__.
Examples include RockawayX's
`Tori Ecosystem Vault <https://etherscan.io/address/0xcd69123b3FBBfC666E1f6a501da27B564C00De54>`__
and `Earn ctUSD <https://etherscan.io/address/0xc87DBBB8C67e4F19fCD2E297c05937567b2572Ce>`__.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/upshift>`__
- `Homepage <https://www.upshift.finance/>`__
- `App <https://app.upshift.finance/>`__
- `Documentation <https://docs.upshift.finance/>`__
- `Twitter <https://x.com/upshift_fi>`__
- `DefiLlama <https://defillama.com/protocol/upshift>`__

.. autosummary::
   :toctree: _autosummary_upshift
   :recursive:

   eth_defi.erc_4626.vault_protocol.upshift.vault
