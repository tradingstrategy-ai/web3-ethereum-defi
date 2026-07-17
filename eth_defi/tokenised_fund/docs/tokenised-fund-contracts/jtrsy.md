# Janus Henderson Anemoy Treasury Fund (JTRSY) contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Janus Henderson Anemoy Treasury Fund (`JTRSY`) | Ethereum ERC-20, [`0x8c213ee79581ff4984583c6a801e5263418c4b86`](https://etherscan.io/address/0x8c213ee79581ff4984583c6a801e5263418c4b86#code) | `Tranche`; a direct deployment, **not a proxy** | A 6-decimal, permissionable Centrifuge tranche/share token. It is an ERC-20 with EIP-2612 permit, ERC-1404 transfer-restriction queries, ERC-7575 share-token vault links, and an external compliance-hook callback on mint, burn, and transfer. The token is not itself an ERC-4626/7540 vault or the subscription/redemption contract. | [centrifuge/liquidity-pools](https://github.com/centrifuge/liquidity-pools), [`Tranche.sol`](https://github.com/centrifuge/liquidity-pools/blob/main/src/token/Tranche.sol) | [Centrifuge token compliance](https://docs.centrifuge.io/developer/protocol/features/token-compliance/), [share-token concept](https://docs.centrifuge.io/user/concepts/share-tokens/), [Centrifuge app pool](https://app.centrifuge.io/pool/281474976710660) |

## On-chain identification and verification

