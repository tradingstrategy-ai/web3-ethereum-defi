Test helper API
---------------

Reusable helpers for writing fast, reliable Anvil mainnet-fork tests. These live
under ``eth_defi.testing`` rather than ``tests`` so they can be imported from
test suites in this repository and in downstream projects.

The canonical, authoritative description of the shared-fork pattern — fixed fork
block, shared Anvil process, warm RPC cache — is the module docstring of
:py:mod:`eth_defi.testing.anvil_fork_pool`. ``eth_defi/testing/README.md`` in
the source tree has the step-by-step how-to, and
``docs/README-test-suite-performance.md`` the background and measurements.

Three caching layers cooperate to keep fork tests off the upstream archive node:

- **Shared forks** — :py:mod:`eth_defi.testing.anvil_fork_pool` reuses one Anvil
  process per ``(chain, block, launch config)``, probes a reused fork for
  liveness and recycles it if it has wedged.
- **Fork state cache** — :py:mod:`eth_defi.testing.rpc_cache` seeds Anvil's
  on-disk fork RPC cache (``~/.foundry/cache/rpc``) from cache files committed to
  the repository, so a cold runner starts warm. Anvil only flushes that cache on
  a graceful shutdown, which is why
  :py:meth:`eth_defi.provider.anvil.AnvilLaunch.close` terminates it gently.
- **Token metadata cache** — :py:mod:`eth_defi.testing.token_cache` installs a
  committed :py:class:`eth_defi.token.TokenDiskCache` as the default vault token
  cache, so vaults do not re-resolve token addresses and metadata over RPC in
  every process.

Fixed fork blocks shared by the characterisation tests are defined in
:py:mod:`eth_defi.testing.fork_blocks`, and per-test EVM state isolation is
provided by :py:mod:`eth_defi.testing.evm_snapshot_fixture`.

.. autosummary::
   :toctree: _autosummary_testing
   :recursive:

   eth_defi.testing.anvil_fork_pool
   eth_defi.testing.fork_blocks
   eth_defi.testing.rpc_cache
   eth_defi.testing.token_cache
   eth_defi.testing.evm_snapshot_fixture
