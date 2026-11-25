# Diagnosing HyperEVM fork timeouts

A HyperEVM fork can start successfully and stall later when a client requests the fork block with full transactions. In a March 2026 simulation at block 30,356,898, header and account reads succeeded before a local Anvil request timed out. The logs suggested Anvil was waiting for the upstream full block. They did not establish whether the upstream provider or Anvil caused the stall.

Use the [Anvil launcher](../../eth_defi/provider/anvil.py) with `warm_up_block=True` to request the full fork block during startup. With `verbose=True` and `inherit_stdio=True`, the launcher forwards child output to the parent logs. Compare those messages with the proxy's upstream request timings to see which request is outstanding.

The [HyperEVM warm-up test](../../tests/rpc/test_anvil_hyperliquid_warmup.py) reads the same block again after startup. In the March run, the fork pinned block 30,359,545, an empty block. The second read took 13 ms. The proxy saw two block requests in total, consistent with Anvil serving the later read locally. A block with transactions may take longer.

See the [fork testing guide](../../eth_defi/testing/README.md#anvil-failure-modes) for other causes of stalled fork tests.
