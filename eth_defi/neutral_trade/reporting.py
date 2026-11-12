"""Manual verification tables for persisted Neutral Trade observations."""

import json
from decimal import Decimal

from tabulate import tabulate

from eth_defi.neutral_trade.metrics import NeutralTradeDatabase

#: Conservative review thresholds, not automatic source corrections.
RETURN_REVIEW_LIMIT = 0.1
PPS_ROUNDING_TOLERANCE = 0.000002


def render_verification_report(db: NeutralTradeDatabase) -> str:  # noqa: PLR0914 - one report owns consistent column units
    """Render public lead summaries, boundary samples and data-quality warnings.

    Unknown scales and fee basis remain explicit. Legacy USD fallback values
    are never labelled verified valuations. The operator must inspect this
    report; a successful scan is not automatic data-quality approval.

    :param db: Locked private source archive.
    :return: Three human-readable tabulate tables and a manual-review reminder.
    """
    summaries, samples, warnings = [], [], []
    for lead in db.iter_leads():
        raw = lead["raw"]
        if lead["skip_reason"]:
            if raw.get("visible") is True:
                summaries.append((None, lead["address"], lead["chain"], "excluded", lead["skip_reason"], 0, None, None, None, None, None, None, None))
            continue
        if raw.get("visible") is not True or raw.get("private") is not False:
            continue
        scale = Decimal(10) ** raw["asset"]["decimals"]
        rows = db.con.execute("SELECT endpoint,timestamp,raw FROM observations WHERE chain=? AND address=? ORDER BY timestamp", [lead["chain"], lead["address"]]).fetchall()
        states = db.con.execute("SELECT endpoint,complete,error FROM history_sync WHERE chain=? AND address=?", [lead["chain"], lead["address"]]).fetchall()
        status = "failed" if any(s[2] for s in states) else "complete" if states and all(s[1] for s in states) else "pending"
        metrics = (lead["metrics"] or {}).get("data") or {}
        latest = json.loads(rows[-1][2]) if rows else {}
        latest_pps = latest.get("pps", latest.get("closePps"))
        latest_shares = latest.get("totalShares", latest.get("closeTotalShares"))
        nav = metrics.get("tvl", {}).get("value", {}).get("token")
        fees = metrics.get("feeSchedule")
        summaries.append((raw.get("name"), lead["address"], lead["chain"], raw["asset"]["symbol"], status, len(rows), rows[0][1] if rows else None, rows[-1][1] if rows else None, Decimal(latest_pps) / scale if latest_pps else None, Decimal(latest_shares) / scale if latest_shares else None, nav, fees, "Global canonical PPS; personal net fees unknown"))
        for endpoint in ("share-price", "snapshots"):
            series = [(ts, json.loads(encoded)) for ep, ts, encoded in rows if ep == endpoint]
            selected = set(range(min(5, len(series)))) | set(range(max(0, len(series) - 5), len(series)))
            returns = []
            previous = None
            for index, (timestamp, value) in enumerate(series):
                pps = Decimal(value["pps" if endpoint == "share-price" else "closePps"]) / scale
                change = float(pps / previous - 1) if previous else None
                previous = pps
                returns.append(change)
                if change is not None and abs(change) > RETURN_REVIEW_LIMIT:
                    warnings.append((lead["address"], timestamp, "PPS move exceeds 10%; inspect source and scales"))
                if index and value.get("isLegacy") != series[index - 1][1].get("isLegacy"):
                    selected.update((index - 1, index))
            ranked = sorted((i for i, change in enumerate(returns) if change is not None), key=lambda i: abs(returns[i]), reverse=True)[:3]
            selected.update(ranked)
            for index in sorted(selected):
                timestamp, value = series[index]
                pps = Decimal(value["pps" if endpoint == "share-price" else "closePps"]) / scale
                equity = Decimal(value["totalEquity" if endpoint == "share-price" else "closeTvlToken"]) / scale
                shares = Decimal(value["totalShares" if endpoint == "share-price" else "closeTotalShares"]) / scale
                legacy = value.get("isLegacy")
                samples.append((raw.get("name"), endpoint, timestamp, pps, equity, shares, returns[index], legacy, value.get("sourceEvent", value.get("method")), "unknown" if legacy else value.get("closeTvlUsd", "unknown")))
                if shares and abs(float(pps - equity / shares)) > PPS_ROUNDING_TOLERANCE:
                    warnings.append((lead["address"], timestamp, "PPS and equity/shares differ; inspect observation/fee timing"))
            for ep, complete, error in states:
                if ep == endpoint and (error or not complete):
                    warnings.append((lead["address"], endpoint, error or "History coverage is pending"))
    return "\n\n".join((tabulate(summaries, headers=["Vault", "API ID", "Chain", "Asset", "Coverage", "Rows", "First", "Last", "Latest PPS (asset)", "Latest shares", "Current TVL (asset)", "Provider fee bps", "Basis"], missingval="unknown"), tabulate(samples, headers=["Vault", "Source", "UTC", "PPS (asset)", "TVL (asset)", "Shares", "Period return (canonical)", "Legacy", "Method", "USD source value"], missingval="unknown", disable_numparse=True), tabulate(warnings, headers=["Vault", "Time/source", "Inspect"], missingval="unknown"), "Manually inspect these tables. Global canonical returns are not personal net returns. No automatic approval."))
