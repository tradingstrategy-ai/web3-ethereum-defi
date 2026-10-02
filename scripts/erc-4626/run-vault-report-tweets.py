"""Run/status/bootstrap/reconcile/dry-run an approved chart tweet queue.

Environment: MODE, CAMPAIGN_FILE, APPROVED_CAMPAIGN_DIGEST, STATE_DIR,
MAX_SENDS, PROFILE_CHECK_REASON; reconciliation additionally uses ENTRY_ID,
RESOLUTION, RECONCILIATION_REASON, TWEET_ID and SENT_AT (explicit UTC).
Read README-tweets.md before any live launch. This script never sources secrets.
"""

import logging
import os
import signal
import datetime
import time
from pathlib import Path

from tabulate import tabulate

from eth_defi.vault_report.ghost import GhostAdminClient
from eth_defi.vault_report.tweet_plan import load_campaign, parse_utc, verify_source
from eth_defi.vault_report.tweet_scheduler import TweetLedger, account_lock, run_campaign
from eth_defi.vault_report.twitter import TwitterError, TwitterWriter

logger = logging.getLogger(__name__)


def main() -> None:
    """Execute a queue operation with explicit persistent account state.

    Status and simulation need no secrets and never invoke posting APIs.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mode = os.environ.get("MODE", "dry-run")
    path = Path(os.environ["CAMPAIGN_FILE"]).expanduser().resolve()
    state = Path(os.environ.get("STATE_DIR", "~/.tradingstrategy/vault-report-tweets/state/tradingprotocol")).expanduser().resolve()
    campaign = load_campaign(path, os.environ.get("APPROVED_CAMPAIGN_DIGEST"))
    if mode == "dry-run":
        logger.info("Simulation only:\n%s", tabulate([(e["scheduled_at"], e["chart_key"], e["body"], e["image"]) for e in campaign["entries"]], headers=["UTC", "Chart", "Exact body", "Image"]))
        return
    if mode == "status":
        ledger = TweetLedger(state)
        try:
            logger.info("Account ledger:\n%s", tabulate([tuple(row) for row in ledger.db.execute("SELECT id,status,tweet_id,sent_at FROM tweets")], headers=["Entry", "State", "Tweet", "Sent UNIX UTC"]))
            for entry in campaign["entries"]:
                row = ledger.db.execute("SELECT status FROM tweets WHERE id=?", (entry["id"],)).fetchone()
                if row is None or row[0] != "sent":
                    logger.info("Next effective slot: %s UTC", datetime.datetime.fromtimestamp(ledger.effective_time(entry), datetime.UTC).isoformat())
                    break
        finally:
            ledger.close()
        return
    writer = TwitterWriter.from_env()
    if mode in {"bootstrap", "reconcile"}:
        with account_lock(state):
            identity = writer.fetch_identity()
            if mode == "bootstrap":
                reason = os.environ.get("PROFILE_CHECK_REASON")
                if not reason:
                    raise ValueError("First-use bootstrap requires PROFILE_CHECK_REASON documenting a manual profile/duplicate check")
                if (state / "tweets.sqlite").exists():
                    raise ValueError("Ledger already exists; do not rebootstrap")
                if any(path.parent.glob("execution-*.json")):
                    raise ValueError("Execution receipts exist; restore/reconcile the missing ledger")
                logger.warning("Operator first-use profile evidence: %s", reason)
            ledger = TweetLedger(state, bootstrap=mode == "bootstrap")
            try:
                ledger.bind_account(str(identity["id"]))
                if mode == "bootstrap":
                    with ledger.db:
                        ledger.db.execute("INSERT INTO operator_audit VALUES (?,NULL,'bootstrap',?)", (time.time(), reason))
                if mode == "reconcile":
                    try:
                        logger.info("Recent timeline candidates: %s", writer.fetch_recent_posts(str(identity["id"])))
                    except TwitterError as exc:
                        logger.warning("Timeline unavailable; use the public profile: %s", exc)
                    if os.environ.get("RESOLUTION"):
                        sent_at = parse_utc(os.environ["SENT_AT"]).replace(tzinfo=datetime.UTC).timestamp() if os.environ.get("SENT_AT") else None
                        ledger.reconcile(os.environ["ENTRY_ID"], os.environ["RESOLUTION"], os.environ["RECONCILIATION_REASON"], os.environ.get("TWEET_ID"), sent_at)
            finally:
                ledger.close()
        return
    if mode != "run" or not os.environ.get("APPROVED_CAMPAIGN_DIGEST"):
        raise ValueError("Live run needs MODE=run and the manually reviewed APPROVED_CAMPAIGN_DIGEST")
    maximum = int(os.environ["MAX_SENDS"]) if os.environ.get("MAX_SENDS") else None
    if maximum is not None and maximum < 1:
        raise ValueError("MAX_SENDS must be positive")
    client = GhostAdminClient(os.environ.get("GHOST_ADMIN_API_URL") or os.environ["GHOST_CONTENT_API_URL"], os.environ["GHOST_ADMIN_API_KEY"])
    # KeyboardInterrupt reaches the queue's finally block for durable cleanup.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        run_campaign(path, state, writer, lambda c: verify_source(client, c), maximum, ready_file=Path(os.environ["READY_FILE"]) if os.environ.get("READY_FILE") else None, launch_gate=Path(os.environ["LAUNCH_GATE"]) if os.environ.get("LAUNCH_GATE") else None)
    except KeyboardInterrupt:
        logger.info("Paused safely; resume the same compiled campaign and STATE_DIR")


if __name__ == "__main__":
    main()
