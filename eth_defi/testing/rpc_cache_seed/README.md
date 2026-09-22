# Repository-supplied Anvil fork RPC cache seed

Committed Foundry (Anvil) fork RPC cache files, so **every** runner — GitHub
Actions, other CI, and local first runs — starts warm for the canonical
`*_MIDNIGHT_BLOCK` fork blocks instead of cold-fetching from (and being throttled
by) the upstream archive node. This is the **primary** warm-cache mechanism; the
workflows carry no `actions/cache` step for the fork RPC cache.

The `_seed_foundry_rpc_cache` session fixture (`tests/conftest.py`) copies this
tree into `~/.foundry/cache/rpc` before any fork launches. See
`eth_defi/testing/rpc_cache.py` (the mechanism), `eth_defi/testing/README.md`
section 5 (how to regenerate/update/purge), and
`docs/README-test-suite-performance.md` (why it matters).

Anvil only writes these files on a graceful shutdown, so regenerating the seed
depends on the graceful-shutdown behaviour in `AnvilLaunch.close()`.

## Layout

Mirror Foundry's on-disk cache layout exactly — keyed by Foundry's **network
name** (not chain id), then block, then the cache file:

```
rpc_cache_seed/
  mainnet/25598869/storage.json
  arbitrum/487039644/storage.json
  base/30659990/storage.json  # Lagoon Base lifecycle characterisation
  base/49030926/storage.json
  base/51649628/storage.json  # Lagoon v1 production characterisation
  bsc/111758906/storage.json  # BUSD fork state for tests/rpc/test_anvil.py
  ...
```

At session start `seed_default_foundry_rpc_cache()` copies this tree into
`~/.foundry/cache/rpc/`, **never overwriting** an existing (warmer) live file.

`base/51649628/` is an intentional exception to the canonical midnight-block
set. It is the fixed Base block at which the production Lagoon `v1.0.0` vault
was observed failing, and is required by
`tests/lagoon/test_lagoon_v1.py`. The canonical Base midnight block predates
that deployment, so replacing this seed with a midnight block would not test
the production compatibility boundary.

`base/30659990/` is another intentional fixed-block exception. It is the
historical Base block used by the shared Lagoon fork fixture in
`tests/lagoon/conftest.py` and by some Guard integration tests. The seed contains
the reads captured from the Lagoon lifecycle and selected Guard paths. It
reduces cold archive reads but is not an exhaustive cache of every test at this
block; cache misses still use the configured provider.

The BSC midnight seed contains the BUSD contract code, the unlocked historical
holder account, and the metadata and balance slots used by
`tests/rpc/test_anvil.py`. Bootstrap checks still reach the configured provider,
through the bounded failover proxy when multiple providers are configured.
Anvil uses the seed for captured BUSD state and its configured provider for any
cache misses.

## Capturing a seed file

1. Run the relevant fork test locally against a responsive archive provider so
   Anvil populates `~/.foundry/cache/rpc/<network>/<block>/`.
2. Copy the resulting `<network>/<block>/` directory here, keeping the layout.
3. Keep files small and dense — canonical midnight blocks plus explicitly
   justified fixed-block characterisation exceptions such as the Lagoon v1
   block above. Do not commit mutable-tip (`latest`) fork caches; they are
   non-reproducible and never re-used.

An external (uncommitted, larger) seed directory can also be supplied at runtime
via the `ETH_DEFI_RPC_CACHE_SEED_DIR` environment variable.

## Archive bootstrap and cache invariant

Stored state covers repeatable historical reads after Anvil starts. The initial
chain-identity check, and any enabled archive-availability check, are
necessarily remote. With multiple providers, they must use the same bounded
failover proxy as Anvil; no setup check may make the first provider a single
point of failure. To refresh a seed, start with an empty Foundry cache
directory, run its complete fixed-block integration group, then commit the
`storage.json` written when Anvil closes cleanly.
