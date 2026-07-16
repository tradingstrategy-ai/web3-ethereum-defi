#!/usr/bin/env python3
"""Backfill one Securitize fund's off-chain NAV and on-chain supply history.

The script delegates to ``backfill-history.py`` so it preserves unrelated
leads, reader state and Parquet rows. It selects ACRED for the RedStone path by
default. Select STAC and provide Chronicle's signed history JSON export for the
Chronicle path.

Run RedStone ACRED with::

    SECURITIZE_NAV_BACKFILL_SOURCE=redstone \\
    source .local-test.env && poetry run python scripts/securitize/backfill-offchain-history.py

Run Chronicle STAC with::

    SECURITIZE_NAV_BACKFILL_SOURCE=chronicle \\
    CHRONICLE_STAC_HISTORY_URL=https://... \\
    source .local-test.env && poetry run python scripts/securitize/backfill-offchain-history.py

``CHRONICLE_STAC_HISTORY_URL`` must be a signed Chronicle history export. The
public dashboard does not currently document a stable machine-readable URL.
"""

import os
import runpy
from pathlib import Path


def main() -> None:
    """Select one reviewed off-chain source and invoke the scoped migration.

    :return:
        None.
    :raise ValueError:
        If the requested source is unsupported or lacks its required history
        export URL.
    """

    source = os.environ.get("SECURITIZE_NAV_BACKFILL_SOURCE", "redstone").lower()
    match source:
        case "redstone":
            os.environ.setdefault("SECURITIZE_PRODUCTS", "0x17418038ecf73ba4026c4f428547bf099706f27b")
        case "chronicle":
            if not os.environ.get("CHRONICLE_STAC_HISTORY_URL"):
                message = "CHRONICLE_STAC_HISTORY_URL is required for a Chronicle STAC backfill"
                raise ValueError(message)
            os.environ.setdefault("SECURITIZE_PRODUCTS", "0x51c2d74017390cbbd30550179a16a1c28f7210fc")
        case _:
            message = "SECURITIZE_NAV_BACKFILL_SOURCE must be redstone or chronicle"
            raise ValueError(message)

    runpy.run_path(str(Path(__file__).with_name("backfill-history.py")), run_name="__main__")


if __name__ == "__main__":
    main()
