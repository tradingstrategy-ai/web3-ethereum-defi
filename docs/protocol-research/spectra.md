# Spectra Finance

## Summary

This is an ERC-4626 wrapper contract deployed by Spectra Finance for wrapping WUSDN (Wrapped Ultimate Synthetic Delta Neutral) tokens.

**Important note:** This specific vault (`0x06a491e3efee37eb191d0434f54be6e42509f9d3`) is a **Spectra ERC4626 Wrapper** contract, not a native Spectra yield tokenisation vault. It wraps WUSDN from the SMARDEX protocol to make it compatible with Spectra's yield tokenisation system.

## Contract details

- **Chain:** Ethereum Mainnet
- **Address:** `0x06a491e3efee37eb191d0434f54be6e42509f9d3`
- **Explorer link:** https://etherscan.io/address/0x06a491e3efee37eb191d0434f54be6e42509f9d3
- **Contract name:** `SpectraWrappedWusdn`
- **Token symbol:** `sw-WUSDN`
- **Token name:** Spectra ERC4626 Wrapper: Wrapped Ultimate Synthetic Delta Neutral
- **Underlying vault:** WUSDN (`0x99999999999999Cc837C997B882957daFdCb1Af9`)
- **Underlying protocol:** SMARDEX

## Protocol information

- **Protocol name:** Spectra Finance (formerly APWine Finance)
- **Protocol type:** Interest rate derivatives / Yield tokenisation
- **Web page:** https://www.spectra.finance/
- **App:** https://app.spectra.finance
- **Documentation:** https://docs.spectra.finance/
- **Developer documentation:** https://dev.spectra.finance/
- **Governance:** https://gov.spectra.finance
- **Twitter:** https://x.com/spectra_finance
- **GitHub organisation:** https://github.com/perspectivefi
- **Core contracts repository:** https://github.com/perspectivefi/spectra-core
- **Token list repository:** https://github.com/perspectivefi/token-list
- **DefiLlama:**
  - Main: https://defillama.com/protocol/spectra
  - V2: https://defillama.com/protocol/spectra-v2
  - V1: https://defillama.com/protocol/spectra-v1

## Protocol description

Spectra is an open-source interest rate derivatives protocol that enables:

- **Yield tokenisation:** Splits ERC-4626 compliant interest-bearing tokens into Principal Tokens (PT) and Yield Tokens (YT)
- **Fixed rate yields:** Users can lock in fixed rates on yield strategies
- **Yield trading:** Trade and arbitrage interest rates across DeFi
- **Permissionless pools:** Anyone can create pools for any ERC-4626 compatible token

The protocol was formerly known as APWine Finance and rebranded to Spectra Finance in mid-2023.

## Audit reports

All security reviews are public and all reported issues have been addressed:

1. **Code4rena Audit (March-April 2024)**
   - 42 security auditors participated
   - Findings: 0 high-risk, 2 medium-risk, 11 low-risk/non-critical
   - Scope: https://github.com/code-423n4/2024-02-spectra
   - Report: https://code4rena.com/reports/2024-02-spectra

2. **Pashov Audit Group (March 2024)**
   - Findings: 0 high-risk, 3 medium-risk, 15 low-risk/non-critical
   - Report: https://github.com/pashov/audits/blob/master/team/pdf/Spectra-security-review.pdf

3. **Sherlock Audit (September 2025)**
   - Scope: MetaVaults V1
   - Findings: 0 high-risk, 2 medium-risk, 9 low/info issues (all resolved)

## Fee structure

### Pool swap fees

Spectra collects swap fees from all IBT/PT pool transactions:

- 60% to veSPECTRA voters
- 20% to liquidity providers (LPs)
- 20% to Curve DAO

Fees for veSPECTRA voters and Curve DAO are converted to ETH for distribution.

### Yield fees

- **3% fee on all accrued yield** from Yield Tokens (YT)
- Points earned through integrated protocols are treated as yield for fee purposes
- Collected fees go to the Spectra DAO treasury: `0xe59d75C87ED608E4f5F22c9f9AFFb7b6fd02cc7C`

