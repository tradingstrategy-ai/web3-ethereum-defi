# Fast Anvil fork tests: shared forks, block cache and session deployments

This directory holds the reusable helpers that make Anvil mainnet-fork tests
fast. Fork tests are slow for two reasons — launching Anvil replays archive
state, and every test hitting the upstream archive node adds latency and can be
rate-limited. The helpers here attack both:

1. **Shared, session-scoped forks** — many tests reuse *one* Anvil process
   instead of launching one each.
2. **A canonical block per chain** — so those tests share a fork *and* their
   archive reads land in a dense, reusable on-disk cache.
3. **The Foundry fork RPC cache** — committed for reproducible warm starts in CI
   and locally, so warm runs barely touch the upstream archive.
4. **Per-test snapshot/revert** — cheap state isolation on a shared fork.

If you are writing a new fork test, read this before copying an old per-file
`fork_network_anvil` fixture.

> **Canonical reference.** The authoritative, single-source description of this
> pattern — the required rules, the rationale (warm CI RPC cache) and a
> copy-paste module skeleton — is the module docstring of
> [`eth_defi/testing/anvil_fork_pool.py`](anvil_fork_pool.py). This README is a
> practical companion; when the two disagree, the docstring wins.
>
> The operator checklist for diagnosing a failed fork is in [Anvil failure
> modes](#anvil-failure-modes).

## The pieces

| File | Purpose |
|------|---------|
| `eth_defi/testing/anvil_fork_pool.py` | `AnvilForkPool` — session registry of shared Anvil forks keyed by launch config |
| `eth_defi/testing/fork_blocks.py` | `MIDNIGHT_BLOCKS` / per-chain `*_MIDNIGHT_BLOCK` constants + `get_midnight_block()` |
| `eth_defi/testing/evm_snapshot_fixture.py` | `evm_snapshot_revert()` — per-test EVM state reset on a shared fork |
| `tests/conftest.py` | exposes the pool as the session-scoped `anvil_fork_pool` fixture |

## 1. Read-only characterisation test (the common case)

A test that only *reads* a vault (name, symbol, fees, TVL) should fork the
**canonical midnight block for its chain** and take its `web3` from the pool.
All same-chain tests carrying the same `xdist_group` marker then share one Anvil
process on one xdist worker under `--dist loadgroup`.

**Reference: [`tests/erc_4626/vault_protocol/test_goat.py`](../../tests/erc_4626/vault_protocol/test_goat.py)**
(and `test_harvest.py`, `test_cap.py`, and the other Arbitrum/Ethereum
`vault_protocol` tests).

```python
import os
import pytest
from web3 import Web3

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    # Same string for every test sharing this (chain, block) so --dist loadgroup
    # co-locates them on one worker and they share one Anvil.
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)
```

Rules of thumb:

- **Use your chain's `*_MIDNIGHT_BLOCK` constant**, not a hand-picked block, so
  you share the fork and the warm cache with every other same-chain test.
- **The `xdist_group` string must be identical** across all modules sharing the
  fork: `fork:<chain>:midnight`.
- **Do not close the launch** — the pool owns it and tears every fork down at
  session end.
- Block-dependent assertions (share price, TVL, PnL) must match the values at
  the canonical block; read them once and hard-code them. Most metadata
  assertions (name/symbol/fees) are stable and need no change.

## 2. Adding a new chain

`fork_blocks.py` records the last block at/before `2026-07-24 00:00 UTC` per
chain. To add one, binary-search the archive node for that timestamp and add the
constant + a `MIDNIGHT_BLOCKS` entry:

```python
web3 = create_multi_provider_web3(os.environ["JSON_RPC_<CHAIN>"])
target = int(datetime.datetime(2026, 7, 24, tzinfo=datetime.timezone.utc).timestamp())
lo, hi, ans = 1, web3.eth.block_number, 1
while lo <= hi:
    mid = (lo + hi) // 2
    if web3.eth.get_block(mid)["timestamp"] <= target:
        ans, lo = mid, mid + 1
    else:
        hi = mid - 1
# -> add ans to fork_blocks.py
```

**Chains without archive history (e.g. Monad) cannot be normalised** — leave
those tests on their own fork.

## 3. Mutating tests on a shared fork: snapshot/revert

If tests mutate the fork (send transactions, deposit/redeem) but can still share
one long-lived fork, reset EVM state between tests with `evm_snapshot_revert`
via an autouse fixture. Snapshot/revert restores storage but **not** wall-clock
time — call `evm_setNextBlockTimestamp` yourself if you assert on it.

**Reference: [`tests/lagoon/conftest.py`](../../tests/lagoon/conftest.py)**

```python
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert

@pytest.fixture(scope="module")
def anvil_base_fork(anvil_fork_pool, ...) -> AnvilLaunch:
    # Pooled fork instead of a per-file fork_network_anvil launch.
    return anvil_fork_pool.get_launch(JSON_RPC_BASE, fork_block_number=..., unlocked_addresses=[...])

@pytest.fixture(autouse=True)
def _evm_snapshot(anvil_base_fork):
    yield from evm_snapshot_revert(anvil_base_fork)
```

> ⚠️ The repository has seen `pytest-xdist` hangs from *many* snapshot/revert
> cycles on a long-lived fork (see the `AnvilSnapshotState` docstring in
> `eth_defi/provider/anvil.py`). Validate on CI before converting a large group.

## 4. Deploy once per session

Expensive Safe/vault deployments (~30–90 s each) should be done **once per
worker** and reused, not redeployed per test. A module-scoped, session-cached
fixture deploys outside the per-test snapshot window (pytest instantiates
higher-scoped fixtures first), so the deployment survives every per-test revert
while each test still sees it pristine.

**Reference: `shared_automated_lagoon_vault` in
[`tests/lagoon/conftest.py`](../../tests/lagoon/conftest.py)**, used by
[`tests/lagoon/test_lagoon_flow_analysis.py`](../../tests/lagoon/test_lagoon_flow_analysis.py).

Use the per-test deploy fixture (e.g. `automated_lagoon_vault`) only when the
deployment *is* the test subject (custom parameters, deliberate misconfiguration).

## 5. The warm Foundry fork RPC cache

Anvil caches archive reads at a fixed block under
`~/.foundry/cache/rpc/<network>/<block>/storage.json`. Because all same-chain
tests share one canonical block, that cache is small and dense — warm runs replay
from disk and barely touch (and so are not throttled by) the upstream archive.
The committed seeds are generated with the CI-pinned Foundry/Anvil release
(`v1.3.2` at the time of writing); the cache format is release-sensitive, so
refresh a seed and its toolchain together.

### How persistence actually works (the graceful-shutdown requirement)

**Anvil only writes that cache on a graceful shutdown** (its Rust `Drop` flushes
`storage.json`). A `SIGKILL` discards it. For a long time our teardown
`SIGKILL`'d Anvil (`shutdown_hard`), so **the fork cache was never written** —
which is why CI kept cold-fetching every run and getting rate-limited (the
`read_timeout` fork-setup failures). Fixed: `AnvilLaunch.close()` now sends
`SIGTERM` and waits up to `ANVIL_GRACEFUL_SHUTDOWN_TIMEOUT` (5 s) for the flush,
then `SIGKILL`s as a fallback (bounded, so teardown cannot hang). With this,
every fork test persists its cache.

### The cache ships in the repo (the primary mechanism)

The warm cache is **committed** under `eth_defi/testing/rpc_cache_seed/<network>/
<block>/storage.json` (one per canonical midnight block). The session-autouse
`_seed_foundry_rpc_cache` fixture (`tests/conftest.py`,
`eth_defi/testing/rpc_cache.py`) copies it into `~/.foundry/cache/rpc` before any
fork launches, non-destructively (a warmer live file is never overwritten). So
**every runner starts warm — GitHub Actions, other CI, and local first runs
alike — with no GitHub-Actions-cache dependency.** The workflows carry **no**
`actions/cache` step for the fork RPC cache (only the immutable Foundry toolchain
is Actions-cached).

Locally the live cache in `~/.foundry/cache/rpc` also persists between runs and is
enriched automatically as you run tests (graceful shutdown, above).

### How to (re)create / update the committed seed

Regenerate after bumping a `*_MIDNIGHT_BLOCK`, adding a chain, or to enrich
coverage (the committed seed is fork-init level for most chains; running the full
suite adds the contract state the tests read):

```shell
# 1. Warm the live cache by running the fork tests (each flushes on teardown).
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/ -m "not slow"

# 2. Copy the midnight-block dirs into the committed seed (mirror <network>/<block>/).
#    Only the canonical MIDNIGHT_BLOCKS; each storage.json is small (KBs).
#    e.g. for each chain:
cp -r ~/.foundry/cache/rpc/mainnet/25598869 eth_defi/testing/rpc_cache_seed/mainnet/

# 3. Commit.
git add eth_defi/testing/rpc_cache_seed/ && git commit -m "test: refresh fork RPC cache seed"
```

Network directory names are Foundry's own (`mainnet`, `arbitrum`, `base`, `bsc`,
`avalanche`, `plasma`, `hyperliquid`, `sonic`, `berachain`, `polygon`, …) — copy
whatever `~/.foundry/cache/rpc` created so the paths match on every runner.

