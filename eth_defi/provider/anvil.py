"""Anvil integration.

_ ..anvil:

This module provides Python integration for Anvil.

- `Anvil <https://github.com/foundry-rs/foundry/tree/master?tab=readme-ov-file#anvil>`__
  is a blazing-fast local testnet node implementation in Rust from
  `Foundry project <https://github.com/foundry-rs/foundry>`__

- Anvil can replace :py:class:`eth_tester.main.EthereumTester` as the unit/integration test backend.

- Anvil is mostly used in mainnet fork test cases.

- Anvil is a more stable an alternative to Ganache (:py:mod:`eth_defi.ganache`)

- Anvil is part of `Foundry <https://github.com/foundry-rs/foundry>`__,
  a toolkit for Ethereum application development.

To install Anvil on:

.. code-block:: shell

    curl -L https://foundry.paradigm.xyz | bash
    PATH=~/.foundry/bin:$PATH
    foundryup  # Needs to be in path, or installation fails

This will install `foundryup`, `anvil` at `~/.foundry/bin` and adds the folder to your shell rc file `PATH`.

For more information see `Anvil reference <https://book.getfoundry.sh/reference/anvil/>`__.

See also :py:mod:`eth_defi.trace` for Solidity tracebacks using Anvil.

The code was originally lifted from Brownie project.
"""

import fcntl
import logging
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import warnings
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from subprocess import DEVNULL, PIPE
from typing import TYPE_CHECKING, Any, Optional, TextIO, Union

import psutil
import requests
from eth_typing import HexAddress
from requests.exceptions import ConnectionError as RequestsConnectionError
from web3 import HTTPProvider, Web3

from eth_defi.utils import is_localhost_port_listening, shutdown_hard

if TYPE_CHECKING:
    from eth_defi.provider.rpc_proxy import RPCProxy, RPCProxyConfig

logger = logging.getLogger(__name__)

#: Prefix for the per-port advisory lock files shared by pytest-xdist workers.
#:
#: The files themselves are not reservations. ``fcntl.flock()`` associates the
#: reservation with an open file descriptor and the operating system releases
#: it automatically if a worker crashes. Lock files are deliberately retained:
#: unlinking a live lock file could let another worker create a new inode for
#: the same port and acquire an independent lock.
ANVIL_PORT_LOCK_FILE_PREFIX = "web3-ethereum-defi-anvil-port"

#: Per-thread state tracking the last used RPC index for multi-RPC fork URLs.
#: This is a workaround for test flakiness on CI: when multiple RPC endpoints
#: are available (space-separated in fork_url), each call to launch_anvil()
#: rotates to the next RPC endpoint instead of always using the first one.
#: This spreads the load across RPC providers and avoids repeatedly hitting
#: a flaky endpoint across multiple test fixtures.
_anvil_rpc_state = threading.local()


@dataclass(slots=True, frozen=True)
class AnvilForkMetadata:
    """Metadata for a locally running Anvil JSON-RPC endpoint.

    This metadata lets later ``Web3`` objects created from only the local
    Anvil URL still report which upstream fork RPC providers Anvil was
    configured to use.

    :ivar chain_id:
        Chain id reported by the local Anvil instance after startup.

    :ivar upstream_rpc_urls:
        Original upstream RPC URLs passed to Anvil fork mode. If Anvil was
        started as a standalone local backend, this is empty.

    :ivar fork_block_number:
        Explicit fork block used for Anvil, if any.

    :ivar effective_fork_url:
        URL passed to Anvil as ``--fork-url``. With multiple upstreams this can
        be a local failover proxy URL instead of one of the upstream RPC URLs.
    """

    #: Chain id reported by the local Anvil instance after startup.
    chain_id: int | None

    #: Original upstream RPC URLs passed to Anvil fork mode.
    upstream_rpc_urls: tuple[str, ...]

    #: Explicit fork block used for Anvil, if any.
    fork_block_number: int | None

    #: URL passed to Anvil as ``--fork-url``.
    effective_fork_url: str | None


#: Protect the process-local Anvil metadata registry.
_anvil_launch_metadata_lock = threading.Lock()

#: Local Anvil JSON-RPC URL -> fork metadata.
#:
#: ``create_multi_provider_web3()`` often receives only
#: ``AnvilLaunch.json_rpc_url``. Without this registry, retry logs can only
#: show ``localhost:<port>`` and lose the upstream fork provider context.
#:
#: The returned :py:class:`AnvilLaunch` object is the canonical metadata source
#: for callers. This registry is a process-local mirror used to recover the same
#: metadata from a localhost URL later. HTTP providers receive a copied snapshot
#: for logging only.
_anvil_launch_metadata: dict[str, AnvilForkMetadata] = {}


@dataclass(slots=True)
class _AnvilPortLease:
    """Inter-process ownership lease for one candidate Anvil TCP port.

    ``find_free_port()`` historically performed a check-then-use operation:
    it checked that a port was not listening and returned the integer, but did
    not reserve it. Under ``pytest -n auto``, another worker could select and
    bind the same port before the first worker started Anvil. The losing
    worker could then mistake the winner's JSON-RPC server for its own process
    and, for example, receive Ethereum chain id ``1`` from an intended
    Arbitrum fork.

    This lease uses a stable, per-port ``fcntl.flock()`` lock shared by all
    local Python processes. The lock is held for the whole Anvil lifetime.
    Holding it beyond startup is inexpensive and makes ownership unambiguous:
    cooperative launchers never consider an active Anvil's port, even during
    the short interval before its TCP listener becomes observable.

    The lock is advisory. A non-cooperating process can still bind the TCP
    port, so the allocator also checks the actual listener state after taking
    the lock. Fork launches additionally verify the resulting chain id against
    the upstream RPC.

    See Python's `fcntl.flock() documentation
    <https://docs.python.org/3/library/fcntl.html#fcntl.flock>`__ for the
    advisory lock API and its relationship to the open file descriptor.
    """

    #: Localhost TCP port protected by the lease.
    port: int

    #: Stable advisory lock-file path. The file is intentionally never unlinked.
    path: Path

    #: Open descriptor whose lifetime controls the kernel lock.
    file: TextIO

    #: Guard repeated fixture cleanup and partially failed startup cleanup.
    released: bool = False

    def release(self) -> None:
        """Release this process's advisory port ownership.

        The method is idempotent because pytest fixture teardown can run after
        a partially failed setup. Closing the descriptor would release the
        lock by itself, but explicitly unlocking first documents the lifecycle
        and makes the port immediately available to another worker.
        """

        if self.released:
            return

        fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()
        self.released = True
        logger.debug("Released Anvil port lease %d at %s", self.port, self.path)


def _try_reserve_anvil_port(port: int) -> _AnvilPortLease | None:
    """Try to obtain exclusive inter-process ownership of an Anvil port.

    The lock is acquired before checking whether the TCP port is listening.
    This ordering closes the race between cooperating xdist workers: only the
    lock owner may decide that the port is available and proceed to launch.

    :param port:
        Candidate localhost TCP port.

    :return:
        Live lease when both the advisory lock and TCP port are available, or
        ``None`` when another process owns or listens on the candidate.
    """

    lock_path = Path(tempfile.gettempdir()) / f"{ANVIL_PORT_LOCK_FILE_PREFIX}-{port}.lock"

    # ``a+`` creates the stable coordination inode when it does not exist and
    # never truncates it. No data is stored in the file; only its descriptor
    # and inode identity matter to ``flock()``.
    lock_file = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        logger.debug("Anvil port %d is leased by another process", port)
        return None

    # A non-cooperating program may already own the TCP port even though no
    # eth-defi worker holds its advisory lock. Release the lease and let the
    # caller try a different candidate in that case.
    if is_localhost_port_listening(port, "127.0.0.1"):
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        logger.debug("Anvil port %d has an existing listener", port)
        return None

    logger.debug("Reserved Anvil port %d using %s", port, lock_path)
    return _AnvilPortLease(port=port, path=lock_path, file=lock_file)


