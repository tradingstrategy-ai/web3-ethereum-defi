# Gearbox Protocol (Hyperithm curated vault)

## Summary

- **Chain**: Plasma
- **Address**: `0xb74760fd26400030620027dd29d19d74d514700e`
- **Explorer link**: https://plasmascan.to/address/0xb74760fd26400030620027dd29d19d74d514700e
- **Protocol name**: Gearbox Protocol (Hyperithm curated)
- **Smart contract name**: PoolV3 (version 3.10)
- **Underlying asset**: USDT0

## Protocol information

- **Protocol homepage**: https://gearbox.finance/
- **Protocol documentation**: https://docs.gearbox.finance/
- **Protocol Twitter**: https://x.com/GearboxProtocol
- **GitHub repository**: https://github.com/Gearbox-protocol/core-v3
- **DefiLlama**: https://defillama.com/protocol/gearbox

## Curator information

This vault is curated by **Hyperithm**, a premier digital asset manager based in Tokyo and Seoul.

- **Curator name**: Hyperithm
- **Curator homepage**: https://www.hyperithm.com/
- **Curator Twitter**: https://x.com/hyperithm
- **Curator DefiLlama adapter**: https://github.com/DefiLlama/DefiLlama-Adapters/blob/main/projects/hyperithm/index.js

Hyperithm is backed by Coinbase Ventures, Hashed, Samsung Next, and Kakao Investment. They specialise in algorithmic/HFT market-neutral strategies and are a fully regulated Virtual Asset Service Provider (VASP) in Japan and Korea.

## Audit reports

Gearbox Protocol has been audited by ChainSecurity:

- **V3 Core audit (March 2024)**: https://cdn.prod.website-files.com/65d35b01a4034b72499019e8/670f8a78eb3de411d2966ea7_ChainSecurity_Gearbox_Protocol_Gearbox_V3_Core_audit.pdf
- **V3.10 Core & Oracles audit**: https://cdn.prod.website-files.com/65d35b01a4034b72499019e8/680a8552b47c21da89ebe9ec_ChainSecurity_Gearbox_Core_&_Oracles_V3_10_audit.pdf
- **V3.1 Integrations audit**: https://www.chainsecurity.com/security-audit/gearbox-v3-integrations
- **All audits**: https://docs.gearbox.finance/risk-and-security/audits-bug-bounty

## Fee information

Gearbox Protocol has a modular fee structure that can vary by Credit Manager:

### Withdrawal fees
- The PoolV3 contract has a configurable `withdrawFee` parameter (in basis points)
- For passive lenders, there is typically no withdrawal fee

### Liquidation fees
- Liquidator fee: 3-4% (varies by Credit Manager)
- Protocol fee: 1-1.5% (varies by Credit Manager)

### Base APY spread
- Borrowers pay interest at rate `r(u) × (1 + spreadFee)`
- Lenders receive `r(u)`
- The DAO captures `r(u) × spreadFee`
- APY Spread Fee is approximately 50%

### Quota fees
- Applied when buying a quota (entry, rebalancing, leverage increases)
- Examples: 0.01% for ETH positions, 0.03-0.05% for medium-tail assets

All protocol fees go to the Gearbox DAO.

## Smart contract details

- **License**: BUSL-1.1 (Business Source License)
- **Solidity version**: 0.8.17+
- **Contract type**: ERC-4626 lending pool with EIP-2612 permit support
- **Smart contract developer**: Gearbox Foundation

The smart contracts are developed by Gearbox Protocol and deployed by Hyperithm on Plasma chain.

## Notes

- This is a Gearbox V3 PoolV3 contract deployed on Plasma chain
- The vault is curated by Hyperithm, who manages the vault parameters
- The underlying asset is USDT0, a cross-chain USDT token on Plasma
- The contract supports credit manager borrowing for leveraged positions
- Gearbox Protocol has been actively deploying on Plasma chain with integrations for Pendle and other DeFi protocols
