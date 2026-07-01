# Mellow vault architecture integration plan

## Summary

This document records the technical mapping and phased implementation plan for
Mellow Core Vault support. The initial PR implements factory-led discovery,
`mellow_like` routing, the `MellowVault` adapter skeleton, share-supply
historical reads, oracle-derived share price and denomination-token TVL rows,
metadata row support, the activity-filter exemption, API docs and the manual
mapping script. Queue flow accounting remains explicitly unsupported until a
separate queue-address event reader is implemented and fixed-block checks pin
those semantics.

Mellow Core Vaults should be added as a sibling vault architecture alongside ERC-4626 at the adapter level, but use `ERC4626Feature.mellow_like` as a pipeline compatibility/routing flag.

The adapter should subclass `eth_defi.vault.base.VaultBase` directly:

- `eth_defi/mellow/vault.py`: `MellowVault`
- `eth_defi/mellow/historical.py`: `MellowVaultHistoricalReader`
- `eth_defi/mellow/discovery.py`: factory and component discovery
- `eth_defi/mellow/flow.py`: deposit and redeem queue event support

The initial target vault for validation is Lido Earn USD:

- Vault: `0x014e6DA8F283C4aF65B2AA0f201438680A004452`
- Share manager / share token: `0x4Ce1ac8F43E0E5BD7A346A98aF777bF8fbeA1981`
- Public token symbol: `earnUSD`
- Chain: Ethereum

## Why Mellow is not ERC-4626

Mellow Core Vaults do not expose the standard ERC-4626 accounting surface on the vault contract.

ERC-4626 assumes:

- The vault contract is also usually the ERC-20 share token, or exposes ERC-7575-style share lookup.
- A single `asset()` denomination token.
- Standard `totalAssets()`, `convertToAssets()`, `convertToShares()`, `maxDeposit()` calls.
- Standard `Deposit` and `Withdraw` events are enough for generic discovery and flow reading.

Mellow Core Vaults instead use a modular architecture:

- `Vault`: central coordinator and access-control surface.
- `ShareManager`: separate share accounting contract; tokenised variants are ERC-20 share tokens.
- `DepositQueue`: per-asset asynchronous deposit queues.
- `RedeemQueue`: asynchronous redemption queues.
- `Oracle`: reports prices and drives queue settlement.
- `FeeManager`: protocol, performance, deposit, and redeem fees.
- `RiskManager`: limits, balances, allowed assets, and pending asset accounting.
- `Subvault`: execution and custody compartments controlled by the vault.
- Hooks and verifiers: queue hooks and constrained curator execution.

Because user deposits and redeems settle through queues and oracle reports, a generic ERC-4626 reader will miss core state and may read incorrect share price, TVL, and flow status.

Despite this, the production scanner should add and use
`ERC4626Feature.mellow_like` for compatibility with the existing vault database,
metadata row and price scanning pipeline. This feature flag is a routing marker,
not a claim that the contract implements ERC-4626. Any code that sees
`mellow_like` must instantiate `MellowVault`, not `ERC4626Vault`, and must avoid
ERC-4626 ABI calls such as `asset()`, `totalAssets()` and `convertToAssets()`.

`ERC4262VaultDetection` should also support Mellow detections even though the
underlying contract is not ERC-4626. For Mellow, the detection address is the
Mellow `Vault` proxy address emitted by `Factory.Created`, and
`features={ERC4626Feature.mellow_like}` is enough to route downstream code. The
class name remains historical; do not introduce a parallel detection object
unless the shared pipeline can no longer carry the needed fields.

## Current discovery status

`scripts/mellow/scan-initial-mellow.py` has been added as an initial mapping tool.

It scans Mellow Core Vault factory `Created(address,uint256,address,bytes)` events with Hypersync and enriches results with:

- vault address
- factory version
- owner
- creation block
- `shareManager()`
- share token metadata
- registered assets and queue count, where available
- public Mellow API TVL, where available

The production scanner now reuses the same factory topic and decoder helpers,
but this script remains the operator-facing mapping and diagnostics tool.

Open discovery questions:

- Confirm if Base has a deployed Core Vault factory. Mellow Core deployment docs currently document Mainnet, Plasma, Arbitrum and Monad factories used by this integration; broader Mellow product lines are outside this initial scanner scope.
- Decide whether the production scanner should include only Core Vault factory products or all Mellow API vault product lines.
- Confirm if older Mellow vault families, e.g. MultiVault / Symbiotic vaults, should use a separate adapter instead of `MellowVault`.

## Alignment with existing event-based lead discovery

The current ERC-4626 scanner uses an event-based baseline to find candidate
vault contracts before probing them.

Current baseline flow:

1. `eth_defi/erc_4626/discovery_base.py` defines discovery event topics.
2. Standard baseline topics are ERC-4626 `Deposit` and `Withdraw`.
3. The baseline has already been extended with protocol-specific deposit-like
   and withdraw-like events for BrinkVault, Ember, TokenGateway and Royco.
4. `PotentialVaultMatch` is keyed by the log-emitting contract address.
5. A lead becomes a candidate only after it has at least one deposit-like event
   and at least one withdraw-like event:
   `deposit_count > 0 and withdrawal_count > 0`.
6. Hypersync and JSON-RPC backends both use the same topic map:
   - `HypersyncVaultDiscover.build_query()` scans all configured topic0 values
     without restricting addresses.
   - `JSONRPCVaultDiscover.build_query()` calls `read_events_concurrent()` for
     the same event set.
7. `VaultDiscoveryBase.scan_vaults()` then probes the candidate log-emitting
   addresses with ERC-4626/classification calls.

This is a good baseline for ERC-4626-like protocols because the log-emitting
address is usually the vault/share-token contract we later probe.

Mellow does not align with this assumption:

- Mellow user flow events are expected on `DepositQueue` and `RedeemQueue`
  contracts, not necessarily on the primary `Vault` contract.
- The primary adapter address should be the `Vault`, while ERC-20 share state
  lives on `ShareManager`.
