"""Session-scoped Anvil fork pool for reusing forks across test modules.

**This module docstring is the canonical, authoritative description of the
shared Anvil fork + fixed fork-block + warm RPC-cache test pattern.** Other
places in the repository (``CLAUDE.md``, the ``add-vault-protocol`` skill,
:mod:`eth_defi.testing.fork_blocks`, :file:`eth_defi/testing/README.md`) only
point here; do not duplicate the rationale — update this docstring instead.

Reusable testing helper (kept under ``eth_defi`` rather than ``tests`` per the
repository convention) that lets many tests sharing the same
``(chain, fork_block_number, launch config)`` reuse a single Anvil process
instead of each launching (and archive-replaying) its own. This is Lever 1 of
the test-suite performance plan (:file:`docs/README-test-suite-performance.md`).

The pytest fixture wrapper lives in the top-level ``tests/conftest.py``; this
module holds only the reusable pool class.

Required pattern for new Anvil mainnet-fork tests
-------------------------------------------------

Any new Anvil mainnet-fork characterisation test **must** use this pattern
rather than launching its own per-file fork at a chain-tip / ad-hoc block:

1. **Fork at a fixed, shared block, never ``latest``.** Use your chain's
   canonical ``*_MIDNIGHT_BLOCK`` constant from
   :mod:`eth_defi.testing.fork_blocks` (via :func:`get_midnight_block` for a
   programmatic lookup). A mutable chain tip is non-reproducible, cannot be
   shared, and defeats the RPC cache. A fixed block also lets value assertions
   use exact numbers (per the repository test rules) rather than fuzzy bounds.
2. **Obtain Web3 from the shared pool**, not from a private
   ``fork_network_anvil`` call, so every same-chain, same-block test reuses one
   Anvil process. Have the module's ``web3`` fixture call
   :meth:`AnvilForkPool.get_web3` with the session-scoped ``anvil_fork_pool``
   fixture.
3. **Carry the matching ``xdist_group`` marker** so ``--dist loadgroup`` (used
   in CI) co-locates all sharers on one worker — session scope is per worker, so
   without the marker the "shared" fork silently forks once per worker. Use one
   stable group name per (chain, block), e.g.
   ``@pytest.mark.xdist_group("fork:ethereum:midnight")``.

Why this matters — the warm RPC cache
--------------------------------------

Anvil persists every ``eth_call`` / ``eth_getStorageAt`` archive response it
replays to ``~/.foundry/cache/rpc/<network>/<block>/``. When many
characterisation tests fork **the same fixed block**, they read overlapping
state, so that on-disk cache becomes dense and later runs replay from disk
instead of re-hammering the upstream archive node. CI restores and re-saves this
directory across runs (see the "Foundry fork RPC cache" steps in
``.github/workflows/test-vault-protocol.yml`` and
``docs/README-test-suite-performance.md``), so a warm cache turns cold-archive
stalls (the ~476 s startup seen on cold Ethereum archive forks) into ~seconds.

Forking ``latest`` or a per-test arbitrary block breaks all three benefits: the
cache key never repeats, nothing is shared, and every run pays full
archive-replay latency. That is why the fixed shared block is mandatory.

Bounded provider retries
------------------------

Pooled forks must also fail within a bounded period when an upstream archive
provider is unavailable. For multi-provider configurations, the pool gives the
Anvil RPC proxy one attempt per configured provider. The Web3 client then
retries the local Anvil only once by default because the proxy has already
performed upstream failover. Callers with legitimately slow fork operations can
override the Web3 retry count and HTTP timeout in :meth:`AnvilForkPool.get_web3`.

Reference tests to copy
-----------------------

- Read-only pooled fork: ``tests/erc_4626/vault_protocol/test_goat.py`` and
  ``tests/erc_4626/vault_protocol/test_aarna.py``.
- Shared fork plus once-per-session expensive deployment (Safe/Lagoon):
  ``tests/lagoon/conftest.py``.

Copy-paste module skeleton
--------------------------

.. code-block:: python

    import os

    import pytest
    from web3 import Web3

    from eth_defi.testing.anvil_fork_pool import AnvilForkPool
    from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK

    JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

    pytestmark = [
        pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
        # Co-locate every same-block Ethereum sharer on one xdist worker.
        pytest.mark.xdist_group("fork:ethereum:midnight"),
    ]


    @pytest.fixture(scope="module")
    def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
        # Read-only test: shares one Anvil fork from the session pool, so no
        # snapshot/revert reset is needed between tests.
        return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)

Design notes:

- **Opt-in, never autouse.** A test module's own ``web3`` fixture calls the pool
  explicitly. An ``autouse=True`` restore fixture in a shared ``conftest.py``
  causes ``ScopeMismatch`` when sibling modules override ``web3`` at function
  scope.
- **Pin sharers to one xdist worker.** ``--dist loadgroup`` (used in CI) sends
  all tests marked with the same ``xdist_group`` to one worker, and pytest
  session scope is per worker — so tests sharing a fork must carry an identical
  ``@pytest.mark.xdist_group("fork:<chain>:<block>")`` marker.
- **The registry key is the full launch config**, not just ``(chain, block)``:
  :func:`eth_defi.provider.anvil.fork_network_anvil` is a thin alias of the fully
  configurable ``launch_anvil``, so differing hardfork / gas / unlocked-account /
  tracing options must not collide on one cached process.

.. warning::

    **Proof-of-concept, gated on CI.** The repository documents that repeated
    snapshot/revert cycles on a long-lived, module/session-scoped fork can
    degrade Anvil responsiveness and hang CI under ``pytest-xdist`` (see the
    ``AnvilSnapshotState`` docstring in :mod:`eth_defi.provider.anvil`). The
    initial proof-of-concept only shares forks between **read-only** tests, which
    do not mutate fork state and therefore need no snapshot/revert between tests.
    Mutating tests that share a fork must additionally reset it with
    :func:`eth_defi.testing.evm_snapshot_fixture.evm_snapshot_revert` or
    :func:`eth_defi.provider.anvil.reset_anvil_snapshot`; that path is not yet
    wired here, pending a bounded CI run that proves no xdist hang.
"""

