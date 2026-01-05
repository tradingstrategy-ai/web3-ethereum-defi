# Maple Finance protocol research

## Summary

- **Chain**: Ethereum (also Solana, Base)
- **Protocol name**: Maple Finance
- **Web page**: https://maple.finance/
- **App**: https://app.maple.finance/
- **GitHub repository**: https://github.com/maple-labs
- **Protocol registry**: https://github.com/maple-labs/protocol-registry
- **Documentation link**: https://docs.maple.finance/
- **DefiLlama link**: https://defillama.com/protocol/maple-finance
- **DefiLlama adapter**: https://github.com/DefiLlama/DefiLlama-Adapters/blob/main/projects/maple/index.js
- **Twitter**: https://twitter.com/maplefinance

## Supported vaults

### Syrup vaults

Permissionless yield-bearing tokens for institutional lending:

- **syrupUSDC**: `0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b` ([Etherscan](https://etherscan.io/address/0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b))
- **syrupUSDT**: `0x356b8d89c1e1239cbbb9de4815c39a1474d5ba7d` ([Etherscan](https://etherscan.io/address/0x356b8d89c1e1239cbbb9de4815c39a1474d5ba7d))

### AQRU Pool (Real-World Receivables)

- **Address**: `0xe9d33286f0E37f517B1204aA6dA085564414996d`
- **Explorer link**: https://etherscan.io/address/0xe9d33286f0E37f517B1204aA6dA085564414996d
- **Web page**: https://aqru.io/real-world-receivables/

The AQRU Pool on Maple refers to the AQRU Receivables Pool (also known as the Real-World
Receivables account), a liquidity pool on the Maple Finance DeFi platform. It bridged
decentralised finance with traditional assets by providing financing for IRS tax credit
receivables owed to US businesses by the government.

AQRU plc served as the pool delegate, managing the loan book and overseeing borrower
applications, while partnering with Intero Capital Solutions for sourcing, due diligence,
and execution of transactions. Intero focused on vetted originators in sectors like
renewable energy and R&D, with the pool advancing USDC against pledged tax credits that
were typically settled by the IRS within 3-5 months (average duration around 6 months for capital).

Key features:

- **Yield and returns**: Lenders deposited USDC to earn competitive yields, starting at
  around 10% APY net of fees, later increased to 14.2% net (16.2% gross) in mid-2023
- **Loan structure**: Quasi-government backed, with low default risk due to IRS obligations
- **Accessibility**: Initially required $250,000 minimum (later reduced to $50,000)
- **Lock-up period**: 45-day lock-up period, updated to weekly liquidity after lock-up
- **Performance**: Purchased 382 receivables worth $18M; over 85% repaid within 120 days

The pool was launched in January 2023 as part of Maple's comeback strategy post-2022 defaults.
The underlying US Treasury tax credits programme was set to run until the end of Q3 2025.

## Audit documents

Maple Finance has undergone extensive security audits:

### December 2022 release (3 audits)
- Trail of Bits
- Spearbit
- Three Sigma

### June 2023 release (2 audits)
- Spearbit Auditors via Cantina
- Three Sigma

### December 2023 release (2 audits)
- Three Sigma
- 0xMacro

### August 2024 release (3 audits)
- Three Sigma
- 0xMacro
- Three Sigma (Router)

### Other audit resources
- Dedaub audit report: https://dedaub.com/audits/maple/maple-finance-core-mar-12-2021/
- 0xMacro audit: https://0xmacro.com/library/audits/maple-1
- Security documentation: https://docs.maple.finance/technical-resources/security/security
- Bug bounty program on Immunefi

## Fee information

Maple Finance uses a multi-tier fee structure:

### Pool management fees
- Cash management pools: 0.50% annualised management fee
- No upfront subscription fees, redemption fees, or hidden fees

### Fee distribution
- Pool delegates earn establishment fees (paid by borrowers) and ongoing fees (% of interest yield)
- During loans, borrowers pay interest where 10% goes to the Pool Delegate and the rest to Pool Cover providers and lenders
- Establishment fee: 33% to pool delegate, 67% to Maple DAO

### Protocol revenue
- At current loan rates (~8-9%) and $450M+ loan book, equates to ~150bps net interest margin (~$7M run-rate revenue)
- BTC Yield product: 0.4% management fee + 20% of staking yield from CORE rewards

## Notes

- Maple Finance is a DeFi institutional lending protocol founded in 2019
- Co-founded by Sid Powell (CEO) and Joe Flanagan
- Launched May 2021
- Provides undercollateralised loans to institutional borrowers through on-chain liquidity pools
- Uses "Pool Delegates" as specialised underwriters who vet borrowers and approve loans
- Implements ERC-4626 standard for tokenised vault shares
- Has processed over $12B in loans with 99% repayment rate
- SYRUP token launched May 2024 for permissionless yield access
- Uses Chainlink oracles for price feeds
- Critical monitoring via Tenderly Web3 Actions checking protocol invariants every block

## Smart contract architecture

The Pool contracts are part of Maple's modular architecture:

- **Pool**: ERC-4626 vault managing pooled liquidity
- **PoolManager**: Manages pool operations and configurations
- **FixedTermLoanManager**: Manages fixed-term loans
- **OpenTermLoanManager**: Manages open-term loans
- **WithdrawalManager**: Handles withdrawal requests (cyclical or queue-based)
- **PoolDelegateCover**: First-loss capital from pool delegate

Source code is verified on Etherscan and compiled with Solidity v0.8.7.
