Yuzu Money API
--------------

`Yuzu Money <https://yuzu.money/>`__ integration.

Yuzu Money is a DeFi protocol that packages high-yield strategies into an overcollateralised
stablecoin (yzUSD). The protocol is deployed on the Plasma chain and offers multiple products
for users seeking yield on their stablecoin holdings.

The protocol features a multi-token architecture:

- **yzUSD**: Overcollateralised stablecoin targeting $1 USD, backed 1:1 by USDC
- **syzUSD**: Yield-bearing token received when staking yzUSD (ERC-4626 vault)
- **yzPP**: Junior tranche / insurance liquidity pool providing first-loss capital

Key features:

- Institutional-grade risk mitigation with risk tranching
- Nexus Mutual smart contract insurance coverage
- Hypernative threat monitoring
- Real-time Proof of Reserves dashboard
- Audited by Pashov Audit Group

Fee structure:

Yuzu Money does not charge traditional performance fees. Instead, the protocol employs a
`yield-smoothing mechanism <https://yuzu-money.gitbook.io/yuzu-money/faq-1/performance-fee>`__
where a consistent weekly yield target is distributed to users, backed by a Reserve Fund
that acts as a buffer. This approach provides more predictable returns without explicit
management or performance fees.

- `Homepage <https://yuzu.money/>`__
- `App <https://app.yuzu.money/>`__
- `Documentation <https://yuzu-money.gitbook.io/yuzu-money/>`__
- `Twitter <https://x.com/YuzuMoneyX>`__
- `Audit report <https://github.com/pashov/audits/blob/master/team/pdf/YuzuUSD-security-review_2025-08-28.pdf>`__
- `DefiLlama <https://defillama.com/protocol/yuzu-money>`__
- `Contract on Plasmascan <https://plasmascan.to/address/0xebfc8c2fe73c431ef2a371aea9132110aab50dca>`__


.. autosummary::
   :toctree: _autosummary_yuzu_money
   :recursive:

   eth_defi.erc_4626.vault_protocol.yuzu_money.vault