### How to purge it

- **Committed seed:** `git rm -r eth_defi/testing/rpc_cache_seed/<network>/<block>/`
  (drop a stale block after bumping a midnight constant).
- **Local live cache:** `rm -rf ~/.foundry/cache/rpc` (or one
  `.../<network>/<block>/`); it re-warms from the committed seed + test runs.
- There is no Actions cache to purge — it was removed in favour of the committed
  seed.

### Keep generic policy tests out of protocol-specific historical forks

The canonical module documentation explains why a Guard, Safe, permission or
calldata-validation assertion must not inherit a protocol lifecycle fixture's
exceptional historical block, and how to split it onto the shared canonical
fork: see **“Generic policy assertions must not inherit lifecycle fork
exceptions”** in [`anvil_fork_pool.py`](anvil_fork_pool.py). The standard
ERC-4626 Guard policy test named there is the reference implementation.

## Anvil failure modes

This section is the troubleshooting checklist for Anvil fork failures. A fixed
fork block and a committed `storage.json` seed make a test reproducible, but do
not make it an offline replay: bootstrap checks and cache misses can still make
live archive RPC calls. A failure that only appears in CI is therefore not
automatically an Anvil or library regression.

### Cold-fork read timeouts (the "out of credits" red herring)

The vault-protocol / GMX jobs sometimes fail at fork setup with a 60-second
`eth_chainId` read timeout. The error historically hinted "you might be out of
API credits" — this is **misleading**. The classified `failure_mode` is
`read_timeout`: Anvil is blocked initialising its fork against the upstream
archive and does not answer the first call in time.

