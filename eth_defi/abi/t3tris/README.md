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
When updating these files, start from `https://app.t3tris.finance/vaults`, fetch
the current chunk list, and locate the current export named `IVaultAbi`.

## Reference links

- T3tris homepage: https://t3tris.finance/
- T3tris vault app: https://app.t3tris.finance/vaults
- T3tris GraphQL endpoint: https://api.t3tris.finance/graphql
- T3tris docs repository: https://github.com/t3tris-finance/mdoc-t3tris
- Introduction: https://raw.githubusercontent.com/t3tris-finance/mdoc-t3tris/main/docs/en/01-introduction.md
- Vault interface: https://raw.githubusercontent.com/t3tris-finance/mdoc-t3tris/main/docs/en/04-developers/02-vault-interface.md
- Vault getters: https://raw.githubusercontent.com/t3tris-finance/mdoc-t3tris/main/docs/en/04-developers/03-vault-getters.md
- Events reference: https://raw.githubusercontent.com/t3tris-finance/mdoc-t3tris/main/docs/en/04-developers/08-events-reference.md

## Known documentation drift

The Markdown interface documentation is useful, but some signatures differ from
the ABI used by the live app:

- Docs list `requestDeposit(uint256 assets, address receiver, bytes permit2Data)`;
  the live ABI has `requestDeposit(address receiver, bool unsafe, uint256 assets,
  bytes permit2Data)`.
- Docs list `requestRedeem(uint256 shares, address receiver, address owner,
  address previousClaimReceiver)`; the live ABI has `requestRedeem(address
  receiver, address owner, address previousClaimReceiver, bool unsafe, uint256
  shares)`.
- Docs list `getPerfFee()`; the live ABI has `getPerformanceFee()`.
- Docs list some request getter arguments as `(uint256 requestId, address user)`;
  the live ABI uses `(address owner, uint256 requestId)`.

Use `IVault.json` as the implementation source for selector generation unless
T3tris later publishes verified contracts or canonical JSON ABI artefacts.