import dataclasses
from typing import Any

from web3 import Web3

from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.provider.rpc_proxy import RPCProxy, RPCProxyConfig

#: Maximum duration of one request to an upstream archive provider.
POOL_PROXY_TIMEOUT: float = 15.0

#: Do not multiply an already bounded Anvil/proxy failure through six Web3 retries.
POOL_WEB3_RETRIES: int = 1

#: Preserve the established connect/read timeout for legitimate cold-cache calls.
POOL_WEB3_HTTP_TIMEOUT: tuple[float, float] = (3.0, 60.0)


def _freeze(value: Any) -> Any:
    """Recursively turn a value into a hashable, order-stable form.

    Used to build the pool's cache key from arbitrary ``launch_anvil`` keyword
    arguments — some of which are lists (e.g. ``unlocked_addresses``) or dicts —
    which are otherwise unhashable and cannot be used in a ``dict`` key.

    :param value:
        Any launch-argument value.

    :return:
        A hashable representation (lists/tuples become tuples, dicts become
        sorted key/value tuples, sets become sorted tuples, scalars unchanged).
    """
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, RPCProxyConfig):
        return (
            RPCProxyConfig,
            tuple((field.name, _freeze(getattr(value, field.name))) for field in dataclasses.fields(value)),
        )
    if isinstance(value, RPCProxy):
        return (RPCProxy, id(value))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(_freeze(v) for v in value))
    return value


