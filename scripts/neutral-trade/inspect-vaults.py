"""Inspect Neutral Trade partner contracts without writing pipeline state.

Requires NEUTRAL_TRADE_API_KEY. Optional MAX_VAULTS=3, MAX_PAGES=2,
PAGE_SIZE=2 and VAULT_IDS bound the sample. WSOL is always excluded.
See eth_defi/neutral_trade/README-delta-neutral.md for the manual checklist.
"""

import logging
import os

from tabulate import tabulate

from eth_defi.coloured_logging import setup_console_logging
from eth_defi.neutral_trade.api import NeutralTradeClient

# ruff: noqa: EM101, PLR2004

logger = logging.getLogger(__name__)


def main() -> None:
    """Print directory and raw history samples for human verification.

    Preserve source amount strings and identify their units as unverified.
    The inspector writes no data files or shared pipeline state.

    :return: None after all selected requests complete.
    """
    setup_console_logging(os.environ.get("LOG_LEVEL", "info"))
    key = os.environ.get("NEUTRAL_TRADE_API_KEY")
    if not key:
        raise SystemExit("Set NEUTRAL_TRADE_API_KEY in the private environment")
    try:
        max_vaults = int(os.environ.get("MAX_VAULTS", "3"))
        max_pages = int(os.environ.get("MAX_PAGES", "2"))
        page_size = int(os.environ.get("PAGE_SIZE", "2"))
    except ValueError:
        raise SystemExit("MAX_VAULTS, MAX_PAGES and PAGE_SIZE must be integers") from None
    if min(max_vaults, max_pages) < 1 or not 1 <= page_size <= 100:
        raise SystemExit("MAX_VAULTS/MAX_PAGES must be positive; PAGE_SIZE must be 1..100")
    selected = {v.strip() for v in os.environ.get("VAULT_IDS", "").split(",") if v.strip()}
    with NeutralTradeClient(key) as client:
        status = client.fetch_status()
        logger.info("Projection cursor: %s", status.get("asOf"))
        vaults = list(client.fetch_vaults())
        # Hidden/private names and IDs must not leak into terminal reports.
        public = [v for v in vaults if v.public and (not selected or v.address in selected)]
        if selected:
            logger.info("Selected IDs without a public directory match: %s", len(selected - {v.address for v in public}))
        print(tabulate([(v.name, v.address, v.chain, v.symbol, v.decimals, v.skip_reason or "eligible") for v in public], headers=["Vault", "API ID", "Chain", "Asset", "Asset decimals", "Decision"]))
        chosen = [v for v in public if not v.skip_reason][:max_vaults]
        if not chosen:
            raise SystemExit("No eligible public vaults; authenticated history verification incomplete")
        for vault in chosen:
            detail = client.fetch_vault(vault)
            metrics = client.fetch_metrics(vault)["data"]
            if metrics is None:
                logger.warning("Provider metrics unavailable for %s", vault.address)
                metrics = {}
            print(tabulate([(field, metrics.get(field, "<missing>")) for field in ("rateUnit", "metricSource", "apy7d", "apy30d", "apy30dAfterFees", "roi", "feeSchedule", "computedAt")], headers=[f"{vault.name}: provider metric", "Value (unverified)"]))
            logger.info("Inspecting %s, detail fields: %s", vault.address, sorted(detail["data"]))
            for endpoint in ("share-price", "snapshots"):
                rows = []
                last_cursor = None
                for page in client.fetch_history_pages(vault, endpoint, max_pages=max_pages, page_size=page_size):
                    data = page["data"]
                    last_cursor = data["nextCursor"]
                    for row in data["series" if endpoint == "share-price" else "snapshots"]:
                        fields = ("blockTime", "pps", "totalEquity", "totalShares", "isLegacy", "sourceEvent", "priceUsd") if endpoint == "share-price" else ("date", "closePps", "closeTvlToken", "closeTotalShares", "isLegacy", "method", "priceUsd")
                        rows.append((vault.name, endpoint, *(row.get(field, "<missing>") for field in fields)))
                print(tabulate(rows, headers=["Vault", "Source", "Source time", "PPS raw", "TVL raw", "Shares raw", "Legacy", "Method", "USD reference (unverified)"], disable_numparse=True))
                logger.info("%s sample rows=%s; more pages=%s; accounting scales UNVERIFIED", endpoint, len(rows), last_cursor is not None)
                if not rows:
                    raise SystemExit("Empty history sample; real historical verification incomplete")
    logger.info("Manually inspect units, timestamps, provenance and coverage. No automatic data-quality approval.")


if __name__ == "__main__":
    main()
