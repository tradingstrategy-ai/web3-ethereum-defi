# Yuzu Money

Yuzu Money is a DeFi protocol that packages high-yield strategies into an overcollateralised stablecoin (yzUSD).

## Summary

- **Chain**: Plasma (chain ID: 9745)
- **Address**: `0xebfc8c2fe73c431ef2a371aea9132110aab50dca` (yzPP - Yuzu Protection Pool)
- **Explorer link**: https://plasmascan.to/address/0xebfc8c2fe73c431ef2a371aea9132110aab50dca
- **Protocol name**: Yuzu Money
- **Web page**: https://yuzu.money/
- **App**: https://app.yuzu.money/
- **Github repository**: https://github.com/Telos-Consilium/ouroboros-contracts (private, referenced in audit)
- **Documentation link**: https://yuzu-money.gitbook.io/yuzu-money/
- **DefiLlama link**: https://defillama.com/protocol/yuzu-money
- **DefiLlama adapter**: https://github.com/DefiLlama/DefiLlama-Adapters/blob/main/projects/yuzu-money/index.js
- **TVL**: ~$40M (as of January 2026)

## Token structure

| Token | Symbol | Description | Type |
|-------|--------|-------------|------|
| Yuzu Stablecoin | yzUSD | Overcollateralised stablecoin targeting $1 USD, backed 1:1 by USDC | ERC-20 |
| Staked Yuzu USD | syzUSD | Yield-bearing token received when staking yzUSD | ERC-4626 vault |
| Yuzu Protection Pool | yzPP | Junior tranche / insurance liquidity pool token | ERC-20 (first-loss) |
| YUZU | YUZU | Governance and utility token | ERC-20 |

### Key addresses on Plasma

- **yzUSD**: `0x6695c0f8706c5ace3bdf8995073179cca47926dc`
- **yzPP**: `0xebfc8c2fe73c431ef2a371aea9132110aab50dca`

## Audit reports

1. **Pashov Audit Group** - YuzuUSD Security Review (2025-08-28)
   - PDF: https://github.com/pashov/audits/blob/master/team/pdf/YuzuUSD-security-review_2025-08-28.pdf
   - Markdown: https://github.com/pashov/audits/blob/master/team/md/YuzuUSD-security-review_2025-08-28.md
   - Auditors: unforgiven, merlinboii, IvanFitro, ni8mare
   - Scope: YuzuUSD.sol, YuzuILP.sol, StakedYuzuUSD.sol, YuzuIssuer.sol, YuzuOrderBook.sol, YuzuProto.sol
   - Total issues found: 18 (3 High, 2 Medium, 13 Low)
   - All High and Medium issues marked as resolved

## Fee information

- Redemption fees apply when redeeming yzUSD and syzUSD
- Fee can be positive (fee) or negative (incentive) depending on protocol parameters
- Specific fee percentages are configurable by protocol admin via `setRedeemOrderFee()` and `setRedeemFeePpm()`
- Performance fees mentioned in documentation but specific percentages not publicly disclosed
- From audit: fee calculation uses parts-per-million (PPM) with 1e6 = 100%

## Security infrastructure

- **Hypernative/Sentinel**: Real-time threat detection and monitoring
- **Nexus Mutual**: Smart contract insurance coverage
- **Fordefi MPC**: Multi-party computation for wallet infrastructure
- **Multisig**: 3/5 multisig with 48-hour timelock for admin actions
- **Proof of Reserves**: https://yuzu.accountable.capital/

## Smart contract developer

Smart contracts are developed in the private repository `Telos-Consilium/ouroboros-contracts`, as referenced in the Pashov Audit Group security review. The protocol is incubated by Ouroboros Capital.

## Notes

- yzUSD minting/redemption is restricted to qualified/accredited investors only (KYC/KYB required)
- Secondary market trading of yzUSD is permissionless on DEXs
- syzUSD is the yield-bearing wrapper and is ERC-4626 compliant
- yzPP (Protection Pool) acts as first-loss capital / junior tranche for the protocol
- Protocol uses transparent proxy pattern (ERC-1967) for upgradeability
- Deployed October 2025, relatively new protocol
- Integrations: Euler Finance lending markets, Pendle Finance liquidity provision

## Social links

- **Twitter/X**: https://x.com/YuzuMoneyX
- **Discord**: https://discord.gg/yuzumoney
- **Medium**: https://yuzumoney.medium.com/
