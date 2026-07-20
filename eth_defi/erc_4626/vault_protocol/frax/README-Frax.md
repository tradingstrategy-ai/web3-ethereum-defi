# Frax vault families

- **Chains:** Ethereum and Arbitrum for the reviewed Fraxlend pairs; Ethereum for the reviewed staking vaults.
- **Fraxlend example:** `0x0601b72bef2b3f09e9f48b7d60a8d7d2d3800c6e` ([Etherscan](https://etherscan.io/address/0x0601b72bef2b3f09e9f48b7d60a8d7d2d3800c6e#code)).
- **Staking vaults:** `0x03cb4438d015b9646d666316b617a694410c216d` ([Etherscan](https://etherscan.io/address/0x03cb4438d015b9646d666316b617a694410c216d#code)), `0xa663b02cf0a4b149d2ad41910cb81e23e1c41c32` ([Etherscan](https://etherscan.io/address/0xa663b02cf0a4b149d2ad41910cb81e23e1c41c32#code)) and `0xcf62f905562626cfcdd2261162a51fd02fc9c5b6` ([Etherscan](https://etherscan.io/address/0xcf62f905562626cfcdd2261162a51fd02fc9c5b6#code)).
- **Protocol:** Frax.
- **Homepage:** https://frax.com/
- **Application:** https://frax.com/earn
- **GitHub:** https://github.com/FraxFinance/fraxlend
- **Documentation:** https://docs.frax.com/
- **DefiLlama:** https://defillama.com/protocol/frax-finance
- **Audits and bounty:** https://docs.frax.finance/smart-contracts/bug-bounty
- **Twitter:** https://x.com/fraxfinance
- **Fees:** Each Fraxlend pair exposes a timelock-controlled protocol share of lender interest revenue through `currentRateInfo()`. The adapter reads it on-chain because the value varies by pair. Fraxlend internalises the fee by minting shares to the protocol, diluting lenders as interest accrues. The reviewed sFRAX and sfrxUSD vaults expose no explicit management or performance fee; Frax documents sfrxUSD as having no staking or unstaking fees.

## Classification evidence

Fraxlend deployers emit
[`LogDeploy`](https://github.com/FraxFinance/fraxlend/blob/main/src/contracts/FraxlendPairDeployer.sol)
with the new pair as the first indexed argument. Historical deployers used both
`LogDeploy(address,address,address,string)` and the current seven-argument
signature. A HyperSync scan covering both signatures on 2026-07-20 found 59
pair deployments across four reviewed Ethereum deployers and four pair
deployments from the reviewed Arbitrum deployer. The deployer seeds every new
pair through `deposit()`, so generic vault event discovery also receives a
standard `Deposit` event without needing a Fraxlend-only lead source.

All 63 event-derived Fraxlend pairs, including every pair in the reviewed
public vault listing, successfully answered the immutable
[`DEPLOYER_ADDRESS()`](https://github.com/FraxFinance/fraxlend/blob/main/src/contracts/FraxlendPairAccessControl.sol)
accessor. The return values covered four historical Ethereum deployers and one
Arbitrum deployer. Public GitHub code search found the exact accessor mainly in
Frax interfaces and integrations. Runtime classification also checks the
returned address against those five event-derived Frax deployers, excluding
third-party Fraxlend forks while retaining one ABI call per candidate vault.

The two sFRAX deployments are verified as `StakedFrax`, a linear-reward vault.
The sfrxUSD proxy resolves to the verified `SfrxUSD` implementation. These
implementations are not uniquely Frax-identifiable and must therefore be
routed through the reviewed address list. Frax documents
[sFRAX](https://docs.frax.finance/frax-v3-100-cr-and-more/sfrax) as continuously
redeemable and [sfrxUSD](https://docs.frax.com/frxusd/stake-and-unstake-overview)
as having no lock-up or staking/unstaking fees.

## Adapter design

Both product families report the protocol name `Frax`, but use different
concrete readers:

- `FraxlendPairVault` reads the pair-specific internalised Fraxlend interest fee on-chain and links to the pair page.
- `FraxStakingVault` reports the reviewed staking contracts as feeless and links to Frax Earn.

`FraxVault` remains the shared protocol-level base class so callers can group
both product families without erasing their different fee behaviour.
