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
  base/49030926/storage.json
  ...
```

At session start `seed_default_foundry_rpc_cache()` copies this tree into
`~/.foundry/cache/rpc/`, **never overwriting** an existing (warmer) live file.

## Capturing a seed file

1. Run the relevant fork test locally with a warm archive so Anvil populates
   `~/.foundry/cache/rpc/<network>/<block>/`.
2. Copy the resulting `<network>/<block>/` directory here, keeping the layout.
3. Keep files small and dense — only the canonical midnight blocks. Do not
   commit mutable-tip (`latest`) fork caches; they are non-reproducible and
   never re-used.

An external (uncommitted, larger) seed directory can also be supplied at runtime
via the `ETH_DEFI_RPC_CACHE_SEED_DIR` environment variable.

## Archive provider invariant

Stored state covers repeatable historical reads made after an Anvil fork has
started. Bootstrap remains allowed to contact the configured archive providers:
it confirms the chain identity and that the requested block is available. When
multiple space-separated providers are configured, both bootstrap and later
Anvil requests must go through the same bounded failover proxy; no setup check
may turn the first provider into a single point of failure. Refresh a seed by
running its complete fixed-block integration group with an empty cache
directory, then commit the gracefully flushed `storage.json` it produces.
