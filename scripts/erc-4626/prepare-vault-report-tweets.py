"""Prepare, review, compile or publish a monthly report tweet plan.

Environment: MODE=generate|preview|compile|publish-preview,
GHOST_POST_SLUG, OUTPUT_ROOT, CHART_EVIDENCE, PREVIOUS_PLAN, TWEET_PLAN,
GITHUB_PR, ASSETS_BRANCH, MEDIA_HOSTS. Credentials come from the sourced
local environment. See eth_defi/vault_report/README-tweets.md.
"""

import logging
import os
import json
from pathlib import Path

from eth_defi.vault_report.ghost import GhostAdminClient
from eth_defi.compat import native_datetime_utc_now
from eth_defi.vault_report.tweet_plan import MEDIA_HOSTS, compile_campaign, fetch_campaign_assets, prepare_campaign, publish_preview, render_preview, write_json
from eth_defi.vault_report.twitter import TwitterWriter

logger = logging.getLogger(__name__)


def main() -> None:
    """Execute the explicitly selected environment-driven preparation mode.

    Generation and preview never post tweets or overwrite editorial Markdown.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mode = os.environ.get("MODE", "generate")
    if mode == "generate":
        client = GhostAdminClient(os.environ.get("GHOST_ADMIN_API_URL") or os.environ["GHOST_CONTENT_API_URL"], os.environ["GHOST_ADMIN_API_KEY"])
        path = prepare_campaign(client, os.environ["GHOST_POST_SLUG"], Path(os.environ.get("OUTPUT_ROOT", "~/.tradingstrategy/vault-report-tweets")).expanduser(), Path(os.environ["CHART_EVIDENCE"]) if os.environ.get("CHART_EVIDENCE") else None, Path(os.environ["PREVIOUS_PLAN"]) if os.environ.get("PREVIOUS_PLAN") else None, frozenset(os.environ["MEDIA_HOSTS"].split(",")) if os.environ.get("MEDIA_HOSTS") else MEDIA_HOSTS)
        logger.info("Editable tweet plan: %s", path)
        logger.info("Preview: %s", render_preview(path))
    else:
        path = Path(os.environ["TWEET_PLAN"]).expanduser().resolve()
        if mode == "verify-handles":
            evidence_path = path.with_name("chart-evidence.json")
            evidence = json.loads(evidence_path.read_text())
            writer = TwitterWriter.from_env()
            writer.fetch_identity()
            handles = sorted({w["handle"] for record in evidence.values() for field in ("winners", "entities") for w in record.get(field, []) if w.get("handle")})
            users = {}
            for offset in range(0, len(handles), 100):
                users.update(writer.fetch_handles(handles[offset : offset + 100]))
            for record in evidence.values():
                for field in ("winners", "entities"):
                    for winner in record.get(field, []):
                        user = users.get((winner.get("handle") or "").lower())
                        if user and winner.get("official_source"):
                            winner.update(handle=user["username"], user_id=str(user["id"]), verified_at=native_datetime_utc_now().isoformat() + "Z", verification_method="x_api")
            write_json(evidence_path, evidence)
            logger.info("Verified %d winner accounts; regenerate the preview and review changed evidence", len(users))
        elif mode == "hydrate":
            fetch_campaign_assets(path)
            logger.info("Restored verified campaign images")
        elif mode == "preview":
            logger.info("Preview: %s", render_preview(path))
        elif mode == "compile":
            logger.info("Approved campaign: %s", compile_campaign(path))
        elif mode == "publish-preview":
            logger.info("Review comment: %s", publish_preview(path, os.environ["GITHUB_PR"], os.environ["ASSETS_BRANCH"]))
        else:
            raise ValueError("Unknown preparation MODE")


if __name__ == "__main__":
    main()
