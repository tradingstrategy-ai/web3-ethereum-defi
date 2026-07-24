"""Manually stress-test Anvil's inter-process ``fcntl`` port leases.

This script reproduces the launch pattern that exposed wrong-chain Anvil
connections in parallel CI. Multiple spawned Python processes cross a barrier,
select ports from the same deliberately narrow range, launch standalone Anvil
nodes, and keep every node alive until the parent has inspected all results.

Keeping the nodes alive at the same time is important. It proves that every
successful worker owns a distinct TCP port and a distinct advisory lease,
rather than merely observing ports that were released by earlier workers.
Using the ``spawn`` multiprocessing context also mirrors pytest-xdist better
than threads would: each child has independent Python globals and coordinates
only through the operating system's ``fcntl.flock()`` state.

Run with the Poetry environment:

.. code-block:: shell

    poetry run python scripts/anvil/manual-test-fcntl-port-leases.py

Configuration is provided through environment variables, following the
repository's command-line script conventions:

``ANVIL_PORT_TEST_WORKERS``
    Concurrent child processes per round. Defaults to ``8``.

``ANVIL_PORT_TEST_ROUNDS``
    Number of fresh contention rounds. Defaults to ``5``.

``ANVIL_PORT_TEST_RANGE_SIZE``
    Number of candidate ports shared by all children. Defaults to the worker
    count, deliberately saturating the range with live Anvil listeners. It
    must be at least the worker count so every live Anvil can own a port
    simultaneously.

``ANVIL_PORT_TEST_MIN``
    First candidate port. Defaults to ``23000``.

``ANVIL_PORT_TEST_ATTEMPTS``
    Random candidates each child may try before failing. Defaults to ``250``;
    the high value allows the final workers to find the few remaining ports in
    a deliberately crowded range.

The script does not use chain RPC credentials. Standalone Anvil nodes report
the default development chain id ``31337``. This confirms each live listener
is an Anvil development node, but because every worker uses the same chain id
it does not exercise fork-specific upstream chain-id mismatch rejection. Any
duplicate port, unexpected chain id, child error, or leaked child process makes
the script exit unsuccessfully.
"""

import logging
import multiprocessing
import os
import queue
import threading
from dataclasses import dataclass
from typing import Any

from tabulate import tabulate
from web3 import HTTPProvider, Web3

from eth_defi.provider.anvil import AnvilLaunch, launch_anvil

logger = logging.getLogger(__name__)

#: Standalone Anvil's default chain id.
EXPECTED_CHAIN_ID = 31337

#: Highest valid TCP port number.
MAX_TCP_PORT = 65_535


@dataclass(slots=True, frozen=True)
class WorkerConfig:
    """Immutable inputs for one spawned launcher process.

    Keeping worker configuration in one serialisable value makes the
    multiprocessing boundary explicit and avoids a long positional argument
    list where two integer fields could be accidentally transposed.
    """

    #: One-based stress round number.
    round_number: int

    #: One-based worker number inside the round.
    worker_number: int

    #: Inclusive lower bound of the shared candidate range.
    port_min: int

    #: Exclusive upper bound of the shared candidate range.
    port_max: int

    #: Maximum random lease candidates for this worker.
    attempts: int


@dataclass(slots=True, frozen=True)
class WorkerResult:
    """Serializable result returned by one spawned launcher process."""

    #: One-based stress round number.
    round_number: int

    #: One-based worker number inside the round.
    worker_number: int

    #: ``"ok"`` for a live validated Anvil, otherwise ``"error"``.
    status: str

    #: Leased Anvil port, when startup succeeded.
    port: int | None

    #: Chain id read back from the live localhost JSON-RPC listener.
    chain_id: int | None

    #: Operating-system process id of the Anvil subprocess.
    process_id: int | None

    #: Diagnostic text for a failed worker.
    error: str | None