`scripts/measure-cold-fork-time.py` measures the real cost. Locally (anvil
1.7.1, 2026-07-25) a single cold fork of a midnight block completes in **~3 s**,
and **six concurrent** cold forks stay ~2.5 s each. So the 60 s read timeout is
already ~20× a healthy cold fork — it is **sufficient**, and raising it only
delays the failure. Do not raise it to mask a slow provider.

A 60 s timeout in CI therefore means the **upstream provider is slow or
rate-limiting the runner IP**, not that credits are exhausted — and CI was
throttled because it cold-fetched *every* run. The **root cause was that the fork
cache was never written** (Anvil `SIGKILL`'d before it could flush); the
graceful-shutdown fix in section 5 lets the cache warm across runs, which is the
primary remedy. Also useful: two space-separated `JSON_RPC_*` providers per chain
for failover, or a provider that does not throttle the CI IP. Run the measurement
script from a machine with the CI RPC secrets to compare against the ~3 s
baseline.

### Provider failures and cache misses

HTTP 500 responses, `header for hash not found`, connection errors, and a
`read_timeout` that disappears when the same fixed-block test is rerun locally
with the committed seed are upstream-provider or runner symptoms. A fixed block
still needs the provider for every reply not present in `storage.json`; a sparse
seed therefore does not protect the test from a bad provider. Do not increase
the local Web3 timeout to hide this failure. Instead, inspect the automatic
proxy's provider warnings, configure more than one `JSON_RPC_*` endpoint, and
warm and commit the missing replies with the exact pinned Anvil version.

If the timeout cannot be reproduced locally with the same fixed block, Anvil
release, cache seed, and comparable concurrency, treat it as an upstream or CI
provider problem until Anvil's own logs show evidence to the contrary. The
original investigation and representative full tracebacks are recorded in
[`PR #1589`](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1589).

### Local Anvil wedges and localhost timeouts

The `localhost` URL in a test traceback only identifies the test-to-Anvil hop;
Anvil may itself be blocked waiting for an upstream archive response. Use the
captured proxy warnings and Anvil logs to distinguish that case from a genuinely
wedged local process:

- an upstream URL, HTTP response, or provider timeout in the logs means the
  cache/provider path needs attention;
- a warm fixed-block fork whose upstream requests have completed but which still
  cannot answer `eth_chainId` or a custom RPC request is a local Anvil wedge;
- a reused pooled fork should be disposed and relaunched. The
  `AnvilForkPool` liveness probe does this automatically for reused forks; do not
  keep retrying a dead local process or blame the archive provider without
  evidence.

Repeated snapshot/revert cycles can also degrade a long-lived fork under
`pytest-xdist`. Keep the warning in the `AnvilSnapshotState` docstring in mind
and use a fresh fork when a test leaves the process unresponsive.

### Saved-state and Foundry release incompatibility

`anvil_dumpState`/`anvil_loadState` saved states and the fork `storage.json`
cache are Anvil-release-sensitive artefacts. Pair a checked-in
`*.anvilstate` file, the fork RPC seed, and the Foundry/Anvil binary that wrote
them. In the compatibility investigation, Foundry `v1.5.0` and `v1.7.1` timed
out in local `anvil_loadState` while restoring
`tests/aave_v3/aave_v3_deployment.anvilstate`; Foundry `v1.8.3` introduced a
broader set of Anvil-dependent regressions. The CI-compatible release used by
the repository is `v1.3.2`.

These failures are local saved-state compatibility failures, not archive RPC
failures. When changing Foundry, regenerate saved states and fixed-block seeds
together, then run the complete affected integration group. See the evidence
and attempted-version history in
[`PR #1589`](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1589).

### Graceful shutdown and incomplete seeds

Anvil writes `storage.json` only during graceful shutdown. A `SIGKILL`, a
crashed process, or a teardown that reaches the bounded `SIGKILL` fallback can
leave a seed missing or incomplete. That condition causes later runs to make
live provider calls; it is not proof that the provider was healthy or unhealthy.
Use `AnvilLaunch.close()`, inspect the resulting file, and refresh the seed from
a clean cache before diagnosing a provider failure.

The cache-seed layout and refresh procedure live in
[`eth_defi/testing/rpc_cache_seed/README.md`](rpc_cache_seed/README.md).

## 6. The committed token cache (ERC-20 + vault token addresses)

Separate from the fork state cache above, vault **token** lookups are cached at
two levels — and both only engage when the vault's `token_cache` is a
`TokenDiskCache`:

- vault → denomination / share **token address** resolution
  (`eth_defi/erc_4626/vault_token.py`), gated on
  `isinstance(self.token_cache, TokenDiskCache)` in `eth_defi/erc_4626/vault.py`;
- ERC-20 **metadata** (`name` / `symbol` / `decimals` / `supply`) via
  `fetch_erc20_details()`.

The library default (`DEFAULT_TOKEN_CACHE`) is an in-memory `cachetools.LRUCache`,
which **silently disables the address-resolution cache** and starts empty in
every process — including each xdist worker. Vaults therefore re-read token
addresses and metadata over RPC on every cold fork.

So the test session installs a committed `TokenDiskCache`
(`eth_defi/testing/token_cache_seed/tokens.sqlite`) as the vault default, via the
`_seed_token_cache` session fixture (`eth_defi/testing/token_cache.py`). Each
xdist worker gets its own copy in a temp dir, so workers never contend on one
SQLite file and the committed seed is never mutated in place.

### Rebuild / update

The seed is regenerated from a real run, not hand-maintained:

```shell
source .local-test.env && \
    ETH_DEFI_TOKEN_CACHE_REBUILD=1 \
    poetry run pytest tests/erc_4626/vault_protocol/ -m "not slow"
git add eth_defi/testing/token_cache_seed/
```

Everything resolved during the session is merged into the seed at teardown
(existing entries preserved, only new keys added). Run it against a responsive
provider — a throttled one resolves fewer tokens and yields a thinner seed.

### Purge / disable

- **Purge:** `git rm eth_defi/testing/token_cache_seed/tokens.sqlite` — it is
  regenerated by the rebuild command; a missing seed is a safe no-op.
- **Disable** (e.g. to measure cold behaviour):
  `ETH_DEFI_TOKEN_CACHE_DISABLE=1 poetry run pytest ...`

## When NOT to normalise / share

- The test **deposits/redeems and needs impersonated signers** the shared
  read-only fork doesn't set up (e.g. it was reverted from the shared pool) —
  give it its own fork or wire the signer setup.
- A **value invariant no longer holds** at the canonical block (e.g. a lending
  vault is over-utilised there) — keep the test's own block.
- The vault is **epoch/phase-dependent** and behaves differently at a later
  block — keep its own block.
- The chain has **no archive history** (Monad) — cannot use a fixed block.

## See also

- `docs/README-test-suite-performance.md` — the wider plan and CI measurements.
- `docs/README-hypersync-tests.md` — the slow Hypersync scans (disabled on CI).