- A deposit-like event emitted by a `DepositQueue` and a redeem-like event
  emitted by a different `RedeemQueue` would produce two separate
  `PotentialVaultMatch` keys in the current baseline, neither of which is the
  `MellowVault.address`.
- Probing the queue address with ERC-4626 calls would either fail or classify
  the wrong contract.
- Mellow vault creation is better identified by `Factory.Created`, because that
  event emits the actual vault instance address.

Therefore Mellow Core Vault discovery should be factory-led, not
Deposit/Withdraw-led, but it should still run inside the same per-chain
Hypersync query pipeline as ERC-4626 discovery. The slower JSON-RPC fallback
must also scan configured Mellow factories over the same block range so fallback
runs do not silently omit Mellow vaults:

- Use `Factory.Created(address instance,uint256 version,address owner,bytes initParams)`
  as the canonical lead event for vault identity.
- Add Mellow factory `Created` topic selections to the same Hypersync stream
  used for ERC-4626 deposit/withdraw lead discovery when the scanned chain has
  configured Mellow factories.
- Demultiplex logs by topic/address:
  - ERC-4626 and ERC-4626-like flow topics update `PotentialVaultMatch` keyed by
    the log-emitting address.
  - Mellow `Factory.Created` topics create `PotentialVaultMatch` objects keyed
    by the emitted `instance` vault address, with the decoded factory metadata
    attached to the lead.
- The shared Hypersync query must include enough fields for both discovery
  families:
  - ERC-4626-style discovery needs the existing block/log identity fields.
  - Mellow factory discovery needs factory log `address`, `transaction_hash`,
    `block_number`, `log_index`, `topic0`, `topic1`, `topic2`, `topic3` and
    `data` so `instance`, `version`, `owner` and `initParams` can be decoded
    whether the factory event uses indexed or non-indexed parameters.
- Mellow factory log selections must be address-restricted to the configured
  factory addresses for the scanned chain. Do not scan every contract that emits
  a same-signature `Created` topic.
- After factory log decoding, run the emitted Mellow vault addresses through
  the shared `probe_vaults()` classification path with Mellow-specific probes.
  The output must be `ERC4262VaultDetection(features={ERC4626Feature.mellow_like})`,
  not an unclassified lead row.
- Use `Vault.QueueCreated(address queue,address asset,bool isDepositQueue)` and
  `queueAt(asset,index)` to map queue contracts back to a vault.
- Use queue deposit/redeem events for flow accounting only after the queue has
  been associated with a known vault.
- Do not add Mellow queue events to `get_vault_discovery_events()` unless the
  lead model is changed to support "child contract emitted an event for parent
  vault" relationships.

Reusable pieces from the current baseline:

- The `LeadScanReport` / `PotentialVaultMatch` shape should be reused for
  Mellow as well as ERC-4626 event leads. Mellow attaches the decoded
  `Factory.Created` metadata to the lead, while final rows are still based on
  `ERC4262VaultDetection`, not raw leads.
- The Hypersync streaming structure can be reused.
- The topic-kind idea can be reused for Mellow queue flow events, but only after
  queue-to-vault mapping is known.
- The "event lead first, ABI probe second" pattern remains correct: Mellow
  factory events should be followed by the narrow classification probes
  `shareManager()` and `getAssetCount()` before accepting a vault. Optional
  component reads such as `oracle()` and `feeManager()` belong to metadata
  enrichment, not lead classification.

Required production change:

- Add Mellow-specific discovery handling under `eth_defi/mellow/discovery.py`,
  but wire it into the existing per-chain Hypersync discovery pipeline instead
  of launching a separate Hypersync scan.
- The combined per-chain discovery result should contain both ERC-4626
  detections and Mellow `ERC4262VaultDetection` objects keyed by
  `VaultSpec(chain_id, vault_address)`.
- The class name `ERC4262VaultDetection` is intentional in the current codebase,
  even though it looks like a transposed ERC-4626 name. Use the existing class
  name in implementation and tests unless the whole codebase is renamed in a
  separate refactor.
- Keep ERC-4626 `Deposit`/`Withdraw` baseline unchanged.
- Add a regression test that adding Mellow discovery does not change the
  existing ERC-4626 lead counts/classification for a known control vault.
- Add a negative test proving that Mellow queue contracts are not accidentally
  stored as vault addresses.
- For initial vault discovery and price scanning, Mellow metadata rows must
  store `deposit_count = 0` and `redeem_count = 0`.
- Be precise about count field names:
  - `PotentialVaultMatch` lead objects use `deposit_count` and
    `withdrawal_count`.
  - `ERC4262VaultDetection` objects use `deposit_count` and `redeem_count`.
  - Mellow factory leads use `PotentialVaultMatch` for the shared discovery and
    probe path, but after probing Mellow detections store integer `0` in
    `ERC4262VaultDetection.deposit_count` and
    `ERC4262VaultDetection.redeem_count`.
- `mellow_like` detections must be centrally exempt from deposit/redeem activity
  filters by checking `ERC4626Feature.mellow_like in detection.features`, never
  by checking count values or count field names. Do not implement this as
  scattered caller-by-caller special cases.
- Add explicit code comments at the detection construction site and at the
  activity-filter exemption explaining why counts are zero:
  - Mellow flow events are emitted by `DepositQueue` and `RedeemQueue`
    contracts, not by the canonical `Vault` address.
  - The vault identity comes from `Factory.Created`.
  - Getting true flow counts requires a second-stage queue-address scan after
    queue discovery.
  - Initial vault discovery and price scanning do not need those counts.
  - Using integer zero preserves compatibility with existing numeric consumers,
    while the `mellow_like` exemption prevents the zero counts from silently
    dropping Mellow vaults.

## Pre-implementation gates

Do not start the `MellowVault` historical reader or flow reader before these
facts are locked down with verified ABIs and fixed-block checks:

- Canonical verified ABIs for `Vault`, `Factory`, `ShareManager`,
  `DepositQueue`, `RedeemQueue`, `Oracle`, `FeeManager`, `RiskManager` and
  `Subvault`.
