# Flying Tulip ftUSD vault modelling research

Research date: 2026-08-24.

## Identification

- Protocol name: Flying Tulip
- Product: ftUSD staking
- Vault/share token: Staked Flying Tulip USD (`sftUSD`)
- Denomination asset: Flying Tulip USD (`ftUSD`)
- Reward token: Flying Tulip (`FT`)
- Homepage and app: [Flying Tulip ftUSD](https://flyingtulip.com/ftusd/)
- Documentation: [ftUSD product documentation](https://docs.flyingtulip.com/product-suite/ft-usd/)
- Official deployment registry: [Flying Tulip ftUSD contracts API](https://api.flyingtulip.com/ftusd/contracts/all)
- Public deployment manifests: [Ethereum](https://flyingtulipdotcom.github.io/deployments/prod-eth-ftusd.toon) and [Sonic](https://flyingtulipdotcom.github.io/deployments/prod-sonic-ftusd.toon)
- Public GitHub organisation: [flyingtulipdotcom](https://github.com/flyingtulipdotcom)
- X/Twitter: [@flyingtulip_](https://x.com/flyingtulip_)
- DefiLlama: [Flying Tulip ftUSD](https://defillama.com/protocol/flying-tulip-ftusd)
- DefiLlama reward adapter: [flying-tulip-ftusd](https://github.com/DefiLlama/yield-server/blob/master/src/adaptors/flying-tulip-ftusd/index.js)
- Security material: [public security repository](https://github.com/flyingtulipdotcom/security), [known issues](https://github.com/flyingtulipdotcom/security/blob/master/KNOWN_ISSUES.md), and [live Sherlock bug bounty](https://audits.sherlock.xyz/bug-bounties/248)
- Deployer clue: explorers label the deployer as `Flying Tulip: Deployer`.
- Contract developer: Flying Tulip. The public `flyingtulipdotcom/ft` repository contains the FT omnichain token, not the ftUSD or `EpochRewardsVault` source. The canonical public source for the staking contracts is currently the verified implementation source on the explorers.

### Deployments

| Chain | Chain ID | `sftUSD` proxy | Current implementation | Explorer |
|---|---:|---|---|---|
| Ethereum | 1 | `0xeb48218a4c35c814c7678cBcae88C6Ee037F7625` | `0xea95e4636badc00881f8f73a0623b0fe8627b6da` | [Etherscan](https://etherscan.io/address/0xeb48218a4c35c814c7678cBcae88C6Ee037F7625#code) |
| Sonic | 146 | `0xD1E5A86f1005F6356Bd022C587dE0f430CD2aeb1` | `0x5aee4b34df62790581e2f2c31468ddfd7020e841` | [SonicScan](https://sonicscan.org/address/0xD1E5A86F1005F6356BD022C587DE0F430CD2AEB1#code) |
| BNB Chain | 56 | `0xe1716796d6Bf37e4049bdb6e1150Cb713800FeEe` | `0x1A5531176CB0055104Bb3802eAa5087452D311cF` | [BscScan](https://bscscan.com/address/0xe1716796d6Bf37e4049bdb6e1150Cb713800FeEe#code) |

The official contracts API is the freshest deployment source. It added BNB Chain after the current DefiLlama reward adapter was written. At the research snapshot BNB Chain was deployed but dormant: epoch, supply and assets were all zero.

The `ftUSD` address is CREATE2-deterministic and the same on all three chains:
`0xF7D85EC4E7710F71992752EAC2111312E73E9C9C`.

## What should be modelled as the vault

Model the `sftUSD` `EpochRewardsVault` proxy as the vault and `ftUSD` as its denomination asset.

Do not model the base `ftUSD` ERC-20 as an ERC-4626 vault. `ftUSD` is the non-yielding stablecoin and settlement asset. Its `MintAndRedeem` engine accepts collateral and issues or burns ftUSD, but it is not the user staking vault. The documentation explicitly separates non-yielding ftUSD from opt-in staking into sftUSD.

`EpochRewardsVault` inherits OpenZeppelin ERC-4626 and implements the standard surface:

- `asset()` returns ftUSD.
- `deposit()` and `mint()` issue sftUSD.
- `withdraw()` and `redeem()` burn sftUSD for ftUSD.
- Standard `Deposit` and `Withdraw` events are emitted, so the existing Hypersync ERC-4626 discovery path can find active deployments.
- The vault fixes `convertToAssets()` and `convertToShares()` at 1:1.
- `totalAssets()` deliberately returns `totalSupply()`.

The scanner already discovers the active Ethereum and Sonic contracts as generic ERC-4626 vaults. The missing part is protocol-specific classification and economically correct return and redemption metadata. A hardcoded discovery lead is optional for the dormant BNB deployment; active deployments do not need one because they emit the standard discovery events.

## Critical accounting distinction

The contract has two economically different value streams:

1. Principal is always represented contractually as one sftUSD share per one ftUSD.
2. Yield is paid as separately claimable FT rewards and does not increase the ERC-4626 share price.

The verified `EpochRewardsVault` source states that rewards are distributed externally through epochs, not through share appreciation. Each epoch emits:

```solidity
EpochSettled(uint32 epochId, uint256 rewardAmount, uint256 stakeTime, uint256 rateRay)
```

Rewards are weighted by each account's stake-seconds. `previewClaimable(address)` and `claim(address)` expose the account-specific accrued FT. Transfers checkpoint the sender and receiver, so accrued rewards do not simply travel with the ERC-20 share token.

This means the current scanner's share-price-only profit calculation will correctly observe a constant principal price of 1.0, but will incorrectly show zero investment return. A reward-adjusted price is not the ERC-4626 redemption price.

For the common vault-history pipeline, the selected representation is therefore a GMX-style `share_price_equivalence` rather than a direct vault share price:

- `share_price_equivalence`: a feature flag on Flying Tulip historical rows.
- `share_price`: compounded FT-distribution-adjusted ftUSD equivalent used for charts and return metrics; it is explicitly non-redeemable.
- `total_assets`: the matching synthetic performance value (`share_price * total_supply`) needed by the common historical price identity; it is not contractual assets or principal TVL.
- `contractual_redemption_share_price`: 1 ftUSD per sftUSD, exposed by live ERC-4626 reads, protocol metadata and documentation rather than represented by the historical equivalent-price column.
- `principal_nav`: sftUSD supply in ftUSD units, kept in live protocol metadata and operational reporting.
- `backing_value`: `ftYieldWrapperV2.valueOfCapital()`, kept separate from contractual `totalAssets()`.
- `external_reward_token`: FT.
- `external_reward_amount`: FT distributed by `EpochSettled` events.
- `external_reward_apr`: rolling FT distributions valued in ftUSD relative to time-weighted staked ftUSD.

Keeping `backing_value` separate matters because `totalAssets()` is defined to equal liabilities (`totalSupply()`), not actual wrapper capital. At the snapshot all wrappers were fully backed, but a strategy loss would not reduce the contract-reported share price. The scanner should expose the backing ratio rather than silently assume the hardcoded 1:1 conversion proves solvency.

Protocol aggregation must avoid double counting. sftUSD is a receipt for ftUSD, while ftUSD is already backed by the collateral wrappers. Adding sftUSD principal TVL to ftUSD collateral TVL would count the same economic capital twice.

## Reward return calculation

Historical reward discovery must use Hypersync, not JSON-RPC `eth_getLogs`.

The supported performance history begins when the Ethereum Curve FT/ftUSD pool
became the canonical market at block `25,531,725` (Unix timestamp
`1,784,042,255`). Earlier reward epochs are not priced or included in CAGR.
Their mint and burn provenance is retained only to reconstruct correct supply
at the first tracked settlement, which establishes the 1.0 equivalent-price
baseline.

For a rolling window, collect `EpochSettled` events and calculate:

```text
average staked ftUSD = sum(epoch.stakeTime) / elapsed seconds
reward value ftUSD   = sum(epoch.rewardAmount in FT) * historical FT/ftUSD price
reward APR           = reward value ftUSD / average staked ftUSD * seconds per year / elapsed seconds
```

Because rewards do not auto-compound, this is economically an external-reward APR even if a consumer labels the field APY. The event's `rateRay` can be retained as the raw onchain reward-rate observation. A historical FT/ftUSD price join is required to express returns in ftUSD; record raw FT rewards even when the price source is unavailable rather than reporting a false zero.

The DefiLlama adapter uses a simpler 30-day calculation based on current supply and labels it `apyReward`. It is a useful cross-check, but the scanner can be more precise by using event `stakeTime` as the denominator.

The base ftUSD market price and the protocol's fixed $1 accounting value should also remain separate. The principal series is denominated in ftUSD; any USD display should use a market-price source so peg deviations are not hidden.

## Redemption lifecycle

The vault's standard ERC-4626 redemption is conditionally synchronous:

- The sftUSD vault calls its ftUSD `ftYieldWrapperV2`.
- The wrapper can withdraw available strategy capital immediately.
- A `CircuitBreakerV2` rate-limits the final ftUSD outflow.
- An outflow within current circuit-breaker capacity is paid immediately.
- An excess outflow burns shares immediately, transfers the ftUSD to the circuit breaker, and returns a queue ID for later settlement.
- The live settlement delay on all three chains was 21,600 seconds (6 hours).

The standard `redeem()` and `withdraw()` functions discard the returned queue ID. Integrations should instead use `redeemWithQueueId()` or `withdrawWithQueueId()`, persist a non-zero queue ID, and later call the circuit breaker's `executeQueued()` after `timeUntilSettled()` reaches zero.

The generic synchronous ERC-4626 deposit manager must therefore not be advertised as safely supporting Flying Tulip redemptions. A successful transaction can burn the user's shares without delivering ftUSD immediately. A specialised manager should model redemption as a hybrid immediate/queued lifecycle and parse the queue ID.

For scanner metadata:

- Use a withdrawal period of 0 to the live `settlementDelay()`, with delay type `delay`.
- Report immediate capacity as the minimum of wrapper liquidity and `CircuitBreakerV2.withdrawalCapacity(wrapper, ftUSD, currentTvl)`.
- Do not call the balance above the capacity "utilisation". It is a security rate limit, not capital deployed or borrowed.
- Record the live queue count and pause state as separate operational metrics if the public schema is extended.

Snapshot at blocks Ethereum 25,823,924, Sonic 78,061,278 and BNB Chain 117,778,930:

| Chain | sftUSD supply / contractual assets | Wrapper capital | Wrapper liquidity | Immediate circuit-breaker capacity | Epoch |
|---|---:|---:|---:|---:|---:|
| Ethereum | 1,793,028.419503 | 1,793,028.419503 | 1,793,028.419503 | 179,716.167091 | 264 |
| Sonic | 281,092.877794 | 281,092.877794 | 281,092.877794 | 28,368.762930 | 282 |
| BNB Chain | 0 | 0 | 0 | 0 | 0 |

All values except epoch are in ftUSD. The circuit-breaker capacity was approximately 10% of TVL on the active deployments. It is dynamic and must be read at the requested block rather than encoded as a constant.

## Fees and permissions

The sftUSD staking vault has no management, performance, deposit or withdrawal fee. Its yield is a discretionary FT distribution funded from net strategy yield and protocol fee revenue.

The separate ftUSD `MintAndRedeem` route charged 7 basis points on both mint and redeem for the enabled Ethereum and Sonic collateral, and 10 basis points on BNB Chain, at the research snapshot. This is an indirect acquisition or exit cost when a user enters from USDC, USDT, USSD or FDUSD; it is not a fee charged by the sftUSD vault and should not be put in the vault's deposit or withdrawal fee fields.

Deposits are permissionless in the identity sense, but they remain subject to:

- vault pause state;
- ftUSD pause state;
- ftUSD sender, receiver and owner blacklist checks;
- available ftUSD balance and allowance.

The vault and supporting contracts are UUPS/ERC-1967 upgradeable and owner-controlled. Epoch rewards are settled by the owner or appointed epoch settler, and the documentation says distributions occur at treasury discretion. These governance dependencies belong in the vault notes.

## Security and audits

Flying Tulip publishes a risk framework, verified source, a security repository, known issues and a live Sherlock bounty covering deployed contracts. A Sherlock contest also exists for Flying Tulip's Perpetual PUT system.

No approved public report-level audit specifically covering the deployed `EpochRewardsVault`, `ftYieldWrapperV2` and `CircuitBreakerV2` versions was located. The bug bounty and PUT contest should not be described as a completed ftUSD staking-vault audit. The explorer pages also do not attach a contract audit report. Scanner notes should make this scope limitation explicit.

The public known-issues file includes operational and strategy-wrapper risks relevant to ftUSD, including strategy removal, withdrawal-liquidity assumptions, yield-claimer responsiveness and a circuit-breaker same-block capacity edge case. It also acknowledges that protocol-level losses are handled operationally rather than through an onchain loss-sharing mechanism.

## Recommended implementation

### Initial correct classification

1. Add `ERC4626Feature.flying_tulip_like` and map it to protocol name `Flying Tulip`.
2. Maintain a chain-aware deployment registry for the three reviewed sftUSD proxies. Do not use address-only classification because every chain has a different sftUSD address and the official API can add chains.
3. Add `FlyingTulipVault(ERC4626Vault)` and route the feature to it in `create_vault_instance()`.
4. Return the ftUSD app link, zero direct fees, permissionless deposit policy, and protocol notes covering external FT rewards, the fixed 1:1 conversion, upgrades, treasury discretion and conditional queues.
5. Tag the active strategies conservatively from their chain-specific manifests. Ethereum and Sonic currently combine lending and delta-neutral/carry allocations; BNB Chain has no active strategy history yet.

This phase makes protocol naming, principal TVL and risk metadata correct, but must explicitly state that share-price performance excludes FT distributions.

### Reward-aware scanner support

1. Add a protocol reward-event reader using `configure_hypersync_from_env()` and `open_hypersync_stream()` for `EpochSettled`.
2. Persist raw reward amount, stake-time and rate observations in the protocol-owned contextual DuckDB cache; no generic Parquet schema extension is needed.
3. Join historical FT/ftUSD prices and calculate a compounded reward-adjusted `share_price` equivalent.
4. Add `share_price_equivalence` so the common sparse filter ignores principal flows and downstream returns reuse the same path as GMX.
5. Keep the direct 1:1 redemption price, principal TVL and wrapper backing values out of the historical equivalent-price semantics and label all public outputs accordingly.
6. Cross-check current results against Flying Tulip's dashboard and DefiLlama without making either offchain API the sole historical source.

### Liquidity and transaction support

1. Commit the verified implementation ABIs for `EpochRewardsVault`, `ftYieldWrapperV2` and `CircuitBreakerV2` under `eth_defi/abi/flying_tulip/`, with an ABI README recording every proxy, implementation, explorer source and fetch date.
2. Extend the historical reader with wrapper backing, wrapper liquidity, circuit-breaker capacity, settlement delay and pause/queue state.
3. Implement a Flying Tulip deposit manager using the queue-ID-returning redemption functions.
4. Treat a non-zero queue ID as an asynchronous request; track and execute it after the 6-hour delay. Keep FT reward claiming a separate optional action.
5. Add fixed-block fork tests for classification, 1:1 accounting, deposits, immediate redemption, forced queued redemption and queue execution. Because this is a material external integration, also run and record a minimal real-provider integration check.

## Conclusion

Flying Tulip fits the scanner as a specialised ERC-4626 income-distributing vault, not as a conventional auto-compounding ERC-4626 vault. The common historical curve is a flagged share-price equivalent built from external rewards, while live ERC-4626 reads retain the contractual 1:1 conversion. Correct redemption support still requires preserving the circuit-breaker queue ID. Modelling only `totalAssets() / totalSupply()` would list the vault but miss essentially all of its yield and part of its liquidity risk.