def _read_positive_int(name: str, default: int) -> int:
    """Read and validate one positive integer environment variable.

    :param name:
        Environment variable name.

    :param default:
        Value used when the variable is not set.

    :return:
        Parsed positive integer.
    """

    value = int(os.environ.get(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def _run_worker(
    config: WorkerConfig,
    start_barrier: Any,
    release_event: Any,
    result_queue: Any,
) -> None:
    """Launch and retain one Anvil while sibling processes contend for ports.

    The child publishes its result only after the localhost listener answers
    with standalone Anvil's default chain id. This is a basic listener sanity
    check, not coverage for fork-specific chain-id mismatch rejection. The
    child then waits for the parent to release the round, ensuring all reported
    ports remain concurrently occupied.

    :param config:
        Immutable worker identity and shared port-range configuration.

    :param start_barrier:
        Multiprocessing barrier aligning all launch calls.

    :param release_event:
        Parent-controlled event allowing successful workers to shut down.

    :param result_queue:
        Multiprocessing queue carrying :class:`WorkerResult` values.
    """

    launch: AnvilLaunch | None = None
    try:
        # Align the children immediately before launch to maximise contention
        # in the small shared port range.
        start_barrier.wait(timeout=30)
        launch = launch_anvil(
            port=(config.port_min, config.port_max, config.attempts),
            fork_url=None,
            launch_wait_seconds=20,
        )

        web3 = Web3(HTTPProvider(launch.json_rpc_url, request_kwargs={"timeout": 3}))
        chain_id = web3.eth.chain_id
        if chain_id != EXPECTED_CHAIN_ID:
            raise RuntimeError(
                f"Worker {config.worker_number} connected to chain {chain_id} at {launch.json_rpc_url}; expected standalone Anvil chain {EXPECTED_CHAIN_ID}",
            )

        result_queue.put(
            WorkerResult(
                round_number=config.round_number,
                worker_number=config.worker_number,
                status="ok",
                port=launch.port,
                chain_id=chain_id,
                process_id=launch.process.pid,
                error=None,
            ),
        )

        # Keep both the TCP listener and fcntl descriptor alive until the
        # parent has checked every worker in this round.
        if not release_event.wait(timeout=90):
            message = "Parent did not release the completed stress round"
            raise TimeoutError(message)
    except (AssertionError, OSError, RuntimeError, TimeoutError, ValueError, threading.BrokenBarrierError) as e:
        result_queue.put(
            WorkerResult(
                round_number=config.round_number,
                worker_number=config.worker_number,
                status="error",
                port=launch.port if launch is not None else None,
                chain_id=None,
                process_id=launch.process.pid if launch is not None else None,
                error=f"{type(e).__name__}: {e}",
            ),
        )
    finally:
        if launch is not None:
            # Successful stress rounds intentionally stop many healthy nodes.
            # Keep their startup banners out of the report; shutdown failures
            # still propagate through ``close()`` and fail the worker.
            launch.close()


def _run_round(
    context: multiprocessing.context.BaseContext,
    *,
    round_number: int,
    workers: int,
    port_min: int,
    port_max: int,
    attempts: int,
) -> list[WorkerResult]:
    """Run one process-contention round and clean up every child.

    :param context:
        Spawn-based multiprocessing context.

    :param round_number:
        One-based round number.

    :param workers:
        Number of simultaneous Anvil launchers.

    :param port_min:
        Inclusive lower bound of the shared candidate range.

    :param port_max:
        Exclusive upper bound of the shared candidate range.

    :param attempts:
        Maximum lease candidates per worker.

    :return:
        One result from each child process.
    """

    start_barrier = context.Barrier(workers)
    release_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_run_worker,
            args=(
                WorkerConfig(
                    round_number=round_number,
                    worker_number=worker_number,
                    port_min=port_min,
                    port_max=port_max,
                    attempts=attempts,
                ),
                start_barrier,
                release_event,
                result_queue,
            ),
            name=f"anvil-port-worker-{round_number}-{worker_number}",
        )
        for worker_number in range(1, workers + 1)
    ]

    results: list[WorkerResult] = []
    failed_processes: list[str] = []
    try:
        for process in processes:
            process.start()

        for _worker in range(workers):
            try:
                results.append(result_queue.get(timeout=60))
            except queue.Empty:
                results.append(
                    WorkerResult(
                        round_number=round_number,
                        worker_number=0,
                        status="error",
                        port=None,
                        chain_id=None,
                        process_id=None,
                        error="Timed out waiting for a worker result",
                    ),
                )
                break
    finally:
        # Release successful workers before joining. If a child is wedged,
        # terminate only that child process after the grace period so no manual
        # Anvil process is intentionally left behind.
        release_event.set()
        for process in processes:
            process.join(timeout=30)
            if process.is_alive():
                logger.error("Terminating stuck child %s (pid %d)", process.name, process.pid)
                process.terminate()
                process.join(timeout=10)
            if process.exitcode != 0:
                # A worker can publish a successful launch and still fail
                # while shutting Anvil down. Treat that as a failed manual
                # test because cleanup is part of the lease lifecycle.
                failed_processes.append(f"{process.name} (pid {process.pid}, exit code {process.exitcode})")

        result_queue.close()
        result_queue.join_thread()

    if failed_processes:
        raise RuntimeError(f"Worker processes did not exit cleanly: {', '.join(failed_processes)}")

    return sorted(results, key=lambda result: result.worker_number)