- Confirmed `Factory.Created` ABI indexed layout before implementing Hypersync
  field selection or demux code:
  - whether `instance`, `version` and `owner` are indexed topics or encoded in
    `data`
  - decoder code must assert against the verified ABI layout instead of
    assuming `topic1`, `topic2` and `topic3`
  - test both the documented layout and reject malformed logs with clear errors
- Confirmed `priceD18` orientation for Lido Earn USD:
  - Mellow reports raw `shares / assets`.
  - `VaultHistoricalRead.share_price` is human-readable assets per share:
    `10 ** (share_decimals + 18 - asset_decimals) / priceD18`.
  - the historical reader samples `Oracle.getReport(denomination_token)` and
    writes `total_assets = share_price * total_supply`.
- Confirmed queue event names and event argument order for:
  - deposit request
  - deposit settlement / claim
  - redeem request
  - redeem settlement / claim
- Confirmed shared Hypersync query feasibility:
  - one stream can contain unrestricted ERC-4626 deposit/withdraw topic
    selections and address-restricted Mellow factory `Created` selections
    without cross-applying the address filter
  - a regression test proves ERC-4626 lead counts are unchanged when the Mellow
    factory selection is added
  - a regression test proves Mellow `Created` logs are restricted to configured
    factory addresses and same-signature `Created` events from unrelated
    contracts are ignored
- One pinned real deposit event block and one pinned real redeem event block for
  flow-reader regression tests.
- Confirmed handling for `BasicShareManager`:
  - either prove all initial Core Vault targets use tokenised share managers
  - or implement an explicit unsupported error and API/offchain metadata fallback
    for non-tokenised managers.

## Proposed package layout

```text
eth_defi/mellow/
    __init__.py
    abi.py
    core.py
    discovery.py
    vault.py
    historical.py
    flow.py
    deposit_redeem.py
```

`eth_defi/erc_4626/` should not own Mellow. The ERC-4626 scanner can still reference Mellow only to suppress false positives or to bridge legacy listing code during migration.

## ABI requirements

Store verified ABIs under:

```text
eth_defi/abi/mellow/
    Vault.json
    Factory.json
    TokenizedShareManager.json
    BurnableTokenizedShareManager.json
    DepositQueue.json
    RedeemQueue.json
    Oracle.json
    FeeManager.json
    RiskManager.json
    Subvault.json
```

Do not hand-edit generated ABI JSON files. Fetch verified source ABIs from explorers or canonical Mellow source releases.

Minimum ABI methods needed for `MellowVault`:

- `Vault.shareManager()`
- `Vault.feeManager()`
- `Vault.oracle()`
- `Vault.getAssetCount()`
- `Vault.assetAt(uint256)`
- `Vault.getQueueCount()`
- `Vault.queueAt(address,uint256)`
- `Vault.isDepositQueue(address)`
- `Vault.isPausedQueue(address)`
- `ShareManager.name()`
- `ShareManager.symbol()`
- `ShareManager.decimals()`
- `ShareManager.totalSupply()`
- fee, risk, oracle methods after source mapping confirms exact names

Minimum ABI events needed:

- `Factory.Created(address instance,uint256 version,address owner,bytes initParams)`
- `Vault.QueueCreated(address queue,address asset,bool isDepositQueue)`
- `Vault.QueueRemoved(address queue,address asset)`
- `Vault.ReportHandled(address asset,uint256 priceD18,uint256 depositTimestamp,uint256 redeemTimestamp,...)`
- deposit queue request/claim events, exact names to be confirmed from ABI
- redeem queue request/claim events, exact names to be confirmed from ABI
- `ShareManager.Transfer(address indexed from,address indexed to,uint256 value)` for share supply cross-checks

## `MellowVault`

`MellowVault` should subclass `VaultBase`.

`eth_defi/mellow/vault.py` must start with an extensive module-level docstring
that documents the Mellow Core Vault architecture for future maintainers. This
docstring should be treated as the canonical local architecture note for the
adapter, not just a short module description.

The head docstring must cover:

- why Mellow Core Vaults are modelled as `VaultBase` and not `ERC4626Vault`
- the component graph: `Vault`, `ShareManager`, `DepositQueue`, `RedeemQueue`,
  `Oracle`, `FeeManager`, `RiskManager`, `Subvault`, hooks, and verifiers
- which contract address is the primary vault address and which one is the
  ERC-20 share token
- how deposits and redemptions move through queues and oracle settlement
- why TVL and share price cannot be read with ERC-4626 `totalAssets()` /
  `convertToAssets()`
- how historical reading is expected to derive total supply, share price and
  TVL
- known unsupported cases, especially non-tokenised `BasicShareManager`
- links to canonical Mellow docs, deployments, source repositories and example
  explorer pages

Constructor:

```python
class MellowVault(VaultBase):
    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: TokenDiskCache | None = None,
        default_block_identifier: BlockIdentifier | None = None,
    ):
        ...
```

Primary address:

- `VaultBase.address` should return the Mellow `Vault` contract address, not the `ShareManager`.
- The `VaultSpec.vault_address` should therefore be the `Vault` address discovered from the factory.

Core properties and methods:

- `chain_id`: from `web3.eth.chain_id`
- `address`: vault proxy address
- `share_manager_address`: `vault_contract.functions.shareManager().call()`
- `share_token`: ERC-20 metadata for tokenised `ShareManager`
- `name`: share token name when tokenised, otherwise fallback to API/offchain metadata
- `symbol`: share token symbol when tokenised
- `fetch_info()`: return a `MellowVaultInfo` dataclass with all component addresses
- `fetch_share_token()`: `fetch_erc20_details()` on `shareManager`
- `fetch_denomination_token()`: base token for valuation, not necessarily the only deposit token
- `fetch_nav()`: Mellow-specific TVL calculation
- `fetch_portfolio()`: balances of vault plus subvaults, if subvault enumeration is available
- `get_historical_reader(stateful)`: return `MellowVaultHistoricalReader`
- `get_flow_manager()`: return `MellowVaultFlowManager`
- `get_deposit_manager()`: return unsupported stub at first unless deposit execution is explicitly implemented