### Tokenisation fees

Spectra chose not to implement a custom AMM, instead relying on existing solutions (starting with Curve) for efficiency and composability.

## Underlying asset: WUSDN / USDN

This Spectra wrapper holds WUSDN, which is a wrapped version of USDN from the SmarDex protocol.

### WUSDN token details

- **Token name:** Wrapped Ultimate Synthetic Delta Neutral
- **Symbol:** WUSDN
- **Address:** `0x99999999999999Cc837C997B882957daFdCb1Af9`
- **Explorer:** https://etherscan.io/token/0x99999999999999Cc837C997B882957daFdCb1Af9
- **Deployer:** SmarDex
- **Underlying:** USDN (rebasing token)

### USDN protocol overview

USDN is a decentralised synthetic US dollar developed by SmarDex. It uses a delta-neutral strategy to maintain its ~$1 peg while generating yield.

**How it works:**

The protocol has two sides that balance each other:

1. **Vault side (USDN holders):** Users deposit wstETH and receive USDN tokens. They gain dollar-denominated exposure with potential yields.

2. **Long side (leveraged traders):** Traders open leveraged long positions on the underlying asset (ETH). They deposit collateral and choose leverage multiples (e.g., 5x leverage).

**Delta-neutral mechanism:**

- The vault's assets are long the underlying (wstETH)
- Long traders' positions create a natural counterbalance
- This offsetting of exposures reduces the protocol's net directional risk
- The protocol maintains balance without relying on centralised exchanges

**Yield generation:**

- Yields come from **funding rates** - payments between vault holders and leveraged traders
- When long exposure exceeds vault balance (bullish market), longs pay funding to the vault
- Rates are priced dynamically based on protocol imbalance
- Additional yield comes from wstETH staking rewards

**Rebase mechanism (USDN):**

- USDN is a rebasing token that increases user balances when price exceeds $1
- When USDN reaches ~$1.005, a rebase is triggered
- Token supply increases proportionally to bring price back to ~$1
- Holders receive more tokens directly in their wallets

**WUSDN vs USDN:**

- USDN: Rebasing token (balance increases over time)
- WUSDN: Non-rebasing wrapper (value increases instead of balance)
- WUSDN represents a share of the total USDN supply
- Similar concept to wstETH vs stETH

### SmarDex protocol information

- **Protocol name:** SmarDex
- **Web page:** https://smardex.io/
- **USDN app:** https://smardex.io/usdn
- **Documentation:** https://docs.smardex.io/ultimate-synthetic-delta-neutral
- **Twitter:** https://x.com/SmarDex

### USDN audit reports

1. **Guardian Audits (December 2024)**
   - Report: https://github.com/GuardianAudits/Audits/blob/main/Smardex/12-18-2024_Smardex_USDN.pdf
   - 34 medium-severity findings identified

2. **Bailsec Audits**
   - USDN Protocol: https://github.com/bailsec/BailSec/blob/main/Bailsec%20-%20Smardex%20USDN%20-%20Final%20Report.pdf
   - SmarDex Ecosystem: https://github.com/bailsec/BailSec/blob/main/Bailsec%20-%20Smardex%20Ecosystem%20-%20Final%20Report.pdf
   - Router: https://github.com/bailsec/BailSec/blob/main/Bailsec%20-%20Smardex%20-%20Router%20-%20Final%20Report.pdf

3. **Bug bounty programme**
   - Active on Immunefi: https://immunefi.com/bug-bounty/smardex/
   - Critical vulnerabilities: up to $500,000

## Notes

- The specific vault contract `0x06a491e3efee37eb191d0434f54be6e42509f9d3` is an ERC-4626 wrapper that makes WUSDN (from SmarDex) compatible with Spectra's yield tokenisation system
- The underlying WUSDN token is at `0x99999999999999Cc837C997B882957daFdCb1Af9`
- Spectra operates on multiple chains including Ethereum, Arbitrum, Base, Optimism, and others
- The protocol implements EIP-5095 for Principal Tokens and EIP-2612 for permit functionality
- USDN is backed by wstETH, providing additional staking yield on top of funding rate income