Etherscan identifies the address as **Janus Henderson Anemoy Treasury Fund
(JTRSY)**, with six decimals, source code verified, and contract name
`Tranche`. The deployment is also an exact creation- and runtime-bytecode match
in [Sourcify](https://sourcify.dev/server/v2/contract/1/0x8c213ee79581ff4984583c6a801e5263418c4b86).
Sourcify reports the fully qualified source name
`src/token/Tranche.sol:Tranche`, Solidity `0.8.26`, optimiser enabled with 500
runs, Cancun EVM target, deployment block `20460672`, and deployment
transaction
[`0x503224f5…c8ddc742`](https://etherscan.io/tx/0x503224f5582af888011900a2e5dcfbe57a7668de67f8b555ae0d9d3c8ddc742).

Sourcify's proxy-resolution result is `isProxy: false`, with no implementation
contracts. Integrations should therefore use the token address directly rather
than trying to resolve an EIP-1967 implementation.

The verified source tree names the contract `Tranche` and includes Centrifuge's
`Auth`, `ERC20`, `ITranche`, `IHook`, and `IERC7575` components. A public
GitHub search for the contract name and the distinctive `authTransferFrom`,
`setHookData`, and `updateVault` functions finds Centrifuge's public
`liquidity-pools` codebase. That is strong protocol-family evidence. Sourcify's
deployment-specific source is the source of record; do not assume the current
`main` branch is byte-for-byte the historic deployment without a separate build
comparison.

## Contract surface and behaviour

`Tranche` is the share token within Centrifuge's fund architecture. It has no
`asset`, `deposit`, `withdraw`, `redeem`, `requestDeposit`, or
`requestRedeem` function, so it must not be treated as a vault merely because
the wider Centrifuge system supports ERC-4626, ERC-7540, and ERC-7575.
The linked vaults, selected by deposited-asset address, are exposed separately
through `vault(asset)`.

| Area | Material functions | Behaviour |
| --- | --- | --- |
| ERC-20 and permit | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `permit`, `nonces`, `DOMAIN_SEPARATOR` | Standard ERC-20 reads/transfers and EIP-2612-style approvals by signature. `transfer` and `transferFrom` also invoke the configured hook. |
| Issuance and destruction | `mint(to,value)`, `burn(from,value)` | Only an authorised `ward` can mint or burn. The token limits total supply and each packed balance to `uint128`. Mint and burn invoke the same compliance hook as ordinary transfers. |
| Permissioning | `hook`, `file("hook",address)`, `hookDataOf`, `setHookData`, `checkTransferRestriction`, `detectTransferRestriction`, `messageForTransferRestriction` | An optional external `IHook` receives callbacks for standard transfers, minting, burning, and authorised transfers. It can reject the operation. The token also implements ERC-1404-style pre-flight restriction queries. Its per-account `bytes16` hook data is packed alongside the balance. |
| Vault association | `vault(asset)`, `updateVault(asset,vault_)`, `VaultUpdate` | Authorised operations associate a tokenised share class with a vault for a particular ERC-20 asset. This is an ERC-7575 share-token relationship, not evidence that the token contract is the vault. |
| Privileged transfers | `authTransferFrom(sender,from,to,value)` | An authorised `ward` can make an allowance-aware transfer while identifying a logical sender. The compliance hook receives a dedicated authorised-transfer callback. This is used by Centrifuge fund/vault flows. |
| Governance | `wards`, `rely(user)`, `deny(user)`, `file("name",string)`, `file("symbol",string)` | MakerDAO-style `Auth`: any current ward can add or remove wards and administer token metadata; a ward or the hook can set the hook reference/data as allowed by the verified source. |

The transfer hook is central to the security and operational model. If `hook`
is non-zero, `transfer`, `transferFrom`, `mint`, `burn`, and
`authTransferFrom` revert unless the relevant callback returns its expected
selector. `detectTransferRestriction` uses the hook's corresponding view check
and returns `0` (`transfer-allowed`) or `1` (`transfer-blocked`). This is a
generic hook interface: the share-token bytecode alone does not identify the
specific live restriction policy, so integration code should read the live
`hook()` address and inspect it before assuming transfers are unrestricted.

## Fund and protocol conclusion

**Conclusion: Centrifuge liquidity-pools `Tranche` token for the Janus
Henderson Anemoy Treasury Fund — high confidence.**

The conclusion is supported independently by the exact verified source name
and ABI, the Centrifuge public codebase match, Etherscan's JTRSY token label,
and Centrifuge's own [JTRSY announcement](https://centrifuge.io/blog/jtrsy-aa-plus-rating),
which says the fund is powered by Centrifuge and provides on-chain exposure to
short-duration US Treasury bills. Centrifuge's official documentation describes
its share tokens as ERC-20 tokens with ERC-1404 and modular transfer hooks, the
same architecture present in this deployment.

Janus Henderson is the fund's sub-investment manager, while Centrifuge/Anemoy
provide the tokenisation and fund infrastructure. The
[S&P Global report hosted by Centrifuge](https://centrifuge.mypinata.cloud/ipfs/QmQ9P1BuH6mBkN9Gs1aBZo34zX6NYigRZ84nu13Wi52CKC)
describes whitelisted wallet access, token issuance on supported chains, and
USDC subscription/redemption processing. It should not be read as proof that
any wallet can buy, receive, or redeem this ERC-20.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned **783,691,014.920462
JTRSY** (6 decimals). The `Tranche` ABI does not expose a share-price or NAV
function; price accrual and valuation belong to the linked Centrifuge fund/vault
and its off-token accounting.

## Integration implications

- Identify JTRSY from the direct `Tranche` token address and standard ERC-20
  fields, but classify it as a **Centrifuge permissioned share token**, not an
  ERC-4626 vault.
- Before attempting a transfer, read `hook()` and either simulate the transfer
  or query `checkTransferRestriction`; transfer eligibility is deliberately
  delegated to the hook and can change with its configuration.
- Use the linked vault / Centrifuge pool route for subscription and redemption
  semantics. Token-level `mint` and `burn` are issuer-authorised accounting
  actions, not public investment entry points.
- Do not derive NAV or yield from `totalSupply`. Centrifuge describes share
  tokens as price-accruing; pricing and investor flows live in the fund/pool
  infrastructure rather than this ERC-20 contract.

## Primary sources

- [Etherscan token and verified source](https://etherscan.io/address/0x8c213ee79581ff4984583c6a801e5263418c4b86#code)
- [Sourcify exact-match record and deployment source](https://sourcify.dev/server/v2/contract/1/0x8c213ee79581ff4984583c6a801e5263418c4b86?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution)
- [Centrifuge `liquidity-pools` public source repository](https://github.com/centrifuge/liquidity-pools)
- [Centrifuge `Tranche.sol` public source](https://github.com/centrifuge/liquidity-pools/blob/main/src/token/Tranche.sol)
- [Centrifuge token-compliance documentation](https://docs.centrifuge.io/developer/protocol/features/token-compliance/)
- [Centrifuge JTRSY fund announcement](https://centrifuge.io/blog/jtrsy-aa-plus-rating)
