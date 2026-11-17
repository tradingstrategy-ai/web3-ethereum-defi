# Fluid ABI inventory

Both files are complete, directly verified Sourcify ABI arrays. The deployed
contracts are not proxies according to their Sourcify metadata. Keep their
interfaces separate; do not manufacture a combined capacity-view ABI.

| Filename | Source | Fetched at (UTC) | Reason to fetch |
| --- | --- | --- | --- |
| `iToken.json` | [Complete verified ABI](https://sourcify.dev/server/v2/contract/1/0x7f47b1ced85f5d773cd68ca606ed6f0fee381046?fields=abi,proxyResolution,compilation); chain 1, `0x7f47b1ced85f5d773cd68ca606ed6f0fee381046` | 2026-11-16T19:29:08.312852+00:00 | Read the complete ERC-20 lending-route interface, including the nine-field getData() configuration tuple; all 61 entries retained. |
| `FTokenNativeUnderlying.json` | [Complete verified ABI](https://sourcify.dev/server/v2/contract/1/0x90551c1795392094FE6D29B758EcCD233cFAa260?fields=abi,proxyResolution,compilation); chain 1, `0x90551c1795392094FE6D29B758EcCD233cFAa260` | 2026-11-16T19:29:08.420852+00:00 | Read the native-currency route, including NATIVE_TOKEN_ADDRESS() and getData(); all 73 entries retained. |

## Route identities

- `iToken.json`: Ethereum `0x7f47b1ced85f5d773cd68ca606ed6f0fee381046`,
  verified as `contracts/protocols/lending/iToken/main.sol:iToken`.
- `FTokenNativeUnderlying.json`: Ethereum
  `0x90551c1795392094FE6D29B758EcCD233cFAa260`, verified as
  `contracts/protocols/lending/fToken/nativeUnderlying/fTokenNativeUnderlying.sol:fTokenNativeUnderlying`.

Capacity reads use the shared `getData()` surface through the complete iToken
ABI. The optional native-route probe uses the separate complete native ABI;
absence of that selector on an ERC-20 deployment remains an ordinary failed
probe. The native sentinel identifies the Liquidity contract's ETH balance,
even though the vault's ERC-4626 asset is WETH. The `getData()` boolean is a
rewards flag, not a native-asset flag. A nonzero reserve still does not resolve
Fluid's expanding withdrawal limits and remains a partial capacity measurement.
