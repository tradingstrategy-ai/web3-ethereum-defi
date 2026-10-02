"""Durable half-day campaign queue and detached GNU Screen launcher.

There is one writer per account. Ambiguous dispatches block that account until
an operator reconciles them. State is independent of campaign revisions.
"""

import contextlib
import datetime
import fcntl
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, Iterator

from eth_defi.vault_report.tweet_plan import INTERVAL_SECONDS, digest, load_campaign, parse_utc, safe_asset, write_json
from eth_defi.vault_report.twitter import AmbiguousPostError, TwitterError, TwitterRateLimit, TwitterWriter

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def account_lock(state_dir: Path) -> Iterator[None]:
    """Hold an exclusive Linux lock across sending or ledger mutations.

    Locks are released automatically on exit or process death.

    :param state_dir: Persistent account directory.
    :yield: Locked operation scope.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "account.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another campaign runner holds the TradingProtocol account lock") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class TweetLedger:
    """Small transaction-backed account ledger; no scanner state is accessed."""

    def __init__(self, state_dir: Path, bootstrap: bool = False) -> None:
        """Open established state or explicitly initialise a first-use ledger.

        A normal start refuses a missing database to avoid silent reposting.

        :param state_dir: Persistent account directory.
        :param bootstrap: Explicit operator first-use operation.
        """
        path = state_dir / "tweets.sqlite"
        if not path.exists() and not bootstrap:
            raise ValueError("Ledger missing: restore a backup or explicitly bootstrap after checking the profile")
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS identity (account_id TEXT PRIMARY KEY)")
        self.db.execute("CREATE TABLE IF NOT EXISTS tweets (id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, status TEXT NOT NULL, attempt_at REAL, sent_at REAL, tweet_id TEXT, campaign_digest TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS operator_audit (recorded_at REAL NOT NULL, entry_id TEXT, resolution TEXT NOT NULL, reason TEXT NOT NULL)")
        self.db.commit()

    def close(self) -> None:
        """Close the database after a queue or status operation.

        Callers use a finally block so locks/state never leak.
        """
        self.db.close()

    def bind_account(self, account_id: str) -> None:
        """Require one unchanged numeric publishing account across restarts.

        Username spelling alone is insufficient when tokens or handles change.

        :param account_id: Verified numeric X id.
        """
        existing = self.db.execute("SELECT account_id FROM identity").fetchone()
        if existing and existing[0] != account_id:
            raise ValueError("Publishing account id changed")
        self.db.execute("INSERT OR IGNORE INTO identity VALUES (?)", (account_id,))
        self.db.commit()

    def register(self, campaign: dict, now: float) -> None:
        """Register a reviewed queue, retaining sent/ambiguous identities.

        Revised unsent payloads are accepted, but interrupted dispatches pause.

        :param campaign: Verified compiled campaign.
        :param now: Current UNIX time for fresh-start validation.
        """
        entries = campaign["entries"]
        known = [self.db.execute("SELECT * FROM tweets WHERE id=?", (entry["id"],)).fetchone() for entry in entries]
        if not any(known) and parse_utc(entries[0]["scheduled_at"]).replace(tzinfo=datetime.UTC).timestamp() < now + 600:
            raise ValueError("A fresh campaign needs a start at least ten minutes after preflight")
        with self.db:
            self.db.execute("UPDATE tweets SET status='needs_reconciliation' WHERE status='sending'")
            for entry, old in zip(entries, known, strict=True):
                payload_hash = digest({k: entry[k] for k in ("body", "image_sha256", "alt")})
                if old and old["status"] in {"sent", "needs_reconciliation", "sending"}:
                    continue
                self.db.execute("INSERT INTO tweets (id,payload_hash,status,campaign_digest) VALUES (?,?,'pending',?) ON CONFLICT(id) DO UPDATE SET payload_hash=excluded.payload_hash,campaign_digest=excluded.campaign_digest", (entry["id"], payload_hash, campaign["digest"]))

    def effective_time(self, entry: dict) -> float:
        """Calculate the next send time with account-wide minimum spacing.

        Overdue slots cannot create a catch-up burst after a restart.

        :param entry: Approved tweet with scheduled_at.
        :return: Earliest safe UNIX time.
        """
        latest = self.db.execute("SELECT MAX(sent_at) FROM tweets WHERE status='sent'").fetchone()[0]
        planned = parse_utc(entry["scheduled_at"]).replace(tzinfo=datetime.UTC).timestamp()
        return max(planned, latest + INTERVAL_SECONDS if latest is not None else planned)

    def begin_send(self, entry: dict, now: float) -> None:
        """Durably record dispatch intent before the network request.

        Crash recovery treats this state as ambiguous, never as unsent.

        :param entry: Approved pending tweet.
        :param now: Attempt time.
        """
        with self.db:
            cursor = self.db.execute("UPDATE tweets SET status='sending',attempt_at=? WHERE id=? AND status='pending'", (now, entry["id"]))
            if cursor.rowcount != 1:
                raise ValueError("Dispatch intent was not saved for exactly one pending entry")

    def finish_send(self, entry: dict, tweet_id: str, now: float) -> None:
        """Save the confirmed returned id immediately after success.

        A sent identity is retained even when campaign wording later changes.

        :param entry: Dispatched tweet.
        :param tweet_id: Confirmed X id.
        :param now: Confirmed send time.
        """
        with self.db:
            self.db.execute("UPDATE tweets SET status='sent',tweet_id=?,sent_at=? WHERE id=?", (tweet_id, now, entry["id"]))

    def set_status(self, entry_id: str, status: str) -> None:
        """Persist a known rejection or unresolved network outcome.

        Only a known no-write rejection may return an entry to pending.

        :param entry_id: Stable post/chart identity.
        :param status: Pending or needs_reconciliation.
        """
        assert status in {"pending", "needs_reconciliation"}
        with self.db:
            self.db.execute("UPDATE tweets SET status=? WHERE id=?", (status, entry_id))

    def reconcile(self, entry_id: str, resolution: str, reason: str, tweet_id: str | None = None, sent_at: float | None = None) -> None:
        """Apply an explicit, evidenced operator reconciliation.

        An empty timeline result alone is never an acceptable resolution.

        :param entry_id: Ambiguous entry.
        :param resolution: sent or not_sent.
        :param reason: Recorded operator evidence.
        :param tweet_id: Existing tweet id for a sent resolution.
        :param sent_at: Actual UTC posting timestamp as UNIX seconds.
        """
        if not reason or resolution not in {"sent", "not_sent"}:
            raise ValueError("Reconciliation needs an explicit resolution and evidence")
        old = self.db.execute("SELECT status FROM tweets WHERE id=?", (entry_id,)).fetchone()
        if not old or old[0] not in {"sending", "needs_reconciliation"}:
            raise ValueError("Entry has no ambiguous dispatch to reconcile")
        with self.db:
            if resolution == "sent":
                if not tweet_id or not tweet_id.isdigit() or sent_at is None:
                    raise ValueError("Sent resolution requires tweet id and actual UTC time")
                self.db.execute("UPDATE tweets SET status='sent',tweet_id=?,sent_at=? WHERE id=?", (tweet_id, sent_at, entry_id))
            else:
                self.db.execute("UPDATE tweets SET status='pending' WHERE id=?", (entry_id,))
            self.db.execute("INSERT INTO operator_audit VALUES (?,?,?,?)", (time.time(), entry_id, resolution, reason))
        logger.warning("Operator resolved %s as %s: %s", entry_id, resolution, reason)


def run_campaign(path: Path, state_dir: Path, writer: TwitterWriter, source_check: Callable[[dict], None], max_sends: int | None = None, now: Callable[[], float] = time.time, wait: Callable[[float], None] = time.sleep, ready_file: Path | None = None, launch_gate: Path | None = None) -> None:
    """Drain a verified queue with durable state and observable bounded waits.

    The injected clock/writer allow crash and schedule tests without real posts.

    :param path: Compiled campaign.
    :param state_dir: Established durable account state.
    :param writer: User-authenticated X writer.
    :param source_check: Publication/content preflight.
    :param max_sends: Optional supervised send limit.
    :param now: UNIX clock.
    :param wait: Bounded wait function.
    :param ready_file: Optional launcher readiness receipt.
    :param launch_gate: Optional launcher acknowledgement gate.
    """
    campaign = load_campaign(path)
    with account_lock(state_dir):
        ledger = TweetLedger(state_dir)
        try:
            identity = writer.fetch_identity()
            ledger.bind_account(str(identity["id"]))
            source_check(campaign)
            ledger.register(campaign, now())
            if ready_file:
                write_json(ready_file, {"pid": os.getpid(), "digest": campaign["digest"], "account_id": str(identity["id"])})
            if launch_gate:
                deadline = now() + 60
                while not launch_gate.exists():
                    if now() >= deadline:
                        raise ValueError("Launcher did not acknowledge readiness")
                    wait(1)
            sent = 0
            for entry in campaign["entries"]:
                row = ledger.db.execute("SELECT * FROM tweets WHERE id=?", (entry["id"],)).fetchone()
                if row["status"] == "sent":
                    continue
                if ledger.db.execute("SELECT 1 FROM tweets WHERE status IN ('sending','needs_reconciliation') LIMIT 1").fetchone():
                    raise ValueError("An ambiguous account dispatch needs operator reconciliation")
                while now() < ledger.effective_time(entry):
                    logger.info("Next chart %s at %s UTC", entry["chart_key"], datetime.datetime.fromtimestamp(ledger.effective_time(entry), datetime.UTC).isoformat())
                    wait(max(0, min(60, ledger.effective_time(entry) - now())))
                while True:
                    try:
                        source_check(campaign)
                        load_campaign(path, campaign["digest"])
                        tagged = [w for w in entry["winners"] if w.get("handle") and w.get("verified_at") and w.get("official_source")]
                        try:
                            users = writer.fetch_handles([w["handle"] for w in tagged])
                        except TwitterRateLimit:
                            raise
                        except TwitterError:
                            if not all(w.get("verification_method") == "official_link" and w.get("verification_note") for w in tagged):
                                raise
                            logger.warning("Winner lookup unavailable; using explicitly reviewed official-link evidence for %s", entry["chart_key"])
                            users = {w["handle"].lower(): {"id": w.get("user_id")} for w in tagged}
                        for winner in tagged:
                            user = users.get(winner["handle"].lower())
                            if not user or winner.get("user_id") and str(user["id"]) != str(winner["user_id"]):
                                raise ValueError("Winner account missing, renamed or reassigned; review handles")
                        media = writer.upload_chart(safe_asset(path.parent, entry["image"]), entry["alt"], entry["image_sha256"])
                        if media.expires_at <= now():
                            raise TwitterError("Media expired before dispatch")
                        ledger.begin_send(entry, now())
                        tweet_id = writer.create_post(entry["body"], media)
                        logger.warning("X confirmed chart %s: https://x.com/tradingprotocol/status/%s", entry["chart_key"], tweet_id)
                        ledger.finish_send(entry, tweet_id, now())
                        write_json(path.parent / f"execution-{entry['chart_key']}.json", {"account_id": str(identity["id"]), "tweet_id": tweet_id, "campaign_digest": campaign["digest"], "sent_at": now()})
                        logger.info("Posted https://x.com/tradingprotocol/status/%s", tweet_id)
                        sent += 1
                        break
                    except TwitterRateLimit as exc:
                        ledger.set_status(entry["id"], "pending")
                        logger.warning("X rate limit, deferring chart %s", entry["chart_key"])
                        while now() < exc.reset_at:
                            logger.info("Waiting for X reset; next chart %s", entry["chart_key"])
                            wait(max(0, min(60, exc.reset_at - now())))
                    except AmbiguousPostError:
                        ledger.set_status(entry["id"], "needs_reconciliation")
                        raise
                    except TwitterError:
                        row = ledger.db.execute("SELECT status FROM tweets WHERE id=?", (entry["id"],)).fetchone()
                        if row[0] == "sending":
                            ledger.set_status(entry["id"], "pending")
                        raise
                if max_sends is not None and sent >= max_sends:
                    logger.info("Supervised send limit reached; resume this campaign for the remaining queue")
                    return
            logger.info("Campaign finished")
        finally:
            try:
                backup_path = state_dir / "tweets-backup.sqlite.tmp"
                backup = sqlite3.connect(backup_path)
                try:
                    ledger.db.backup(backup)
                finally:
                    backup.close()
                backup_path.replace(state_dir / "tweets-backup.sqlite")
            except (sqlite3.Error, OSError) as exc:
                logger.warning("Ledger backup failed (%s); retain the durable primary database", type(exc).__name__)
            finally:
                ledger.close()


def launch_screen(path: Path, state_dir: Path, expected_digest: str, runner: Path) -> str:
    """Start a detached Screen runner and verify authenticated readiness.

    Secrets are inherited through the environment, never command arguments.

    :param path: Approved compiled file.
    :param state_dir: Durable account state.
    :param expected_digest: Manually reviewed digest.
    :param runner: Absolute runner script path.
    :return: Screen session name.
    """
    campaign = load_campaign(path, expected_digest)
    session = f"vault-report-tweets-{campaign['post_id']}"
    token = uuid.uuid4().hex
    ready, gate = state_dir / f"ready-{token}.json", state_dir / f"gate-{token}"
    with account_lock(state_dir):
        ledger = TweetLedger(state_dir)
        ledger.close()
    environment = os.environ | {"CAMPAIGN_FILE": str(path.resolve()), "STATE_DIR": str(state_dir.resolve()), "MODE": "run", "READY_FILE": str(ready.resolve()), "LAUNCH_GATE": str(gate.resolve()), "APPROVED_CAMPAIGN_DIGEST": expected_digest}
    log = state_dir / f"screen-{token}.log"
    subprocess.run(["screen", "-L", "-Logfile", str(log.resolve()), "-dmS", session, sys.executable, str(runner.resolve())], env=environment, check=True)
    deadline = time.monotonic() + 180
    logger.info("Starting Screen %s; log %s", session, log)
    while time.monotonic() < deadline:
        if ready.exists():
            receipt = json.loads(ready.read_text())
            if receipt["digest"] != expected_digest:
                raise ValueError("Runner readiness digest mismatch")
            os.kill(receipt["pid"], 0)
            gate.touch()
            logger.info("Ready: screen -r %s; log %s", session, log)
            return session
        if not re.search(r"\d+\." + re.escape(session) + r"\s", subprocess.run(["screen", "-ls"], capture_output=True, text=True).stdout):
            raise ValueError(f"Screen runner exited during preflight; inspect {log}")
        logger.info("Waiting for authenticated runner readiness: %s", session)
        time.sleep(5)
    subprocess.run(["screen", "-S", session, "-X", "quit"], check=False)
    raise ValueError(f"Runner readiness timed out; inspect {log}")
