"""Instrument :py:func:`detect_vault_features` probe-by-probe against one vault.

Diagnostic for autodetect calls that appear to hang rather than merely be slow.
:py:func:`eth_defi.erc_4626.classification.detect_vault_features` issues one
sequential ``eth_call`` per probe signature (49 at the time of writing) and has
no per-call visibility, so a stall shows up only as an overall ``ReadTimeout``
with no indication of *which* probe stopped and *why*.

This script replays the same probe loop with per-call timing, flushed
incrementally, so the exact stalling probe is identifiable even when the run
never finishes. It also checks Anvil liveness after every probe, which
distinguishes the two very different failure modes:

- the probe itself is slow (Anvil still answers ``eth_chainId`` afterwards), or
- the probe **wedges Anvil** (the liveness check fails too) — in which case every
  later probe inherits the wedge and the loop can never complete, which no amount
  of per-call slowness would explain.

Run (from a checkout with the venv and secrets)::

    source .local-test.env && poetry run python scripts/debug-vault-autodetect.py

Environment variables:

- ``JSON_RPC_ETHEREUM`` — archive RPC. Pass a single endpoint to remove the
  multi-provider failover proxy from the picture.
- ``VAULT_ADDRESS`` — vault to probe (default: the Upshift Sentora USD Earn vault
  that motivated this script).
- ``FORK_BLOCK`` — block to fork (default: the Upshift multi-asset fork block).
- ``DEBUG_LOG`` — set to ``1`` to enable DEBUG logging for the RPC stack.
- ``CALL_TIMEOUT`` — per-call Web3 read timeout in seconds (default 20).
"""

import logging
import os
import sys
import time

from tabulate import tabulate
from web3 import Web3

from eth_defi.erc_4626.classification import create_probe_calls
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import is_fork_alive

logger = logging.getLogger(__name__)

#: Upshift Sentora USD Earn — the vault whose autodetect never completed.
DEFAULT_VAULT = "0x74ad2f789ed583dbd141bbdafc673fe1f033718b"

#: Upshift multi-asset metadata fork block used by ``tests/.../test_upshift.py``.
DEFAULT_FORK_BLOCK = 25_405_251


def main() -> None:
    """Probe one vault's autodetect calls one at a time and report where it stalls."""
    level = logging.DEBUG if os.environ.get("DEBUG_LOG", "").strip() in {"1", "true", "yes"} else logging.INFO
    logging.basicConfig(level=level, stream=sys.stdout, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if level == logging.DEBUG:
        # These are the layers that would show a retry storm or a stuck socket.
        for noisy in ("web3", "urllib3", "eth_defi.provider", "eth_defi.event_reader"):
            logging.getLogger(noisy).setLevel(logging.DEBUG)

    rpc_url = os.environ["JSON_RPC_ETHEREUM"]
    vault_address = Web3.to_checksum_address(os.environ.get("VAULT_ADDRESS", DEFAULT_VAULT))
    fork_block = int(os.environ.get("FORK_BLOCK", DEFAULT_FORK_BLOCK))
    call_timeout = float(os.environ.get("CALL_TIMEOUT", 20))

    logger.info("Forking block %d to probe vault %s", fork_block, vault_address)
    launch = fork_network_anvil(rpc_url, fork_block_number=fork_block)

    try:
        web3 = create_multi_provider_web3(launch.json_rpc_url, default_http_timeout=(3.0, call_timeout))
        probes = list(create_probe_calls([vault_address], chain_id=1))
        logger.info("Replaying %d probe calls with a %.0fs per-call timeout", len(probes), call_timeout)

        rows = []
        for index, call in enumerate(probes, start=1):
            started = time.time()
            try:
                call.call_as_result(web3, block_identifier=fork_block, ignore_error=True)
                outcome = "ok"
            except Exception as e:  # noqa: BLE001 - diagnostic script records any failure
                outcome = type(e).__name__
            duration = time.time() - started

            # The critical signal: is Anvil still usable *after* this probe?
            alive = is_fork_alive(launch, timeout=5.0)
            rows.append([index, call.func_name, f"{duration:.1f}", outcome, "yes" if alive else "NO"])
            # Flush per probe so the stalling call is visible even if we never finish.
            print(f"{index:3d}/{len(probes)} {call.func_name:36s} {duration:7.1f}s {outcome:18s} anvil_alive={alive}", flush=True)
            if not alive:
                print(f"\n*** Anvil wedged after probe {index} ({call.func_name}) — every later probe would inherit this ***", flush=True)
                break

        print()
        print(tabulate(rows, headers=["#", "probe", "seconds", "outcome", "anvil alive"]))
    finally:
        launch.close(log_level=logging.ERROR)


if __name__ == "__main__":
    main()
