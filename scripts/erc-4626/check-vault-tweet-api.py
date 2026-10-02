"""Manually check user identity and optional chart upload/alt-text capability.

Source ~/local-test.env first. CHECK_CHART enables an ephemeral media upload.
This script never creates, deletes or modifies a public tweet.
"""

import logging
import os
from pathlib import Path

from eth_defi.vault_report.twitter import TwitterWriter

logger = logging.getLogger(__name__)


def main() -> None:
    """Check the supplied real provider credentials without posting.

    The optional media check is an explicit operator-run capability probe.
    Error output is redacted by the writer and failed writes are not retried.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    writer = TwitterWriter.from_env()
    identity = writer.fetch_identity()
    logger.info("Authenticated publishing account: %s (%s)", identity["username"], identity["id"])
    if os.environ.get("CHECK_CHART"):
        writer.upload_chart(Path(os.environ["CHECK_CHART"]).expanduser(), "Monthly vault report chart: manual API capability check")
        logger.info("Chart upload and alt-text write passed; no tweet created")


if __name__ == "__main__":
    main()
