# ARK Venture Fund (ARKVX) Securitize listing with prices

## Goal

List the tokenised ARK Venture Fund (ARKVX) Class D shares, issued through
Securitize on Ethereum, in the tokenised-fund tracker with daily NAV/share and
TVL history reconstructed from onchain settlement data.

## Onchain facts verified on 2026-09-25

| Item | Value |
| --- | --- |
| Share token | DSToken proxy [`0xDf1c8e71CbDF48af50B36F96AD2eb6F5094Ba72a`](https://etherscan.io/token/0xdf1c8e71cbdf48af50b36f96ad2eb6f5094ba72a), EIP-1967, implementation `DSToken` [`0xD57499B239700DDB99bb5B85596DDAb3745bC24d`](https://etherscan.io/address/0xd57499b239700ddb99bb5b85596ddab3745bc24d#code), 6 decimals |
| Token deployment | Block `25_984_222` (2026-09-15 17:01:23 UTC), deployer `0x0c81357A047581bF078a323821226698d39618b8` |
| Subscription contract | `AsyncFundVault` proxy [`0xeF312D033Ed52E2796ba604FEF12B4a56053B292`](https://etherscan.io/address/0xef312d033ed52e2796ba604fef12b4a56053b292), implementation [`0x23848fc9b4dA2b686358D39403d07256b51A3e9C`](https://etherscan.io/address/0x23848fc9b4da2b686358d39403d07256b51a3e9c#code), version `1.0.0`, verified |
| Subscription asset | USDC (`asset()` and `liquidityToken()`) |
| `navProvider()` | Zero address, so `convertToAssets()` returns `0` and `_checkNavPrice()` is skipped |
| `totalAssets()` | Returns `reserveBalance` (USDC redemption reserve), not fund NAV |
| Supply | 33.279 ARKVX across 7 holders (Blockscout), about USD 2,000 |
| Settled deposit generations | 1–4 (status `Fulfilled` = 4); 5 closed with USD 3,000 pending; 6 open with USD 1,000 |
| Redemption generations | Generation 1 open; none fulfilled yet |

Recorded deposit settlement prices (`getDepositGeneration(id).navPrice`, WAD):

| Generation | Settled (UTC) | `navPrice` | Deposits (USDC) | `navPrice × 0.98` | ARK published NAV, prior business day |
| --- | --- | --- | --- | --- | --- |
| 1 | 2026-09-18 14:40 | 61.7430539064 | 520 | 60.5082 | 60.51 (2026-09-17) |
| 2 | 2026-09-18 14:59 | 61.7466174661 | 502 | 60.5117 | 60.51 (2026-09-17) |
| 3 | 2026-09-22 13:46 | 62.1448863636 | 525 | 60.9020 | 60.90 (2026-09-21) |
| 4 | 2026-09-24 14:29 | 61.7224302451 | 511 | 60.4880 | 60.49 (2026-09-23) |

`AsyncFundVault` has no fee concept: `fulfillDeposits(generationId,
navPriceWAD)` stores the settler's price and issues
`totalPendingDeposits / navPriceWAD` shares. All four settlements satisfy
`round(navPrice × (1 − 0.02), 2) == published NAV of the previous business
day`. The settler therefore grosses the price up by the 2% subscription fee.
The residual differences (for example generation 1 versus 2, same NAV) come
from the settler rounding each generation's share amount to 0.001 ARKVX and
back-computing `navPrice = deposits / shares`
(`520 × 0.98 / 60.51 = 8.4218 → 8.422`, `520 / 8.422 = 61.7431`).

The published NAV series is ARK's own API,
`https://www.ark-funds.com/api/venture/nav-historical-change/1009`, with
daily history from 2022-09-23. It is used here only as a validation source.

## Design decisions

### NAV source: event-sourced settlement timeline

Neither RedStone nor Chronicle publishes an ARKVX feed, and the vault's
`navProvider` is unset. The authoritative onchain value is the NAV struck at
each `fulfillDeposits()` call, emitted as:

```solidity
event DepositGenerationFulfilled(uint256 indexed generationId, uint256 navPrice, uint256 totalDeposits);
event RedemptionGenerationFulfilled(uint256 indexed generationId, uint256 navPrice, uint256 fulfillmentRate, uint256 totalLiquidity);
```

The historical multicall reader issues a static call set per scan, so it
cannot first read `currentDepositGenerationId()` and then call
`getDepositGeneration()` for the latest fulfilled id in the same row. Instead:

1. A new module `eth_defi/tokenised_fund/securitize/settlement.py` streams
   `DepositGenerationFulfilled` and `RedemptionGenerationFulfilled` logs for
   the subscription vault through `configure_hypersync_from_env()` and
   `open_hypersync_stream()`. It never uses `eth_getLogs`.
2. Each deposit event becomes a `SecuritizeSettlementPrice` with the block
   number, generation id, raw WAD `navPrice` and fee-adjusted NAV/share. No
   block timestamps are needed, because lookups are by block number.
3. `SecuritizeVault.settlement_prices` fetches this timeline once per adapter
   instance, up to the current head. `process_result()` runs in the
   scanner's main process, so one Hypersync query covers a whole scan, and
   the scheduler creates new adapters every cycle. A persistent DuckDB
   context store, as used by Rysk, is not justified for a few events a week.
4. The historical reader keeps its single `totalSupply()` multicall and, in
   `process_result()`, uses the latest settlement at or before the row's
   block. Rows before the first settlement get an explicit error and no
   price, like the pre-feed RedStone rows.
5. `SecuritizeVault.fetch_share_price(block)`, used by the live scanner,
   applies the same lookup, so the live and historical paths cannot diverge.

The `navProvider` fallback from the first draft is dropped. Its interface
cannot be exercised against the live contract, and wiring it into only one
path would create a silent jump in the series. The fixed-block test asserts
`navProvider() == 0x0`. If Securitize later configures a provider, that
product needs a reviewed price-source change.

Price source classification is `PriceSource.smart_contract_event`, mapped by
the new `settlement_` prefix in `SECURITIZE_PRICE_SOURCE_PREFIXES`.

Settlement NAV is a step function. Deposits are batched in generations that
settle after NAV is struck (the first four settled on 18, 22 and 24 September
2026), and each settlement carries the prior business day's NAV. Rows between settlements repeat the last struck NAV. The
product notes disclose the one-day lag. Hypersync's index can trail the chain
head by a few blocks, so a very recent settlement may first appear in the next
scan cycle.

### Fee adjustment

The feed stores the subscription fee and NAV precision instead of the reader
hard-coding them:

- Deposit settlements:
  `nav = (navPrice / 10**18 × (1 − subscription_fee)).quantize(0.01, ROUND_HALF_UP)`
  using `Decimal`. Cent rounding removes the settler's 0.001-share rounding
  noise and reproduces the fund's published NAV exactly.
- Redemption settlements are not used for pricing until the first real one
  has been checked against the published NAV. Every fulfilled redemption
  generation found in the timeline logs a `WARNING`, so operators notice
  before the deposit-only series could go stale.
- If a fee-adjusted NAV moves more than 20% from the previous settlement, it
  is still recorded, but a `WARNING` names the generation id and block,
  because a fee-structure change would shift the whole series.

### Registry wiring

- Add `ARKVX_ETHEREUM` to `eth_defi/tokenised_fund/securitize/description.py`
  and `SECURITIZE_PRODUCTS`, with `nav_source="settlement_arkvx_deposit_generation"`.
- `SECURITIZE_SETTLEMENT_FEEDS[(1, token)] = SecuritizeSettlementFeed(chain_id,
  token, vault_address, first_block, subscription_fee, nav_decimals)` in
  `settlement.py`. `first_block` is the generation 1 fulfilment block.
- Extend `has_historical_price()` in `securitize/backfill.py` to accept
  settlement feeds. This also enables the recurring scheduler through
  `_is_price_capable_securitize_product()` in `tokenised_fund/scan.py`.
- Add the settlement branch to `SecuritizeVault.fetch_share_price()` and
  `SecuritizeVaultHistoricalReader.process_result()`. The RedStone and
  fixed-price branches stay unchanged.

### Product fee metadata

Today `SecuritizeVault.get_fee_data()` returns `BROKEN_FEE_DATA`. Add an
optional `fee_data: FeeData | None` field to `SecuritizeProduct` and use it
when a product sets it. Other products keep `BROKEN_FEE_DATA`.

For ARKVX:

- `fee_mode=VaultFeeMode.internalised_skimming`: fund expenses accrue daily in
  NAV, so the reconstructed share price is net of the management fee.
- `management=0.0275`: the contractual management fee from the Class D fee
  table in the 2025-10-28 prospectus. Securitize distributes the Tokenized
  Class, which the SEC application says has the same management fee but its
  own "other expenses". The net expense ratio therefore stays out of
  `FeeData` and is stated as a Class D figure in the notes.
- `performance=0`: the fund has no performance fee or carried interest.
- `deposit=0.02`: the tokenised-route subscription fee. It is deducted before
  pricing, so it is outside the share price. Its sources are secondary press
  and the onchain settlement arithmetic, not an SEC filing.
- `withdraw=0`: the prospectus and SEC application state that no early
  repurchase fee applies.
- No lock-up is exported. Quarterly repurchase offers are expected to cover
  only 5% of shares and can be prorated, so a fixed lock-up period would
  suggest an exit time the fund does not offer.

### Keep the subscription contract out of the vault list

`AsyncFundVault` emits ERC-4626 `Deposit` events and implements
`convertToShares()`, so Hypersync lead discovery could classify
`0xeF312D…B292` as a generic ERC-4626/ERC-7540 vault with a zero share price
and the USDC reserve as TVL. That would duplicate ARKVX with wrong numbers.

Add it to `_BROKEN_VAULT_CONTRACTS` in `eth_defi/vault/risk.py`, with a comment
explaining that it is the ARKVX subscription contract and that the DSToken is
the tracked instrument. The existing documentation on
`BROKEN_VAULT_CONTRACTS` confirms that membership hides an address from
discovery, reports and the historical price reader alike. A unit test pins
membership.

The production vault database cannot be inspected from the development
machine. The rollout step below checks it. If the address is already present,
its rows are removed with an explicit, logged maintenance command rather than
silently.

### Curator, tags and logo

- New curator `ark-invest` in `eth_defi/data/feeds/curators/ark-invest.yaml`,
  following the `add-curator` skill.
- Strategy tag `StrategyTag.venture_funding` in `securitize/tags.py`, with
  dated decision material and sources, matching the BCAP entry.
- Logo extracted and post-processed into
  `eth_defi/data/vaults/{original,formatted}_logos/ark-invest/`.

## Fund description

### Source facts

Primary sources, checked on 2026-09-25:

| Fact | Value | Source |
| --- | --- | --- |
| Registrant | ARK Venture Fund, CIK 1905088, 1940 Act file 811-23778, 1933 Act file 333-262496 | [EDGAR filing index](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=1905088) |
| Prospectus and SAI | Dated 2025-10-28 (486BPOS), supplements 2025-11-18 and 2026-01-23 | [486BPOS](https://www.sec.gov/Archives/edgar/data/1905088/000121390025102648/ea0260971-01_486bpos.htm), [ARK prospectus PDF](https://assets.ark-funds.com/media-12243148-2a3e-4996-9763-260f93905eb9/291216db-b9b8-4a0f-ac01-050805dc2e00/ARK%20Venture%20Fund%20Prospectus%202025%20(1).pdf) |
| Tokenised class relief | Investment Company Act Release No. 36333, 2026-09-21, file 812-16031: permits "a class of tokenized shares traded on one or more alternative trading systems" | [SEC order](https://www.sec.gov/Archives/edgar/data/1905088/999999999726001524/filename1.pdf), [40-APP/A](https://www.sec.gov/Archives/edgar/data/1905088/000121390026086478/ea0301027-01_40appa.htm) |
| Objective | "to seek long-term growth of capital" | Prospectus |
| Strategy | 20%–90% of assets in private companies, the rest in public companies; 74.15% private at 2026-08-31 | Prospectus, [ARK fund page](https://www.ark-funds.com/funds/arkvx) |
| Net assets | USD 1,304 million, all classes, 2026-08-31 | ARK fund page |
| Top holdings, 2026-08-31 | SpaceX 7.54%, Kalshi 5.81%, Ayar Labs 5.65%, OpenAI 5.26%, Stripe 4.16%, Anthropic 3.86% | [ARK holdings CSV](https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_VENTURE_FUND_ARKVX_HOLDINGS.csv) |
| Inception | 2022-09-23 at NAV USD 19.87 | ARK NAV API |
| Class D fees | Management 2.75%, borrowing interest 0.04%, other expenses 0.69% (including 0.15% distribution/servicing), acquired fund fees 0.01%; gross 3.49%; waiver/reimbursement 0.59%; net 2.90%; no sales load | Prospectus fee table |
| Expense cap | Remains in effect until the Board approves termination; waived amounts are not recoupable | Prospectus |
| Tokenized Class | No sales load; same management fee; own "Other Expenses" including gas; whitelisted wallets only; no early withdrawal charge | 40-APP/A |
| Tokenised subscription fee | 2%, deducted before pricing at NAV | Secondary press ([Crypto Briefing](https://cryptobriefing.com/arkvx-onchain-subscriptions-usdc-investors/), [Securities.io](https://www.securities.io/ark-invest-and-securitize-tokenize-ark-venture-fund-on-ethereum/)) and the onchain settlement arithmetic |
| Repurchases | Rule 23c-3 quarterly offers of 5%–25% of shares at NAV, expected to be 5%; pro rata when oversubscribed; offers in March, June, September and December; current deadline 2026-09-30 | Prospectus, [N-23C3A](https://www.sec.gov/Archives/edgar/data/1905088/000121390026096448/ea0304158-01_n23c3a.htm) |
| Valuation | NAV each business day as of NYSE close; the adviser is the Rule 2a-5 valuation designee for private holdings | Prospectus |
| Distributions | Annual, reinvested by default | Prospectus |
| Minimum investment | USD 500 for Class D; press reports the same for the tokenised route | Prospectus, secondary press |
| Custody | The Bank of New York Mellon | Prospectus |
| Distribution | Tokenised through Securitize Markets, LLC, an SEC-registered broker-dealer | ARK fund page disclaimer |

Unresolved, and stated as such in the notes:

- The press calls the tokens Class D shares held one-to-one at BNY Mellon.
  The SEC application describes a separate Tokenized Class, which is not yet
  registered in a prospectus amendment.
- The 2% subscription fee appears in no SEC filing, and its recipient is not
  disclosed.
- ARK's press release says the tokenised shares are available "upon release".
  Onchain settlements since 2026-09-18 show that subscriptions are live.

Constants to add in `description.py`:

```python
ARKVX_FUND_PAGE_URL = "https://www.ark-funds.com/funds/arkvx"
ARKVX_PROSPECTUS_URL = "https://www.sec.gov/Archives/edgar/data/1905088/000121390025102648/ea0260971-01_486bpos.htm"
ARKVX_SEC_ORDER_URL = "https://www.sec.gov/Archives/edgar/data/1905088/999999999726001524/filename1.pdf"
ARKVX_REPURCHASE_OFFER_URL = "https://www.sec.gov/Archives/edgar/data/1905088/000121390026096448/ea0304158-01_n23c3a.htm"
```


### `short_description`

```text
Interval fund investing in private and public disruptive-innovation companies, with daily NAV and quarterly repurchase offers expected at 5% of shares
```

Named holdings are kept out of the short description because they change;
the dated notes list them.

### `description`

```text
Tokenised shares of ARK Venture Fund (ARKVX), a registered closed-end interval fund managed by ARK Investment Management LLC that seeks long-term growth of capital by investing 20%–90% of its assets in private companies and the remainder in public companies linked to disruptive innovation. NAV is struck every business day, and the adviser fair-values the private holdings. The fund's Class D shares carry a 2.75% management fee and 2.90% net annual expenses under a Board-terminable expense cap; the tokenised class shares the management fee but may have its own other expenses. Liquidity is limited to quarterly repurchase offers of 5%–25% of shares, expected to be 5%.
```

### `notes`

```markdown
ARK Venture Fund (ARKVX), tokenised on Ethereum through Securitize.

- **Curator:** ARK Investment Management LLC (ARK Invest) manages the fund. Securitize Markets, LLC, an SEC-registered broker-dealer, distributes the tokenised shares, and Securitize provides the tokenisation and investor-onboarding infrastructure. The Bank of New York Mellon is the fund's custodian.
- **Vault strategy:** The fund's objective is "to seek long-term growth of capital". It invests 20%–90% of its assets in private companies and the remainder in public companies aligned with disruptive innovation, such as artificial intelligence, space, robotics, energy storage, fintech and genomics. On 2026-08-31 it held 74.15% in private companies and had USD 1.30 billion of net assets across all share classes. Its largest positions were SpaceX (7.54%), Kalshi (5.81%), Ayar Labs (5.65%), OpenAI (5.26%), Stripe (4.16%) and Anthropic (3.86%).
- **Prospectus and regulatory status:** ARKVX is a closed-end interval fund registered under the Investment Company Act of 1940 (file 811-23778). Its current [prospectus and statement of additional information]({ARKVX_PROSPECTUS_URL}) are dated 2025-10-28. On 2026-09-21 the SEC granted [amended exemptive relief]({ARKVX_SEC_ORDER_URL}) (Release No. 36333) that permits a tokenised share class. The press describes the tokens as Class D shares held one-to-one at BNY Mellon, whereas the SEC application describes a separate Tokenized Class that is not yet registered in a prospectus amendment. Read the prospectus before investing.
- **Fees:** The prospectus fee table for Class D shows a 2.75% management fee, 0.04% interest on borrowed funds, 0.69% other expenses (including a 0.15% distribution and servicing fee) and 0.01% acquired fund fees: 3.49% gross annual expenses. ARK waives or reimburses 0.59%, giving **2.90% net annual expenses for Class D**. The expense cap stays in place until the fund's Board approves its termination, and ARK cannot recoup waived amounts. According to the SEC application, the tokenised class has the same management fee and no sales load, but may bear its own other expenses, including blockchain gas costs, so its total expense ratio may differ. There is no performance fee or early repurchase fee. Press coverage of the Securitize route reports a **2% subscription fee**, deducted before units are priced at NAV; it is not in the SEC filings, but onchain settlement prices are consistent with it.
- **Fund value promise:** The fund does not promise a stable or guaranteed share value. NAV is calculated each business day as of the NYSE close, and ARK, as the Rule 2a-5 valuation designee, fair-values the private holdings. NAV can therefore differ from the price at which those holdings could be sold, and can move sharply when private companies are revalued. The prospectus states that investors "should invest in the Fund only if they can sustain a complete loss". Shares are not bank deposits and are not FDIC-insured or bank-guaranteed.
- **Liquidity and redemptions:** The fund's shares are not listed on an exchange, and the prospectus says no secondary market is expected. The SEC relief permits tokenised shares to trade on alternative trading systems, but ARK's tokenisation announcement still states that no secondary market is expected to develop. The fund's main liquidity route is its quarterly Rule 23c-3 repurchase offers, made in March, June, September and December, for 5%–25% of outstanding shares at NAV; the fund expects to offer 5%. When tenders exceed the offer, repurchases are prorated, so a holder may not be able to sell all of their shares in a given quarter. The current offer's deadline is 2026-09-30 ([notice]({ARKVX_REPURCHASE_OFFER_URL})).
- **Distributions:** The fund intends to make annual distributions, reinvested in shares unless the holder opts out. How the tokenised class receives distributions is not yet documented.
- **Token structure and eligibility:** ARKVX tokens are Securitize DSTokens. Only investors who have passed identity and eligibility checks can subscribe, redeem or receive transfers, and only into whitelisted wallets. Subscriptions are paid in USDC through Securitize's ERC-7540-style `AsyncFundVault`. They are batched into generations that settle after NAV is struck; the first settlements came one to two business days apart. The prospectus sets a USD 500 minimum investment for Class D, and press coverage reports the same minimum for the tokenised route.
- **Price data in this listing:** The share price is rebuilt from onchain `DepositGenerationFulfilled` settlement events. Each settlement records a USDC price that includes the 2% subscription fee, so NAV/share = settlement price × 0.98, rounded to cents. This matches ARK's published NAV for the business day before each settlement. The price updates only when deposits settle, so it lags the fund's own NAV by at least one business day and stays at the last settled value until deposits settle again. TVL covers onchain tokenised shares only, not the whole fund.
- **Fund page:** [ARK Venture Fund]({ARKVX_FUND_PAGE_URL}).
```

The product uses `curator_slug="ark-invest"` and `manager_name="ARK Invest"`.

## Tests

1. Offline unit tests in `tests/securitize/test_securitize_settlement.py`:
   - fee adjustment and cent rounding for all four settlements, against the
     published NAVs as fixed constants
     (`61.7430539064 → 60.51`, `61.7466174661 → 60.51`,
     `62.1448863636 → 60.90`, `61.7224302451 → 60.49`);
   - `ROUND_HALF_UP` on an exact half-cent value;
   - latest settlement at or before a block, including exact-block equality,
     and `None` before the first settlement;
   - the historical reader's error row before the first settlement, and the
     priced row after it, using an injected timeline and a synthetic
     `totalSupply` result;
   - the redemption warning and the >20% jump warning (`caplog`);
   - `has_historical_price()` true for ARKVX;
   - `FeeData`, no lock-up, price source and notes links for ARKVX;
   - an unavailable or empty settlement timeline aborts the scan instead of
     writing unpriced rows;
   - the subscription contract is in `BROKEN_VAULT_CONTRACTS`.
2. Extend the registry, curator and strategy-tag tests with ARKVX.
3. Fixed-block archive test, with no Anvil. The shared
   `ETHEREUM_MIDNIGHT_BLOCK` (`25_598_869`) predates the ARKVX deployment, and
   the `anvil_fork_pool` docstring discourages one-off fork blocks. Read-only
   archive calls through `JSON_RPC_ETHEREUM` at `ARKVX_TEST_BLOCK`, after the
   generation 4 settlement, are deterministic and need no Anvil cache. The test
   asserts supply `33.279`, generation 1–4 `getDepositGeneration()` prices and
   status, and `navProvider() == 0x0`. It also checks
   `SecuritizeVault.fetch_share_price(ARKVX_TEST_BLOCK) == 60.49` and the
   historical reader row, with the timeline injected from those state reads.
4. Live Hypersync integration test, guarded by `HYPERSYNC_API_KEY`, asserting
   that the settlement timeline contains generations 1–4 with the values above.
   It covers one address from block `25_984_222`, so it is fast and needs no
   `skip_hypersync_scan_on_ci` marker. The result goes in a PR comment, as
   `CLAUDE.md` requires.
5. Backfill dry run with isolated `VAULT_DB_PATH`, `UNCLEANED_PRICE_DATABASE`,
   `CLEANED_PRICE_DATABASE` and `READER_STATE_DATABASE`, using
   `SECURITIZE_PRODUCTS=0xdf1c…a72a`.

## Production rollout

1. Inspect `docker-compose.yml` on the production host.
2. Check whether the production vault database already contains
   `0xef312d033ed52e2796ba604fef12b4a56053b292`. If it does, remove that row
   and its price rows with an explicit, logged maintenance step before
   deploying.
3. Dry run in a one-shot container:
   `DRY_RUN=true PROTOCOLS=securitize SECURITIZE_PRODUCTS=0xdf1c8e71cbdf48af50b36f96ad2eb6f5094ba72a scripts/backfill-tokenised-funds.py`.
4. Real run with the same selector. It upserts the ARKVX metadata row and
   rewrites only ARKVX price rows. The scan starts at the DSToken deployment
   block `25_984_222`. Rows before the generation 1 settlement carry errors
   and no price; priced rows begin at the generation 1 fulfilment block.
   Other products, reader states and watermarks are preserved.
5. Confirm that the next looped scanner cycle appends ARKVX rows under the
   `Securitize` scheduler item.

## Risks and open questions

- The fee gross-up is inferred from settlement arithmetic, not from a contract
  or filing. It matches ARK's published NAV for all four settled generations.
  The >20% jump warning catches gross changes, but a small fee change would
  go unnoticed. Recheck several settlements against ARK's fund page during
  periodic curator maintenance. An automated check is not added, because
  ARK's NAV API sits behind a Cloudflare challenge.
- ARK or Securitize may change the subscription fee or configure a
  `navProvider`; either needs a reviewed product change.
- Onchain TVL is about USD 2,000. The site's minimum-TVL filters may hide the
  listing until onchain subscriptions grow.
- ARK's press release says "upon release", while secondary press reports the
  product as live. Onchain settlements since 2026-09-18 show it is live.
- If Class D and the Tokenized Class are separated in a future prospectus
  amendment, the description and fee metadata need an update.

## Review decisions

The Kimi K3 plan review (2026-09-25) was applied as follows:

- Accepted: the secondary-market and ATS wording, attributing fees to Class D,
  "expected 5%" repurchases, sourcing the USD 500 minimum, the redemption
  warning, `Decimal` `ROUND_HALF_UP`, the backfill range statement,
  "settle after NAV is struck" instead of a daily cadence, no holdings in the short description, a
  lock-up comment, and tests for the warnings, the exclusion and the live
  `fetch_share_price()` path.
- Resolved by removal: the `navProvider` preference is dropped, so the
  live/historical asymmetry disappears.
- Resolved by design: the settlement timeline is cached per adapter instance
  and fetched once per scan. The duplicate-listing guard uses
  `BROKEN_VAULT_CONTRACTS`, whose documented semantics cover discovery,
  reports and history. The production database check is a rollout step.
- Not adopted, as overengineering: a persistent timeline store, a scheduled
  CI validation against ARK's NAV API, and restamping rows to the NAV date.

## Documentation and changelog

- Add `eth_defi.tokenised_fund.securitize.settlement` to the Securitize API
  stub under `docs/source/api`.
- Add one `CHANGELOG.md` line: ARK Venture Fund (ARKVX) Securitize listing with
  settlement-based NAV history (2026-09-25).

## Codex review decisions

The Codex (`gpt-6-sol`) review of PR #1599 on 2026-09-25 found four issues,
all verified and fixed:

- An unavailable Hypersync client raised `SecuritizeSettlementError`, which the
  reader turned into unpriced rows. The scheduler restarts at the last priced
  block, so this could erode priced history cycle after cycle. Unavailability
  now raises `RuntimeError`, and an empty timeline after the known first
  settlement is an error, so the scan aborts and the atomic Parquet rewrite
  keeps existing rows.
- A timeline fetched before Hypersync indexed a new settlement would write
  stale prices that the scheduler never revisited. The `securitize` scheduler
  item now uses `refetch_tail=True`, replaying the latest seven stored samples.
- The exported 90-day lock-up suggested an exit time the fund does not offer.
  It is removed.
- The notes described the staleness as a one-business-day lag. They now say
  the price stays at the last settled value until deposits settle again.


## Hypersync as a hard requirement

Settlement NAV has no RPC or archive-state fallback:

- `fetch_settlement_prices()` asserts that a Hypersync client is supplied.
  `SecuritizeVault.settlement_prices` raises `RuntimeError` when
  `configure_hypersync_from_env()` returns no client, and an empty timeline
  after the known first settlement is an error. In every case the scan aborts
  and the atomic Parquet rewrite keeps existing rows.
- The repository's Hypersync client disables internal retries, so a
  rate-limited settlement fetch is retried up to six times. Each wait lasts
  until the server-stated `resets_in` plus a five-second margin.
- On 2026-09-25 the shared `HYPERSYNC_API_KEY` was saturated by another
  consumer: 30,000 cost units per 60-second window, 1,000 per query, with
  `x-ratelimit-remaining: 0` for most of each window. Fetches succeeded only
  intermittently. Production needs a dedicated or higher-tier key for this
  scheduler item to be reliable.