`MellowVaultInfo` should include:

- vault address
- share manager address
- fee manager address
- risk manager address
- oracle address
- deposit queues by asset
- redeem queues by asset
- registered assets
- subvaults, if discoverable
- factory address and factory version, if known
- API metadata, if attached

### `VaultBase` abstract surface mapping

Before coding, implement or explicitly stub every `VaultBase` abstract member.
The first `MellowVault` PR should include this mapping in code comments or test
names so omissions are easy to review.

| `VaultBase` member | Mellow implementation plan |
| --- | --- |
| `chain_id` | Return `web3.eth.chain_id`, cached at construction if needed. |
| `address` | Return the Mellow `Vault` proxy address from `VaultSpec`, not the `ShareManager`. |
| `name` | Return tokenised `ShareManager.name()`; fallback to API/offchain metadata only if the manager is non-tokenised and the fallback is tested. |
| `symbol` | Return tokenised `ShareManager.symbol()`; fallback to API/offchain metadata only if tested. |
| `has_block_range_event_support()` | Return `True` after queue event scanning is implemented; before that return `False` only if `get_flow_manager()` is also an explicit unsupported stub. |
| `has_deposit_distribution_to_all_positions()` | Return `False`; Mellow deposits settle through queues and curator/subvault allocation, not automatic pro-rata distribution to all positions. |
| `fetch_portfolio(universe, block_identifier)` | Initially read balances of registered assets on the vault address. Extend to subvault balances after subvault enumeration is confirmed. If incomplete, return a partial portfolio with explicit notes/errors rather than pretending to have full TVL. |
| `fetch_info()` | Return `MellowVaultInfo` with component addresses, assets, queues, factory metadata and API metadata. |
| `get_flow_manager()` | Return `MellowVaultFlowManager` once queue event support is in place; otherwise raise a clear `NotImplementedError`. |
| `get_deposit_manager()` | Return an unsupported deposit manager or raise a clear `NotImplementedError` until active deposits/redeems are implemented. Reading support does not require transaction execution. |
| `get_historical_reader(stateful)` | Return `MellowVaultHistoricalReader(self, stateful=stateful)`. |
| `fetch_denomination_token()` | Return the configured/API base token for valuation; preserve all deposit and withdraw tokens separately in `MellowVaultInfo`. |
| `fetch_nav()` | Return on-chain base-asset NAV only after the TVL method is confirmed. Until then raise a clear unsupported exception or return `None` only if downstream non-fatal behaviour is covered by tests. |
| `fetch_share_token()` | Return ERC-20 details for tokenised `ShareManager`. For `BasicShareManager`, raise a protocol-specific unsupported error unless the fallback path has been implemented and tested. |

Non-abstract but relevant fee methods:

- Override `get_protocol_name()` to return `Mellow`, or make sure the generic
  implementation does not route Mellow through `ERC4626Feature.broken`.
- Read FeeManager D6 rates with `depositFeeD6()`, `redeemFeeD6()`,
  `performanceFeeD6()` and `protocolFeeD6()`.
- Map `protocolFeeD6()` to the shared `management_fee` field because Mellow
  defines it as an annual time-based protocol fee and the generic schema does
  not have a separate protocol-fee column.
- Map `performanceFeeD6()` to `performance_fee`, `depositFeeD6()` to deposit
  fee and `redeemFeeD6()` to withdraw fee.

### Detection and adapter routing

Mellow should reuse the existing detection and adapter factory path where
possible:

- `ERC4262VaultDetection` must be able to represent a Mellow Core Vault with:
  - `address`: Mellow `Vault` proxy address, not `ShareManager` and not queue
    address
  - `features`: `{ERC4626Feature.mellow_like}`
  - `first_seen_at_block`: `Factory.Created` block
  - `deposit_count`: integer `0`
  - `redeem_count`: integer `0`
- Add line comments where `deposit_count = 0` and `redeem_count = 0` are set.
  The comments must explain that real Mellow flow events live on queue
  contracts and need a second-stage queue-address scan, while initial discovery
  and price scanning intentionally do not depend on those counts.
- Add a central activity-filter helper, e.g.
  `is_activity_filter_exempt(detection)`, that returns `True` based on
  `ERC4626Feature.mellow_like in detection.features`. The helper must not look
  at `deposit_count`, `redeem_count` or `withdrawal_count`.
- `create_vault_instance()` must branch on `ERC4626Feature.mellow_like` before
  generic ERC-4626 fallback handling and return `MellowVault(web3, spec, ...)`.
- `create_vault_instance_autodetect()` must rely on the same ERC-4626
  classification/probe path for Mellow. Add Mellow-specific probes to that path
  so autodetection can classify a Mellow vault as `ERC4626Feature.mellow_like`
  and then route to `MellowVault`.
- `probe_vaults()` should run the shared multicall probe path for Mellow factory
  leads. Add the two Mellow-specific classification probes, `shareManager()`
  and `getAssetCount()`, so `identify_vault_features()` can add
  `ERC4626Feature.mellow_like` even when standard ERC-4626 probes fail.
- `identify_vault_features()` must evaluate successful Mellow-specific probes
  before assigning final broken/unsupported status based on failed ERC-4626
  probes. A failed `convertToShares()` or `asset()` call must not prevent
  `mellow_like` classification when Mellow probes succeeded.
- Mellow factory leads should become `ERC4262VaultDetection` objects through
  the same classification envelope as ERC-4626 leads. The difference is that
  `mellow_like` is a routing feature, and later adapter construction must use
  `MellowVault` instead of `ERC4626Vault`.
- Keep Mellow factory metadata on `PotentialVaultMatch`, keyed by lower-case
  vault address in the normal lead map. This metadata must carry
  `factory_address`, `factory_version`, `owner`, `created_block`, `created_at`,
  transaction hash and log index from the decoded `Factory.Created` log.
