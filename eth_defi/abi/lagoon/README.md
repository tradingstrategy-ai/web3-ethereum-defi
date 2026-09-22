# Lagoon ABI provenance

The versioned Lagoon vault ABIs in this directory are protocol-specific
interfaces loaded through `eth_defi.abi.get_abi_by_filename()`.

- `Vault.json` is the legacy Lagoon interface.
- `v0.4.0/Vault.json` and `v0.5.0/Vault.json` are the corresponding Lagoon
  release artefacts already used by the deployment code. Their source
  counterparts are the [`v0.4.0`](https://github.com/hopperlabsxyz/lagoon-v0/tree/v0.4.0/src/v0.4.0)
  and [`v0.5.0`](https://github.com/hopperlabsxyz/lagoon-v0/tree/v0.5.0/src/v0.5.0)
  release trees.
- `v0.6.0/Vault.json` is the bare ABI exported as `vaultAbi_v0_6_0` by the
  official Hopper Labs SDK at commit
  [`529dabf21c44c5f64013d21295f52ffbfdc59310`](https://github.com/hopperlabsxyz/sdk-v0/tree/529dabf21c44c5f64013d21295f52ffbfdc59310/packages/v0-core/src/constants).
  It is used as the compatibility interface for Lagoon `v0.6.0` and the
  deployed `v1.0.0` vaults described below.

Lagoon `v1.0.0` compatibility was characterised against the Base proxy
[`0x7eea189f34e10e7fe5386ba8d49ab41f95b7d54f`](https://basescan.org/address/0x7eea189f34e10e7fe5386ba8d49ab41f95b7d54f)
at block `51_649_628` on 2026-09-22. Its EIP-1967 implementation was
`0xF21ECC876afdEE2ed65D4cD4d869D9439c2Bf5fd`; the implementation was not
verified on BaseScan at the time of inspection. The v1 adapter therefore uses
the official v0.6 ABI only for the observed compatible surface and must not
describe it as a verified v1 ABI.

The ERC-7201 role and pending-Silo storage constants and field order used by
the modern adapter are based on `RolesLib.sol`, `Roles.sol`, `ERC7540Lib.sol`
and `ERC7540.sol` in the official Lagoon v0.6 source at commit
[`a8e73f5a5276aa4047b901083cbce127d7f7b470`](https://github.com/hopperlabsxyz/lagoon-v0/tree/a8e73f5a5276aa4047b901083cbce127d7f7b470/src/v0.6.0).
When Lagoon publishes verified v1 source, re-check the storage layout and
replace this compatibility boundary with a version-specific ABI if required.

These artefacts extend read and scanner compatibility only. Vault deployment
continues to use the repository's v0.5 contracts; this directory does not
contain verified Lagoon v1 deployment bytecode or initialisation artefacts.
