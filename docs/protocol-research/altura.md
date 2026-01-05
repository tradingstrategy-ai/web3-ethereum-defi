# Altura protocol research

## Overview

- **Chain**: HyperEVM (Hyperliquid)
- **Address**: `0xd0ee0cf300dfb598270cd7f4d0c6e0d8f6e13f29`
- **Explorer link**: https://hyperevmscan.io/address/0xd0ee0cf300dfb598270cd7f4d0c6e0d8f6e13f29
- **Protocol name**: Altura
- **Protocol slug**: `altura`
- **Contract name**: NavVault

## Links

- **Web page**: https://altura.trade
- **App**: https://app.altura.trade
- **Github repository**: https://github.com/AlturaTrade
  - Contracts: https://github.com/AlturaTrade/contracts
  - Documentation: https://github.com/AlturaTrade/docs
- **Documentation link**: https://docs.altura.trade
- **DefiLlama link**: Not yet listed
- **Twitter**: https://twitter.com/alturax
- **Discord**: https://discord.com/invite/EpE9pUtMBp
- **Telegram**: https://t.me/alturax

## Audit documents

Audited by **Adevarlabs** (December 2025):
- Predeposit audit: https://github.com/AlturaTrade/docs/blob/V2/PredepositAudit.pdf
- Vault audit: https://github.com/AlturaTrade/docs/blob/V2/VaultAudit.pdf

## Fee information

- **Instant withdrawal fee**: 0.01% (1 basis point) - applies when withdrawal amount ≤ vault's liquid balance
- **Epoch withdrawal fee**: 0% - applies when withdrawal request exceeds available liquidity
- **Management/performance fees**: Not explicitly documented; yield accrues via Price-Per-Share (PPS) mechanism

From smart contract (NavVault.sol): Exit fee is configurable, currently set to 1 basis point (0.01%).

## Smart contract details

The vault uses an ERC-4626 compliant implementation with:
- Oracle-backed NAV (Net Asset Value) pricing via INavOracle interface
- Withdrawal queue system with epoch-based claiming
- Role-based access control (Admin, Operator, Guardian roles)
- Pausable functionality
- Referral system for deposits
- 6-hour minimum waiting period after deposit before withdrawal can be claimed
- Reentrancy protection

### Contract files

- `NavVault.sol` - Main vault contract (ERC-4626)
- `NavOracle.sol` - Oracle for NAV pricing
- `PreDeposit.sol` - Pre-deposit contract for initial capital raising

## Yield strategy

The protocol allocates capital across three primary sources:
- Arbitrage & Funding (50% allocation)
- Staking & Restaking (30% allocation)
- Structured Liquidity Provision (20% allocation)

Target base APY: 20%

## Notes

- Altura is a multi-strategy yield protocol built on HyperEVM that provides institutional-grade trading strategies
- Users deposit USDT0 (USDT on HyperEVM) into the vault and receive AVLT (Altura Vault Token) shares
- Yield is automatically compounded by increasing the price per share
- Secured $4 million in seed funding (December 2025) led by Ascension
- The smart contracts are developed in-house by Altura team
- Underlying asset: USDT0 (USDT equivalent on HyperEVM)
- Deployment date: Approximately 16 days ago (late December 2025)