- After `probe_vaults()` returns features for a Mellow lead, read the factory
  metadata from the `PotentialVaultMatch` and construct `ERC4262VaultDetection`
  with:
  - `first_seen_at_block = created_block`
  - `first_seen_at = created_at`
  - `updated_at = now`
  - `deposit_count = 0`
  - `redeem_count = 0`
  - `features = features | {ERC4626Feature.mellow_like}`
- Add a fixed-block/unit test asserting the stored Mellow detection
  `first_seen_at_block` equals the `Factory.Created` block, not the block used
  for the probe call.
- Any code path receiving `ERC4262VaultDetection` with `mellow_like` must avoid
  ERC-4626 ABI assumptions. Treat it as a shared vault-detection envelope, not
  a standards-compliance proof.
- Metadata row creation must support `mellow_like` without ERC-4626 scan-record
  assumptions. Keep Mellow on the same `create_vault_scan_record()` path as
  ERC-4626 vaults and make the shared logic call only `VaultBase` methods, with
  defensive fallbacks for optional ERC-4626-only reads like `totalAssets()`.

### Address and share-token audit

Mellow splits the primary vault address from the ERC-20 share token address.
This is the main integration risk for existing vault pipeline code.

Before merging `MellowVault`, audit every downstream path that might treat
`vault.address` as an ERC-20 token:

- historical price reader and `VaultHistoricalReadMulticaller`
- token-cache warmup in `eth_defi/vault/historical.py`
- holder/shareholder readers
- flow event readers
- vault database export and JSON export
- metadata, risk and fee matrix lookup keys
- any `balanceOf(vault.address)` or `Transfer` scanning that should instead use
  `vault.share_token.address`

The audit must confirm these paths use `vault.share_token` or
`fetch_share_token()` whenever they need ERC-20 share state. Add regression
tests for at least the historical reader and export path.

## Denomination token and TVL semantics

Mellow can accept multiple deposit assets and can custody assets across vault and subvault contracts. This does not map cleanly to `VaultBase.denomination_token`, which is a single-token abstraction.

For initial integration:

- Treat the API `base_token` or the configured base asset as the `denomination_token`.
- Preserve all deposit and withdraw assets in `MellowVaultInfo`.
- Do not assume `fetch_nav()` equals the raw balance of the base asset.
- Do not calculate USD TVL from hardcoded stablecoin assumptions in the adapter.

For production on-chain TVL, choose one of these after ABI/source confirmation:

1. Prefer an official Mellow oracle/risk-manager view that returns vault total value or base-asset balance.
2. If unavailable, reconstruct TVL from registered asset balances held by the vault and subvaults plus the latest oracle reports.
3. Use the public Mellow API only as an enrichment or validation source, not as canonical historical data.

## Historical reader

Implement `MellowVaultHistoricalReader(VaultHistoricalReader)`.

It cannot subclass `ERC4626HistoricalReader`, because ERC-4626 calls like `totalAssets()` and `convertToAssets()` are not the accounting source.

The class must implement the exact base hooks from
`eth_defi.vault.base.VaultHistoricalReader`:

- `construct_multicalls() -> Iterable[EncodedCall]`
- `process_result(block_number, timestamp, call_results) -> VaultHistoricalRead`

Use existing ERC-4626 readers only as examples for `EncodedCall` construction
and `VaultHistoricalRead` creation. Do not call
`construct_core_erc_4626_multicall()` or
`process_core_erc_4626_result()`.

Initial multicalls:

- `ShareManager.totalSupply()`
- `ShareManager.decimals()`, warmed via token cache
- `Vault.shareManager()`, if not cached
- `Vault.oracle()`, if not cached
- `Vault.feeManager()`, if not cached
- latest oracle report for the denomination asset through `Oracle.getReport(asset)`
- fee manager state for performance/protocol fees
- risk manager vault balance / limit state, once method names are confirmed

Initial output fields in `VaultHistoricalRead`:

- `share_price`: derive from Mellow oracle `priceD18`, converted from raw
  shares-per-raw-asset to human-readable denomination-token assets per share
- `total_assets`: denomination-token TVL derived as `share_price * total_supply`
- `total_supply`: share manager total supply converted with share token decimals
- `performance_fee`: Mellow `performanceFeeD6`, converted from D6 to a
  fractional fee
- `management_fee`: Mellow `protocolFeeD6`, converted from D6 to a fractional
  annual management-like fee
- `max_deposit`: vault or risk manager remaining capacity if available
- `errors`: explicit reasons for partial reads

The `share_price` formula is pinned by fixed-block tests:

- Lido Earn USD reads `ShareManager.totalSupply()` and `Oracle.getReport(USDC)`.
- The direct Anvil test checks the fixed-block share price.
- The real-chain Hypersync integration test samples two later blocks and asserts
  the historical reader writes an increasing share-price series.

If current-state `fetch_nav()` is temporarily unsupported, add a test proving
that `MellowVaultHistoricalReader.process_result()` and the export path do not
crash when `VaultHistoricalRead.total_assets` or current NAV is `None`; they
must surface an explicit `errors` value instead.

Historical reader phases:

1. Current-state reader:
   - Read share manager, assets, queues, total supply and API TVL.
   - Useful for onboarding and UI mapping.
2. Oracle-based historical reader:
   - Decode `ReportHandled` / oracle report events.
   - Sample latest accepted report at or before each historical block.
   - Combine with total supply at the sampled block.
3. Full on-chain TVL reader:
   - Include subvault balances, pending deposits, pending redemptions and risk manager balance corrections.
   - Cross-check against the public Mellow API for recent blocks.

## Flow event reader

Mellow deposits and redemptions are asynchronous. We need a protocol-specific `MellowVaultFlowManager`.

Map to the existing neutral model:

- deposit queue request event -> `PendingVaultFlow(direction=deposit)`
- deposit queue claim/settle event -> processed deposit flow
- redeem queue request event -> `PendingVaultFlow(direction=redeem)`
- redeem queue claim/settle event -> processed redemption flow

