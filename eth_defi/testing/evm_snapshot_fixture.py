"""EVM snapshot/revert helper for per-test isolation on module-scope Anvil forks.

Use this when a test module shares one Anvil fork fixture (``scope="module"``)
to keep per-test state isolation cheap. Each test brackets in ``evm_snapshot``
and reverts on exit, so the fork warms up once per module instead of once per
test.

Usage in a conftest or test file:

.. code-block:: python

    import pytest
    from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
    from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert


    @pytest.fixture(scope="module")
    def anvil_base_fork(...) -> AnvilLaunch:
        launch = fork_network_anvil(JSON_RPC_BASE, fork_block_number=N, ...)
        try:
            yield launch
        finally:
            launch.close()


    @pytest.fixture(autouse=True)
    def _evm_snapshot(anvil_base_fork):
        yield from evm_snapshot_revert(anvil_base_fork)

See the Anvil fork caching design doc for the full strategy:
``docs/superpowers/specs/2026-05-27-anvil-fork-caching-design.md``
"""

import logging
from collections.abc import Iterator

from web3 import HTTPProvider, Web3

logger = logging.getLogger(__name__)


def evm_snapshot_revert(fork) -> Iterator[None]:
    """Snapshot EVM state before, revert after — generator helper for autouse fixtures.

    Yields once after taking the snapshot, then reverts on resume. Designed to
    be used with ``yield from`` inside a per-file
    ``@pytest.fixture(autouse=True)`` so each test sees a clean state on a
    module-scope Anvil fork.

    :param fork:
        Object with a ``json_rpc_url`` attribute, typically
        :class:`~eth_defi.provider.anvil.AnvilLaunch`. The fixture that yields
        ``fork`` should itself be ``scope="module"`` or ``scope="session"`` —
        otherwise the snapshot/revert dance buys nothing.

    :return:
        Generator yielding ``None`` once, then performing the revert on resume.

    :raises RuntimeError:
        If the ``evm_snapshot`` RPC call returns a non-result (e.g. an older
        Anvil build or a non-Anvil backend snuck in via a different fixture).

    .. note::

        ``evm_revert`` restores EVM state and storage but **does not** reset
        block timestamp. Tests asserting on ``block.timestamp == X`` must call
        ``evm_setNextBlockTimestamp`` themselves.

    .. seealso::

        `Anvil custom JSON-RPC methods
        <https://book.getfoundry.sh/anvil/#custom-methods>`_
    """
    url = fork if isinstance(fork, str) else fork.json_rpc_url
    web3 = Web3(HTTPProvider(url))
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
