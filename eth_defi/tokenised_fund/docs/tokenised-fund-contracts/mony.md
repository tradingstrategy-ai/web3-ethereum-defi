# My OnChain Net Yield Fund (MONY) contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| My OnChain Net Yield Fund (`MONY`) | Ethereum ERC-20, [`0x6a7c6aa2b8b8a6a891de552bdeffa87c3f53bd46`](https://etherscan.io/address/0x6a7c6aa2b8b8a6a891de552bdeffa87c3f53bd46#code) | `FactTokenDiamond`, an EIP-2535 Diamond dispatcher with 16 live facets | A permissioned ERC-20 fund-share token built from a modular FACT Diamond implementation. The facets provide ERC-20 balances/allowances, token and account activation controls, minting, request-based redemption/burn flows, account stop codes, pausing, role/owner controls, and a timelock. It is not an ERC-4626 vault and has no direct public `deposit`, `withdraw`, or NAV function. | No public J.P. Morgan/Kinexys implementation repository located. The verified Diamond code attributes the architectural pattern to [EIP-2535](https://eips.ethereum.org/EIPS/eip-2535); see the [reference implementation](https://github.com/mudgen/diamond-3). | [J.P. Morgan launch announcement](https://www.prnewswire.com/news-releases/jp-morgan-asset-management-launches-its-first-tokenized-money-market-fund-302642262.html), [RWA.xyz MONY listing](https://app.rwa.xyz/assets/MONY) |

## On-chain identification and verification

J.P. Morgan Asset Management's [launch announcement](https://www.prnewswire.com/news-releases/jp-morgan-asset-management-launches-its-first-tokenized-money-market-fund-302642262.html)
names this exact Ethereum address as MONY's blockchain token address and says
the fund is powered by Kinexys Digital Assets and distributed through Morgan
Money. RWA.xyz independently identifies it as MONY's Ethereum ERC-20 token.