Hypersync should scan queue contracts, not only the vault contract, because user request events are expected to be emitted by the queue modules.

Implementation steps:

- Discover queue addresses from `QueueCreated` events and `queueAt(asset,index)`.
- Build topic lists from `DepositQueue` and `RedeemQueue` ABIs.
- Add `fetch_mellow_vault_flow_events_hypersync()`.
- Convert queue logs into `PendingVaultFlow`.
- Add tests modelled after `tests/vault/test_pending_vault_flow_events.py`.

Flow tests must be pinned to known events:

- one fixed deposit request or processed deposit event block
- one fixed redeem request or processed redeem event block
- absolute assertions for owner/controller, asset or share amount, queue
  address, vault address, transaction hash and log index

Do not use a broad block range with only an "at least one event" assertion.

## Discovery and classification

Mellow discovery should be factory-based.

Production discovery:

- scan configured Mellow Core Vault factories as part of the shared per-chain
  Hypersync query
- decode `Created` events into factory lead candidate addresses
- run those candidate addresses through `probe_vaults()` with Mellow-specific
  probes
- produce `ERC4262VaultDetection(features={ERC4626Feature.mellow_like})`
- instantiate through `create_vault_instance()` so the shared adapter factory
  returns `MellowVault`
- attach factory version and first-seen block
- optionally enrich with public Mellow API metadata

Add `ERC4626Feature.mellow_like` for Mellow Core Vaults as an integration
compatibility flag. Document in `eth_defi/erc_4626/core.py` that this flag means
"Mellow-like vault routed through the shared vault pipeline" and does not imply
ERC-4626 compliance. For Core Vaults, discovery remains factory-led and the
adapter remains `MellowVault(VaultBase)`.

Initial integration points:

- Add `scripts/mellow/scan-initial-mellow.py` as the manual mapping tool.
- Add `eth_defi/mellow/discovery.py` for reusable library code.
- Extend `eth_defi/vault/scan_all_chains.py` and the ERC-4626 scanner pipeline
  so Mellow factory discoveries are merged into the normal EVM smart-contract
  vault scan cycle.
- Add protocol metadata under `eth_defi/data/vaults/metadata/mellow.yaml` once
  listings can consume non-ERC-4626 adapters without implying ERC-4626
  compliance.

## Tests

Initial focused tests:

- `tests/mellow/test_mellow_discovery.py`
  - decode known factory `Created` events
  - confirm Lido Earn USD is discovered
  - assert Base is skipped unless `MELLOW_BASE_VAULT_FACTORY` is configured
- `tests/mellow/test_mellow_vault_info.py`
  - instantiate `MellowVault`
  - read component addresses
  - read share token metadata
  - read registered assets and queues
  - prove `vault.address` is the Mellow `Vault` and
    `vault.share_token.address` is the `ShareManager`
  - test non-tokenised `BasicShareManager` handling, either as a clear
    unsupported error or a working API/offchain metadata fallback
- `tests/mellow/test_mellow_historical_reader.py`
  - construct `construct_multicalls()` output
  - process a fixed-block read for Lido Earn USD
  - assert absolute values at a fixed Ethereum block
  - pin `priceD18` orientation by cross-checking
    `share_price * total_supply` against Mellow API TVL or a confirmed on-chain
    TVL view
  - prove unsupported/unknown NAV or `total_assets=None` is represented as a
    `VaultHistoricalRead.errors` entry and does not crash the export path
- `tests/mellow/test_mellow_flow_events.py`
  - scan pinned deposit/redeem queue event blocks
  - convert to `PendingVaultFlow`
  - assert absolute decoded event values, transaction hash and log index
- `tests/mellow/test_mellow_not_erc4626.py`
  - assert Lido Earn USD is not detected as an ERC-4626 vault
  - assert Mellow Core Vaults are surfaced through `ERC4626Feature.mellow_like`
    only as a routing flag
  - assert `ERC4626Feature.mellow_like` routes to `MellowVault`, not
    `ERC4626Vault`
  - construct an `ERC4262VaultDetection` with `features={mellow_like}` and
    assert `create_vault_instance()` returns `MellowVault`
  - assert `create_vault_instance_autodetect()` uses ERC-4626 classification
    probes to classify Lido Earn USD as `mellow_like` and returns `MellowVault`
  - assert `create_vault_instance()` does not call ERC-4626 methods such as
    `asset()`, `totalAssets()` or `convertToAssets()` for a `mellow_like`
    detection
  - assert failed ERC-4626 probes do not mark a vault broken when the two
    Mellow-specific probes, `shareManager()` and `getAssetCount()`, succeeded
  - assert the activity-filter exemption fires because `mellow_like` is present,
    not because any count field has a particular value
  - assert existing ERC-4626 detection behaviour for a known control vault is
    unchanged
- `tests/mellow/test_mellow_export.py`
  - assert a Mellow `ERC4262VaultDetection` can be converted into a
    `VaultDatabase` metadata row without ERC-4626 ABI calls
  - assert export/listing paths do not use `vault.address` as the ERC-20 share
    token address
  - assert `vault.share_token.address` is used for share-token fields

Use fixed archive block numbers for Anvil/mainnet-fork tests and assert absolute values where possible.

## Manual script test

Add an Ethereum block-range based manual test path before production pipeline
integration. The sample range must stay capped at 1,000,000 blocks so the test
is large enough to exercise Hypersync pagination and metadata/price reads, but
small enough for routine operator use.

The manual script should support these environment variables:

- `CHAINS=ethereum`
- `START_BLOCK`
- `END_BLOCK`
- `BLOCK_RANGE=1000000` as a convenience when `START_BLOCK` is omitted
- `MELLOW_TEST_LEADS=true`
- `MELLOW_TEST_METADATA=true`
- `MELLOW_TEST_PRICES=true`
- `PIPELINE_DATA_DIR` for temporary test output
- `SKIP_WRITE=true` for read-only diagnostics where useful

Example command:

