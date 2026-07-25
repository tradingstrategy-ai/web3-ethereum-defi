# Repository-supplied Anvil fork RPC cache seed

Optional default Foundry (Anvil) fork RPC cache files, committed so a **cold**
CI cache or a fresh checkout starts warm for the canonical `*_MIDNIGHT_BLOCK`
fork blocks instead of re-hammering the upstream archive node.

See `eth_defi/testing/rpc_cache.py` (the seeding mechanism) and
`docs/README-test-suite-performance.md` (why the warm cache matters).

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
