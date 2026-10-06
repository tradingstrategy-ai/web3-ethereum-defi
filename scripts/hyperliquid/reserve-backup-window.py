"""Reserve or cancel the shared two-day Hyperliquid maintenance backup window.

Use ``RESERVATION_ID``, optional ``RESERVATION_ACTION`` (reserve/heartbeat/cancel)
and ``DATABASE_NAMES`` (JSON array). This only writes private backup scheduling
metadata; stopping owners, taking due snapshots and migrating are separate steps.
"""

import json
import logging
import os

from eth_defi.utils import setup_console_logging
from eth_defi.vault.duckdb_backup import create_private_backup_client, reserve_backup_window

logger = logging.getLogger(__name__)


def main() -> None:
    """Persist an explicit named private-bucket maintenance reservation.

    Routine backups defer these databases until their common due window, and
    abandoned reservations expire after 72 hours without resetting upload gates.

    :return: ``None`` after logging the reservation's timing and status.
    """
    setup_console_logging(default_log_level="info")
    client = create_private_backup_client()
    names = json.loads(os.environ.get("DATABASE_NAMES", '["hyperliquid-vaults","hyperliquid-vaults-hf"]'))
    receipt = reserve_backup_window(client, os.environ["R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME"], os.environ["RESERVATION_ID"], names, action=os.environ.get("RESERVATION_ACTION", "reserve"))
    logger.info("Maintenance reservation: %s", json.dumps(receipt))


if __name__ == "__main__":
    main()