```shell
source .local-test.env
END_BLOCK=$(poetry run python - <<'PY'
from eth_defi.provider.multi_provider import create_multi_provider_web3
import os

web3 = create_multi_provider_web3(os.environ["JSON_RPC_ETHEREUM"])
print(web3.eth.block_number)
PY
)
START_BLOCK=$((END_BLOCK - 1000000))

CHAINS=ethereum \
START_BLOCK=$START_BLOCK \
END_BLOCK=$END_BLOCK \
MELLOW_TEST_LEADS=true \
MELLOW_TEST_METADATA=true \
MELLOW_TEST_PRICES=true \
PIPELINE_DATA_DIR=/tmp/mellow-vault-test \
LOG_LEVEL=info \
poetry run python scripts/mellow/scan-initial-mellow.py
```

The manual test must produce tabulated output for:

- leads discovered from `Factory.Created`
- metadata rows after component probing
- price sample rows. In the initial PR these rows contain on-chain
  `ShareManager.totalSupply()` and API TVL where the factory lead matches the
  public Mellow API. Oracle-derived share price and denomination-token
  `total_assets` are covered by the production historical reader and its
  fixed-block tests.

Minimum manual assertions:

- Lido Earn USD appears as a vault lead when its creation block is inside the
  selected range, or the output explicitly says the selected range starts after
  the creation block.
- No deposit or redeem queue address is reported as a vault address.
- Metadata includes `Vault`, `ShareManager`, assets and queues.
- Price rows include block number, total supply, share price, total assets or an
  explicit unsupported/error reason.
- The script exits non-zero if leads pass but metadata or price decoding fails
  unexpectedly.

## Implementation phases

### Phase 1: ABI and static mapping

- Add verified Mellow ABIs.
- Confirm `priceD18` orientation and document the formula.
- Confirm exact queue event names and argument order.
- Identify pinned deposit and redeem event blocks for tests.
- Decide and document tokenised vs non-tokenised `ShareManager` support.
- Move reusable discovery code from `scripts/mellow/scan-initial-mellow.py` into `eth_defi/mellow/discovery.py`.
- Keep script as the operator-facing CLI.
- Add a static known factory registry for Ethereum, Plasma, Arbitrum and Monad.
- Leave Base configurable until a canonical Core factory is confirmed.

### Phase 2: `MellowVault`

- Add the extensive `eth_defi/mellow/vault.py` head docstring before adding
  implementation logic, so the contract architecture assumptions are reviewed
  together with the adapter code.
- Implement or explicitly stub every `VaultBase` abstract member listed in the
  abstract-surface mapping above.
- Implement `MellowVaultInfo`.
- Implement component accessors.
- Implement share token metadata.
- Implement asset and queue enumeration.
- Add `fetch_nav()` as best-effort:
  - use official on-chain view if found
  - otherwise raise a clear unsupported exception, or return `None` only after
    tests prove this is non-fatal for historical reads and exports
- Add the address/share-token downstream audit and fix any code path that treats
  `vault.address` as the ERC-20 share token.

### Phase 3: historical reader

- Implement `MellowVaultHistoricalReader`.
- Start with total supply and oracle price.
- Add TVL once exact oracle/risk-manager semantics are confirmed.
- Add reader-state support only after the stateless reader is reliable.
- Add fixed-block tests for `priceD18` orientation and `VaultHistoricalRead`
  output.

### Phase 4: flow reader

- Implement queue discovery and Hypersync event scanning.
- Map queue events to `PendingVaultFlow`.
- Add pending deposit and redemption accounting.
- Add pinned-event tests with absolute decoded values.

### Phase 5: production scanner integration

- Integrate Mellow into `scripts/erc-4626/scan-vaults-all-chains.py` /
  `eth_defi/vault/scan_all_chains.py` as part of the base EVM smart-contract
  vault scan cycle.
- Do not add a `SCAN_MELLOW=true` opt-in flag. Mellow should be scanned
  whenever the normal EVM vault scanner scans a chain that has Mellow factory
  configuration.
- Use existing EVM chain controls:
  - `TEST_CHAINS`, `CHAIN_ORDER` and `DISABLE_CHAINS` decide whether Ethereum,
    Plasma, Arbitrum, Monad or Base are scanned.
  - `SCAN_CYCLES` applies through the existing chain item, e.g.
    `Ethereum=8h`, not a separate `Mellow=8h` item.
  - retry handling and dashboard state remain on the chain result.
- Add a per-chain Mellow factory registry with documented Core deployments
  enabled by default: Ethereum, Plasma, Arbitrum and Monad. Keep Base enabled
  only when a canonical factory is confirmed or configured.
- Extend `scan_vaults_for_chain()` or the function it calls so one EVM chain
  scan performs both through one shared Hypersync stream:
  - existing ERC-4626 lead discovery and metadata extraction from
    deposit/withdraw-like event topics
  - Mellow factory candidate discovery, `probe_vaults()` classification and
    metadata extraction from configured factory `Created` topics
- Avoid a second full-chain Hypersync pass for Mellow. Mellow discovery should
  reuse the same block range, stream lifecycle, retry handling, head clipping
  and rate limiting as ERC-4626 discovery.
- Keep JSON-RPC fallback support functionally equivalent by scanning configured
  Mellow factory addresses over the same block range and feeding decoded factory
  candidates into the same `probe_vaults()` path.
- Merge Mellow metadata rows into the same shared `VaultDatabase` update for
  that chain.
- Chain-level metrics should include separate ERC-4626 and Mellow counts in
  logs, while `ChainResult.vault_count` remains the total smart-contract vault
  count for the chain.
- Add Mellow-specific durable state under `PIPELINE_DATA_DIR` only if needed
  for protocol-local reader state. Do not introduce a separate top-level
  protocol scan cycle for Mellow.
- When global `SCAN_PRICES=true`, the same EVM chain price pass must include
  Mellow vaults for that chain.
- Decide how non-ERC-4626 vaults are represented in the vault database.
- Extend scan/export paths to include `VaultBase` adapters that are not `ERC4626Vault`.
- Ensure Mellow rows contain enough detection data for price scanning to
  instantiate `MellowVault` through the `ERC4626Feature.mellow_like` routing
  branch, not the generic `ERC4626Vault`.
