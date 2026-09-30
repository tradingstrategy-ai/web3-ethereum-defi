#!/usr/bin/env python
"""Report recorded broken contract calls from persisted vault reader states.

Use ``poetry run python scripts/erc-4626/check-reader-states.py``. The default
is the all-chain pipeline's ``vault-reader-state-1h.pickle``; set
``READER_STATE_PATH`` for another scanner. Reads no contracts or RPC providers.
See ``eth_defi/erc_4626/vault_protocol/README-reader-states.md`` for limitations.
"""

import logging
import os
import pickle
from collections import Counter
from pathlib import Path

from tabulate import tabulate

from eth_defi.chain import CHAIN_NAMES
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


def main() -> None:
    """Read stored call outcomes without modifying reader progress.

    Current files contain ``VaultSpec`` keys and serialised state dictionaries.
    Legacy tuple keys and reader objects remain supported. An empty call-status
    map means no checks were recorded, rather than proof of a healthy contract.

    :return: None; logs recorded failures and totals in tables.
    """
    setup_console_logging(os.environ.get("LOG_LEVEL", "info"))
    path = Path(os.environ.get("READER_STATE_PATH", str(get_pipeline_data_dir() / "vault-reader-state-1h.pickle"))).expanduser()
    with path.open("rb") as source:
        states = pickle.load(source)
    logger.info("Loaded %d reader states from %s", len(states), path)
    failures = []
    checked = 0
    for spec, state in states.items():
        chain_id, address = (spec.chain_id, spec.vault_address) if isinstance(spec, VaultSpec) else spec
        call_status = state.get("call_status", {}) if isinstance(state, dict) else getattr(state, "call_status", {})
        checked += len(call_status)
        failures.extend((CHAIN_NAMES.get(chain_id, str(chain_id)), address, function, block) for function, (block, reverts) in call_status.items() if reverts)
    logger.info("Recorded broken calls:\n%s", tabulate(failures, headers=["Chain", "Vault address", "Function", "Checked block"]))
    counts = Counter(row[0] for row in failures)
    logger.info("Broken calls by chain:\n%s", tabulate(sorted(counts.items()), headers=["Chain", "Broken calls"]))
    logger.info("Reader states: %d; recorded checks: %d; broken calls: %d. Unchecked calls have unknown status.", len(states), checked, len(failures))


if __name__ == "__main__":
    main()