def _reserve_anvil_port(port: int | tuple[int, int, int]) -> _AnvilPortLease:
    """Reserve an explicit port or choose a leased random port from a range.

    The tuple form matches the historical ``launch_anvil(port=...)`` contract:
    ``(minimum, maximum, attempts)`` with an exclusive maximum. Candidate
    selection remains random so parallel jobs spread across the range, while
    the per-port ``flock()`` makes the selection safe across processes.

    :param port:
        Explicit port number or ``(minimum, maximum, attempts)`` tuple.

    :return:
        Exclusive lease that must be retained until Anvil is shut down.

    :raise RuntimeError:
        If no candidate can be reserved within the configured attempt count.
    """

    if type(port) is int:
        lease = _try_reserve_anvil_port(port)
        if lease is None:
            raise RuntimeError(f"Cannot reserve explicit Anvil port {port}: it is leased or already listening")
        return lease

    min_port, max_port, max_attempts = port
    assert type(min_port) is int
    assert type(max_port) is int
    assert type(max_attempts) is int
    assert min_port < max_port, f"Invalid Anvil port range {min_port} - {max_port}"
    assert max_attempts > 0, f"Invalid Anvil port attempt count {max_attempts}"

    for _attempt in range(max_attempts):
        # Randomness only spreads concurrent workers across the candidate
        # range; the kernel lock, not unpredictability, provides exclusivity.
        candidate = random.randrange(start=min_port, stop=max_port)  # noqa: S311
        logger.info("Attempting to reserve port %d for Anvil", candidate)
        if lease := _try_reserve_anvil_port(candidate):
            return lease

    raise RuntimeError(f"Could not reserve an Anvil port with spec: {min_port} - {max_port}, {max_attempts} attempts")


def _get_anvil_launch_metadata(json_rpc_url: str) -> AnvilForkMetadata | None:
    """Return metadata for a locally launched Anvil endpoint.

    :param json_rpc_url:
        Local Anvil JSON-RPC URL.

    :return:
        Stored launch metadata, or ``None`` if this endpoint was not created by
        :py:func:`launch_anvil` in the current Python process.
    """

    with _anvil_launch_metadata_lock:
        return _anvil_launch_metadata.get(json_rpc_url)


def _register_anvil_launch_metadata(
    json_rpc_url: str,
    metadata: AnvilForkMetadata,
) -> None:
    """Store metadata for a locally launched Anvil endpoint.

    :param json_rpc_url:
        Local Anvil JSON-RPC URL.

    :param metadata:
        Metadata copied from the canonical :py:class:`AnvilLaunch` values.
    """

    with _anvil_launch_metadata_lock:
        _anvil_launch_metadata[json_rpc_url] = metadata


def _unregister_anvil_launch_metadata(json_rpc_url: str) -> None:
    """Remove metadata for a closed Anvil endpoint.

    :param json_rpc_url:
        Local Anvil JSON-RPC URL.
    """

    with _anvil_launch_metadata_lock:
        _anvil_launch_metadata.pop(json_rpc_url, None)


#: HyperEVM mainnet/testnet chain ids.
#:
#: HyperEVM RPCs are special when forking with Anvil: asking Anvil to fork the
#: chain tip without an explicit ``--fork-block-number`` can fail even after
#: ``eth_blockNumber`` succeeded against the upstream RPC.
#:
#: The failure mode we have seen in production is:
#:
#: - Anvil launches with ``--fork-url <HyperEVM RPC>`` and no
#:   ``--fork-block-number``
#: - during genesis creation, Anvil asks the upstream RPC for the default Anvil
#:   deployer account state at ``latest``
#: - HyperEVM sometimes responds with HTTP 400 and JSON-RPC error
#:   ``{"message":"Unknown block","code":26}``
#: - Anvil aborts with ``Error: failed to create genesis``
#:
#: Because of this, HyperEVM forks must be pinned slightly behind the tip unless
#: the caller already supplied an explicit, known-good block number.
HYPEREVM_CHAIN_IDS: set[int] = {998, 999}

#: How many blocks behind the tip we pin HyperEVM Anvil forks by default.
#:
#: Four blocks matches the manual workaround already used by HyperEVM test
#: fixtures in this repository and keeps us away from the unstable chain tip
#: that can return ``Unknown block`` during Anvil genesis creation.
HYPEREVM_ANVIL_FORK_TIP_LATENCY = 4


class InvalidArgumentWarning(Warning):
    """Lifted from Brownie."""


class RPCRequestError(Exception):
    """Lifted from Brownie."""


class ArchiveNodeRequired(Exception):
    """RPC endpoint does not provide archive node access.

    This is raised when a fork test requires historical block access
    but the RPC endpoint only provides recent blocks.
    """

    def __init__(
        self,
        message: str,
        rpc_url: str | None = None,
        requested_block: int | None = None,
        available_block: int | None = None,
        response_headers: dict | None = None,
    ):
        super().__init__(message)
        self.rpc_url = rpc_url
        self.requested_block = requested_block
        self.available_block = available_block
        self.response_headers = response_headers or {}


#: Mappings between Anvil command line parameters and our internal argument names
CLI_FLAGS = {
    "port": "--port",
    "host": "--host",
    "fork": "--fork-url",
    "fork_block_number": "--fork-block-number",
    "hardfork": "--hardfork",
    "chain_id": "--chain-id",
    "default_balance": "--balance",
    "gas_limit": "--gas-limit",
    "block_time": "--block-time",
    "steps_tracing": "--steps-tracing",
    "code_size_limit": "--code-size-limit",
    "verbose": "-vvvvv",
}


def _launch(cmd: str, inherit_stdio: bool = False, **kwargs) -> tuple[psutil.Popen, list[str]]:
    """Launches the RPC client.

    Args:
        cmd: command string to execute as subprocess"""
    if sys.platform == "win32" and not cmd.split(" ")[0].endswith(".cmd"):
        if " " in cmd:
            cmd = cmd.replace(" ", ".cmd ", 1)
        else:
            cmd += ".cmd"
    cmd_list = cmd.split(" ")
    for key, value in [(k, v) for k, v in kwargs.items() if v]:
        try:
            if value is True or value is False:
                # GNU style flags like --step-tracing
                if value:
                    cmd_list.append(CLI_FLAGS[key])
            else:
                cmd_list.extend([CLI_FLAGS[key], str(value)])
        except KeyError:
            warnings.warn(
                f'Ignoring invalid commandline setting for anvil: "{key}" with value "{value}".',
                InvalidArgumentWarning,
            )

    # USDC hack
    # Some contracts are too large to deploy when they are compiled unoptimized
    # TODO: Move to argument
    # cmd_list += ["--code-size-limit", "99999"]

    final_cmd_str = " ".join(cmd_list)
    logger.info("Launching anvil: %s", final_cmd_str)
    if inherit_stdio:
        out = None
    else:
        out = DEVNULL if sys.platform == "win32" else PIPE
    env = os.environ.copy()
    env["RUST_BACKTRACE"] = "1"  # Get tracebacks from crashed anvil
    return psutil.Popen(cmd_list, stdin=DEVNULL, stdout=out, stderr=out, env=env), cmd_list


def make_anvil_custom_rpc_request(web3: Web3, method: str, args: Optional[list] = None) -> Any:
    """Make a request to special named EVM JSON-RPC endpoint.

    - `See the Anvil custom RPC methods here <https://book.getfoundry.sh/reference/anvil/>`__.

    :param method:
        RPC endpoint name

    :param args:
        JSON-RPC call arguments

    :return:
        RPC result

    :raise RPCRequestError:
        In the case RPC method errors
    """

    if args is None:
        args = ()

    args = tuple(args)

    try:
        response = web3.provider.make_request(method, args)  # type: ignore
        if "result" in response:
            return response["result"]
    except (AttributeError, RequestsConnectionError):
        raise RPCRequestError("Web3 is not connected.")

    raise RPCRequestError(response["error"]["message"])


