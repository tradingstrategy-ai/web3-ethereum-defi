#!/usr/bin/env python3
"""Smoke test console logging formats.

This script is for visually checking the different console logging modes used
by command line scripts and Docker logs.

Environment variables:

- ``LOG_LEVEL``: Logging level. Default: ``debug``.
- ``LOGGING_SMOKE_LOG_FILE``: Optional path for the file logging scenario.
  Default: ``logs/test-console-logging.log``.
"""

import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)
worker_logger = logging.getLogger("eth_defi.logging_smoke.worker")


def emit_section(title: str) -> None:
    """Write a visible section separator outside the logging subsystem.

    :param title:
        Section title.
    """

    sys.stderr.write("\n" + "=" * 96 + "\n")
    sys.stderr.write(f"{title}\n")
    sys.stderr.write("=" * 96 + "\n")
    sys.stderr.flush()


def emit_sample_logs() -> None:
    """Emit representative log lines for visual inspection.

    The sample includes all common severity levels, printf-style interpolation,
    mapping interpolation, long values and an exception traceback.
    """

    logger.debug("DEBUG line with hidden-by-default detail: rpc=%s block=%d", "https://example.invalid/rpc", 22_222_222)
    logger.info("INFO line with string=%s integer=%d float=%.4f repr=%r", "USDC", 12_345, 0.123456, {"chain": "base", "ok": True})
    logger.info("INFO mapping interpolation: vault=%(vault)s chain=%(chain)s tvl=%(tvl).2f", {"vault": "smoke-test-vault", "chain": "ethereum", "tvl": 1234567.89})
    logger.info("INFO long value for wrap check: tx=%s", "0x" + "abc123" * 80)
    logger.warning("WARNING line with retry_count=%d provider=%s", 3, "alchemy")
    logger.error("ERROR line with status=%d endpoint=%s", 503, "https://example.invalid/api")

    try:
        message = "Intentional smoke-test exception"
        raise ValueError(message)
    except ValueError:
        logger.exception("EXCEPTION line with compact traceback")


def emit_threaded_logs() -> None:
    """Emit logs from multiple threads to check thread name formatting."""

    def worker(worker_id: int) -> None:
        worker_logger.info("Thread worker_id=%d synthetic_pair=%s", worker_id, f"WETH-USDC-{worker_id}")

    threads = [threading.Thread(target=worker, name=f"smoke-worker-{worker_id}", args=(worker_id,)) for worker_id in range(1, 4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def run_scenario(
    title: str,
    configure: Callable[[], None],
    *,
    include_threads: bool = False,
) -> None:
    """Run one logging setup scenario.

    :param title:
        Scenario title.
    :param configure:
        Function that configures logging.
    :param include_threads:
        Emit threaded sample logs.
    """

    emit_section(title)
    configure()
    emit_sample_logs()
    if include_threads:
        emit_threaded_logs()


def configure_logging(*, force_colour: bool | None = None, **kwargs) -> None:
    """Configure logging for a smoke-test scenario.

    :param force_colour:
        ``True`` sets ``FORCE_COLOR``, ``False`` sets ``NO_COLOR`` and
        ``None`` leaves colour detection to ``setup_console_logging()``.
    :param kwargs:
        Extra keyword arguments passed to ``setup_console_logging()``.
    """

    os.environ.pop("FORCE_COLOR", None)
    os.environ.pop("NO_COLOR", None)

    if force_colour is True:
        os.environ["FORCE_COLOR"] = "1"
    elif force_colour is False:
        os.environ["NO_COLOR"] = "1"

    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "debug"),
        **kwargs,
    )


def configure_default() -> None:
    """Configure default console logging."""

    configure_logging()


def configure_forced_rich() -> None:
    """Configure Rich console logging via ``FORCE_COLOR``."""

    configure_logging(force_colour=True)


def configure_no_colour() -> None:
    """Configure plain logging via ``NO_COLOR``."""

    configure_logging(force_colour=False)


def configure_simplified() -> None:
    """Configure simplified message-only logging."""

    configure_logging(
        force_colour=True,
        simplified_logging=True,
    )


def configure_thread_colours() -> None:
    """Configure Rich logging with per-thread colours."""

    configure_logging(
        force_colour=True,
        coloured_threads=True,
    )


def configure_file_logging() -> None:
    """Configure console and plain file logging."""

    log_file = Path(os.environ.get("LOGGING_SMOKE_LOG_FILE", "logs/test-console-logging.log"))
    configure_logging(
        force_colour=True,
        log_file=log_file,
    )
    logger.info("Plain file log target: %s", log_file)


def main() -> None:
    """Run all console logging smoke scenarios."""

    run_scenario("Scenario 1: default autodetect", configure_default)
    run_scenario("Scenario 2: forced Rich colours", configure_forced_rich)
    run_scenario("Scenario 3: NO_COLOR plain output", configure_no_colour)
    run_scenario("Scenario 4: simplified Rich output", configure_simplified)
    run_scenario("Scenario 5: Rich output with coloured threads", configure_thread_colours, include_threads=True)
    run_scenario("Scenario 6: Rich console plus plain file log", configure_file_logging)


if __name__ == "__main__":
    main()
