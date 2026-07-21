# Bulla Factoring — TCS Settlement Pool Token V2.1

## Summary

`0xc099773267308D8e9E805f47EABf9ab13bBc9e37` is a **Bulla Factoring**
ERC-4626 invoice-factoring pool. The verified Arbitrum contract is
`BullaFactoringV2_1`; its source identifies it as a Bulla Factoring Fund and
imports Bulla Claim and BullaFrendLend v2 interfaces.

The vault token is named **TCS Settlement Pool Token V2.1** (`BFT-TCS-V2_1`).
It accepts PYUSD and pools funds to finance invoices and loan offers. This is a
permissioned pool: deposit, redemption and factoring permissions are separate
contracts, and redemptions may enter a FIFO redemption queue when liquidity is
not available.

## Details

- **Chain**: Arbitrum One (chain ID 42161)
- **Address**: `0xc099773267308D8e9E805f47EABf9ab13bBc9e37`
- **Explorer**: https://arbiscan.io/address/0xc099773267308D8e9E805f47EABf9ab13bBc9e37
- **Contract name**: `BullaFactoringV2_1`
- **Token**: TCS Settlement Pool Token V2.1 (`BFT-TCS-V2_1`)
- **Underlying asset**: PayPal USD (`PYUSD`),
  `0x46850aD61C2B7d64d08c9C754F45254596696984`
- **Created**: 2026-04-24 19:08:48 UTC, block 455955959
- **Creator**: `0x81f1e947811496453dbbaea0ac2c250e056bbd96`
- **Owner**: `0xB17f273e15F8907965f64E3fE8a1A16E10d499De`

## Protocol: Bulla Factoring

- **Protocol web page**: https://www.bulla.network/
- **Factoring contracts repository**: https://github.com/bulla-network/factoring-contracts
- **Bulla Claim v2 contracts repository**: https://github.com/bulla-network/bulla-contracts-V2
- **Documentation**: https://docs.bulla.network/
- **DefiLlama**: no Bulla Factoring listing found; the current Bulla Exchange
  listing is a separate Berachain DEX, not this protocol.

The Bulla Factoring repository describes the system as an ERC-4626 vault that
pools investor capital to factor invoices. It integrates Bulla Claim v2 for
tokenised receivables and BullaFrendLend v2 for direct loan offers.

## Fees and parameters

The following values were read from the vault on Arbitrum:

- **Protocol fee**: 30 basis points (0.30%)
- **Admin fee**: 0 basis points
- **Target yield**: 800 basis points (8.00%)
- **Grace period**: 60 days
- **Pool name**: `tcs`
- **Bulla DAO recipient**: `0x47Ee085AC0Cdd254D4BFeca3405cD970f44728AB`
- **Underwriter**: `0x5d72984B2e1170EAA0DA4BC22B25C87729C5EBB3`

Fees are withheld while funding invoices. The contract code supports a
protocol fee, an administrator fee and an underwriter-defined spread; the
spread is separate from the two vault-level fee fields above.

## Audit information

The Bulla Factoring repository includes its audit scope and audit reports:

- https://github.com/bulla-network/factoring-contracts/blob/main/audit_scope.md
- https://github.com/bulla-network/factoring-contracts/tree/main/audits

## Recognition status in this repository

The vault was already detected and stored in the local vault database. It was
first seen on 2026-04-24 and had 43 deposits and 3 redemptions in the reviewed
record, but it was originally labelled only as **`ERC-4626`**. Bulla Network is
now a recognised protocol integration with metadata and documentation.

Detection intentionally uses one Bulla-specific ABI probe only:
``bullaDao()``. This no-argument public getter is present on Bulla Factoring
V2 pools, avoiding a copied full contract ABI for classification.

## Notes

- The contract is not an upgradeable EIP-1967 proxy.
- Although `maxDeposit(address(0))` returns the ERC-4626 maximum, actual
  deposit eligibility is controlled by the configured permission contract.
- `maxRedeem(address(0))` is zero, which is expected for the zero address and
  does not by itself establish the redemption availability for a holder.
