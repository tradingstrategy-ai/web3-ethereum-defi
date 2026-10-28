# Frankencoin shares integration research

Researched and implemented on 2026-09-30. The tracking adapter is
[`FrankencoinSharesVault`](../../eth_defi/erc_4626/vault_protocol/frankencoin/shares.py);
the [targeted backfill](../../scripts/erc-4626/backfill-frankencoin-shares.py)
defaults to a metadata dry run. Production state has not been changed.

The [linked announcement](https://www.linkedin.com/feed/update/urn:li:activity:7510965794470682624/)
introduces Frankencoin Shares (FCS). Our existing Frankencoin integration covers
svZCHF savings vaults. FCS is a separate equity and governance product: profits
increase its shared backing capital, while losses reduce it. Each FCS wraps one
FPS, but one FCS is not worth one ZCHF.

## Integration scope

The Ethereum FCS adapter sits alongside the svZCHF savings adapter. It uses
ERC-4626 accounting with ZCHF as its asset and the existing historical reader,
with separate classification, fees, valuation and redemption-state metadata.
It supports tracking; public transactions remain uncertified until a guarded
lifecycle test covers conditional exits and minimum-output protection.

## Contracts

The [official deployment configuration](https://github.com/Frankencoin-ZCHF/Frankencoin/blob/8b4c4ab67bb361b91d58c474b87f4608fc4c0566/exports/address.config.ts)
lists the addresses below. The FCS asset, underlying FPS and reserve pointers
were also confirmed through Ethereum RPC.

| Contract | Ethereum address | Tracker role |
| --- | --- | --- |
| FCS | [0xDb861830D9Ae2d1fCF99fA0cfd3973de382B0B5b](https://etherscan.io/address/0xDb861830D9Ae2d1fCF99fA0cfd3973de382B0B5b) | Vault and share token; ERC-4626 accounting, FPS wrapping, governance and redemption gates |
| Equity / FPS | [0x1bA26788dfDe592fec8bcB0Eaff472a42BE341B2](https://etherscan.io/address/0x1bA26788dfDe592fec8bcB0Eaff472a42BE341B2) | Holds the reserve, issues the underlying FPS and defines its bonding curve |
| Frankencoin / ZCHF | [0xB58E61C3098d85632Df34EecfB899A1Ed80921cB](https://etherscan.io/address/0xB58E61C3098d85632Df34EecfB899A1Ed80921cB) | Denomination token; exposes net equity and reserved minter capital |
| Uniswap v3 ZCHF/FCS pool | [0xcD795ae77A7318A396D6645bAf0562d8a0312323](https://etherscan.io/address/0xcD795ae77A7318A396D6645bAf0562d8a0312323) | Optional market-price and secondary-market liquidity source; verified pair and 0.3% pool fee |
| WFPS legacy wrapper | [0x5052D3Cc819f53116641e89b96Ff4cD1EE80B182](https://etherscan.io/address/0x5052D3Cc819f53116641e89b96Ff4cD1EE80B182) | Legacy migration route; unnecessary for core FCS accounting |

The [published FCS source](https://github.com/Frankencoin-ZCHF/Frankencoin/blob/8b4c4ab67bb361b91d58c474b87f4608fc4c0566/contracts/equity/shares/FCS.sol)
inherits `FCSMintRedeem` and `AccumulatingVotesToken`; these are components of
the deployed FCS contract, not additional vault addresses.

Etherscan's authenticated source API reports `ContractName=FCS`, Solidity
`0.8.24`, and `Proxy=0`. Its verified `FCS.sol` and `FCSMintRedeem.sol` contents
matched the pinned GitHub checkout above. The deployment is block **25,852,506**,
transaction
[0x5b7df19db201535b1a10f3e30a9d343cf2f78fcc645d059b6f5f3b84aeee70ec](https://etherscan.io/tx/0x5b7df19db201535b1a10f3e30a9d343cf2f78fcc645d059b6f5f3b84aeee70ec).

GovernanceFactory, MainnetVotes, InterestGovernance, MinterGovernance,
CCIPGovernance and remote BridgedVotes modules form the wider governance
system. They are context for governance risk, not necessary inputs to basic
FCS price and TVL history. Cross-chain voting does not imply a separate FCS
vault on each chain; only the reviewed Ethereum deployment is mapped.

## Valuation and return semantics

The [accounting implementation](https://github.com/Frankencoin-ZCHF/Frankencoin/blob/8b4c4ab67bb361b91d58c474b87f4608fc4c0566/contracts/equity/shares/FCSMintRedeem.sol)
exposes three distinct quantities:

| Tracker field | Read | Interpretation |
| --- | --- | --- |
| Backing TVL | `FCS.totalAssets()` | FCS holders' proportional net equity capital in ZCHF |
| Reference share-price history | `FCS.convertToAssets(one_raw_share)` | Bonding-curve marginal valuation before execution costs |
| Redemption quote | `FCS.previewRedeem(raw_shares)` | Size-specific proceeds, including the curve, underlying fee and extra redemption discount |

For positive equity and FPS supply, let `E = ZCHF.equity()`,
`S = FPS.totalSupply()` and `W = FCS.totalSupply()`, normalised to token units:

```text
FCS backing TVL       = E × W / S
Backing per FCS       = E / S
Reference price/FCS   = 3 × E / S
Reference valuation  = 3 × FCS backing TVL
```

The factor of three comes from `Equity.VALUATION_FACTOR()`. Preserve the
contract's reference price for history, but label its meaning explicitly.
Do not replace backing TVL with reference price multiplied by FCS supply.
The generic reader already reads `totalAssets()` and `convertToAssets()`
independently, so this does not inherently require a Parquet schema change.

`ZCHF.equity()` subtracts minter reserves from the ZCHF held by Equity.
Counting the raw reserve balance as equity TVL would overstate backing.
FPS and FCS share one pool: adding full FPS equity TVL to FCS backing TVL
would count the wrapped fraction twice.

Changes in the curve price reflect profits, losses and capital entering or
leaving the pool. Present historical price returns as equity returns, not a
promised savings rate. The homepage's protocol ROE is a separate statistic;
it must not be copied into FCS APY. Actual Uniswap prices can differ from the
contract reference price. Use a separate reviewed market-price series if
secondary-market performance is required.

## Live snapshot

Manual read-only checks using the configured `JSON_RPC_ETHEREUM` provider,
at block **26,089,593**, timestamp **2026-09-30 10:07:35 UTC**. All accounting
reads below used that same block; FCS and ZCHF both report 18 decimals.

| Observation | Value |
| --- | ---: |
| Total protocol net equity | 3,618,405.590684 ZCHF |
| FCS backing TVL / `totalAssets()` | 1,383,499.536765 ZCHF |
| FCS supply | 3,267.850967 FCS |
| FPS supply | 8,546.739550 FPS |
| FPS held by FCS | 3,267.850967 FPS |
| Backing per FCS | 423.366779 ZCHF |
| Reference price per FCS | 1,270.100336 ZCHF |
| Preview for redeeming one FCS | 1,265.367594 ZCHF |
| `isBinding()` | `false` |
| `FPS.canRedeem(FCS_address)` | `false` |
| FCS share of underlying FPS votes | Approximately 0.46194% |
| Uniswap pool FCS balance | Approximately 233.691360 FCS |
| `maxRedeem(pool_address)` | 0 |
| `maxWithdraw(pool_address)` | 0 |

**Direct ZCHF redemption was unavailable at this snapshot**, despite a
non-zero preview and shares held by the pool. This is time-dependent state.
The pool balance alone does not measure executable DEX liquidity or slippage.

Before this integration, live autodetection returned generic `ERC4626Vault`,
an empty feature set and protocol name `ERC-4626`. Its share-price source was
`smart_contract_state`. The new chain-and-address mapping routes this deployment to
`FrankencoinSharesVault`; the generic interface probes alone did not identify it.

## Entry, exit and fees

The [FCS mechanics documentation](https://docs.frankencoin.com/pool-shares/fcs)
describes ZCHF deposits that invest into FPS and issue FCS, plus direct 1:1
FPS wrapping. Wrapping changes FCS supply without adding capital to the shared
pool. Its `Wrapped` and `Unwrapped` events must remain distinct from ZCHF
`Deposit` and `Withdraw` flows when reporting new capital inflows.

ZCHF redemption requires both:

- FCS controlling **more than two thirds of underlying FPS voting power**.
- The FCS contract itself satisfying the underlying FPS average holding-period
  requirement of at least 90 days.

There is no separate fixed 90-day wait for each FCS investor. A successful
preview does not establish eligibility. When enabled, redemption settles in
one transaction; there is no request/claim queue in these methods.

The underlying curve applies a 0.3% entry fee and a 0.3% redemption fee through
its share-reduction calculation. FCS additionally discounts redemption based
on size and recent redemptions. Recent volume decays over seven days absent
further activity, but a new redemption still has its own size-dependent discount.
`ask()` and `bid()` exclude the underlying fee. These are transaction costs,
not the savings product's optional referral fee.

A `withdraw` can burn at most 10% of total FCS supply in one call. `redeem`
has no equivalent cap, and large redemptions can produce less ZCHF despite
burning more shares. `depositExpected` and `redeemExpected` provide
minimum-output protection. Unwrapping delivers FPS and has a separate
holding-duration condition; it is not ZCHF redemption.

## Repository implementation

- `frankencoin_fcs_like` routes the reviewed Ethereum address to the FCS adapter.
  Protocol and curator are Frankencoin; existing logos and ZCHF support are reused.
- The adapter binds the verified FCS interface once as `vault_contract`, reads
  the FPS pointer and reports the actual voting and holding-period exit gates.
  Technical risk remains unassessed, independently of the savings rating.
- The `protocol_equity`, `lending`, `rwa` and `rwa_lending` classifications are
  maintained by address, with dated evidence in `frankencoin/tags.py`.
- The targeted backfill preserves discovery history, unrelated vaults, chain
  cursors and scheduled reader state. New leads distinguish ZCHF flows from
  FPS wrapping. Historical prices use archive RPC and the existing dense
  Hypersync timestamp cache, with staged outputs and dry run enabled by default.

## Revenue review and additional strategy tags

Reviewed on 2026-09-30 against current official documentation, the contract
sources already pinned above, and a fresh successful HTTP read of the
[official position API](https://api.frankencoin.com/positions/list). Its
[field reference](https://docs.frankencoin.com/api-docs/positions) distinguishes
raw debt amounts from position state flags.

| Flow | How it affects equity | Source |
| --- | --- | --- |
| Borrowing interest/minting fee | Up-front fee on newly minted debt. Newer Position uses the global borrowing rate plus risk premium, applied to the remaining term. Fee ZCHF goes to Equity; the retained minter reserve is accounted separately. | [Position terms](https://docs.frankencoin.com/positions/open), `Position.calculateFee()`, `Frankencoin.mintWithReserve()` |
| Position proposal fee | `MintingHub.openPosition()` collects the 1,000 ZCHF opening fee into equity; clones do not pay a new opening fee. | `MintingHub.OPENING_FEE`, `Frankencoin.collectProfits()` |
| Minting module application fee | The amount forwarded to ZCHF goes into equity. FCS MinterGovernance can retain part of the application payment to refill its enforcement reward pool. | `Frankencoin.suggestMinter()`, `MinterGovernance.suggestMinter()` |
| Liquidation result | Assigned minter reserves are released; sale proceeds settle debt and challenger rewards. Equity receives the net gain or absorbs uncovered losses. Excess sale proceeds can also be allocated partly to the borrower. | [Reserve settlement](https://docs.frankencoin.com/reserve), `MintingHub._finishChallenge()`, `Frankencoin.burnWithoutReserve()` |
| Equity entry/exit costs | Underlying 0.3% entry and nominal share-based exit fee retain backing for remaining holders. The FCS redemption discount is transferred back to FPS Equity. These are holder-flow effects, separate from lending income. | `Equity._calculateShares()`, `Equity.calculateProceeds()`, `FCSMintRedeem._redeem()` |
| Savings expense | Savings interest is paid from equity. Referral payments divide the same gross interest between saver and referrer. | [Savings mechanics](https://docs.frankencoin.com/savings), `AbstractSavings.refresh()`, `Frankencoin.coverLoss()` |

Sources in this table are in the official pinned repository's
`contracts/stablecoin/Frankencoin.sol`, `contracts/minting/Position.sol`,
`contracts/minting/MintingHub.sol`, `contracts/equity/Equity.sol`,
`contracts/equity/shares/MinterGovernance.sol`,
`contracts/equity/shares/FCSMintRedeem.sol` and
`contracts/savings/AbstractSavings.sol`.

The API returned nine non-closed, non-denied positions with positive ZCHF debt
against PAXG or XAUt. Examples include PAXG position
`0x3484c2aaF6Cb7c27AA68c89edCDAc878020A4DA7` with 375,000 ZCHF gross minted
and XAUt position `0x25BD3a5D6EE54f9Ec5536c6fF20C0685d953A70A` with
125,000 ZCHF. Their ZCHF address matches the reviewed Ethereum deployment.
These are dated indexed observations, not an exhaustive portfolio allocation
or a measurement of annual revenue. The
[official collateral catalogue](https://app.frankencoin.com/monitoring/collateral)
also lists these tokens; the [PAXG issuer](https://www.paxos.com/pax-gold)
confirms that PAXG represents physical gold.

The additive classification is therefore:

- `protocol_equity`: the actual FCS/FPS instrument and residual loss exposure.
- `lending`: look-through revenue from collateralised ZCHF borrowing.
- `rwa` and `rwa_lending`: part of that debt is secured by tokenised physical gold.

Collateral stays in borrower positions; FCS holds FPS equity, so this does not
describe a direct gold allocation or direct lender claim. Lending and RWA tags
are deliberately not added to svZCHF savings: savings principal is not lent
out to these borrowers. No FX-trading, arbitrage, AMM, or multistrategy tag is
established merely by CHF denomination, optional borrower trades, a secondary
Uniswap pool, or the existence of several fee types.

Share subscriptions are capital contributions and move the bonding curve;
they are not operating revenue. Secondary-market Uniswap fees belong to LPs.
Historical reference-price appreciation therefore cannot be equated with
borrower revenue or net protocol earnings. A realised revenue breakdown would
need period-specific flow attribution and version-aware expense accounting;
this review establishes mechanisms and exposure, not their revenue weights.

## Validation

Use the shared fixed-block Anvil fork pool, choosing an archive block at or
after FCS deployment. Verify address classification, token scales, backing TVL,
the factor-of-three valuation relationship, quotes while redemption is disabled,
and separation from savings TVL. Test FPS wrapping as migration rather than
fresh ZCHF capital. Any public transaction manager also needs guarded deposit
and redemption lifecycle tests with minimum-output and eligibility checks.

Research used real Ethereum RPC reads and an authenticated Etherscan
source/deployment lookup. Implementation tests cover fixed-state accounting,
classification, protocol curation, strategy tags, fees and conditional exits.
Migration tests cover observed event counters, separate wrapping activity,
unrelated rows, discovery cursors, untouched reader state, dry runs and failed
price preparation. A real metadata dry run exercises Ethereum and authenticated
Hypersync. A bounded manual price dry run on 2026-09-30 also passed: blocks
26,088,000–26,089,594 (end exclusive), six hourly observations, two retained
price-change rows, zero errors. It used configured Ethereum Goldsky/dRPC and
authenticated Ethereum Hypersync, with a separately prepopulated temporary
dense timestamp cache. This was a manual run, separate from CI:

```shell
source .local-test.env && PYTHONPATH="$PWD" \
  VAULT_DB_PATH=/tmp/frankencoin-shares-bounded-real-test/metadata.pickle \
  PARQUET_PATH=/tmp/frankencoin-shares-bounded-real-test/prices.parquet \
  TIMESTAMP_CACHE=/tmp/frankencoin-shares-bounded-real-test/timestamps \
  START_BLOCK=26088000 END_BLOCK=26089594 MAX_WORKERS=2 \
  DRY_RUN=true FRANKENCOIN_SHARES_SCAN_PRICES=true \
  poetry run python scripts/erc-4626/backfill-frankencoin-shares.py
```

A second bounded dry run seeded five existing price rows: two target rows,
one unrelated Ethereum vault, one same-address Base row and one FCS row before
the scan range. It replaced only the two target rows and retained the other
three unchanged. The fixture's scheduled reader-state file also survived.
These validation runs used isolated temporary files and did not change production state.

## Audit and further references

ChainSecurity completed its [FPS2 audit](https://www.chainsecurity.com/security-audit/frankencoin-fps2/)
on 2026-07-14. FPS2 is the pre-rebrand name for FCS. Its
[full report](https://reports.chainsecurity.com/Frankencoin/ChainSecurity_Frankencoin_FPS2_Audit.pdf)
covers governance, wrapping and ERC-4626 behaviour. The protocol documents
remaining preview and rounding deviations; a transaction adapter must account
for them. Matching current verified source to current GitHub does not establish
that every post-audit change was audited.

The [FCS homepage](https://frankencoin.com/fcs) links the official trading pool.
The [migration guide](https://docs.frankencoin.com/pool-shares/fcs-migration)
explains FPS and WFPS routes. The existing
[savings adapter](../../eth_defi/erc_4626/vault_protocol/frankencoin/vault.py)
and [savings documentation](../source/vaults/frankencoin/index.rst) show the
support already present in this repository.
