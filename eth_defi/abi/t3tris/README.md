# T3tris ABI files

T3tris contract source code was not verified on public block explorers during
the initial integration research. The public T3tris documentation says that only
interfaces are published, so these ABI files are sourced from the live frontend
and checked against the published interface documentation.

## Files

| File | Source | Notes |
| --- | --- | --- |
| `IVault.json` | `IVaultAbi` export in the T3tris app chunk `https://app.t3tris.finance/_next/static/chunks/13etv-4ylf72t.js` | Full vault ABI used by the live T3tris frontend. Refreshed from the production app before saving. |
| `Multicall.json` | Separate `multicall(bytes[])` fragment used by the same app chunk against vault addresses | The frontend keeps this as a small local ABI fragment instead of including it in `IVaultAbi`. |

The chunk filename is content-hashed and may change after a frontend deploy.
When updating these files, start from `https://earn.t3tris.finance/vaults` (the
app moved there from `app.t3tris.finance`), fetch the current chunk list, and
locate the current export named `IVaultAbi`.

## Reference links

- T3tris homepage: https://t3tris.finance/
- T3tris vault app: https://earn.t3tris.finance/vaults
- Developer documentation: https://docs.t3tris.finance/
- Onchain reference: https://docs.t3tris.finance/developers/onchain-reference

## Documentation and the deployed ABI

The developer documentation at `docs.t3tris.finance` describes the T3tris
TypeScript SDK, not Solidity signatures. Its fields differ from the deployed
vault: for example, its `requestDeposit` takes `amount`, `controller` and
`permit2Data`, while the vault has `requestDeposit(address receiver, bool
unsafe, uint256 assets, bytes permit2Data)`.

The earlier Markdown interface docs, in the since-deleted
`t3tris-finance/mdoc-t3tris` repository, also differed from the deployed ABI:

- `requestDeposit(uint256 assets, address receiver, bytes permit2Data)` against
  the live `requestDeposit(address receiver, bool unsafe, uint256 assets, bytes
  permit2Data)`.
- `requestRedeem(uint256 shares, address receiver, address owner, address
  previousClaimReceiver)` against the live `requestRedeem(address receiver,
  address owner, address previousClaimReceiver, bool unsafe, uint256 shares)`.
- `getPerfFee()` against the live `getPerformanceFee()`.
- Request getters as `(uint256 requestId, address user)` against the live
  `(address owner, uint256 requestId)`.

Use `IVault.json` as the implementation source for selector generation unless
T3tris later publishes verified contracts or canonical JSON ABI artefacts.
