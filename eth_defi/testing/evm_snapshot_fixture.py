"""Shared autouse fixture for per-test EVM state isolation on module-scope Anvil.

This module provides :func:`make_evm_snapshot_fixture` — a factory that creates
an autouse pytest fixture wrapping each test in an ``evm_snapshot`` /
``evm_revert`` pair.

The motivation is CI cost reduction: many test fixtures that wrap
:func:`~eth_defi.provider.anvil.fork_network_anvil` are function-scoped,
spawning a fresh Anvil per test and re-warming the fork from the archive RPC.
Bumping the fork fixture to ``scope="module"`` and adding an autouse
snapshot/revert keeps per-test isolation but spawns Anvil once per module —
typically a 30-50% wall-clock reduction on fork-heavy dirs.

Usage in a conftest or test file:

.. code-block:: python

    from eth_defi.testing.evm_snapshot_fixture import make_evm_snapshot_fixture


    @pytest.fixture(scope="module")
    def anvil_base_fork(...) -> AnvilLaunch:
        launch = fork_network_anvil(JSON_RPC_BASE, fork_block_number=N, ...)
        try:
            yield launch
        finally:
            launch.close()


    _evm_snapshot = make_evm_snapshot_fixture("anvil_base_fork")

The factory takes the *name* of the fork fixture (so it can request that
fixture as a dependency by string lookup) and returns an autouse function-scope
fixture.

See the Anvil fork caching design doc for the full strategy:
``docs/superpowers/specs/2026-05-27-anvil-fork-caching-design.md``
"""

import logging

import pytest
from web3 import HTTPProvider, Web3

logger = logging.getLogger(__name__)


def make_evm_snapshot_fixture(fork_fixture_name: str):
    """Return an autouse pytest fixture that snapshots EVM state per test.

    The returned fixture depends on ``fork_fixture_name`` (which must yield an
    object with a ``json_rpc_url`` attribute, e.g.
    :class:`~eth_defi.provider.anvil.AnvilLaunch`). Before each test it issues
    ``evm_snapshot``; after the test it reverts via ``evm_revert``. This pattern
    lets a module share one Anvil process while each test still sees a clean
    state.

    :param fork_fixture_name:
        Name of an existing module-scope or session-scope Anvil fixture in the
        same conftest/test file. Looked up via
        ``pytest.FixtureRequest.getfixturevalue``.

    :return:
        An autouse function-scope :func:`pytest.fixture` ready to bind to a
        module-level name.

    .. note::

        ``evm_revert`` restores EVM state and storage but **does not** reset
        block timestamp. Tests asserting on ``block.timestamp == X`` must call
        ``evm_setNextBlockTimestamp`` themselves; the snapshot pattern alone is
        insufficient for time-sensitive assertions.

    .. seealso::

        `Anvil custom JSON-RPC methods
        <https://book.getfoundry.sh/anvil/#custom-methods>`_ for the full list
        of ``evm_*`` and ``anvil_*`` methods.
    """

    @pytest.fixture(autouse=True)
    def _evm_snapshot(request):
        fork = request.getfixturevalue(fork_fixture_name)
        web3 = Web3(HTTPProvider(fork.json_rpc_url))
        snap_response = web3.provider.make_request("evm_snapshot", [])
        snap_id = snap_response.get("result")
        if snap_id is None:
            error = snap_response.get("error", {})
            msg = f"evm_snapshot failed: {error}"
            raise RuntimeError(msg)
        try:
            yield
        finally:
            revert_response = web3.provider.make_request("evm_revert", [snap_id])
            ok = revert_response.get("result")
            if ok is not True:
                logger.warning("evm_revert returned %s for snap %s", revert_response, snap_id)

    return _evm_snapshot