- Mellow detections must store integer `0` deposit/redeem counts.
- Add a central `mellow_like` / feature-based activity-filter exemption and use
  it from `scan_prices_for_chain()`, standalone scripts such as
  `scripts/erc-4626/scan-prices.py`, and any post-processing/export filters that
  inspect deposit/redeem activity.
- Add line comments next to the exemption explaining that `deposit_count = 0`
  and `redeem_count = 0` are compatibility placeholders for initial discovery
  and price scanning, not evidence that the vault has no queue activity.
- Add or update Mellow-aware metadata row creation so `create_vault_scan_record`
  or its caller can produce a `VaultDatabase` row for `mellow_like`.
- Update `scan_prices_for_chain()` or add a Mellow-native price scan merge so
  Mellow historical reads are included when `SCAN_PRICES=true`.
- Ensure Mellow price rows are written through the same parquet schema and
  schema-migration safety rules as ERC-4626 rows.
- Ensure post-processing, top-vaults JSON, samples and data-file export can see
  Mellow rows and distinguish protocol name `Mellow`.
- Add metadata, risk and fee matrix entries.
- Add documentation under `docs/source/api/mellow/` and cross-link from `docs/source/api/index.rst`.
- Add a minimal export/listing path for Mellow vaults before claiming export
  support complete.

## Open technical questions

- What is the canonical on-chain method for current Mellow vault TVL?
- Does `priceD18` mean `shares / assets` or `assets / shares` for every Core Vault configuration?
- Are all Core Vault share managers ERC-20 tokenised, or do some use non-tokenised `BasicShareManager`?
- How are subvaults enumerated from the vault contract?
- Which queue events are canonical for pending and processed deposits/redeems?
- Can the public Mellow API be used as a temporary metadata source in production, or only in scripts?
- Should broader Mellow ecosystem vaults, especially Symbiotic/MultiVault vaults, be separate adapters?

## Acceptance criteria by phase

### Phase 1 acceptance

- Verified Mellow ABIs are stored in the repo.
- `Factory.Created` indexed/non-indexed argument layout is documented and
  enforced by decoder tests.
- `priceD18` orientation is documented and backed by a fixed-block comparison.
- Queue event names and argument order are documented.
- Pinned deposit and redeem event blocks are documented for later tests.
- The Ethereum manual script can scan a 1,000,000-block range and tabulate
  leads, metadata and price rows.

### Phase 2 acceptance

- Lido Earn USD can be instantiated as `MellowVault`.
- `eth_defi/mellow/vault.py` has a module-level architecture docstring that
  explains Mellow's component model, queue-based flows, share manager split and
  historical reading assumptions.
- Every required `VaultBase` abstract member is implemented or explicitly
  stubbed with a clear unsupported error.
- `MellowVault` returns stable component metadata and share token details.
- The address/share-token audit has confirmed historical and export code uses
  `vault.share_token` for ERC-20 share state.
- Existing ERC-4626 discovery and historical reading behaviour is unchanged.

### Phase 3 acceptance

- The historical reader returns a `VaultHistoricalRead` row without ERC-4626
  calls.
- `share_price * total_supply` matches a confirmed TVL source within the
  documented tolerance at a fixed block.
- Unsupported NAV/TVL states are represented as explicit errors and do not crash
  historical export.

### Phase 4 acceptance

- The flow reader can discover pinned real deposit and redeem queue events.
- Flow tests assert absolute decoded values, transaction hash and log index.

### Phase 5 acceptance

- Mellow vaults can be listed separately from ERC-4626 vaults in exports.
- Mellow metadata, risk, fee and docs entries are present.
- Non-ERC-4626 production scanning can include Mellow without changing existing
  ERC-4626 behaviour.
- `ERC4626Feature.mellow_like` exists and is documented as a compatibility
  routing flag, not an ERC-4626 compliance claim.
- `ERC4262VaultDetection` can carry Mellow detections with
  `features={ERC4626Feature.mellow_like}` and the Mellow `Vault` address.
- `create_vault_instance()` or its replacement routes `mellow_like` detections
  to `MellowVault`.
- `create_vault_instance_autodetect()` recognises Mellow through the ERC-4626
  classification/probe path and routes to `MellowVault`.
- Mellow factory leads are converted to `ERC4262VaultDetection` objects before
  metadata row creation; raw Mellow leads are not written as final rows.
- Mellow detections preserve `first_seen_at_block` from the factory `Created`
  log after joining factory candidate metadata with `probe_vaults()` results.
- The shared Hypersync query selects factory log payload fields and restricts
  Mellow `Created` logs to configured factory addresses.
- The combined Hypersync query keeps ERC-4626 selections unrestricted and Mellow
  factory selections restricted to configured factory addresses.
- Mellow detection counts are stored as integer `0`, and all price scan entry
  points skip the ERC-4626 low-activity filter for `mellow_like`.
- Zero deposit/redeem counts and the `mellow_like` activity-filter exemption are
  documented with line comments in the implementation.
- The activity-filter exemption is feature-based on
  `ERC4626Feature.mellow_like`, not count-field-name or count-value based.
- `mellow_like` detections can be converted into `VaultDatabase` metadata rows
  without ERC-4626 ABI calls.
- Mellow discovery runs automatically as part of the base EVM chain scan for
  configured Mellow chains; there is no `SCAN_MELLOW` opt-in flag.
- ERC-4626 lead discovery and Mellow factory discovery use the same per-chain
  Hypersync query/stream pipeline and block range.
- The JSON-RPC discovery fallback also scans configured Mellow factory
  `Created` logs and produces the same `PotentialVaultMatch` lead shape before
  `probe_vaults()`.
- `SCAN_PRICES=true` writes Mellow historical price rows through the shared
  price pipeline or a documented Mellow-native merge path for those same EVM
  chains.
- Mellow price scanning is not filtered out because of missing/zero
  deposit/redeem event counts.