@dataclasses.dataclass(slots=True)
class AnvilForkPool:
    """Registry of shared Anvil forks keyed by launch configuration.

    One :class:`~eth_defi.provider.anvil.AnvilLaunch` is created per distinct
    launch configuration and reused for every caller (on the same xdist worker)
    that requests it. Call :meth:`close_all` once to tear every launch down.

    Intended for **read-only** fork tests in its current form; see the module
    warning for the mutating-test caveat.
    """

    #: Cached launches keyed by (rpc_url, fork_block_number, sorted launch kwargs).
    launches: dict[tuple, AnvilLaunch] = dataclasses.field(default_factory=dict)

    def get_launch(
        self,
        rpc_url: str,
        fork_block_number: int,
        **launch_kwargs: Any,
    ) -> AnvilLaunch:
        """Return a shared Anvil launch for this exact launch configuration.

        Launches Anvil lazily on the first request for a configuration and
        returns the cached process on every subsequent request.

        :param rpc_url:
            Upstream archive JSON-RPC URL to fork from.

        :param fork_block_number:
            Fixed block to fork at. Required — a mutable chain tip cannot be
            shared safely.

        :param launch_kwargs:
            Any other state-affecting ``fork_network_anvil`` arguments; they are
            part of the cache key so incompatible configs never share a process.

        :return:
            The shared :class:`~eth_defi.provider.anvil.AnvilLaunch`.
        """
        configured_rpc_urls = rpc_url.split()
        standard_rpc_urls = [url for url in configured_rpc_urls if not url.startswith("mev+")]
        available_rpc_count = len(standard_rpc_urls or configured_rpc_urls)
        if available_rpc_count > 1 and "proxy_multiple_upstream" not in launch_kwargs:
            # Bound the inner retry layer. The default proxy budget can take
            # around 90 seconds; combining that with Web3's default six
            # localhost retries made one unavailable archive request stall a
            # pooled xdist group for about eight minutes. Try each configured
            # upstream once before returning the error to Anvil.
            launch_kwargs["proxy_multiple_upstream"] = RPCProxyConfig(
                timeout=POOL_PROXY_TIMEOUT,
                retries=available_rpc_count,
            )

        key = (rpc_url, fork_block_number, _freeze(launch_kwargs))
        launch = self.launches.get(key)
        if launch is None:
            launch = fork_network_anvil(
                rpc_url,
                fork_block_number=fork_block_number,
                **launch_kwargs,
            )
            self.launches[key] = launch
        return launch

    def get_web3(
        self,
        rpc_url: str,
        fork_block_number: int,
        *,
        web3_retries: int = POOL_WEB3_RETRIES,
        web3_http_timeout: tuple[float, float] = POOL_WEB3_HTTP_TIMEOUT,
        **launch_kwargs: Any,
    ) -> Web3:
        """Return a fresh Web3 pointed at a shared Anvil fork.

        The underlying Anvil process is shared via :meth:`get_launch`; the
        returned :class:`web3.Web3` object itself is created per call and is not
        shared.

        :param rpc_url:
            Upstream archive JSON-RPC URL to fork from.

        :param fork_block_number:
            Fixed block to fork at.

        :param web3_retries:
            Number of outer retries against the local Anvil endpoint. Keep this
            low because the inner RPC proxy already performs provider failover.

        :param web3_http_timeout:
            Connect and read timeout for local Anvil requests. Override this for
            a known slow cold-cache operation.

        :param launch_kwargs:
            Additional ``fork_network_anvil`` arguments (part of the cache key).

        :return:
            A :class:`web3.Web3` connected to the shared Anvil RPC endpoint.
        """
        launch = self.get_launch(rpc_url, fork_block_number, **launch_kwargs)
        return create_multi_provider_web3(
            launch.json_rpc_url,
            default_http_timeout=web3_http_timeout,
            retries=web3_retries,
        )

    def close_all(self) -> None:
        """Tear down every launched Anvil process.

        Every launch is attempted even if an earlier one fails to close, so a
        single teardown error cannot leak the remaining processes.
        """
        launches = list(self.launches.values())
        self.launches.clear()
        errors: list[BaseException] = []
        for launch in launches:
            try:
                launch.close()
            except (OSError, AssertionError) as e:
                # Anvil teardown is best-effort. shutdown_hard() raises
                # AssertionError if the process will not terminate in time and
                # OSError on process/socket issues; record and continue so one
                # wedged process does not leak the rest. Re-raised below.
                errors.append(e)
        if errors:
            raise errors[0]
