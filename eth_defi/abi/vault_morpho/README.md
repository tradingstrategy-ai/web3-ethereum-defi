# VaultMorpho implementation ABI

`VaultMorpho.json` is the verified Ethereum implementation ABI at
[`0xf392a5b96d3248440bb4841b4f50f0862060c1a3`](https://etherscan.io/address/0xf392a5b96d3248440bb4841b4f50f0862060c1a3#code),
used by vault `0x21ed44c18c926c60092b1b2985e2c999421a5a69` at block 26402309.
The verified source and ABI were retrieved on 13 November 2026.

The implementation is named `VaultMorpho`, uses Morpho Blue markets and has an
additional `tvlCap` check in `deposit()`. Its owning protocol and upstream GitHub
repository have not been identified. It must not inherit canonical Morpho
attribution merely because it exposes `MORPHO()`.

See the [canonical integration guide](../../../docs/source/vaults/vault_morpho/index.rst)
for the deployment evidence, capability detection and metadata boundaries.
The ABI supports contract reads; it is not proof of official protocol provenance.

## Unsupported integration

All deployments identified as the unattributed VaultMorpho family report
`unsupported_protocol`, on every chain and implementation. This status excludes
new investment like a permissioned vault but does not assert an account whitelist.
Verified source for a handful of deployments is not a maintained protocol
integration. There is no implementation-address exception list or proxy-slot
permission check.

The ABI remains for price/metadata reads and recovery of existing positions.
Stored history, cap diagnostics, attribution warnings and redemption methods are
retained. Migration 078 marks all saved family records unsupported, not just the
four originally examined deployments; normal export preserves the status in
`deposit_permission` and `whitelist.status`. Production apply/export is pending.
