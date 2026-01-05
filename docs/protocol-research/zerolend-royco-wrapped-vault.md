# ZeroLend Royco wrapped vault

## Summary

This is a **ZeroLend** vault deployed through **Royco Protocol's** WrappedVault infrastructure. The smart contract (`WrappedVault`) is developed by Royco Protocol and deployed by ZeroLend for their RWA (Real World Assets) USDC lending market.

## Details

- **Chain**: Ethereum Mainnet
- **Address**: `0x887d57a509070a0843c6418eb5cffc090dcbbe95`
- **Explorer link**: https://etherscan.io/address/0x887d57a509070a0843c6418eb5cffc090dcbbe95
- **Contract name**: WrappedVault
- **Contract type**: ERC-4626 compliant vault wrapper with integrated rewards system
- **Implementation address**: `0x3c44c20377e252567d283dc7746d1bea67eb3e66`
- **Deployer**: ZeroLend: Deployer (`0x0f6e98a756a40dd050dc78959f45559f98d3289d`)

## Protocol: ZeroLend

ZeroLend is a multi-chain DeFi lending protocol built on Layer 2 solutions, based on Aave V3. It specialises in:

- Liquid Restaking Tokens (LRTs) lending
- Real World Assets (RWAs) lending
- Account abstraction

- **Web page**: https://zerolend.xyz/
- **Application**: https://app.zerolend.xyz/
- **Documentation**: https://docs.zerolend.xyz/
- **GitHub**: https://github.com/zerolend
- **Twitter/X**: https://x.com/zerolendxyz
- **DefiLlama**: https://defillama.com/protocol/zerolend

### ZeroLend audits

ZeroLend has been audited by multiple security firms:

- **Mundus Security**: https://github.com/zerolend/audits
- **PeckShield**: https://github.com/zerolend/audits
- **Halborn**: https://www.halborn.com/case-studies
- **Zokyo Security**: https://zokyo.io/reports/zerolend
- **Immunefi Bug Bounty**: https://immunefi.com/audit-competition/zerolend-boost/leaderboard/

Full audit documentation: https://docs.zerolend.xyz/security/audits

## Smart contract developer: Royco Protocol

The WrappedVault smart contract is developed by Royco Protocol, an Incentivised Action Market (IAM) Protocol that allows protocols to create incentivised ERC-4626 vault markets.

- **Web page**: https://royco.org/
- **Documentation**: https://docs.royco.org/
- **GitHub**: https://github.com/roycoprotocol/royco
- **Twitter/X**: https://x.com/roycoprotocol

### Royco contract addresses

- **WrappedVaultFactory**: `0x75e502644284edf34421f9c355d75db79e343bca`
- **WrappedVault implementation**: `0x3c44c20377e252567d283dc7746d1bea67eb3e66`
- **VaultMarketHub**: `0xa97eCc6Bfda40baf2fdd096dD33e88bd8e769280`

Full contract addresses: https://docs.royco.org/for-incentive-providers/contract-addresses

### Royco audits

Royco Protocol has been audited by:

- **Spearbit**: Audit conducted October 2024
- **Cantina Private Competition**
- **Cantina Open Competition**

Full audit documentation: https://docs.royco.org/for-incentive-providers/audits

## Notes

- This vault wraps an underlying ZeroLend RWA market vault
- When USDC is supplied, it gets supplied into the ZeroLend RWA market
- USDC is borrowed by yield traders leveraging RWA assets as collateral
- The market charges a premium on interest rates for higher yields
- The vault supports multiple simultaneous reward programmes (max 20)
- Rewards can be claimed as tokens or points
