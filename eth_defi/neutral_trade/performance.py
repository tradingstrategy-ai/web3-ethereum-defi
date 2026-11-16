"""Manual lifetime performance reports from retained Neutral Trade observations.

Use the shared vault period-metrics implementation, never the provider's APY
or display/backtest series. See https://api.neutral.trade/openapi.json.
"""

import datetime
import json
from decimal import Decimal

import pandas as pd
from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.neutral_trade.constants import make_solana_vault_address
from eth_defi.neutral_trade.metrics import NeutralTradeDatabase
from eth_defi.neutral_trade.vault_data_export import CANONICAL_STARTS, PPS_RETURN_REVIEW_LIMIT, build_raw_prices_dataframe
from eth_defi.research.vault_metrics import calculate_period_metrics, prepare_daily_share_price_series
from eth_defi.vault.fee import FeeData

#: Conservative manual-review triggers, not automatic price repairs.
PPS_BASIS_REVIEW_JUMP = 0.05
EQUITY_STABILITY_LIMIT = 0.02


def build_performance_summary(db: NeutralTradeDatabase, *, as_of: datetime.datetime | None = None, public_names: dict[str, str] | None = None) -> pd.DataFrame:  # noqa: PLR0914 - one report keeps source and metric units together
    """Calculate comparable canonical-history metrics for all public vaults.

    Age is days since the provider's first live tick, falling back to the first
    retained non-legacy observation. Performance covers the validated retained
    interval, which can be shorter. EVM source-only prices can be analysed here
    without writing shared EVM records. Excluded, incomplete and discontinuous
    histories have no manufactured performance values.

    :param db: Locked private source archive.
    :param as_of: Naive UTC report reference, default current UTC.
    :param public_names: Optional names from the public yield catalogue.
    :return: DataFrame containing name/address/chain, integer age_days,
        naive history_start/history_end, decimal cagr, unitless sharpe, sample
        count and status. Missing metrics remain null.
    """
    as_of = as_of or native_datetime_utc_now()
    assert as_of.tzinfo is None
    native = build_raw_prices_dataframe(db)
    result = []
    for lead in db.iter_leads():
        raw = lead["raw"]
        if raw.get("visible") is not True or (not lead["skip_reason"] and raw.get("private") is not False):
            continue
        row = {"name": raw.get("name") or (public_names or {}).get(lead["address"], "Excluded vault"), "address": lead["address"], "chain": lead["chain"], "age_days": None, "history_start": None, "history_end": None, "cagr": None, "sharpe": None, "samples": 0, "status": "WSOL excluded" if lead["skip_reason"] == "unsupported_denomination" else lead["skip_reason"] or "ok"}
        result.append(row)
        if lead["skip_reason"]:
            continue
        observations = db.con.execute("SELECT timestamp,raw FROM observations WHERE chain=? AND address=? AND endpoint='share-price' ORDER BY timestamp", [lead["chain"], lead["address"]]).fetchall()
        canonical = [(ts, json.loads(value)) for ts, value in observations if json.loads(value).get("isLegacy") is False]
        metrics = (lead["metrics"] or {}).get("data") or {}
        first_live = metrics.get("firstTickBlockTime")
        inception = datetime.datetime.fromtimestamp(first_live, datetime.UTC).replace(tzinfo=None) if isinstance(first_live, int) and not isinstance(first_live, bool) else canonical[0][0] if canonical else None
        if inception is not None:
            row["age_days"] = max(0, (as_of.date() - inception.date()).days)
        states = db.con.execute("SELECT complete,error FROM history_sync WHERE chain=? AND address=?", [lead["chain"], lead["address"]]).fetchall()
        if not states or any(not complete or error for complete, error in states):
            row["status"] = "incomplete backfill"
            continue
        if lead["chain"] == "solana":
            address = make_solana_vault_address(lead["address"])
            prices = native[native["address"] == address].copy() if not native.empty else pd.DataFrame()
        else:
            scale = Decimal(10) ** raw["asset"]["decimals"]
            # EVM can emit several different PPS observations in one block's
            # second. Use the provider's definitive daily close, not arbitrary
            # intrablock tie-breaking. Legacy day means remain excluded.
            snapshots = db.con.execute("SELECT timestamp,raw FROM observations WHERE chain=? AND address=? AND endpoint='snapshots' ORDER BY timestamp", [lead["chain"], lead["address"]]).fetchall()
            closes = [(ts, json.loads(value)) for ts, value in snapshots if json.loads(value).get("isLegacy") is False and json.loads(value).get("method") == "close"]
            prices = pd.DataFrame([{"timestamp": timestamp, "share_price": float(Decimal(value["closePps"]) / scale), "total_assets": float(Decimal(value["closeTvlToken"]) / scale)} for timestamp, value in closes])
            row["status"] = "canonical daily closes; EVM source-only"
            if not prices.empty:
                conflict = prices.groupby("timestamp")["share_price"].nunique().gt(1).any()
                prices = prices.drop_duplicates("timestamp").sort_values("timestamp")
                if conflict or prices["share_price"].pct_change().abs().gt(PPS_RETURN_REVIEW_LIMIT).any():
                    prices = pd.DataFrame()
        if prices.empty:
            row["status"] = "withheld: no continuous verified history"
            continue
        prices = prices.sort_values("timestamp").set_index("timestamp")
        if inception is not None:
            # Migrated first-tick timestamps can be UTC daily closes. Retain
            # the launch day, but omit earlier initialisation/pre-live days.
            live_day = pd.Timestamp(inception.date())
            prices = prices.loc[prices.index >= live_day]
        if prices.empty:
            row["status"] = "no canonical observations in the live interval"
            continue
        row.update(history_start=prices.index[0], history_end=prices.index[-1], samples=len(prices))
        basis_review = prices["share_price"].pct_change().abs().gt(PPS_BASIS_REVIEW_JUMP) & prices["total_assets"].pct_change().abs().lt(EQUITY_STABILITY_LIMIT)
        if basis_review.any():
            row["status"] = "unverified: PPS changed >5% while equity changed <2%"
            continue
        observations_series = prices["share_price"].astype(float)
        daily, returns = prepare_daily_share_price_series(observations_series)
        unknown_fees = FeeData(fee_mode=None, management=None, performance=None, deposit=None, withdraw=None)
        performance = calculate_period_metrics("lifetime", unknown_fees, unknown_fees, observations_series, daily, returns, prices["total_assets"], pd.Timestamp(as_of))
        row.update(history_start=performance.samples_start_at, history_end=performance.samples_end_at, cagr=performance.cagr_gross, sharpe=performance.sharpe, samples=performance.raw_samples)
        if performance.error_reason:
            row["status"] = performance.error_reason
        elif performance.annualisation_error_reason:
            row["status"] = performance.annualisation_error_reason
        elif lead["address"] in CANONICAL_STARTS:
            row["status"] = "reviewed interval from " + CANONICAL_STARTS[lead["address"]].date().isoformat()
    return pd.DataFrame(result).sort_values("name").reset_index(drop=True) if result else pd.DataFrame()


def format_performance_summary(frame: pd.DataFrame) -> str:
    """Render the requested manual performance table with explicit unknowns.

    Percentage formatting is presentation only; the CSV retains decimal CAGR.

    :param frame: DataFrame returned by :func:`build_performance_summary`.
    :return: GitHub-flavoured table plus metric definition notes.
    """
    rows = [(r["name"], r["address"], int(r["age_days"]) if pd.notna(r["age_days"]) else "—", f"{r['cagr']:.2%}" if pd.notna(r["cagr"]) else "—", f"{r['sharpe']:.2f}" if pd.notna(r["sharpe"]) else "—", r["status"]) for r in frame.to_dict(orient="records")]
    return tabulate(rows, headers=["Vault", "Address", "Age (days)", "Lifetime CAGR", "Sharpe", "Status"], tablefmt="github", disable_numparse=True) + "\n\nAge: days since first provider live tick (or first retained canonical observation). CAGR/Sharpe: validated canonical history, not personal after-fee returns. Sharpe uses daily returns and a zero risk-free rate. CAGR requires 30 days; Sharpe requires 14 days and sufficient daily samples. Reviewed intervals can be shorter than vault age."