def _warm_up_fork_block(
    json_rpc_url: str,
    block_number: int,
    timeout: float = 60.0,
) -> None:
    """Warm up a forked block by forcing Anvil to fetch the full block body once.

    This issues ``eth_getBlockByNumber(block, true)`` against the local Anvil
    instance. On some forked networks this can be an expensive first-time read;
    doing it eagerly during startup helps move the cost from an arbitrary later
    contract interaction to a predictable warm-up phase.

    :param json_rpc_url:
        Local Anvil JSON-RPC URL.

    :param block_number:
        The block number to warm up.

    :param timeout:
        HTTP request timeout in seconds for the warm-up request.
    """
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getBlockByNumber",
        "params": [hex(block_number), True],
        "id": 1,
    }

    logger.info(
        "Warming up Anvil fork block %d with eth_getBlockByNumber(..., true)",
        block_number,
    )
    response = requests.post(json_rpc_url, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise RPCRequestError(f"Anvil fork block warm-up failed: {data['error']}")

    result = data.get("result")
    if result is None:
        raise RPCRequestError(
            f"Anvil fork block warm-up returned no block for {block_number}",
        )

    tx_count = len(result.get("transactions", []))
    logger.info(
        "Anvil fork block %d warm-up completed with %d transactions",
        block_number,
        tx_count,
    )


@dataclass
class AnvilLaunch:
    """Control Anvil processes launched on background.

    Comes with a helpful :py:meth:`close` method when it is time to put Anvil rest.

    The ``chain_id``, ``upstream_rpc_urls``, ``fork_block_number`` and
    ``effective_fork_url`` fields are the canonical launch metadata exposed to
    callers. The module-level metadata registry mirrors these values only so
    that later ``create_multi_provider_web3(launch.json_rpc_url)`` calls can
    attach the same context to retry diagnostics.
    """

    #: Which port was bound by the Anvil
    port: int

    #: Used command-line to spin up anvil
    cmd: list[str]

    #: Where does Anvil listen to JSON-RPC
    json_rpc_url: str

    #: UNIX process that we opened
    process: psutil.Popen

    #: Chain id reported by the local Anvil instance after startup.
    chain_id: int | None = None

    #: Original upstream RPC URLs passed to Anvil fork mode.
    upstream_rpc_urls: tuple[str, ...] = ()

    #: Explicit fork block used for Anvil, if any.
    fork_block_number: int | None = None

    #: URL passed to Anvil as ``--fork-url``.
    effective_fork_url: str | None = None

    #: Optional JSON-RPC failover proxy sitting between Anvil and upstream RPCs.
    #: Automatically started by :py:func:`launch_anvil` when multiple RPCs are
    #: configured in space-separated ``fork_url`` and ``proxy_multiple_upstream``
    #: is not ``False``.
    #: See :py:mod:`eth_defi.provider.rpc_proxy`.
    proxy: "RPCProxy | None" = None

    #: Whether :py:meth:`close` should shut down the proxy.
    #: ``True`` when the proxy was created by :py:func:`launch_anvil`;
    #: ``False`` when the caller passed a pre-built
    #: :py:class:`~eth_defi.provider.rpc_proxy.RPCProxy` instance
    #: (caller is responsible for its lifecycle).
    _proxy_managed: bool = True

    #: Inter-process ownership of :py:attr:`port`.
    #:
    #: Keep this descriptor open for exactly as long as Anvil can answer on the
    #: port. This prevents another pytest-xdist worker from selecting the same
    #: candidate during startup or teardown.
    _port_lease: _AnvilPortLease | None = None

    def close(self, log_level: Optional[int] = None, block=True, block_timeout=30) -> tuple[bytes, bytes]:
        """Close the background Anvil process.

        If this instance owns the :py:class:`~eth_defi.provider.rpc_proxy.RPCProxy`
        (i.e. it was auto-created, not passed in by the caller), the proxy
        is shut down after Anvil exits and its per-provider statistics are
        logged. Port-lease release runs from nested ``finally`` blocks, so a
        shutdown or managed-proxy cleanup error cannot strand the lease until
        the Python worker exits.

        :param log_level:
            Dump Anvil messages to logging

        :param block:
            Block the execution until anvil is gone

        :param block_timeout:
            How long time we try to kill Anvil until giving up.

        :return:
            Anvil stdout, stderr as string
        """
        try:
            stdout, stderr = shutdown_hard(
                self.process,
                log_level=log_level,
                block=block,
                block_timeout=block_timeout,
                check_port=self.port,
            )
            logger.info("Anvil shutdown %s", self.json_rpc_url)
            return stdout, stderr
        finally:
            # Cleanup must run even when shutdown diagnostics raise. Retaining
            # the advisory lease after Anvil has gone would not corrupt data,
            # but it would unnecessarily reduce the ports available to other
            # test workers until this Python process exits.
            _unregister_anvil_launch_metadata(self.json_rpc_url)
            try:
                if self.proxy is not None and self._proxy_managed:
                    self.proxy.close()
            finally:
                # Proxy shutdown has independent network/thread cleanup and may
                # itself raise. Port ownership must never leak because of that.
                if self._port_lease is not None:
                    self._port_lease.release()


def _verify_archive_node_access(
    web3: Web3,
    rpc_url: str,
    fork_block_number: int,
    current_block: int,
    timeout: float = 3.0,
) -> None:
    """Verify that the RPC endpoint can access historical blocks.

    Makes a test call to the fork block number to ensure the RPC
    provides archive node access. If the call fails, raises an
    informative exception with HTTP response headers for debugging.

    .. note::

        This check is best-effort. It only tests ``eth_getBalance`` for
        ``address(0)`` at the historical block. Some non-archive RPCs return
        cached/stale success for simple balance queries but fail on full state
        lookups (account nonces, contract code). When this happens, our check
        passes but Anvil's genesis creation fails later with errors like:

        - ``state histories haven't been fully indexed yet``
        - ``state 0x... is not available``
        - ``missing trie node``

        These Anvil-side failures are caught separately in :py:func:`launch_anvil`
        by inspecting Anvil's stderr output and raising :py:class:`ArchiveNodeRequired`.

    :param web3:
        Web3 connection to test

    :param rpc_url:
        The RPC URL being tested (for error messages)

    :param fork_block_number:
        The historical block number we need to access

    :param current_block:
        The current block number of the chain

    :param timeout:
        Request timeout in seconds

    :raises ArchiveNodeRequired:
        If the RPC cannot access the historical block
    """
    import json

    # Try to get balance at the historical block - this is a cheap call
    # that will fail if the RPC doesn't have archive data
    test_address = "0x0000000000000000000000000000000000000000"

    try:
        # Make a direct HTTP request so we can capture response headers
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_getBalance",
            "params": [test_address, hex(fork_block_number)],
            "id": 1,
        }
        response = requests.post(
            rpc_url,
            json=payload,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        response_headers = dict(response.headers)
        response_data = response.json()

        # Check for JSON-RPC error indicating missing block data
        if "error" in response_data:
            error = response_data["error"]
            error_message = error.get("message", str(error))

            # Common error patterns for missing archive data
            if any(
                pattern in error_message.lower()
                for pattern in [
                    "block out of range",
                    "missing trie node",
                    "header not found",
                    "block not found",
                    "state not available",
                    "state histories haven't been fully indexed",
                    "pruned state",
                    "historical state not available",
                ]
            ):
                raise ArchiveNodeRequired(
                    f"RPC endpoint {rpc_url} does not provide archive access for block {fork_block_number:,}. Current block is {current_block:,}. Error: {error_message}. Response headers: {json.dumps(response_headers, indent=2)}",
                    rpc_url=rpc_url,
                    requested_block=fork_block_number,
                    available_block=current_block,
                    response_headers=response_headers,
                )

    except requests.exceptions.RequestException as e:
        # Network error - wrap with context
        raise ArchiveNodeRequired(
            f"Failed to verify archive access for {rpc_url} at block {fork_block_number:,}: {e}",
            rpc_url=rpc_url,
            requested_block=fork_block_number,
            available_block=current_block,
        ) from e

    # Check that the RPC has actually synced past our fork block.
    # This catches lagging/rate-limited nodes that report stale block heights.
    if current_block < fork_block_number:
        raise ArchiveNodeRequired(
            f"RPC endpoint {rpc_url} is behind: current block is {current_block:,} but fork requires block {fork_block_number:,}. The node may be lagging or rate-limited.",
            rpc_url=rpc_url,
            requested_block=fork_block_number,
            available_block=current_block,
            response_headers={},
        )

    logger.debug("Archive node access verified for %s at block %d", rpc_url, fork_block_number)


def _select_safe_fork_block_number(
    web3: Web3,
    current_block: int,
    fork_block_number: int | None,
) -> int | None:
    """Choose a safer Anvil fork block for problematic chain tips.

    HyperEVM (chain ids 998 and 999) has a recurring failure mode when Anvil is
    asked to fork ``latest`` without an explicit ``--fork-block-number``.

    The upstream RPC can happily answer ``eth_blockNumber`` first, but a moment
    later Anvil's genesis creation may fail while reading account state from the
    same chain tip. The observed error chain looks like this:

    .. code-block:: text

        Error: failed to create genesis
        Context:
        - failed to get account for 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266:
          HTTP error 400 with body:
          {"id":9,"jsonrpc":"2.0","error":{"message":"Unknown block","code":26}}

    This is not a historical archive-data problem. It is a chain-tip stability
    problem specific to HyperEVM RPCs. The most reliable mitigation is to pin
    the fork a few blocks behind the tip.

    If the caller already supplied ``fork_block_number``, we always respect it.
    Otherwise, HyperEVM forks are automatically pinned
    ``HYPEREVM_ANVIL_FORK_TIP_LATENCY`` blocks behind the reported tip.

    :param web3:
        Upstream RPC connection used for the smoke test.

    :param current_block:
        Latest block returned by ``eth_blockNumber`` from the upstream RPC.

    :param fork_block_number:
        Caller-supplied explicit fork block, if any.

    :return:
        Effective fork block number, or ``None`` if forking at ``latest`` is
        still safe for this chain.
    """
    if fork_block_number is not None:
        return fork_block_number

    chain_id = web3.eth.chain_id
    if chain_id in HYPEREVM_CHAIN_IDS:
        safe_fork_block = max(1, current_block - HYPEREVM_ANVIL_FORK_TIP_LATENCY)
        logger.info(
            "HyperEVM fork requested without explicit fork_block_number, pinning Anvil to block %d instead of chain tip %d to avoid upstream 'Unknown block' errors during genesis creation",
            safe_fork_block,
            current_block,
        )
        return safe_fork_block

    return None


def launch_anvil(
    fork_url: Optional[str] = None,
    unlocked_addresses: list[Union[HexAddress, str]] = None,
    cmd="anvil",
    port: int | tuple[int, int, int] = (19999, 29999, 25),
    block_time=0,
    launch_wait_seconds=20.0,
    attempts=3,
    hardfork: str | None = "cancun",
    gas_limit: Optional[int] = None,
    steps_tracing=False,
    test_request_timeout=3.0,
    fork_block_number: Optional[int] = None,
    log_wait=False,
    code_size_limit: int = None,
    rpc_smoke_test=True,
    verbose=False,
    inherit_stdio: bool = False,
    warm_up_block: bool = False,
    archive: bool = True,
    proxy_multiple_upstream: "RPCProxy | RPCProxyConfig | bool" = True,
) -> AnvilLaunch:
    """Creates Anvil unit test backend or mainnet fork.

    - Anvil can be used as web3.py test backend instead of `EthereumTester`.
      Anvil offers faster execution and tracing - see :py:mod:`eth_defi.trace`.

    - Forking a mainnet is a common way to test against live deployments.
      This function invokes `anvil` command and tells it to fork a given JSON-RPC endpoint.

    When called, a subprocess is started on the background.
    To stop this process, call :py:meth:`eth_defi.anvil.AnvilLaunch.close`.

    This function waits `launch_wait_seconds` in order to `anvil` process to start
    and complete the chain fork.

    **Unit test backend**:

    - See `eth_defi.tests.enzyme.conftest <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/tests/enzyme/conftest.py>`__ for an example
      how to use Anvil in your Python based unit test suite

    **Mainnet fork**: Here is an example that forks BNB chain mainnet and transfer 500 BUSD stablecoin to a test
    account we control:

    .. code-block:: python

        from eth_defi.anvil import fork_network_anvil
        from eth_defi.chain import install_chain_middleware
        from eth_defi.gas import node_default_gas_price_strategy


        @pytest.fixture()
        def large_busd_holder() -> HexAddress:
            # An onchain address with BUSD balance
            # Binance Hot Wallet 6
            return HexAddress(HexStr("0x8894E0a0c962CB723c1976a4421c95949bE2D4E3"))


        @pytest.fixture()
        def user_1() -> LocalAccount:
            # Create a test account
            return Account.create()


        @pytest.fixture()
        def anvil_bnb_chain_fork(request, large_busd_holder, user_1, user_2) -> str:
            # Create a testable fork of live BNB chain.
            mainnet_rpc = os.environ["BNB_CHAIN_JSON_RPC"]
            launch = fork_network_anvil(mainnet_rpc, unlocked_addresses=[large_busd_holder])
            try:
                yield launch.json_rpc_url
            finally:
                # Wind down Anvil process after the test is complete
                launch.close(log_level=logging.ERROR)


        @pytest.fixture()
        def web3(anvil_bnb_chain_fork: str):
            # Set up a local unit testing blockchain
            # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
            web3 = Web3(HTTPProvider(anvil_bnb_chain_fork))
            # Anvil needs POA middlware if parent chain needs POA middleware
            install_chain_middleware(web3)
            web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
            return web3


        def test_anvil_fork_transfer_busd(web3: Web3, large_busd_holder: HexAddress, user_1: LocalAccount):
            # Forks the BNB chain mainnet and transfers from USDC to the user.

            # BUSD deployment on BNB chain
            # https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56
            busd_details = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
            busd = busd_details.contract

            # Transfer 500 BUSD to the user 1
            tx_hash = busd.functions.transfer(user_1.address, 500 * 10**18).transact({"from": large_busd_holder})

            # Because Ganache has instamine turned on by default, we do not need to wait for the transaction
            receipt = web3.eth.get_transaction_receipt(tx_hash)
            assert receipt.status == 1, "BUSD transfer reverted"

            assert busd.functions.balanceOf(user_1.address).call() == 500 * 10**18

    `See the full example in tests source code <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/tests/test_anvil.py>`_.

    If `anvil` refuses to terminate properly, you can kill a process by a port in your terminal:

    .. code-block:: shell

        # Kill any process listening to localhost:19999
        kill -SIGKILL $(lsof -ti:19999)

    See also

    - :py:func:`eth_defi.trace.assert_transaction_success_with_explanation`

    - :py:func:`eth_defi.trace.print_symbolic_trace`

    - :py:func:`create_anvil_snapshot_state`

    - :py:func:`reset_anvil_snapshot`

    .. note ::

        Looks like we have some issues Anvil instance lingering around even
        after `AnvilLaunch.close()` if scoped pytest fixtures are used.

        If you intentionally keep a fork alive across multiple tests, pair a
        module-scoped ``launch_anvil()`` / ``fork_network_anvil()`` fixture
        with :py:func:`create_anvil_snapshot_state` and
        :py:func:`reset_anvil_snapshot` so you can reset state cheaply between
        tests.

    :param cmd:
        Override `anvil` command. If not given we look up from `PATH`.

    :param fork_url:
        HTTP JSON-RPC URL of the network we want to fork.

        If not given launch an empty test backend.

    :param unlocked_addresses:
        List of addresses of which ownership we take to allow test code to transact as them

    :param port:
        Localhost port we bind for Anvil JSON-RPC.

        The tuple format is ``(minimum port, exclusive maximum port,
        reservation attempts)``.

        A tuple makes parallel launches choose random candidates from the
        range. Each candidate is protected by a per-port ``fcntl.flock()``
        lease from immediately before Anvil starts until
        :py:meth:`AnvilLaunch.close` finishes. This closes the check-then-bind
        race where two ``pytest-xdist`` workers previously found the same port
        free and one worker then connected to the other worker's chain.

        The advisory lock coordinates eth-defi launchers on the same host. The
        allocator also rejects ports with an existing TCP listener, and fork
        startup checks that the child is still alive and that its chain id
        matches the upstream RPC. Together these checks fail closed instead of
        returning a JSON-RPC URL backed by an unrelated Anvil process.

        You can also specify an individual port.

    :param launch_wait_seconds:
        How long we wait anvil to start until giving up

    :param block_time:

        How long Anvil takes to mine a block. Default is zero:
        Anvil is in `automining mode <https://book.getfoundry.sh/reference/anvil/>`__
        and creates a new block for each new transaction.

        Set to `1` or higher so that you can poll the transaction as you would do with
        a live JSON-RPC node.

    :param attempts:
        How many attempts we do to start anvil.

        Anvil launch may fail without any output. This could be because the given JSON-RPC
        node is throttling your API requests. In this case we just try few more times
        again by killing the Anvil process and starting it again.

    :param gas_limit:
        Set the block gas limit.

    :param hardfork:
        EVM version to use

    :param step_tracing:
        Enable Anvil step tracing.

        Needed to get structured logs.

        Only needed on GoEthereum style tracing, not needed for Parity style tracing.

        See https://book.getfoundry.sh/reference/anvil/

    :param test_request_timeout:
        Set the timeout fro the JSON-RPC requests that attempt to determine if Anvil was successfully launched.

    :param fork_block_number:
        For at a specific block height of the parent chain.

        If not given, fork at the latest block.
        Needs an archive node to work.

        HyperEVM is a special case: if the caller does not supply an explicit
        fork block, :py:func:`launch_anvil` automatically pins the fork a few
        blocks behind the tip. This avoids the recurring HyperEVM failure where
        Anvil aborts with ``Error: failed to create genesis`` because the
        upstream RPC responds with ``{"message":"Unknown block","code":26}``
        while resolving ``latest`` during genesis creation.

    :parma code_size_limit:
        Max smart contract size

    :param rpc_smoke_test:
        Check that the RPC is working before attempting to start Anvil

    :parma log_wait:
        Display info level logging while waiting for Anvil to start.

    :param verbose:
        Make Anvil the proces to dump a lot of stuff to stdout/stderr.

        See -vvvv https://getfoundry.sh/anvil/reference/anvil

    :param inherit_stdio:
        If ``True``, let the Anvil subprocess inherit the parent process
        stdout/stderr instead of capturing them in pipes.

        This is useful in Docker and other supervised environments where you
        want Anvil logs to appear live in the container logs.

        .. warning ::

            When ``False`` (default), stdout/stderr are captured and only read
            when the process is shut down. If Anvil is very chatty, those pipe
            buffers can fill up and stall the subprocess.

    :param warm_up_block:
        If ``True`` and running in fork mode, eagerly call
        ``eth_getBlockByNumber(fork_block, true)`` against the freshly started
        local Anvil instance.

        This can move an expensive first fork hydration from an arbitrary later
        request to startup. It does not remove the cost, but it makes it happen
        once, predictably, before the caller starts using the node.

    :param archive:
        Check that the RPC endpoint provides archive node access.

        When True (default) and ``fork_block_number`` is specified,
        performs a smoke test to verify the RPC can access historical blocks.
        If the RPC cannot access the requested block, raises :py:class:`ArchiveNodeRequired`
        with HTTP response headers to help identify the problematic RPC provider.

    :param proxy_multiple_upstream:
        Controls how multiple upstream RPC providers in ``fork_url`` are handled.

        **Background:** Anvil accepts only a single ``--fork-url`` and has no
        internal retry or failover logic. When the upstream is slow or
        rate-limited, Anvil hangs indefinitely. This parameter enables a
        transparent JSON-RPC proxy (:py:class:`~eth_defi.provider.rpc_proxy.RPCProxy`)
        that sits between Anvil and multiple upstreams, providing automatic
        failover, per-request timeouts, and diagnostics.

        The proxy is only relevant when ``fork_url`` contains multiple
        space-separated RPC URLs. With a single URL this parameter is ignored.

        Accepted values:

        - **``True``** (default) — automatically start a proxy with default
          settings when multiple RPCs are detected.  The proxy lifecycle is
          tied to :py:meth:`AnvilLaunch.close`: it starts before Anvil and
          shuts down (logging per-provider statistics) after Anvil exits.

        - **``False``** — disable the proxy entirely.  Falls back to the
          legacy behaviour of picking one RPC in round-robin order per
          ``launch_anvil()`` call, with no intra-session failover.

        - **An :py:class:`~eth_defi.provider.rpc_proxy.RPCProxyConfig` instance** —
          automatically start a proxy with custom settings.  Any fields not
          set explicitly use their dataclass defaults.  Example::

              from eth_defi.provider.rpc_proxy import RPCProxyConfig

              launch_anvil(
                  fork_url="https://rpc-a.example.com https://rpc-b.example.com",
                  proxy_multiple_upstream=RPCProxyConfig(
                      timeout=15.0,
                      retries=5,
                      auto_switch_request_count=50,
                  ),
              )

        - **An :py:class:`~eth_defi.provider.rpc_proxy.RPCProxy` instance** —
          use a proxy that you created yourself via
          :py:func:`~eth_defi.provider.rpc_proxy.start_rpc_proxy`.  Useful when
          you need full control over the proxy lifecycle or want to share a
          single proxy across multiple Anvil instances.  In this case
          ``launch_anvil`` does **not** manage the proxy lifecycle — you must
          call :py:meth:`~eth_defi.provider.rpc_proxy.RPCProxy.close` yourself.

        See :py:mod:`eth_defi.provider.rpc_proxy` for the full proxy API.

    :raises ArchiveNodeRequired:
        When ``archive=True`` and the RPC endpoint cannot access the requested
        historical block.
    """

    attempts_left = attempts
    process = None
    final_cmd = None
    current_block = 0
    web3 = None

    if unlocked_addresses is None:
        unlocked_addresses = []

    # Give helpful error message
    anvil = shutil.which("anvil")
    assert anvil is not None, f"anvil command not in PATH {os.environ.get('PATH')}"

    # Preserve the requested port specification until upstream RPC validation
    # is complete. Reserving immediately would hold an otherwise usable port
    # while a remote smoke test is slow or retrying. The actual per-port lease
    # is acquired immediately before spawning Anvil below.
    port_spec = port
    if type(port_spec) is int:
        warnings.warn(f"launch_anvil(port={port_spec}) called - we recommend using the default random port range instead", DeprecationWarning, stacklevel=2)

    proxy = None
    available_rpcs = []
    upstream_rpc_urls: tuple[str, ...] = ()
    expected_chain_id: int | None = None
    # Track whether we manage the proxy lifecycle (True) or the caller does (False)
    proxy_managed = True

    if fork_url and " " in fork_url:
        # Multi-RPC syntax: filter out mev+ prefixed endpoints
        # (MEV-protected sequencer endpoints that don't support standard RPC calls).
        all_rpcs = [u for u in fork_url.split(" ") if u]
        available_rpcs = [u for u in all_rpcs if not u.startswith("mev+")]
        if not available_rpcs:
            # All endpoints are mev+, strip the prefix as a fallback
            available_rpcs = [u.replace("mev+", "", 1) for u in all_rpcs]
        upstream_rpc_urls = tuple(available_rpcs)

        if len(available_rpcs) > 1 and proxy_multiple_upstream is not False:
            from eth_defi.provider.rpc_proxy import RPCProxy as RPCProxyClass
            from eth_defi.provider.rpc_proxy import RPCProxyConfig as RPCProxyConfigClass
            from eth_defi.provider.rpc_proxy import start_rpc_proxy

            if isinstance(proxy_multiple_upstream, RPCProxyClass):
                # Caller provided a pre-built proxy — use it, but don't
                # manage its lifecycle (caller is responsible for close()).
                proxy = proxy_multiple_upstream
                proxy_managed = False
                cleaned_fork_url = proxy.url
                logger.info(
                    "Using caller-provided RPC proxy at %s",
                    proxy.url,
                )
            else:
                # Start a failover proxy that sits between Anvil and multiple
                # upstream RPCs, providing retry/timeout/switchover handling.
                config = proxy_multiple_upstream if isinstance(proxy_multiple_upstream, RPCProxyConfigClass) else None
                proxy = start_rpc_proxy(available_rpcs, config=config, suppress_client_disconnect_errors=True)
                cleaned_fork_url = proxy.url
                logger.info(
                    "Started RPC failover proxy at %s for %d upstream providers",
                    proxy.url,
                    len(available_rpcs),
                )
        else:
            # proxy_multiple_upstream is False, or only one RPC available —
            # fall back to legacy round-robin across launches.
            if not hasattr(_anvil_rpc_state, "rpc_index"):
                _anvil_rpc_state.rpc_index = random.randint(0, len(available_rpcs) - 1)
            else:
                _anvil_rpc_state.rpc_index += 1
            rpc_index = _anvil_rpc_state.rpc_index % len(available_rpcs)
            cleaned_fork_url = available_rpcs[rpc_index]
            logger.info("Using Anvil at RPC endpoint %d/%d: %s", rpc_index + 1, len(available_rpcs), cleaned_fork_url)
    else:
        cleaned_fork_url = fork_url if not fork_url or not fork_url.startswith("mev+") else fork_url.replace("mev+", "", 1)
        if cleaned_fork_url:
            upstream_rpc_urls = (cleaned_fork_url,)

    # Check given RPC works.
    # When a proxy is active, smoke-test one of the upstream URLs directly
    # rather than through the proxy. The proxy has its own (longer) timeout
    # budget for failover; testing it with the short smoke-test timeout
    # would defeat the purpose and raise false positives.
    if fork_url and rpc_smoke_test:
        smoke_test_url = available_rpcs[0] if (proxy is not None and available_rpcs) else cleaned_fork_url
        web3 = Web3(HTTPProvider(smoke_test_url, request_kwargs={"timeout": test_request_timeout}))
        # Will raise an exception if not working
        try:
            current_rpc_block = web3.eth.block_number
            # Record the upstream identity before launching the local fork.
            # The readiness check later compares against this value so a
            # localhost listener for a different chain is rejected instead of
            # being mistaken for the Anvil fork we intended to start.
            expected_chain_id = web3.eth.chain_id
        except Exception as e:
            # Clean up the proxy if we started one
            if proxy is not None and proxy_managed:
                proxy.close()
            raise ValueError(f"RPC smoke test failed for {smoke_test_url}: {e}") from e

        fork_block_number = _select_safe_fork_block_number(
            web3=web3,
            current_block=current_rpc_block,
            fork_block_number=fork_block_number,
        )

        # If archive mode and fork_block_number specified, verify RPC can access historical blocks
        if archive and fork_block_number is not None:
            try:
                _verify_archive_node_access(
                    web3=web3,
                    rpc_url=smoke_test_url,
                    fork_block_number=fork_block_number,
                    current_block=current_rpc_block,
                    timeout=test_request_timeout,
                )
            except Exception:
                if proxy is not None and proxy_managed:
                    proxy.close()
                raise

    # Acquire an inter-process lease only after remote RPC validation and
    # immediately before constructing the Anvil command. Unlike the previous
    # check-then-use allocator, this descriptor remains locked until
    # ``AnvilLaunch.close()`` has stopped the local listener.
    try:
        port_lease = _reserve_anvil_port(port_spec)
    except (OSError, RuntimeError):
        # A managed proxy may already be running because upstream validation
        # occurs before local port allocation. Do not leak its listening
        # thread when every candidate port is occupied.
        if proxy is not None and proxy_managed:
            proxy.close()
        raise
    port = port_lease.port
    url = f"http://localhost:{port}"

    # https://book.getfoundry.sh/reference/anvil/
    args = dict(
        port=port,
        fork=cleaned_fork_url,
        hardfork=hardfork,
        gas_limit=gas_limit,
        steps_tracing=steps_tracing,
        verbose=verbose,
    )

    if code_size_limit:
        args["code_size_limit"] = code_size_limit

    if fork_block_number:
        args["fork_block_number"] = fork_block_number
        assert cleaned_fork_url, f"launch_anvil(): passed fork_block_number {fork_url} without JSON-RPC URL. Did you configure environment variables correctly?"

    if block_time not in (0, None):
        assert block_time > 0, f"Got bad block time {block_time}"
        args["block_time"] = block_time

    current_block = chain_id = None

    while attempts_left > 0:
        current_block = None
        chain_id = None
        try:
            process, final_cmd = _launch(
                cmd,
                inherit_stdio=inherit_stdio,
                **args,
            )
        except OSError:
            # ``Popen`` can fail before an Anvil process exists, for example
            # if the executable disappears after ``shutil.which()``. Release
            # resources here because no :class:`AnvilLaunch` will be returned
            # to give the caller a normal ``close()`` path.
            port_lease.release()
            if proxy is not None and proxy_managed:
                proxy.close()
            raise

        # Wait until Anvil is responsive
        timeout = time.time() + launch_wait_seconds

        # Use shorter read timeout here - otherwise requests will wait > 10s if something is wrong
        web3 = Web3(HTTPProvider(url, request_kwargs={"timeout": test_request_timeout}))
        while time.time() < timeout:
            try:
                # See if web3 RPC works
                current_block = web3.eth.block_number
                chain_id = web3.eth.chain_id

                # A successful HTTP response is not enough to prove that our
                # child owns this port. Before the fcntl lease existed, a child
                # losing the bind race could exit while another worker's Anvil
                # answered these requests. Never accept readiness from a dead
                # subprocess.
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    port_lease.release()
                    if proxy is not None and proxy_managed:
                        proxy.close()
                    # ``communicate()`` returns ``None`` streams when Anvil
                    # inherited the parent's stdio. Normalise these only for
                    # diagnostics so the intended startup error is preserved.
                    stdout_tail = stdout[-500:] if stdout else b""
                    stderr_tail = stderr[-500:] if stderr else b""
                    raise RuntimeError(
                        f"Anvil process exited during startup while localhost:{port} answered JSON-RPC; possible foreign listener or bind failure. stdout={stdout_tail!r}, stderr={stderr_tail!r}",
                    )

                # The upstream smoke test establishes the fork identity. A
                # mismatch here means either a non-cooperating process captured
                # the port or the configured fork proxy contains mixed-chain
                # endpoints. Both cases are unsafe: returning this Web3 would
                # let a test transact against the wrong network state.
                if expected_chain_id is not None and chain_id != expected_chain_id:
                    try:
                        shutdown_hard(process, log_level=logging.ERROR, block=False)
                    finally:
                        # Diagnostic shutdown can itself fail, but neither the
                        # lease nor a managed proxy may outlive a rejected
                        # startup attempt.
                        port_lease.release()
                        if proxy is not None and proxy_managed:
                            proxy.close()
                    raise RuntimeError(
                        f"Anvil chain id mismatch at {url}: expected upstream chain {expected_chain_id}, but the local listener reported {chain_id}. Refusing to attach to a possible foreign Anvil process.",
                    )
                break
            except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
                if log_wait:
                    logger.info("Anvil not ready, got exception %s", e)
                # requests.exceptions.ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer'))
                time.sleep(0.1)
                continue

        if current_block is None:
            logger.error("Could not read the latest block from anvil %s within %f seconds, shutting down and dumping output", url, launch_wait_seconds)
            stdout, stderr = shutdown_hard(
                process,
                log_level=logging.ERROR,
                block=True,
                check_port=port,
            )

            # Check if Anvil failed because the RPC lacks archive data.
            #
            # _verify_archive_node_access() only tests eth_getBalance for address(0),
            # which some non-archive RPCs answer from cache. Anvil's genesis creation
            # needs full state access (account nonces, code) and fails with errors like:
            # - "state histories haven't been fully indexed yet" (node still syncing history)
            # - "state 0x... is not available" (pruned state)
            # - "missing trie node" (pruned state on geth-based nodes)
            # - "header not found" (block too old for the node)
            #
            # When we detect these patterns in Anvil stderr, we raise ArchiveNodeRequired
            # so callers get a clear error instead of a generic AssertionError.
            stderr_str = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else str(stderr)
            archive_error_patterns = [
                "state histories haven't been fully indexed",
                "state is not available",
                "missing trie node",
                "header not found",
            ]
            # HyperEVM has a separate failure mode from missing archive state.
            # When Anvil forks HyperEVM at the chain tip, the upstream RPC may
            # answer HTTP 400 with JSON-RPC error {"message":"Unknown block","code":26}
            # while Anvil is creating genesis and requesting account state for
            # the default deployer address 0xf39F...92266. Anvil then exits with:
            #
            # - Error: failed to create genesis
            # - failed to get account for 0xf39F...92266: HTTP error 400 with body:
            #   {"id":9,"jsonrpc":"2.0","error":{"message":"Unknown block","code":26}}
            #
            # We avoid this pre-emptively by auto-pinning HyperEVM forks slightly
            # behind the tip, but keep this comment here because this stderr is
            # otherwise very confusing when seen in logs or CI output.
            if fork_block_number and any(p in stderr_str for p in archive_error_patterns):
                # Anvil has stopped, so release its port before auxiliary proxy
                # cleanup. A proxy error must not retain an unrelated port.
                port_lease.release()
                if proxy is not None and proxy_managed:
                    proxy.close()
                raise ArchiveNodeRequired(
                    f"Anvil failed to fork {cleaned_fork_url} at block {fork_block_number:,}: the RPC does not provide full archive state access. Anvil stderr: {stderr_str[:500]}",
                    rpc_url=cleaned_fork_url,
                    requested_block=fork_block_number,
                    available_block=locals().get("current_rpc_block"),
                    response_headers={},
                )

            if len(stdout) == 0:
                attempts_left -= 1
                if attempts_left > 0:
                    logger.info("anvil did not start properly, try again, attempts left %d", attempts_left)
                    continue

            port_lease.release()
            if proxy is not None and proxy_managed:
                proxy.close()
            raise AssertionError(f"Could not read block number from Anvil after the launch with command '{cmd}': at {url}, stdout is {len(stdout)} bytes, stderr is {len(stderr)} bytes")
        else:
            warm_up_target_block = fork_block_number if fork_url else None
            if warm_up_target_block is None and fork_url:
                warm_up_target_block = current_block

            if warm_up_block and warm_up_target_block is not None:
                try:
                    _warm_up_fork_block(url, warm_up_target_block)
                except (
                    RequestsConnectionError,
                    requests.exceptions.HTTPError,
                    requests.exceptions.ReadTimeout,
                    requests.exceptions.Timeout,
                    ValueError,
                    RPCRequestError,
                ) as e:
                    logger.error(
                        "Anvil fork block warm-up failed at block %d: %s",
                        warm_up_target_block,
                        e,
                    )
                    shutdown_hard(
                        process,
                        log_level=logging.ERROR,
                        block=True,
                        check_port=port,
                    )
                    attempts_left -= 1
                    if attempts_left > 0:
                        logger.info(
                            "Retrying Anvil launch after failed block warm-up, attempts left %d",
                            attempts_left,
                        )
                        continue

                    port_lease.release()
                    if proxy is not None and proxy_managed:
                        proxy.close()
                    raise

            # We have a successful launch
            break
    # Use f-string for a thousand separator formatting
    logger.info(f"anvil forked network {chain_id}, the current block is {current_block:,}, Anvil JSON-RPC is {url}")

    fork_metadata = AnvilForkMetadata(
        chain_id=chain_id,
        upstream_rpc_urls=upstream_rpc_urls,
        fork_block_number=fork_block_number,
        effective_fork_url=cleaned_fork_url,
    )
    _register_anvil_launch_metadata(url, fork_metadata)

    # Perform unlock accounts for all accounts
    for account in unlocked_addresses:
        unlock_account(web3, account)

    return AnvilLaunch(
        port,
        final_cmd,
        url,
        process,
        chain_id=fork_metadata.chain_id,
        upstream_rpc_urls=fork_metadata.upstream_rpc_urls,
        fork_block_number=fork_metadata.fork_block_number,
        effective_fork_url=fork_metadata.effective_fork_url,
        proxy=proxy,
        _proxy_managed=proxy_managed,
        _port_lease=port_lease,
    )


def unlock_account(web3: Web3, address: str):
    """Make Anvil mainnet fork to accept transactions to any Ethereum account.

    This is even when we do not have a private key for the account.

    :param web3:
        Web3 instance

    :param address:
        Account to unlock
    """
    web3.provider.make_request("anvil_impersonateAccount", [address])  # type: ignore


def sleep(web3: Web3, seconds: int) -> int:
    """Call emv_increaseTime on Anvil"""
    make_anvil_custom_rpc_request(web3, "evm_increaseTime", [hex(seconds)])
    return seconds


def mine(web3: Web3, timestamp: Optional[int] = None, increase_timestamp: float = 0) -> None:
    """Call evm_setNextBlockTimestamp on Anvil.

    Mine blocks, optionally set the time of the new block.

    :param web3:
        Web3 connection connected to Anvil JSON-RPC.

    :param timestamp:
        Jump to absolute future timestamp.

    :param increase_timestamp:
        How many seconds we leap to the future.
    """

    if timestamp is None and not increase_timestamp:
        make_anvil_custom_rpc_request(web3, "evm_mine")
    elif increase_timestamp > 0:
        block = web3.eth.get_block(web3.eth.block_number)
        timestamp = int(block["timestamp"] + increase_timestamp)
        make_anvil_custom_rpc_request(web3, "evm_setNextBlockTimestamp", [timestamp])
        make_anvil_custom_rpc_request(web3, "evm_mine")
    else:
        make_anvil_custom_rpc_request(web3, "evm_mine", [timestamp])


def snapshot(web3: Web3) -> int:
    """Call evm_snapshot on Anvil"""
    return int(make_anvil_custom_rpc_request(web3, "evm_snapshot", []), 16)


@dataclass(slots=True)
class AnvilSnapshotState:
    """Mutable reset point for a shared Anvil backend.

    This helper is designed for pytest suites that keep one Anvil process
    alive across multiple tests and reset it cheaply using
    ``evm_snapshot`` / ``evm_revert`` instead of relaunching the fork each
    time.

    Use :py:func:`create_anvil_snapshot_state` to take the initial snapshot
    and :py:func:`reset_anvil_snapshot` to restore it between tests.

    .. note::

        Only use this pattern in **self-contained** test modules or conftest
        files where the ``web3`` fixture is not overridden by sibling modules.
        Placing an ``autouse=True`` restore fixture in a shared ``conftest.py``
        causes ``ScopeMismatch`` errors when other test modules in the same
        directory override ``web3`` with function scope (e.g. for a different
        chain). Additionally, module-scoped Anvil forks combined with repeated
        snapshot/revert cycles can hang on CI runners under ``pytest-xdist``
        parallel execution, likely due to Anvil process responsiveness
        degradation after many revert cycles.

    Example:

    .. code-block:: python

        import pytest

        from eth_defi.provider.anvil import AnvilSnapshotState, create_anvil_snapshot_state, reset_anvil_snapshot


        @pytest.fixture(scope="module")
        def deployed_state(web3, deploy_info) -> AnvilSnapshotState:
            # deploy_info is resolved first so the snapshot captures the
            # expensive post-deployment baseline
            return create_anvil_snapshot_state(web3)


        @pytest.fixture(autouse=True)
        def restore_deployed_state(web3, deployed_state) -> None:
            reset_anvil_snapshot(web3, deployed_state)
    """

    #: Latest reusable Anvil snapshot id.
    snapshot_id: int


def revert(web3: Web3, snapshot_id: int) -> bool:
    """Call evm_revert on Anvil

    https://book.getfoundry.sh/reference/anvil/

    :return:
        True if a snapshot was reverted
    """
    ret_val = make_anvil_custom_rpc_request(web3, "evm_revert", [snapshot_id])
    return ret_val


def create_anvil_snapshot_state(web3: Web3) -> AnvilSnapshotState:
    """Capture the current Anvil state for later reuse.

    This is the manual building block for snapshot-based fixtures. Call this
    once after the expensive setup you want to reuse, such as a mainnet fork or
    a full protocol deployment.

    See :py:class:`AnvilSnapshotState` for a pytest usage example.
    """

    return AnvilSnapshotState(snapshot_id=snapshot(web3))


def reset_anvil_snapshot(web3: Web3, state: AnvilSnapshotState) -> None:
    """Revert a shared Anvil backend to a stored snapshot and resave it.

    ``evm_revert`` consumes the snapshot it restores. Because of this, the
    helper immediately creates a new snapshot after each reset so the same
    :py:class:`AnvilSnapshotState` instance can be reused by the next test.

    See :py:class:`AnvilSnapshotState` for a pytest usage example.
    """

    reverted = revert(web3, state.snapshot_id)
    assert reverted, f"Snapshot revert failed {state.snapshot_id}"
    state.snapshot_id = snapshot(web3)


def dump_state(web3: Web3) -> int:
    """Call evm_snapshot on Anvil"""
    return make_anvil_custom_rpc_request(web3, "anvil_dumpState")


def load_state(web3: Web3, state: str) -> int:
    """Call evm_snapshot on Anvil"""
    return make_anvil_custom_rpc_request(web3, "anvil_loadState", [state])


def set_balance(web3: Web3, address: str, raw_amount: int) -> int:
    """Call anvil_setBalance on Anvil"""

    assert type(raw_amount) == int

    # Call Anvil's custom RPC to set the balance
    web3.provider.make_request(
        "anvil_setBalance",
        [address, hex(raw_amount)],
    )


def is_anvil(web3: Web3) -> bool:
    """Are we connected to Anvil node.

    You need to change some behavior depending if you are
    connected to a real node or Anvil simulation.

    This can be either

    - Mainnet work (chain id copied from the forked blockchain)

    - Anvil test backend

    See also :py:func:`launch_anvil`

    .. warning::

        This method will crash with Base mainnet sequencer:

        ``requests.exceptions.HTTPError: 403 Client Error: Forbidden for url: https://mainnet-sequencer.base.org/``.

    :param web3:
        Web3 connection instance to check

    :return:
        True if we think we are connected to Anvil
    """
    # 'anvil/v0.2.0'
    return "anvil/" in web3.client_version


def is_mainnet_fork(web3: Web3) -> bool:
    """Have we forked mainnet for this test.

    - Only relevant with :py:func:`is_anvil`

    :return:
        True if we think we are connected to a forked mainnet,
        False if we think we are a standalone local dev chain.
    """
    # Heurestics
    return web3.eth.block_number > 500_000


def create_fork_funded_wallet(
    web3: Web3,
    usdc_address: HexAddress,
    large_usdc_holder: HexAddress,
    usdc_amount=Decimal("10000"),
    eth_amount=Decimal("10"),
) -> "eth_defi.hot_wallet.HotWallet":
    """On Anvil forked mainnet, create a wallet with some USDC funds.

    - Make a new private key account on a forked mainnet
    - Top this up with ETH and USDC from a large USDC holder
    """

    from eth_defi.hotwallet import HotWallet
    from eth_defi.token import fetch_erc20_details
    from eth_defi.trace import assert_transaction_success_with_explanation
    from eth_defi.middleware import construct_sign_and_send_raw_middleware_anvil

    assert large_usdc_holder.startswith("0x"), f"Large USDC holder address must start with 0x: {large_usdc_holder}"

    hot_wallet = HotWallet.create_for_testing(web3)
    logger.info("Creating a simulated wallet %s with USDC and ETH funding for testing", hot_wallet.address)

    # Fund with ETH
    tx_hash = web3.eth.send_transaction(
        {
            "from": web3.eth.accounts[0],
            "to": hot_wallet.address,
            "value": web3.to_wei(eth_amount, "ether"),
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Picked on Etherscan
    # https://arbiscan.io/token/0xaf88d065e77c8cc2239327c5edb3a432268e5831#balances
    usdc = fetch_erc20_details(web3, usdc_address)

    forked_balance = usdc.fetch_balance_of(large_usdc_holder)
    assert forked_balance > 0, f"Large USDC holder {large_usdc_holder} does not have enough USDC balance on chain {web3.eth.chain_id}, needed {usdc_amount}, has {forked_balance}"

    tx_hash = usdc.transfer(hot_wallet.address, forked_balance).transact({"from": large_usdc_holder})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Inject web3 middleware for signign
    # GMX code uses legacy signer infrastructure
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware_anvil(hot_wallet.account))

    assert usdc.fetch_balance_of(hot_wallet.address) > 0, "Simulated wallet did not receive USDC"
    assert web3.eth.get_balance(hot_wallet.address) > 0, "Simulated wallet did not receive ETH"

    return hot_wallet


#: Anvil default account #0 private key
ANVIL_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

#: Anvil default account #0 address
ANVIL_DEPLOYER = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

#: Anvil default account #1 address (useful as a Safe owner)
ANVIL_OWNER_1 = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

#: Anvil default account #2 address (useful as a Safe owner)
ANVIL_OWNER_2 = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


def find_erc20_balance_slot(
    web3: Web3,
    token_address: HexAddress | str,
    holder_address: HexAddress | str,
) -> int:
    """Find the ERC-20 ``balanceOf`` mapping storage slot by brute force.

    Tries slots 0-19 using Anvil snapshots, which covers all common
    ERC-20 implementations (OpenZeppelin, Solmate, USDC proxy, etc.).

    .. note::

        Only works on Anvil forks, as it uses ``evm_snapshot``,
        ``evm_revert``, and ``anvil_setStorageAt`` RPC methods.

    :param web3:
        Web3 connected to an Anvil fork.

    :param token_address:
        ERC-20 token contract address.

    :param holder_address:
        Address whose balance slot to find.

    :return:
        Storage slot number (0-19).

    :raises RuntimeError:
        If no matching slot is found in the first 20 slots.
    """
    erc20_abi = [
        {
            "constant": True,
            "inputs": [{"name": "", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}],
            "type": "function",
        }
    ]
    token = web3.eth.contract(
        address=Web3.to_checksum_address(token_address),
        abi=erc20_abi,
    )
    test_amount = 10**18

    for slot in range(20):
        snap = web3.provider.make_request("evm_snapshot", [])["result"]
        key = Web3.solidity_keccak(
            ["uint256", "uint256"],
            [int(holder_address, 16), slot],
        )
        web3.provider.make_request(
            "anvil_setStorageAt",
            [
                Web3.to_checksum_address(token_address),
                "0x" + key.hex(),
                "0x" + test_amount.to_bytes(32, "big").hex(),
            ],
        )
        bal = token.functions.balanceOf(Web3.to_checksum_address(holder_address)).call()
        web3.provider.make_request("evm_revert", [snap])
        if bal == test_amount:
            return slot

    raise RuntimeError(f"Could not find balance slot for token {token_address}")


def fund_erc20_on_anvil(
    web3: Web3,
    token_address: HexAddress | str,
    recipient: HexAddress | str,
    amount: int,
) -> int:
    """Fund an address with ERC-20 tokens by directly setting Anvil storage.

    Auto-detects the ``balanceOf`` mapping slot using
    :func:`find_erc20_balance_slot`, then writes the amount directly
    to the token's storage.

    Example — mint 1000 USDC on an Arbitrum Anvil fork:

    .. code-block:: python

        from eth_defi.provider.anvil import launch_anvil, fund_erc20_on_anvil
        from eth_defi.provider.multi_provider import create_multi_provider_web3
        from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details

        anvil = launch_anvil(fork_url="https://arb1.arbitrum.io/rpc")
        web3 = create_multi_provider_web3(anvil.json_rpc_url)

        chain_id = web3.eth.chain_id  # 42161
        usdc_address = USDC_NATIVE_TOKEN[chain_id]
        usdc = fetch_erc20_details(web3, usdc_address)

        recipient = "0xYourAddress..."
        fund_erc20_on_anvil(
            web3,
            usdc_address,
            recipient,
            usdc.convert_to_raw(1000),  # 1000 USDC
        )

        balance = usdc.fetch_balance_of(recipient)
        assert balance == 1000

    :param web3:
        Web3 connected to an Anvil fork.

    :param token_address:
        ERC-20 token contract address.

    :param recipient:
        Address to receive the tokens.

    :param amount:
        Token amount in raw wei.

    :return:
        The storage slot that was written to.
    """
    slot = find_erc20_balance_slot(web3, token_address, recipient)
    web3.provider.make_request(
        "anvil_setStorageAt",
        [
            Web3.to_checksum_address(token_address),
            "0x"
            + Web3.solidity_keccak(
                ["uint256", "uint256"],
                [int(recipient, 16), slot],
            ).hex(),
            "0x" + amount.to_bytes(32, "big").hex(),
        ],
    )
    logger.info(
        "Funded %s with %d tokens (wei) at %s via storage slot %d",
        recipient,
        amount,
        token_address,
        slot,
    )
    return slot


# Backwards compatibility
fork_network_anvil = launch_anvil
