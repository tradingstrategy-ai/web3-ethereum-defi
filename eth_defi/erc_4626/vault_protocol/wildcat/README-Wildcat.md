# Wildcat lending markets

This package indexes [Wildcat](https://app.wildcat.finance/lender) version 2
lending markets on Ethereum as read-only vaults. Wildcat markets are not
ERC-4626 vaults.

## What a Wildcat market is

A Wildcat market lends one asset, usually USDC or USDT, to one borrower. The
borrower is a KYB-verified company, such as a market maker or trading firm,
and the loan is mostly undercollateralised. The borrower sets the interest
rate, the deposit cap, the reserve ratio, the withdrawal batch duration and
the access rules. The rate is not utilisation-based, but the borrower can
change it: increases apply at once, and a cut of more than 25% within two
weeks temporarily raises the reserve the borrower must return to the market. See the
[protocol introduction](https://docs.wildcat.finance/overview/introduction).

The market contract is the lender's ERC-20 token. Balances rebase like Aave
aTokens, see the
[scale factor documentation](https://github.com/wildcat-finance/v2-protocol/blob/main/docs/Scale%20Factor.md):

```text
balanceOf = scaledBalanceOf * scaleFactor / 1e27
```

The market's view functions accrue interest to `block.timestamp`, so
historical `eth_call` reads return accrued values.

## Value mapping

All amounts come from `currentState()` and `totalAssets()` at the same block.
`currentState()` returns the version 2 `MarketState` struct, decoded by
[`WildcatMarketState`](state.py).

| Common field | Wildcat source |
|---|---|
| Share price | `scaleFactor / 1e27`, in units of `asset()` |
| Total supply | `scaledTotalSupply` |
| Total assets (TVL) | `totalSupply()`, the amount owed to lenders |
| Max deposit | `0` when closed, otherwise `maxTotalSupply − totalSupply` |
| Available liquidity | `max(0, totalAssets − unclaimed − fees − pending)` |
| Utilisation | `(totalSupply − (totalAssets − unclaimed − fees)) / totalSupply` |

Where `unclaimed` is `normalizedUnclaimedWithdrawals`, `fees` is
`accruedProtocolFees` and `pending` is `scaledPendingWithdrawals` converted
with the scale factor.

The market's `totalAssets()` is its idle cash, not lender value. Paid but
unclaimed withdrawals and accrued protocol fees sit in that cash but belong to
withdrawn lenders and the protocol, so they are deducted before comparing cash
with lender supply. Utilisation is therefore the lent-out share of lender
supply; it equals the version 1 `outstandingDebt() / totalSupply()`. Pending
withdrawals are deducted only from available liquidity: older unpaid batches
remain in `scaledPendingWithdrawals`, and deducting them in utilisation would
pin it at 100%. These formulas are our derivation, not protocol definitions.

## Strategy tags

Every market gets `lending` and `unsecured_credit`. Lenders are unsecured
creditors: the borrower pledges no collateral that could be seized or sold,
so repayment depends on the borrower's ability and willingness to pay, and in
an insolvency they rank behind secured creditors. Address-specific tags can be
added to `tags.py` with the `categorise-vault-strategy` skill.

## Read cadence

Wildcat markets are read at most once a day, in the live scanner and the
backfill (`WILDCAT_READ_INTERVAL`). Lending markets do not move fast, so daily
reads avoid unnecessary churn. `WildcatMarketReaderState` slows the shared
adaptive cadence's hourly reads to daily and keeps its weekly reads for tiny,
peaked and faded markets.

## Fees

Lenders pay no fees. The borrower pays the protocol fee, a share of the base
interest rate capped at 10%, on top of the lenders' rate, see
[protocol usage fees](https://docs.wildcat.finance/using-wildcat/protocol-usage-fees).
The fee mode is `feeless` and the share price is net.

## Withdrawals and closed markets

A withdrawal joins the current batch. When the batch expires, lenders can
claim the amount the market's cash covers, pro rata; an unfunded remainder
stays queued until the borrower repays more. The exported withdrawal period is
`withdrawalBatchDuration()`, the contract-enforced wait, which by the shared
`WithdrawalPeriod` contract excludes liquidity shortages.
Fixed-term markets block withdrawals until `fixedTermEndTime`, reported through
`fetch_redemption_closed_reason()` and `fetch_redemption_next_open()` from the
current hooks state.

A closed market reports closed deposits through `fetch_deposit_open()` and
`fetch_deposit_closed_reason()`, and historical rows carry `max_deposit = 0`.
Version 2 markets use zero-length withdrawal batches after closure, so lenders
can still withdraw.

## Deposit permission

Admission is read from the market's hooks instance, whose address is the top
160 bits of `market.hooks()`. `getHookedMarket(market).depositRequiresAccess`
decides `whitelisted` or `permissionless`. The `useOnDeposit` hook bit only
means the hook runs; fixed-term markets enable it for minimum deposits. A
market whose role provider admits anyone is still labelled whitelisted, the
cautious side of the admission rules. An unrecognised hooks template records
`unknown`.

## Delinquency and the `delinquent_markets` flag

The scale factor keeps rising when a borrower stops paying, and the penalty
rate makes it rise faster, so the price curve cannot show a default. A market
whose `timeDelinquent` exceeds `delinquencyGracePeriod()` gets the
descriptive `VaultFlag.delinquent_markets` flag and a note. The penalty
applies in exactly this state, which can last for a while after the borrower
cures the delinquency, see
[delinquency](https://docs.wildcat.finance/using-wildcat/delinquency). The
flag refreshes with ordinary metadata scans, currently every seven days. It is
outside `BAD_FLAGS`, so markets stay visible, and the monthly vault report
leaves flagged markets out of its listings.

## Offchain metadata

[`offchain_metadata.py`](offchain_metadata.py) reads two undocumented,
unauthenticated routes of the Wildcat app, found in its public source
[`wildcat-app-v2`](https://github.com/wildcat-finance/wildcat-app-v2):
`/api/market-summary/{market}` and `/api/profiles/{borrower}`. The vault
description is the market description, or the borrower profile description
when the market has none, kept as the borrower's Markdown. The short
description is the first sentence of the first line that contains a sentence
boundary, so heading, date and link lines are skipped and wrapped lines are
joined. The manager name is the borrower's KYB-verified legal name. Descriptions and aliases are written by borrowers and are
[not verified by Wildcat](https://docs.wildcat.finance/using-wildcat/how-borrowers-are-onboarded).
Entries are cached per chain under `~/.tradingstrategy/cache/wildcat` for two
days, and a failed request keeps the previous value.

## Test markets

Markets of a reviewed Wildcat test borrower (`WILDCAT_TEST_BORROWERS`), such
as Wildcat Labs, whose profile says its markets are "for the explicit purpose
of testing-in-prod", get the `unofficial` bad flag and the `TEST_VAULT` note,
so they are hidden like other test vaults.

## Discovery and onboarding

Every market is registered in the `WildcatArchController`
(`0xfEB516d9D946dD487A9346F6fee11f40C6945eE4` on Ethereum), which emits
`MarketAdded(address indexed controller, address market)`. Version 2 markets
are registered by the `HooksFactory`; registry events from version 1
controllers are skipped. A decoded market becomes a direct lead and skips the
generic feature probe, as Enzyme Blue deployments do.

The lead scan resumes from its stored end block, so existing markets are
onboarded by two numbered migrations that run in one batch. The reviewed
inventory and helpers live in
[`repairs/wildcat.py`](../../../vault_protocols/repairs/wildcat.py).

- `063-wildcat-market-leads.py` checks that the registry at the stored lead
  cursor has no unreviewed market, then seeds a direct lead and a scanner
  metadata row for each of the 82 markets. Rows are built with
  `create_vault_scan_record()` at the lead cursor, using temporary token and
  Wildcat app caches.
- `064-wildcat-price-backfill.py` backfills daily prices with
  `run_price_backfill()` from each market's `MarketAdded` block to the
  Ethereum price cursor plus one. The scanner starts new markets at that
  cursor, so the two meet without overlap. The end comes from the stored
  reader state rather than a frozen constant, because new markets have no
  collected price row to end at.

Preview both on a copy of local pipeline data with a block limit:

```shell
source .local-test.env && PYTHONPATH="$PWD" poetry run python scripts/erc-4626/preview-wildcat-migrations.py
```

The script copies `~/.tradingstrategy/vaults` (override with `SNAPSHOT_DIR`)
into a temporary directory and previews only these two migrations with
`MIGRATION_BACKFILL_BLOCKS=20000`. Production applies them with the ordinary
`vault-migrate run` while the looped scanner is stopped.

## Out of scope

- Deposit, withdrawal and guard support for the trade executor.
- Version 1 markets: seven dead or test markets holding about $61 in
  November 2026.
- Plasma, which can be added with its registry addresses once its RPC is
  confirmed to serve historical state.
- Wildcat's `Wildcat4626Wrapper` contracts, which the generic ERC-4626
  scanner reads; reviewed wrappers can be labelled `wrapped_asset` through
  `VAULT_DESCRIPTIVE_FLAGS`.
