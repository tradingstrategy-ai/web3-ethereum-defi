"""Launch a manually approved campaign in a persistent GNU Screen session.

Source ~/local-test.env first. Set CAMPAIGN_FILE and APPROVED_CAMPAIGN_DIGEST;
STATE_DIR and MAX_SENDS are optional. This is an explicit live launch.
"""

import logging
import os
from pathlib import Path

from eth_defi.vault_report.tweet_scheduler import launch_screen


def main() -> None:
    """Start the explicit reviewed revision and wait for authenticated readiness.

    The launcher hands credentials to Screen only through the inherited environment.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    launch_screen(Path(os.environ["CAMPAIGN_FILE"]).expanduser(), Path(os.environ.get("STATE_DIR", "~/.tradingstrategy/vault-report-tweets/state/tradingprotocol")).expanduser(), os.environ["APPROVED_CAMPAIGN_DIGEST"], Path(__file__).with_name("run-vault-report-tweets.py"))


if __name__ == "__main__":
    main()
