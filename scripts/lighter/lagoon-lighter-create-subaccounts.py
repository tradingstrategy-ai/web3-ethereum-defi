"""Create Lighter sub-accounts for an existing Safe-owned master account.

A Lagoon vault deployed with ``generate_lighter_api_key`` owns one Lighter
master account and holds its delegated API key in a protected operator record.
This script uses that master key to submit ``CreateSubAccount`` L2
transactions, waits for the new sub-accounts to appear in the public API, and
prints their indices.

Each new sub-account still needs its own API key before a trader can use it.
Generate a key pair with ``eth_defi.lighter.api_key.generate_lighter_api_key``
and register it with ``scripts/lighter/lagoon-lighter-change-pubkey.py``,
passing the sub-account index as ``ACCOUNT_INDEX``. That is a Safe-owner
transaction.

The script is not idempotent: each run creates ``COUNT`` more sub-accounts.
It needs the optional official Lighter SDK.

Example::

    SAFE_ADDRESS=0xYourSafe COUNT=2 \
        LIGHTER_OPERATOR_RECORD_FILE=/secure/lighter/vault.json \
        poetry run python scripts/lighter/lagoon-lighter-create-subaccounts.py

Environment variables
---------------------

``SAFE_ADDRESS``                  The Lagoon vault's Safe owning the master account. Required.
``COUNT``                         Number of sub-accounts to create. Default 1.
``LIGHTER_OPERATOR_RECORD_FILE``  Mode 0600 JSON record with the master API key. Required.
"""

import json
import logging
import os
import stat
from pathlib import Path

from tabulate import tabulate
from web3 import Web3

from eth_defi.lighter.api import fetch_lighter_accounts
from eth_defi.lighter.session import create_lighter_session
from eth_defi.lighter.sub_account import create_lighter_sub_accounts
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

#: Group and other permission bits make a private operator record unsafe.
UNSAFE_FILE_PERMISSION_MASK = 0o077


def _require(name: str) -> str:
    """Read a required environment variable.

    :param name: Environment variable name.
    :return: Non-empty environment variable value.
    """
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _load_master_key(path: Path) -> tuple[int, int, str]:
    """Load the master account and its API key from an owner-only record.

    :param path: Operator record written by the Lagoon deployment.
    :return: Master account index, API-key slot and private key.
    """
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & UNSAFE_FILE_PERMISSION_MASK:
        raise PermissionError(f"Refusing insecure Lighter operator record: {path}")
    payload = json.loads(path.read_text())
    deployment = payload.get("deployments", {}).get("ethereum", payload)
    setup = deployment["lighter_account_setup"]
    return int(setup["account_index"]), int(setup["api_key_index"]), str(setup["private_key"])


def main() -> None:
    """Create sub-accounts and print the resulting account tree.

    :return: ``None`` after printing the account table.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    safe_address = Web3.to_checksum_address(_require("SAFE_ADDRESS"))
    count = int(os.environ.get("COUNT", "1"))
    master_index, api_key_index, api_private_key = _load_master_key(Path(_require("LIGHTER_OPERATOR_RECORD_FILE")))

    session = create_lighter_session()
    try:
        new_indices = create_lighter_sub_accounts(
            session=session,
            l1_address=safe_address,
            master_account_index=master_index,
            api_key_index=api_key_index,
            api_private_key=api_private_key,
            count=count,
        )
        accounts = fetch_lighter_accounts(session, safe_address)
    finally:
        session.close()

    assert accounts is not None
    rows = [("master", master_index, "")]
    rows += [("sub-account", index, "new" if index in new_indices else "") for index in accounts.sub_account_indices]
    print(tabulate(rows, headers=["Role", "Account index", "Created now"], tablefmt="simple"))
    print("Register an API key on each new sub-account with scripts/lighter/lagoon-lighter-change-pubkey.py.")


if __name__ == "__main__":
    main()
