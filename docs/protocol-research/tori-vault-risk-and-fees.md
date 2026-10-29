# Tori vault risk and fees

Review date: 29 October 2026. This review extends native staking coverage in
[#1697](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1697) to
every identified third-party vault in the tracked catalogue with trUSD/strUSD
exposure. It does not classify unrelated test trUSD tokens, Satori products,
factoring pools or every new future integration by a name substring.

## Scope and evidence

The local metadata catalogue contained these 17 real deployments. Their receipt
and denomination symbols are frozen as extra identity checks in
`tori.disclosures.TORI_EXPOSURES`; they are not discovery rules. The public
`top_vaults_by_chain.json` feed was also checked. Some wrappers are absent from
that cleaned feed; absence does not remove their saved historical metadata.

| Chain | Vault | Address | Infrastructure | Fee findings |
| --- | --- | --- | --- | --- |
| Ethereum | Staked trUSD | `0x280839980a7ed0d7717f64125fe241012e5f5815` | Tori Finance | No separate management, entry or exit fee; undisclosed retained income share; internalised |
| Ethereum | Tori Ecosystem Vault | `0xcd69123b3fbbfc666e1f6a501da27b564c00de54` | Upshift | Live 0.2% annual management, 10% performance, 0% standard exit; optional instant exit 5% |
| Ethereum | RockawayX Tori Ecosystem | `0x3bd9adae6643ddcddd02746b8b60075e56df9478` | Morpho | Live 0% management, 10% performance, internalised |
| Ethereum | Junior Staked trUSD | `0x3b0cecd83bb9ee016587cbe61bea89e429da7a09` | Royco | Senior, junior and yield-share protocol-fee settings all 0%; first-loss position |
| Ethereum | Senior Staked trUSD | `0xfd43913ee196743e14c54c037b0a96023796f23c` | Royco | Senior, junior and yield-share protocol-fee settings all 0%; finite junior protection |
| Ethereum | Locked Pendle YT trUSD | `0x10b2487173d9f7087c8bdce9e70fd135212a8d7d` | Generic ERC-4626 | No separate wrapper charge; underlying YT interest/reward rates each 5%; withdrawals paused |
| Ethereum | Locked Pendle YT strUSD | `0x6569c554cbf71dfcc32aec48b0edd2582f5c4a96` | Generic ERC-4626 | No separate wrapper charge; underlying YT interest/reward rates each 5%; withdrawals paused |
| Ethereum | Stake DAO frxUSD/trUSD | `0x6324cd92203bb218079f2f44eb3893bd1ebea9a4` | Generic ERC-4626 | 16.5% on fee-bearing rewards; separate 0.025% harvest compensation, not a flat fee on all returns |
| Ethereum | Stake DAO trUSD/USDC | `0xcab1d2a2973763bed06e782b65a43fb3cfe27f13` | Generic ERC-4626 | 16.5% on fee-bearing rewards; separate 0.025% harvest compensation, not a flat fee on all returns |
| Ethereum | Stake DAO strUSD/trUSD | `0xa9163d9a3d69c3a43074c65a32c2b4d7aa585c34` | Generic ERC-4626 | 16.5% on fee-bearing rewards; separate 0.025% harvest compensation, not a flat fee on all returns |
| Ethereum | RockawayX Tori USDC | `0x6f576e5192a14f259f7fe7347ecf63b255d7f7d1` | Term Finance | 10% of reported profit through newly minted shares; underlying allocation fees separate |
| Ethereum | Tenor strUSD/USDC Collateral | `0xef6955d886fce26d87753da45192ea50f59dc91c` | Morpho | Live 0% management and performance, internalised |
| Ethereum | Tenor strUSD/USDC Lending | `0x12a3b3fa107c4847bf214c29e6573cb8d2e7a879` | Morpho | Live 0% management and performance, internalised; existing exclusions preserved |
| Ethereum | USDC/strUSD Market Vault | `0xadfcfff962d7e22f15ada7fa089848f7672724c8` | Morpho | Live 0% management and performance, internalised |
| Monad | Tori Ecosystem Monad | `0x9e98bda72ced55e562bed1dcaf2b123e95eb181e` | Accountable | Live 0% management, 10% performance, internalised |
| Monad | SharpByte AUSD Tori Ecosystem | `0x790e4f829fe865d432c4ea83b8b15f8871415b6c` | Morpho | Live 0% management, 10% performance, internalised; existing exclusions preserved |
| Monad | Curvance Staked trUSD | `0xbde156e035e5501378d07399c404b4d1d5c86ae8` | Curvance | 10% of borrower interest through newly minted shares; no separate management, entry or exit charge found |

The [official inventory](https://docs.tori.finance/resources/contracts)
identifies native strUSD and trUSD on Ethereum. Other chains' strUSD tokens
are bridged representations, not additional ERC-4626 native staking vaults.
The [integration guide](https://docs.tori.finance/resources/integrations)
points to composable collateral, liquidity and yield products. Exposure names
were reconciled with actual cached identities and current maintained adapters.

## Risk assessment

High is a conservative manual technical assessment for native strUSD.
Tori has source code, audits and public documentation, but its upgradeable
issuer contracts, privileged loss reporting, wallet restrictions and dependence
on issuer reporting remain part of its technical and transparency surface.
This is a reasoned judgement, not a reproduced numerical score. Existing
stricter native ratings and incident blacklisting take precedence.

Third-party protocol and product ratings remain unchanged. A USDC/AUSD loan
backed by Tori collateral has an economic loss path through liquidation and
bad debt; without an assessed oracle path, that alone does not justify changing
its technical rating. Product-specific technical assessments of the generic
wrappers, Royco tranches and Curvance position remain unavailable; the new notes
state the remaining audit-to-deployment gap rather than assigning a rating from source publication alone.
No flag or exclusion is removed.

This assessment follows the scope of the published
[technical risk framework](https://tradingstrategy.ai/blog/announcing-vault-technical-risk-framework-beta):
software, control surfaces and transparency. It does not turn trading losses,
collateral volatility or first-loss tranche economics into a technical score.
Those risks are explained separately in each product's note.

At Ethereum block 26299349 the native proxy's implementation was
`0x6561272e3ebc9f2e07cdb12b7c474db9132b977a`. The
[Sourcify v2 record](https://sourcify.dev/server/v2/contract/1/0x6561272e3ebc9f2e07cdb12b7c474db9132b977a?fields=all)
returned HTTP 200 with `match=match` and `runtimeMatch=match`, including source
for `StakedTrUSD.sol`. The earlier legacy full/partial endpoint failure in
the original onboarding record therefore does not establish a present lack
of verification. Sourcify also resolved both PendleYTLock implementations,
the Upshift multi-asset implementation and the four Morpho v2 vaults. A
Sourcify 404 for another product is only a missing record, not proof that its
source is unverified everywhere.

The [public source repository](https://github.com/Tori-Finance/tori-contracts)
and [audit inventory](https://docs.tori.finance/security/audits) were inspected.
The inventory links reports published by Nethermind and Sherlock. Their
existence is not a guarantee that every later proxy upgrade was audited;
this review does not reproduce an audit-to-deployed-bytecode equivalence proof.
The PDFs themselves were fetched and inspected during the follow-up. The
[Nethermind report](https://github.com/NethermindEth/PublicAuditReports/blob/main/NM_0854_Tori.pdf)
names final commit `4ac4e51`; its audited-file table covers core staking,
minting, bridging, governance and interfaces, but does not list PendleYTLock.
The [Sherlock report](https://github.com/sherlock-protocol/sherlock-reports/blob/main/audits/2026.02.10%20-%20Final%20-%20Tori%20Finance%20Collaborative%20Audit%20Report%201770734349.pdf)
names final commit `eb5074003fa2e344ad8e34f464c7080c4c715ed5` and likewise
does not list the lock in its scope. Nethermind's test-output appendix mentions
lock tests; that is not the same as including the lock in the audited-file
table. Fetching `contracts/core/StakedTrUSD.sol` at those final commits under
the current public Tori repository returned HTTP 404. These two reports alone
therefore do not close the audit-to-current-implementation gap, including for
the native vault. This does not prove that another report or source mapping
does not exist.

The [role matrix](https://docs.tori.finance/security/roles) documents the Safe,
timelock, blacklist and reward/loss roles. The
[backing description](https://docs.tori.finance/solution/backing) and
[risk disclosures](https://docs.tori.finance/resources/risks) describe custody,
strategy, peg and liquidity risks. Public notes keep those economic risks
separate from the technical rating.

## Fee sources and limitations

The [native staking terms](https://docs.tori.finance/products/strusd) disclose
income retention before distribution and no separate staking/unstaking charge.
An undisclosed percentage does not make the accounting mode unknown: rewards
and share-price returns are already net. Keep the gross retained percentage
unknown and do not subtract an invented fee again.

The [RockawayX Upshift product disclosure](https://www.rockawayx.com/insights/rockawayx-tori-ecosystem-vault-investment-thesis-risk-disclosure)
describes launch fee waivers and later targets. Current reads at block
26299349 returned `managementFeePercent()=20`, `performanceFeeRate()=100000`,
`withdrawalFee()=0` and `instantRedemptionFee()=500`. The maintained reader
uses 10,000 management/withdrawal units and 1,000,000 performance units.
These establish 0.2%, 10%, 0% and 5% respectively; do not substitute the
historical 0.3% management target for the actual configuration.

### Resolved fee evidence

The remaining fee-reader gaps were investigated using verified deployed source
and direct, read-only calls through the supplied Ethereum and Monad providers.
These are dated configuration snapshots, not immutable fee promises. Explorer
source responses were fetched at a paced rate after an initial request burst
hit the explorer's three-requests-per-second limit; it was not retried as a burst.
No credential, private endpoint, local catalogue or production state was written.

**Royco senior and junior.** Both tranche proxies return kernel
`0x3c40cb4354582005e55370b7e3a1b190dee8eb0b`, whose `ACCOUNTANT()` is
`0xb8d9b04e43d18db437a4f717f1bd72da07c50b0e`. At Ethereum block 26299721,
the [accountant's decoded state](https://etherscan.io/address/0xb8d9b04e43d18db437a4f717f1bd72da07c50b0e#readProxyContract)
returned `stProtocolFeeWAD=0`, `jtProtocolFeeWAD=0` and
`yieldShareProtocolFeeWAD=0` (fractional denominator `10**18`). Non-zero
protocol fees would mint protocol shares. These three zero settings do not
remove yield transferred between the tranches or Tori's retained income share.
They do not establish that every economic cost is zero.

The explorer verified implementations were junior
`0xce3cd2b04d1eb7be689cb389e0eb90bc2d36c0b1`, senior
`0x91d2b422e80ed52d38f7258aa868f60074e5a9b3`, kernel
`0x05c3a164141cd21826c069a780d332fd5bdca685` and accountant
`0x8c14cc70b811b149aad9ce8a61583eec81b6300e`.
Royco authority was `0x7cc6fb28ec7b5e7afc3cb3986141797ffc27253c`, with verified
RoycoFactory/AccessManager implementation
`0x34db2f4215e55ec8e2c3de0a826935ebf158be77`. These facts establish source
availability and the governance dependency, not an independent security audit.

**Pendle locks.** Live EIP-1967 storage at block 26299714 matched verified
implementations `0x40122cda5bcb3062a8e9d89a948141140e1b3cfc` for trUSD and
`0x7bd73c92ae6042a16cf76a31d905dc587a5c7142` for strUSD. Their deployed source
contains no separate wrapper management, performance, entry or exit charge.
Both returned `withdrawalsPaused()=true` and `paused()=false`; deposits and
withdrawals therefore have different access restrictions. The trUSD deployment
has no public `claimYield`/`claimPendleYield` entrypoint or per-share yield index.
Cash yield credited to the locked YT accrues to the lock contract, with no onchain
distribution path to holders. Only the administrator can transfer non-YT funds
out through `rescueTokens`. Whether holders receive separate manual distributions
or points outside the contract has not been verified. The strUSD version does
have per-holder yield accounting and claims that remain available during the
withdrawal-only pause, but not an administrator emergency pause. Its administrator
can also reopen withdrawals without waiting for the timelock; that bypass does
not authorise an immediate upgrade. Do not substitute the latest repository
version for either deployment.

The underlying YT assets were `0x1fcdb1e747419bb8d1a029c4d1f83b41e8afe8ce`
and `0xc4bb3ec6588ff14d7676445a40f2716e690a3203`. Both returned factory
`0x3e6eba46abc5ab18ed95f6667d8b2fd4020e4637`. At block 26299746 that
[verified factory](https://etherscan.io/address/0x3e6eba46abc5ab18ed95f6667d8b2fd4020e4637#readContract)
returned `interestFeeRate()=50000000000000000` and
`rewardFeeRate()=50000000000000000`, each divided by `10**18`: 5% on their
respective interest and reward bases. They are not an additive 10% fee on the
same income. A further live read at block 26299800 returned `expiry()=1795651200`
(26 November 2026, 00:00 UTC) and `getRewardTokens()=[]` for both YT assets.
The configured reward rate therefore does not establish that a separate
onchain reward stream currently exists. Points may be handled outside those
reward-token arrays. The [current Pendle fee page](https://docs.pendle.finance/pendle-v2/ProtocolMechanics/Mechanisms/Fees)
also states 5%; an older educational page's 3% figure is not the live setting.

Both locks' upgrade role was held by Tori timelock
`0xa952453e39f77108ee37c1b92de593f1c59a6ef4`. At block 26299746,
`getMinDelay()` was 86400 seconds. Their default administrator Safe,
`0x0c6bbfd2d5666d44bf28580edeec0263692c8316`, returned `getThreshold()=3`.
The administrator transfer delay was also 86400 seconds. These checks verify
specified controls; they do not establish every possible role assignment.
Administrator emergency pausing and token rescue are separate from timelocked
upgrades. YT future-yield rights expire on 26 November 2026; after maturity only
previously earned amounts can remain claimable under each lock's rules, with no
claim to principal.

**Stake DAO's three LP wrappers.** All three point to RewardVault implementation
`0x74d8dd40118b13b210d0a1639141ce4458cae0c0` and `ACCOUNTANT()`
`0x93b4b9bd266ffa8af68e39edfa8cfe2a62011ce0`. At block 26299708, the
[verified accountant](https://etherscan.io/address/0x93b4b9bd266ffa8af68e39edfa8cfe2a62011ce0#readContract)
returned `getProtocolFeePercent()=165000000000000000` and
`getHarvestFeePercent()=250000000000000`, using `10**18` fractional units.
These mean 16.5% on fee-bearing rewards and separately 0.025% harvest
compensation. The vaults returned `POLICY()=0` (CHECKPOINT) at block 26299749.
Source accounting applies the protocol fee to `feeSubjectAmount`, while harvest
compensation has its own reward base. Do not report a universal 16.525% fee on
all LP returns or principal, or use the reward-index `SCALING_FACTOR=10**27`
as the fee denominator.

The shared CurveStrategy was `0xce37598c3b26ecbdf13356f9e1fff2cd36ee602a`.
[Strategy fee terms](https://docs.stakedao.org/strategyfees) describe reward
charges and no strategy entry/exit fee; pool trading fees and slippage remain
separate. The accountant owner
`0xe5d6d047df95c6627326465cb27b64a8b77a8b91` returned Safe `getThreshold()=1`
at block 26299746. Source fee setters are owner-controlled, so those rates can
change within the source's 40% combined protocol-and-harvest rate cap. A
separately returned protocol-controller owner was not identified as
a Safe by this review; a reverted Safe getter is not proof of an EOA.

**Term RockawayX Tori USDC.** The verified strategy delegates to
TokenizedStrategy `0xbb51273d6c746910c7c06fe718f30c936170fed0`.
At block 26299700 the [vault's delegated getter](https://etherscan.io/address/0x6f576e5192a14f259f7fe7347ecf63b255d7f7d1#readProxyContract)
returned `performanceFee()=1000`, divided by `MAX_BPS=10000`: 10% of reported
profit through newly minted shares. The reviewed implementation has no annual
management-fee mechanism. `strategyState()` references the RockawayX Tori
Morpho vault `0x3bd9adae6643ddcddd02746b8b60075e56df9478`, whose 10%
performance fee is a separate underlying layer. Do not add the two percentages
as though they necessarily applied to the same profit base.

**Curvance strUSD.** The [verified BorrowableCToken](https://monadscan.com/address/0xbde156e035e5501378d07399c404b4d1d5c86ae8#code)
is not an unverified generic wrapper. At Monad block 117664073,
`interestFee()=1000` with `BPS=10000`: 10% of borrower interest, collected by
minting protocol shares, reducing lenders' interest income. Deposited strUSD
is lent to borrowers; exits depend on unborrowed liquidity, and unpaid debt or
failed liquidations can cause losses. It is not a 10% fee on native strUSD yield. No separate
annual management, entry or exit charge was found in the reviewed implementation.
The flash-loan charge is a different facility, not an investor withdrawal fee.
Monad reads used recent available state, not an assumed historical archive.

### Remaining limits

The native retained share of gross trading income remains undisclosed in the
published staking terms. Net onchain rewards do not reveal gross offchain
trading income, so the percentage cannot be inferred independently from those
payouts. A numerical figure requires Tori to disclose the retention policy or
reconciled gross-income and distribution records.

Verified source and fee settings are not security certification. Matching each
current implementation to the exact source revision covered by an auditor
remains incomplete for the third-party deployments and later lock versions.
Published audit reports and role documentation alone do not close that gap.
No product-specific technical risk rating is invented to fill it.

The current numerical `FeeData` reader support is a separate limitation from
this evidence review. Royco, generic locks/LP wrappers, Term and Curvance do not
yet expose all these findings through supported scalar fee getters. Their
sourced notes carry the verified figures; unsupported numerical fields remain
unknown. Reward-only fees, interest-only fees and senior-to-junior yield
transfers must not be mapped blindly to a single share-price performance fee.
The existing Accountable and Morpho fee readers are reused. Wrapper costs are
separate from the income retention embedded in strUSD, borrower charges, pool
swap fees, liquidation costs and exit slippage.

## Refresh and verification

Migration `013-tori-vault-risk-and-fees.py` follows 012 and refreshes only persisted
fees for the fixed 17-target inventory. Current risk resolves during export.
Validation and all live reads complete before any row is updated. Missing targets are reported, never
created. Protocols, curators, flags, saved notes and historical stores are
preserved. Current sourced disclosures are appended after incident, Morpho
and abnormal-value checks select the final warning. This also preserves cached
warning explanations and avoids intercepting adapter-generated warnings.
A successful 012 is not edited or rerun to apply this later change.

Manual real-provider preview on 29 October 2026 succeeded for all 17 targets
using supplied `JSON_RPC_ETHEREUM` and `JSON_RPC_MONAD` configurations; no
endpoint or credential is recorded. Ethereum block 26299384 and Monad block
117651535 were read. The local catalogue was loaded read-only; changes were
only in memory and no production operation was performed.

The repeatable check is:

```sh
source .local-test.env && PYTHONPATH="$PWD" RUN_TORI_EXPOSURE_LIVE_TEST=1 timeout 180s poetry run pytest tests/vault/test_tori_exposure_live.py -q --log-cli-level=info
```

The opt-in check also verifies the saved input catalogue remains byte-identical.
Guarded production preview, apply and export-refresh instructions are in the
Tori deployment documentation. Run after merge with a stamped image and quiet
writers. Review the complete pending batch and all missing-target reports;
merging does not execute the migration or publish refreshed exports.

Focused final verification: **230 passed, one skipped** on 29 October 2026,
including all 17 current-provider reads, native fixed-block fork coverage,
fee export and Morpho warning preservation, and guarded migration tests.

After the deployed-source fee follow-up, **31 focused tests passed** across
the disclosure/migration unit cases, metadata exports, net-fee export and
Morpho warning preservation. The earlier broad result is not a claim that
every test was rerun after this copy-only follow-up.

The changed narrative was checked against the description writing style guide.
The independent Claude Opus 5.5 follow-up review approved the final fee and
access disclosures after corrections to lock-holder yield rights, maturity and
Curvance lender risks. This review did not certify contract safety or close
the audit-matching and offchain-income evidence gaps above.