def main() -> None:
    """Execute all stress rounds and report the concurrent lease allocation.

    Configuration comes exclusively from the environment variables documented
    in the module docstring. The function raises on any launch, identity,
    uniqueness, or cleanup failure; a zero exit status therefore means all
    reported Anvil listeners remained live and distinct during every round.

    :return:
        ``None`` after printing the successful allocation table.
    """

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    workers = _read_positive_int("ANVIL_PORT_TEST_WORKERS", 8)
    rounds = _read_positive_int("ANVIL_PORT_TEST_ROUNDS", 5)
    # Saturating the range is intentional: every candidate must be uniquely
    # leased and listening before the parent releases the round.
    range_size = _read_positive_int("ANVIL_PORT_TEST_RANGE_SIZE", workers)
    port_min = _read_positive_int("ANVIL_PORT_TEST_MIN", 23_000)
    attempts = _read_positive_int("ANVIL_PORT_TEST_ATTEMPTS", 250)
    port_max = port_min + range_size

    if range_size < workers:
        raise ValueError(
            f"ANVIL_PORT_TEST_RANGE_SIZE ({range_size}) must be at least ANVIL_PORT_TEST_WORKERS ({workers}) because all nodes stay live concurrently",
        )
    if port_max > MAX_TCP_PORT:
        raise ValueError(f"Configured port range ends above 65535: {port_min} - {port_max}")

    context = multiprocessing.get_context("spawn")
    all_results: list[WorkerResult] = []
    for round_number in range(1, rounds + 1):
        logger.info(
            "Starting fcntl lease stress round %d/%d: workers=%d, ports=%d-%d",
            round_number,
            rounds,
            workers,
            port_min,
            port_max - 1,
        )
        round_results = _run_round(
            context=context,
            round_number=round_number,
            workers=workers,
            port_min=port_min,
            port_max=port_max,
            attempts=attempts,
        )
        all_results.extend(round_results)

        successful_ports = [result.port for result in round_results if result.status == "ok"]
        errors = [result for result in round_results if result.status != "ok"]
        if errors:
            raise RuntimeError(f"Round {round_number} had {len(errors)} worker errors: {errors}")
        if len(successful_ports) != workers:
            raise RuntimeError(f"Round {round_number} returned {len(successful_ports)} successful workers, expected {workers}")
        if len(set(successful_ports)) != workers:
            raise RuntimeError(f"Round {round_number} allocated duplicate live ports: {successful_ports}")

    rows = [
        [
            result.round_number,
            result.worker_number,
            result.status,
            result.port,
            result.chain_id,
            result.process_id,
        ]
        for result in all_results
    ]
    print(
        tabulate(
            rows,
            headers=["Round", "Worker", "Status", "Port", "Chain id", "Anvil pid"],
            tablefmt="github",
        ),
    )
    logger.info(
        "All %d rounds passed: %d concurrent Anvil launches used unique fcntl-leased ports",
        rounds,
        len(all_results),
    )


if __name__ == "__main__":
    main()
