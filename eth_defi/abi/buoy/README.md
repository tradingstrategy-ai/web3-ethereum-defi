# Buoy interfaces and source verification

Chain: HyperEVM (999). Retrieved and checked on 2026-11-14.

| Interface | Clone implementation / contract | Canonical source |
| --- | --- | --- |
| BuoyVaultV1.json | `0x27cd383395f366be39b5a38a08dc98775d665bb0` | Etherscan v2 verified source/ABI |
| BuoyVaultV1_1.json | `0x3fe85a627dcc8ff6896b1376beda7b1493f2c538` | Etherscan v2 verified source/ABI |
| BuoyVaultV2.json | `0xe412625761b8256832a018765dcf2ef8e7b0397b` | Etherscan v2 verified source/ABI |
| BuoyVaultV3.json | `0x47d0e7aca4246da0334f97d6840cf8559d771663` | Etherscan v2 verified source/ABI |
| Router.json | proxy `0x4f4e4e94b94d40600b30100a7f8e5e312f59738e` | Official application export |
| VaultFactory.json | `0x8c5e0ef4efbf57016d1b55d37e035f115d65a0a5` | Official application export |
| VaultPolicy.json | `0xa5ff5ab8e8d2ce7d059490f74942f03fb99905fd` | Official application export |

The [explorer](https://hyperevmscan.io/) browser pages returned 403, but the
[Etherscan API](https://api.etherscan.io/v2/api) returned verified Solidity
standard-input source bundles and complete vault ABIs using `chainid=999`,
`module=contract`, `action=getsourcecode`. Source path in each bundle:
`contracts/vault/BuoyVault.sol`. The undocumented intermediate implementation
is labelled v1.1 here to distinguish its ABI; this is not an onchain version claim.

The Router proxy reported implementation
`0x37e46a019402435b9fec3c0af2d80a0ef3d639b8`. That implementation did not return
a verified ABI. Router, factory and policy interfaces were extracted as data
from the official [app bundle](https://buoy.finance/assets/index-L25Mo988.js).
This records interface provenance, not a Router source/security audit. No
transaction execution is supported. The interface sources agree with the
[developer documentation](https://docs.buoy.finance/developers/smart-contract-integration).

## Reviewed accounting

All four verified implementations have the same storage-only valuation rules:

- `totalAssets() = max(latestNav - totalOwedUsdc, 0)`. Pending deposits are
  excluded when the fulfiller supplies gross NAV, not subtracted again here.
- `pricePerShare() = totalAssets() * 1e18 / totalSupply()`, using integer
  division. Zero supply returns the 1e18 bootstrap price, not an investment return.
- Share and asset units both have six decimals. `convertToAssets` rounds at
  raw USDC precision; the canonical series uses `pricePerShare` for precision.
- Performance fees mint shares at new high-water marks, diluting supply.
  v2/v3 redirect `protocolFeeShareBps` of that fee mint to
  `protocolFeeRecipient`; it is included in the aggregate performance fee.
  The exported protocol component is the performance rate times that split,
  on the same new-profit basis. Pre-v2 has no protocol split. A zero recipient
  disables it, matching `_crystallizeFee` in the verified source.
- `_cacheNav` and bootstrap deposit settlement stamp `latestNavTimestamp`.
  Payouts decrease gross NAV and liabilities together without changing the
  timestamp; write-down accounting can also adjust cached balances. Polling
  those views does not create a new valuation.
- Pre-v3 implementations have no depositor whitelist. v3 consults its fixed
  policy pointer, which may be zero; policy read failures remain unknown.
- These accounting getters do not read live HyperCore precompiles. The separate
  Core movement and trading methods are not exposed through a deposit manager.

At fixed block 51,248,351 the factory enumerated 60 clones across these four
implementations. A targeted Hypersync query found exactly 60 official creation
logs, spanning both creation signatures. The frozen migration inventory keeps
those onchain first-seen blocks/timestamps, independently of API visibility.
The Router's legacy-factory count was zero at that observation block.

## Validation

The real-provider integration test is
`tests/erc_4626/vault_protocol/test_buoy.py::test_buoy_real_registry_and_published_accounting`.
It reads the supplied `JSON_RPC_HYPERLIQUID` connection at the fixed block,
verifies registry enumeration, accounting identities, decimals, admission,
unsupported execution and the shared scan-record path. It sends no transactions.
