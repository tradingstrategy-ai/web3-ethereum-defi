# Fast Anvil fork tests: shared forks, block cache and session deployments

This directory holds the reusable helpers that make Anvil mainnet-fork tests
fast. Fork tests are slow for two reasons — launching Anvil replays archive
state, and every test hitting the upstream archive node adds latency and can be
rate-limited. The helpers here attack both:

1. **Shared, session-scoped forks** — many tests reuse *one* Anvil process
   instead of launching one each.
2. **A canonical block per chain** — so those tests share a fork *and* their
   archive reads land in a dense, reusable on-disk cache.
3. **The Foundry fork RPC cache** — persisted across CI runs so warm runs barely
   touch the upstream archive.
4. **Per-test snapshot/revert** — cheap state isolation on a shared fork.

If you are writing a new fork test, read this before copying an old per-file
`fork_network_anvil` fixture.

> **Canonical reference.** The authoritative, single-source description of this
> pattern — the required rules, the rationale (warm CI RPC cache) and a
> copy-paste module skeleton — is the module docstring of
> [`eth_defi/testing/anvil_fork_pool.py`](anvil_fork_pool.py). This README is a
> practical companion; when the two disagree, the docstring wins.

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

## Cold-fork read timeouts (the "out of credits" red herring)

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