The address is an exact creation- and runtime-bytecode match in
[Sourcify](https://sourcify.dev/server/v2/contract/1/0x6a7c6aa2b8b8a6a891de552bdeffa87c3f53bd46?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution).
The deployed dispatcher is `FactTokenDiamond`
(`contracts/FactTokenDiamond.sol:FactTokenDiamond`), compiled with Solidity
`0.8.17`; Sourcify classifies it as a direct deployment, not an EIP-1967 proxy.
That does **not** make its behaviour immutable: the dispatcher's verified
source sends each function selector to a facet with `delegatecall`, and its
Diamond-cut mechanism can add, replace, or remove selectors.

Unlike a conventional ERC-20 contract, the Diamond dispatcher's own verified
ABI is empty because its callable surface is supplied by facets. The live
`facets()` view returned 16 facet contracts during this check. All 16 were
separately source-verified as exact matches by Sourcify.

| Live facet | Purpose |
| --- | --- |
| [`ERC20FacetTransferBurnableStoppable`](https://etherscan.io/address/0x1aa6cf30f6b4f18f5cf12cc0f2c8543eaabe62d6#code) | ERC-20 `name`, `symbol`, `decimals`, supply, balance, allowance, approval, `transfer`, and `transferFrom`, with active-account, pause, token-active, stop-code, and burner-address checks. |
| [`ManagedAccountFacet`](https://etherscan.io/address/0x71ff8c0e761b7640c1e294bf9b1b05b3a7df77a6#code) | Activates/deactivates accounts; locks/unlocks balances; and permits account-admin `forceTransfer` and `forceBurn` operations. |
| [`MintableFacet`](https://etherscan.io/address/0xe70c597957e542209ed44f2fbae58370089e59be#code) | `TOKENIZATION_AGENT`-controlled, request-ID-based minting to an active account. |
| Burn facets | [`BurnPreparableFacet`](https://etherscan.io/address/0x981b3af7957841ce2c8869ee25055a693a89006b#code), [`BurnRespondableFacet`](https://etherscan.io/address/0x8c2fbc1df5918cb15b96ad4d57298988c73dac32#code), and [`BurnableFacet`](https://etherscan.io/address/0x88183f91a0a7315bd3e2102be7520c88ce9eff67#code) implement prepare/approve/reject/execute burn requests. A transfer to an address with the `BURNER` role creates a burn request. |
| [`StopCodesFacet`](https://etherscan.io/address/0x996440281f77b4d664878b0b14a10e909fc3310b#code) | `ACCOUNT_ADMIN` can set per-account controls that stop transfers from/to an account or redemption from it. |
| Pause/timelock facets | [`PausableFacetTimelockable`](https://etherscan.io/address/0xb1d84e1289942e45caa9c1103cbc867104f5f087#code), [`ManagedTokenFacetTimelockable`](https://etherscan.io/address/0xefbd67c1a8bc587599a475f720783282724e83b5#code), [`AccessControlFacetTimelockable`](https://etherscan.io/address/0xe79a30d2a2134fccecbfb9a79f39e6ab1637b3f#code), and [`TimelockableFacet`](https://etherscan.io/address/0xca76e9791de59331219bba4be1d05404eda8764c#code) provide pause/unpause, token activation, selected role changes, and queued delayed operations. |
| Diamond/ownership facets | [`DiamondCutFacet`](https://etherscan.io/address/0x6e4d1a8b0a6b2ed26c50873c2c75feac77385295#code), [`DiamondLoupeFacet`](https://etherscan.io/address/0x4d9832828a3898c9fcaf8799cacdcf709068ba24#code), [`ERC173FacetTimelockable`](https://etherscan.io/address/0x7d7d5c1c2c9d45f6c73db9ccf6236ceb84d71965#code), and [`ERC165Facet`](https://etherscan.io/address/0x66f853ea787118985be98a7f3a071b9fe1390231#code) support Diamond upgrades/introspection, two-step ownership, and interface registration/querying. |
| [`TokenMetadataFacet`](https://etherscan.io/address/0x5be781f803552a0532fb03f6513d5a4bacb665a9#code) | Token-admin metadata read/write path. |

## Contract surface and behaviour

| Area | Material functions | Behaviour |
| --- | --- | --- |
| ERC-20 | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom` | Standard ERC-20 fields and allowance mechanics, routed through a facet. Transfers require the token to be active and unpaused and the relevant accounts to be active. |
| Investor/account controls | `accountActive`, `activateAccount`, `deactivateAccount`, batch variants, `lock`, `unlock`, `lockBalanceOf`, `forceTransfer`, `forceBurn` | The account-admin role controls which accounts are active, can lock their token balance, and has forced movement/burn powers. These are issuer/transfer-agent controls, not ordinary investor actions. |
| Issuance | `mint(bytes32,address,uint256)` | A `TOKENIZATION_AGENT` mints against a request ID, only while the token is active/unpaused and the recipient account is active. |
| Redemption/burn workflow | `prepareBurn`, `burnRequestOf`, `approveBurn`, `rejectBurn`, `burn` | The Diamond supports asynchronous/request-based burn processing. Sending tokens to a `BURNER` address creates a burn request; authorised agents can approve/reject and execute the burn. |
| Transfer restrictions | `updateStopCodes`, `getStopCodes` | Per-account stop codes can prevent outgoing transfers, incoming transfers, or redemptions. They supplement active-account and pause checks. |
| Governance and upgrades | `grantRole`, `renounceRole`, `owner`, `transferOwnership`, `acceptOwnership`, `diamondCut`, `facets`, `facetAddress`, `setDelay`, `queueTransaction`, `cancelTransaction`, `isExecutable` | Ownership/roles and the Diamond cut control the executable implementation. Selected role/ownership/token-activation actions use timelock facets; `diamondCut` remains a separately privileged code-upgrade surface. |

## Known protocol and fund conclusion

**Conclusion: J.P. Morgan/Kinexys MONY fund token using a proprietary FACT
implementation built on the EIP-2535 Diamond standard — high confidence.**

The issuer and platform attribution comes from J.P. Morgan's announcement and
the RWA.xyz listing. The technical conclusion comes directly from the exact
verified source: `FactTokenDiamond`, `DiamondCut`, the `facets()` interface,
and 16 verified FACT-named facets. This is not a standard ERC-4626 vault,
Kinexys/JLTXX's Quorum deployment, or a generic Securitize/Centrifuge token.

GitHub and web searches for `FactTokenDiamond`, `fact.diamond.storage`, the
deployed facet names, and the imported `@odaplatform/da-fact-smartcontracts`
package did not locate an issuer-maintained public source repository. The
verified Etherscan/Sourcify packages are therefore the reproducible source
reference. The EIP-2535 reference implementation is pattern context, not
evidence of J.P. Morgan code provenance.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned **102,080,329.02 MONY**
(4 decimals). The `FactTokenDiamond` ABI has no direct share-price or NAV
function; use Kinexys/issuer NAV and distribution records rather than treating
the token supply as a valuation input.

## Integration implications

- Track MONY as an EIP-2535 Diamond whose ERC-20 functions are facets. Do not
  model the dispatcher alone as a simple immutable ERC-20 or an ERC-4626 vault.
- Discover the live facet map with `facets()`/`facetAddress(bytes4)` at
  integration time and monitor `DiamondCut` events. A facet replacement can
  change token behaviour without moving the token address.
- Expect issuer-controlled active-account, stop-code, lock, forced-transfer,
  and forced-burn restrictions. A successful ERC-20 ABI decode does not mean
  an arbitrary wallet may receive, transfer, or redeem MONY.
- MONY's fund subscription/redemption is handled through Morgan Money and the
  issuer's operational workflow. The token's request-based burn functions do
  not independently establish public redemption eligibility or settlement.
- Use the issuer/platform's NAV and distribution records rather than deriving
  fund value from `totalSupply`; the dispatcher exposes no on-chain NAV oracle.

## Primary sources

- [Etherscan MONY Diamond dispatcher](https://etherscan.io/address/0x6a7c6aa2b8b8a6a891de552bdeffa87c3f53bd46#code)
- [Sourcify exact-match MONY Diamond source package](https://sourcify.dev/server/v2/contract/1/0x6a7c6aa2b8b8a6a891de552bdeffa87c3f53bd46?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution)
- [J.P. Morgan Asset Management launch announcement](https://www.prnewswire.com/news-releases/jp-morgan-asset-management-launches-its-first-tokenized-money-market-fund-302642262.html)
- [RWA.xyz MONY asset listing](https://app.rwa.xyz/assets/MONY)
- [EIP-2535 Diamond standard](https://eips.ethereum.org/EIPS/eip-2535)
- [EIP-2535 reference implementation](https://github.com/mudgen/diamond-3)
