# Wildcat markets as read-only vaults

Plan date: 2026-11-10. Status: implemented on 2026-11-10, see
implementation notes at the end.

Principle: reuse the existing scanner, reader, flag and migration machinery.
Do not add tracking, caches or extra reads the value mapping does not need.

## Goal

Index [Wildcat Finance](https://app.wildcat.finance/lender) V2 lending
markets on Ethereum in the common vault pipeline: metadata, hourly share
price, TVL, available liquidity, utilisation, withdrawal period, deposit
permission and strategy tags. The adapter is read-only.

## Background facts

These facts were collected from the protocol sources and checked onchain on
Ethereum mainnet on 2026-11-10. Re-verify any address before freezing it in
code.

- Sources: [v2-protocol](https://github.com/wildcat-finance/v2-protocol)
  (`f5a2614`), [subgraph](https://github.com/wildcat-finance/subgraph),
  [wildcat.ts SDK constants](https://github.com/wildcat-finance/wildcat.ts/blob/main/src/constants.ts),
  [contract deployments](https://github.com/wildcat-finance/wildcat-docs/blob/main/technical-overview/contract-deployments.md).
- A market is a single-borrower, mostly unsecured, credit pool. The market
  contract is the lender's ERC-20 token. Balances rebase like Aave aTokens:
  `balanceOf = scaledBalanceOf * scaleFactor / 1e27`
  ([Scale Factor.md](https://github.com/wildcat-finance/v2-protocol/blob/main/docs/Scale%20Factor.md)).
- `scaleFactor()`, `totalSupply()`, `scaledTotalSupply()` and
  `currentState()` accrue interest to `block.timestamp` in view calls.
  Historical `eth_call` works: `scaleFactor()` differs between head and
  head − 50,000 blocks on a mainnet market.
- `totalAssets()` is `asset.balanceOf(market)`: idle cash only, not lender
  value. `totalSupply()` is the normalised amount owed to lenders.
- Lender rate is `annualInterestBips()`, plus `delinquencyFeeBips()` while
  the penalty applies. The protocol fee (`protocolFeeBips`) is paid by the
  borrower on top of lender interest
  ([protocol usage fees](https://github.com/wildcat-finance/wildcat-docs/blob/main/using-wildcat/protocol-usage-fees.md)).
- Withdrawals are queued into batches of `withdrawalBatchDuration()` seconds,
  paid pro rata at expiry, with unpaid batches kept in a FIFO. Fixed-term
  hooks block withdrawals until `fixedTermEndTime`, which can be reduced
  after deployment
  ([core behaviour](https://github.com/wildcat-finance/wildcat-docs/blob/main/technical-overview/security-developer-dives/core-behaviour.md)).
- A closed market (`isClosed()`) has its debt repaid and interest set to
  zero, but lenders can still hold claims. New withdrawal batches on a closed
  V2 market use a zero duration.
- `isDelinquent` in `currentState()` is the stored flag from the last state
  write. `timeDelinquent` is accrued to `block.timestamp`, and the penalty
  rate applies while `timeDelinquent > delinquencyGracePeriod()`, including
  for a while after the borrower has cured the delinquency.
- Access: V2 markets use access-control hooks (OpenTermHooks,
  FixedTermHooks). The `useOnDeposit` bit in `HooksConfig` only means the
  hook is called. The actual policy is `HookedMarket.depositRequiresAccess`,
  read with `getHookedMarket(market)` on the market's hooks instance, whose
  address is the top 160 bits of the packed `HooksConfig` returned by
  `market.hooks()`
  ([Access Control Hooks.md](https://github.com/wildcat-finance/v2-protocol/blob/main/docs/hooks/templates/Access%20Control%20Hooks.md)).
  Both templates' `HookedMarket` structs start with the same three static
  fields: `isHooked`, `transferRequiresAccess`, `depositRequiresAccess`.
  FixedTermHooks adds `fixedTermEndTime` later in its struct.
- Registry: `WildcatArchController` registers markets and emits
  `MarketAdded(address indexed controller, address market)`; `market` is not
  indexed. For V2 markets the controller is the HooksFactory. View:
  `getRegisteredMarkets()`.
- Ethereum (1): ArchController `0xfEB516d9D946dD487A9346F6fee11f40C6945eE4`
  (start block 18686645), HooksFactory
  `0xdd7dd3b5076cf89440d05585ff56d246386207be`.
- Size on 2026-11-10: 89 registered Ethereum markets. 82 are V2 (31 closed).
  The 7 V1 markets (`version()` returns `"1.0"`) are dead or test markets: 5
  are closed and all 7 together hold about $61. DefiLlama reports about
  $3.7M idle cash and $153M borrowed on Ethereum.
- Offchain metadata: the Wildcat app
  ([`wildcat-app-v2`](https://github.com/wildcat-finance/wildcat-app-v2),
  `1de5d59`) serves unauthenticated, undocumented JSON routes under
  `https://app.wildcat.finance/api`, checked live on 2026-11-10:
  - `GET /market-summary/{market}?chainId=1` returns `{"description": ...}`,
    borrower-written Markdown, or 404. 7 of 82 V2 markets have one.
  - `GET /profiles/{borrower}?chainId=1` returns the borrower profile:
    `name`, `alias`, `description`, `website`, `twitter`, `linkedin`,
    `founded`, `jurisdiction` and more. 21 of 32 borrowers have a
    description.
  - `GET /borrower-names?chainId=1` returns every borrower's `address`,
    `name` and optional `alias` in one response.
  - Per the [onboarding docs](https://github.com/wildcat-finance/wildcat-docs/blob/main/using-wildcat/how-borrowers-are-onboarded.md),
    the legal name comes from KYB and is verified; aliases, descriptions,
    links and market descriptions are borrower-supplied and not verified.
- ABIs: ArchController and HooksFactory are full matches on Sourcify.
  Individual markets are not verified (CREATE2 from init-code storage). The
  market ABI is published in the subgraph
  [`abis/`](https://github.com/wildcat-finance/subgraph/tree/main/abis)
  folder and the v2-protocol deployment outputs.

## Design

### Value mapping

| Common field | Wildcat source |
|---|---|
| `share_price` | `scaleFactor() / 1e27`, denominated in `asset()` |
| `total_supply` | `scaledTotalSupply()` converted with the market decimals |
| `total_assets` | `totalSupply()` converted with the market decimals; never the market's `totalAssets()` |
| `max_deposit` | `0` when closed, otherwise `max(0, maxTotalSupply() − totalSupply())`, matching ERC-4626 `maxDeposit()` semantics |
| `available_liquidity` | `max(0, totalAssets − unclaimed − fees − normalize(scaledPendingWithdrawals))` |
| `utilisation` | `(totalSupply − (totalAssets − unclaimed − fees)) / totalSupply`, clamped to `[0, 1]`; `None` when supply is zero |
| `deposits_open`, `redemption_open` | Left `None` in historical rows, as the ERC-4626 reader does; current status comes from the adapter accessors |
| `performance_fee`, `management_fee` | Left `None` in historical rows, as the ERC-4626 reader does; fees come from the protocol fee matrix |

`unclaimed`, `fees` and `scaledPendingWithdrawals` are
`normalizedUnclaimedWithdrawals`, `accruedProtocolFees` and
`scaledPendingWithdrawals` from `currentState()`.

Utilisation is lent-out debt over supply. Paid-but-unclaimed withdrawals and
accrued protocol fees sit in the market's cash but are not lender liquidity,
so they are subtracted. Pending withdrawals are subtracted only from
available liquidity: older unpaid batches stay in `scaledPendingWithdrawals`,
so subtracting them in utilisation would pin it at 100%.

`share_price` is a contract-defined exchange rate, so the adapter does not
set `share_price_equivalence` and uses the ordinary static Multicall path.

### Discovery

Follow the Enzyme Blue direct-lead pattern
(`eth_defi/enzyme/blue_discovery.py`, `create_enzyme_blue_potential_vault_match()`
and `create_enzyme_blue_factory_detection()` in
`eth_defi/erc_4626/discovery_base.py`, and the log selection in
`eth_defi/erc_4626/hypersync_discovery.py`).

1. Add `eth_defi/erc_4626/vault_protocol/wildcat/discovery.py` with a frozen
   `WildcatMarketCandidate` dataclass (chain, market, block, timestamp),
   `fetch_wildcat_market_added_event_topic()`, `is_wildcat_registry_log()` and
   `create_wildcat_market_candidate()`. `market` is decoded from the log
   data. Logs whose indexed controller is not the HooksFactory are V1 markets
   and are skipped.
2. Add a Hypersync `LogSelection` for `MarketAdded` on the Ethereum
   ArchController in `HypersyncVaultDiscover`.
3. Add a `wildcat_market_candidate` slot to `PotentialVaultMatch`, read through
   the existing `getattr(..., None)` compatibility shims in `is_candidate()`
   and `_prepare_probe_leads()`. The registry event is sufficient
   identification, so the lead skips the generic feature probe and becomes a
   direct `{ERC4626Feature.wildcat_like}` detection on every scan, with
   `first_seen_at_block` set to the `MarketAdded` block.
4. Add `wildcat_like` to the activity-filter exemption and to
   `LENDING_PROTOCOL_FEATURES` in `eth_defi/erc_4626/core.py`. Without the
   latter, `eth_defi/erc_4626/scan.py` skips the liquidity and utilisation
   reads.

`MarketRemoved` is not tracked. Leads are never deleted, so a deregistered
market keeps its row and price history.

### Classification and protocol identity

- Add `ERC4626Feature.wildcat_like = "wildcat_like"` with a docstring link to
  the protocol.
- Return `"Wildcat"` from `get_vault_protocol_name()` and add the slug to the
  protocol slug mapping.
- Route `wildcat_like` in `create_vault_instance()` to `WildcatMarket`.
- Do not add a market probe to `identify_vault_features()`; markets are
  identified only through the registry.

### Adapter

Create `eth_defi/erc_4626/vault_protocol/wildcat/` with:

- `constants.py`: Ethereum ArchController, HooksFactory and FixedTermHooks
  template addresses.
- `vault.py`: `WildcatMarket(VaultBase)`, modelled on `RyskVault` for the
  read-only surface.
  - `name()`, `symbol()` from the market; share token is the market;
    denomination is `asset()`.
  - `fetch_total_assets()` and `fetch_nav()` return normalised
    `totalSupply()`; `fetch_total_supply()` returns `scaledTotalSupply()`.
    The scanner calls `fetch_total_assets()` first, so it must be explicit.
  - `fetch_available_liquidity()` and `fetch_utilisation_percent()` per the
    value mapping, read from one `currentState()` snapshot.
  - `fetch_deposit_open()` and `fetch_deposit_closed_reason()` implement the
    status described under Closed markets.
  - `get_withdrawal_period()` returns a `delay` period of
    `withdrawalBatchDuration()`, zero for closed markets.
  - `is_whitelisted_deposit()`: read `getHookedMarket(market)` on the hooks
    instance unpacked from `market.hooks()`, decoding only the three shared
    leading fields with one minimal ABI fragment. `depositRequiresAccess`
    `True` means whitelisted, `False` means permissionless. A market whose
    access is granted through an open role provider is therefore labelled
    whitelisted, which is the cautious side of the admission rules. A failed
    read raises one of the existing `DEPOSIT_PERMISSION_READ_EXCEPTIONS`, so
    the shared resolver records `unknown`. Do not infer admission from the
    `useOnDeposit` bit.
  - On fixed-term markets, identified by
    `HooksFactory.getHooksTemplateForInstance()` returning a FixedTermHooks
    template, `fetch_redemption_closed_reason()` and
    `fetch_redemption_next_open()` report the current `fixedTermEndTime`,
    decoded with the FixedTermHooks ABI. The term is not added to the
    withdrawal period. If the structs' shared prefix makes a template lookup
    unnecessary in practice, drop it during implementation.
  - `wildcat_metadata` is a `cached_property` returning
    `fetch_wildcat_market_metadata()`, as `LagoonVault.lagoon_metadata` does.
  - `description()` returns `normalise_vault_description()` of the market
    description, falling back to the borrower profile description.
    `short_description()` returns `normalise_vault_short_description()` of
    the same text. Both return `None` when Wildcat has no text.
  - `manager_name()` returns the borrower's KYB-verified legal `name` from
    the offchain metadata, never the unverified alias, and `None` when
    unavailable. The borrower address is never used, because it would flow
    into curator identification.
  - `fetch_scan_record_extra_data()` exports only fields with a consumer:
    `borrower`, `annualInterestBips`, `isClosed` and `timeDelinquent`.
  - `get_deposit_manager()` and `get_flow_manager()` raise
    `NotImplementedError` with a reason constant, as Rysk does.
  - `get_strategy_tags()` reads an address mapping in `tags.py`, adding
    `StrategyTag.lending`. Categorise every market with
    `categorise-vault-strategy`, and add `wildcat_like` to the priority and
    resolver tables in `eth_defi/vault_protocols/repairs/strategy_tags.py`.
  - No fee overrides. Add `VAULT_PROTOCOL_FEE_MATRIX` (`eth_defi/vault/fee.py`)
    and `VAULT_PROTOCOL_RISK_MATRIX` (`eth_defi/vault/risk.py`) entries as the
    `add-vault-protocol` skill requires; the fee mode is `feeless`, because
    the protocol fee is paid by the borrower.
- `offchain_metadata.py`: see Offchain metadata below.
- `historical.py`: `WildcatHistoricalReader(VaultHistoricalReader)` following
  `ERC4626HistoricalReader` in `eth_defi/erc_4626/vault.py`:
  - multicalls `scaleFactor()`, `scaledTotalSupply()`, `totalSupply()`,
    `totalAssets()`, `maxTotalSupply()` and `currentState()`, each tagged
    with `extra_data["function"]` and `first_block_number=self.first_block`;
  - results keyed by function name, with the same-block sanity check from
    `dictify_multicall_results()`;
  - each failed or missing call appends to `errors` and leaves its field
    `None`, with zero-supply errors as in `process_core_erc_4626_result()`;
    a failed `scaleFactor()` never produces a derived price;
  - `VaultReaderState` for adaptive polling, calling `on_called()` only after
    the share price decoded successfully;
  - `max_deposit`, `available_liquidity` and `utilisation` per the value
    mapping.

### Offchain metadata

`eth_defi/erc_4626/vault_protocol/wildcat/offchain_metadata.py`, following
`eth_defi/erc_4626/vault_protocol/lagoon/offchain_metadata.py` and the IPOR
module of the same name:

- Module docstring describing the reverse-engineered app routes, their
  source commit, the verified and unverified fields, and the cache design.
- Constants: `DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "wildcat"`,
  `DEFAULT_API_BASE_URL = "https://app.wildcat.finance/api"`,
  `DEFAULT_CACHE_DURATION = datetime.timedelta(days=2)` and
  `FAILED_REFRESH_RETRY_DELAY`.
- `WildcatMarketMetadata(TypedDict)` with `#:` field comments:
  `market_description`, `borrower_name`, `borrower_alias`,
  `borrower_description` and `borrower_website`, all optional.
- `fetch_wildcat_market_metadata(web3, market_address, borrower_address,
  now_=None, cache_path=DEFAULT_CACHE_PATH)` returns the merged metadata or
  `None`. It reads one JSON cache file per chain holding the names
  response, profiles keyed by borrower and market descriptions keyed by
  market, each with its fetch time. A stale or missing entry is fetched
  once; a 404 is cached as `None`. Names come from the bulk
  `/borrower-names` response, so the per-borrower profile request is only
  for the description and website.
- Two-level caching, as Lagoon does: the disk file with a file lock
  (`wait_other_writers()`, atomic writes) and an expiring in-process
  dictionary for the looped scanner.
- On a transport or schema failure, log a concise `WARNING`, keep the stale
  cached entry and retry after `FAILED_REFRESH_RETRY_DELAY`. An API
  omission never erases a previously cached description.
- Parsing strips Markdown markers (headings, `**` emphasis, list bullets)
  before the shared normalisers flatten the text.
- About 115 requests per chain every two days: one names request, up to
  one profile per borrower and one description per market.

### Delinquency and default risk

The scale factor keeps rising when a borrower stops paying, and the penalty
rate makes it rise faster. The price curve therefore cannot show a default.

- Add `VaultFlag.delinquent_markets = "delinquent_markets"` in
  `eth_defi/vault/flag.py`. For now it is used only for Wildcat markets
  whose borrower has defaulted, defined as
  `currentState().timeDelinquent > delinquencyGracePeriod()`: the penalty
  rate applies. Its `#:` docstring states this Wildcat-only scope, that the
  flag can stay for a while after a cure while the penalty still applies,
  and that the share price keeps rising during a default, so the curve
  cannot be read as realised yield.
- `WildcatMarket.get_flags()` adds the flag from current state, merged with
  inherited manual flags. It refreshes with ordinary metadata, currently
  every seven days; this lag is accepted and documented.
- The flag is descriptive and stays outside `BAD_FLAGS`, like
  `withdrawal_restrictions`, so defaulted markets remain visible with a
  warning. `filter_eligible_vaults()` in `eth_defi/vault_report/sections.py`
  reads only manual flags and risk, so add an explicit check of the exported
  flags there to keep defaulted markets out of best-performer lists. Hiding a
  market remains a manual decision through an existing bad flag.
- `get_notes()` explains the default and that accrued interest may not be
  recoverable.
- Check every consumer of `VaultFlag` values in this repository and the
  frontend before adding the value. An older release cannot unpickle a
  database containing it, so a rollback past this change is not possible.

### Closed markets

A closed market is treated like a vault whose deposits are closed, using the
existing deposit status API rather than a new status:

- `fetch_deposit_open()` returns `False` when `isClosed()` is `True`.
- `fetch_deposit_closed_reason()` returns `"Market closed by borrower"`.
  When the market is open but `totalSupply()` has reached
  `maxTotalSupply()`, it returns `"Max deposit cap reached"` and
  `fetch_deposit_open()` also returns `False`.
- Historical rows carry `max_deposit=0` while closed, as an ERC-4626 vault
  with `maxDeposit() == 0` does.
- Redemptions stay open: lenders of a closed market can still withdraw.
  Price history is kept, and the market is not hidden or flagged.

### ERC-4626 wrappers

Wildcat's `Wildcat4626Wrapper` contracts (factory
`0xEA6DE11f8F3F83c79bD9d8Db5517fCFDf2Bb148a`, 15 on Ethereum) wrap a market
token as an ERC-4626 vault. The generic scanner can already read them, and
their price curve is correct, so no wrapper code is added. Before the PR,
check production metadata for wrapper addresses. If any are present, add
them to the existing `VAULT_DESCRIPTIVE_FLAGS` table with
`VaultFlag.wrapped_asset` and a reviewed comment.

### ABIs

Store under `eth_defi/abi/wildcat/` with a `README.md` recording the source and
commit of each file, following `eth_defi/abi/README.md`:

- `WildcatMarket.json` (V2) from the subgraph `abis/` folder or the
  v2-protocol build outputs.
- `WildcatArchController.json` and `HooksFactory.json` from Sourcify.
- `FixedTermHooks.json` for `fixedTermEndTime`. The shared `HookedMarket`
  prefix used for deposit permission is a small inline fragment.

## Production onboarding

The incremental lead scan resumes from the chain's stored end block, so
`MarketAdded` events before the deployment are not seen. Metadata only
refreshes for markets with a stored lead, and a new vault's prices start at
the chain's price cursor (`eth_defi/vault/historical.py`), not at its first
block.

This PR adds one numbered migration, `NNN-wildcat-market-leads.py`, per
`.claude/docs/migration-scripts.md`, to run before the first scanner run
with the new code:

- It embeds the reviewed inventory as constants: market, `MarketAdded`
  block and timestamp for every V2 market ever registered on Ethereum up to
  a frozen block B.
- It inserts the leads, carrying `wildcat_market_candidate`, through
  `context.metadata()`. It does not build metadata rows; the scanner builds
  missing rows on its next run.
- It fails if `getRegisteredMarkets()` at the chain's stored lead cursor
  contains a V2 market missing from the constants, so markets added between
  B and the cursor cannot be skipped. Re-freeze and retry in that case.
- It preserves reader state, other leads and all rows.

Add a test that a seeded lead produces a detection and a metadata row on a
later lead scan, and the "Run after merge" PR reminder.

The historical price backfill is a follow-up PR, because its ranges end at
each market's first collected price row, which exists only after the scanner
has run. It uses `run_price_backfill()` over frozen half-open ranges from
each market's `MarketAdded` block, as
`016-neutral-trade-autopilot-price-backfill.py` does, after checking the
Ethereum dense timestamp cache.

## Protocol metadata and documentation

Use the `add-vault-protocol` skill for the protocol YAML, logo
(`extract-vault-protocol-logo`, `post-process-logo`), feed YAML entry,
Sphinx API stub under `docs/source/api/wildcat/` and the `CHANGELOG.md` line.
Add a separate Sphinx vault page only if the skill requires one for every
protocol. Descriptions follow `.claude/docs/description-writing-style.md`.

Write one `eth_defi/erc_4626/vault_protocol/wildcat/README-Wildcat.md`, as
Rysk has, covering the value mapping, both formulas, delinquency and the
onboarding. Add a Wildcat row to
`eth_defi/erc_4626/vault_protocol/README-utilisation.md` and the README to
the CLAUDE.md README table.

## Tests

Fork tests use the shared Anvil fork pool and the Ethereum midnight block
pattern from `eth_defi/testing/anvil_fork_pool.py`, with absolute-value
assertions.

- `tests/erc_4626/vault_protocol/test_wildcat.py`, with one open-term, one
  fixed-term and one closed market chosen from the registry at the fork
  block:
  - share price equals `scaleFactor / 1e27`; total assets equals
    `totalSupply()`;
  - available liquidity and utilisation;
  - the historical reader decodes at two fixed blocks;
  - deposit permission from `depositRequiresAccess`, and the fixed-term
    redemption reason;
  - closed market: `fetch_deposit_open()` is `False`, the closure reason is
    set, `max_deposit` is `0` and the withdrawal period is zero;
  - `delinquent_markets` set or absent according to `timeDelinquent`.
- `tests/erc_4626/vault_protocol/test_wildcat_offchain_metadata.py`, like
  the existing offchain metadata tests: no-RPC parsing tests from recorded
  responses (Markdown stripping, 404 caching, stale-entry retention on
  failure, legal name rather than alias), plus one real API test fetching
  a known market description, borrower profile and the names list, as the
  repository's external integration rule requires. Record its result in a
  PR comment.
- No-RPC unit tests for `MarketAdded` decoding and V1 skipping, the
  liquidity and utilisation formulas with decoded state (including older
  unpaid batches), `PotentialVaultMatch` compatibility with older pickled
  leads, and the report filter excluding `delinquent_markets`.
- A Hypersync discovery test for `MarketAdded` over a short fixed range,
  marked with `skip_hypersync_scan_on_ci` if it takes minutes.

## Out of scope

- Deposit, withdrawal and guard integration for the trade executor.
- V1 markets: 7 dead or test markets holding about $61 in total.
- Plasma: about $1.4M in markets. It can be added later with its
  ArchController, HooksFactory and FixedTermHooks addresses, once its RPC is
  confirmed to serve historical state.
- Wrapper-specific code; see ERC-4626 wrappers.
- Wildcat subgraph and MarketLens as data sources.
- Tracking deregistration through `MarketRemoved`.
- Per-block fixed-term and permission history; only current state is read.
- Credit research beyond the strategy tags.
- The app's `/defaulted-markets` route; `delinquent_markets` is computed
  from onchain state.
- The historical price backfill, which is a follow-up PR.

## Open questions

1. Which wrappers are in the production vault database?
2. Which consumers outside this repository (website, frontend) need
   updating for the new `delinquent_markets` flag?
3. Wildcat's own test markets belong to a borrower with alias
   `[TEST BORROWER]`. Should they be hidden with a manual flag entry?

Settled (2026-11-10):

- Defaulted borrowers get the descriptive `delinquent_markets` flag.
- Closed markets use the existing deposit-closed status.
- V2 on Ethereum only; V1, Plasma and wrapper code are out of scope.
- Markets with an open role provider are labelled whitelisted.
- Descriptions and the manager name come from the Wildcat app API through
  `offchain_metadata.py`; the manager name is the verified legal name.

## Implementation notes

Simplifications made while implementing, compared with the plan above:

- The hooks template is identified by the hooks instance's `version()`
  (`OpenTermHooks` or `FixedTermHooks`), not through the HooksFactory, and
  its own ABI decodes `getHookedMarket()`.
- `offchain_metadata.py` uses only `/market-summary` and `/profiles`; the
  profile already carries the legal name, so `/borrower-names` is not needed.
  The cache is one locked JSON file per chain with per-entry expiry and no
  in-process layer.
- The historical reader makes two calls per block, `currentState()` and
  `totalAssets()`; the decoded `MarketState` in `state.py` carries the scale
  factor, supply, cap and pending withdrawals.
- No `fetch_scan_record_extra_data()` override: the exported fields had no
  consumer.
- The inventory was frozen at block 26,386,736: 82 V2 markets and 7 V1
  markets, none deregistered.
- The price backfill is no longer a follow-up PR. Migration 063 also builds
  metadata rows with `create_vault_scan_record()`, and migration 064 backfills
  each market from its `MarketAdded` block to the Ethereum price cursor plus
  one, read from the stored reader state, so both run in one batch. The
  inventory and helpers live in `eth_defi/vault_protocols/repairs/wildcat.py`.
  `scripts/erc-4626/preview-wildcat-migrations.py` previews both on a copy of
  local pipeline data with `MIGRATION_BACKFILL_BLOCKS`.
- Markets are read at most daily (`WILDCAT_READ_INTERVAL`): the live reader
  state slows hourly cadences to daily, and the backfill samples every
  7,200 blocks through a new optional `step` argument of `run_price_backfill()`.
- Wildcat Labs' test markets are hidden with the `unofficial` flag through
  `WILDCAT_TEST_BORROWERS`, settling the open test-market question.
- Long descriptions keep the borrower's Markdown; short descriptions use the
  first line with a sentence boundary, after joining wrapped lines.
