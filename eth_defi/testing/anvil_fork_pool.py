"""Session-scoped Anvil fork pool for reusing forks across test modules.

Reusable testing helper (kept under ``eth_defi`` rather than ``tests`` per the
repository convention) that lets many tests sharing the same
``(chain, fork_block_number, launch config)`` reuse a single Anvil process
instead of each launching (and archive-replaying) its own. This is Lever 1 of
the test-suite performance plan (:file:`docs/README-test-suite-performance.md`).

The pytest fixture wrapper lives in the top-level ``tests/conftest.py``; this
module holds only the reusable pool class.

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
        key = (rpc_url, fork_block_number, tuple(sorted(launch_kwargs.items())))
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

        :param launch_kwargs:
            Additional ``fork_network_anvil`` arguments (part of the cache key).

        :return:
            A :class:`web3.Web3` connected to the shared Anvil RPC endpoint.
        """
        launch = self.get_launch(rpc_url, fork_block_number, **launch_kwargs)
        return create_multi_provider_web3(launch.json_rpc_url)

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
            except OSError as e:
                # Anvil teardown is best-effort; record and continue so one
                # wedged process does not leak the rest. Re-raised below.
                errors.append(e)
        if errors:
            raise errors[0]
