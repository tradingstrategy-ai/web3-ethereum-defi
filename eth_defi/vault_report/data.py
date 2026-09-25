"""Load vault metrics and share price history for the monthly vault report.

The report uses the same data as the live
`vault dashboard <https://tradingstrategy.ai/trading-view/vaults>`__, so that
the numbers in a blog post match what readers see on the website:

- Vault metrics (returns, TVL, Sharpe, risk, flags) come from the public
  top vaults JSON export produced by :py:mod:`eth_defi.vault.top_vaults_json`.
- Share price history for the charts comes from the cleaned vault price
  Parquet, available through the Pro
  `vault datasets <https://tradingstrategy.ai/trading-view/vaults/datasets>`__
  download API.

On the production scanner host both files already exist in the pipeline data
directory and can be passed as local paths instead of downloading them.
"""

import datetime
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.research.vault_metrics import MAX_VALID_NAV, USDollarAmount
from eth_defi.vault_report.sections import SPARKLINE_URL, classify_vault

logger = logging.getLogger(__name__)

#: Public top vaults JSON export used by the tradingstrategy.ai vault pages
TOP_VAULTS_JSON_URL = "https://top-defi-vaults.tradingstrategy.ai/top_vaults_by_chain.json"

#: Pro dataset download endpoint for the cleaned vault price Parquet.
#:
#: Needs ``?api-key=`` query parameter with a Pro subscription API key.
VAULT_PRICES_DOWNLOAD_URL = "https://tradingstrategy.ai/vaults/datasets/download/vault-prices"

#: Re-download cached files older than this
DEFAULT_CACHE_MAX_AGE = datetime.timedelta(hours=6)

#: Vault flag for perpetual DEX native trading vaults (Hyperliquid, GRVT, Lighter...)
PERP_DEX_TRADING_VAULT_FLAG = "perp_dex_trading_vault"


@dataclass(slots=True)
class VaultReportData:
    """Input data for the monthly vault report.

    Created by :py:func:`fetch_vault_report_data`.
    """

    #: One row per vault, indexed by ``{chain_id}-{address}`` vault id.
    #:
    #: See :py:func:`prepare_vault_metrics` for the columns.
    vaults_df: pd.DataFrame

    #: Path to the cleaned vault price Parquet file used for charts
    prices_path: Path

    #: Strategy category key -> category description, from the top vaults export
    categories: dict[str, dict] = field(default_factory=dict)

    @property
    def data_end_at(self) -> datetime.datetime:
        """The most recent metrics period end across all vaults.

        :return:
            Naive UTC datetime of the latest vault data point.
        """
        return self.vaults_df["end_date"].max().to_pydatetime()


def fetch_file(
    url: str,
    path: Path,
    params: dict | None = None,
    max_age: datetime.timedelta = DEFAULT_CACHE_MAX_AGE,
    timeout: float = 120.0,
) -> Path:
    """Download a file with a progress bar, reusing a fresh cached copy.

    The file is downloaded to a temporary name first and renamed into place,
    so an interrupted download never leaves a truncated cache file behind.

    Error messages include ``url`` but never ``params``, so an API key passed in
    ``params`` does not end up in logs or tracebacks.

    :param url:
        URL to download, without secrets.

    :param path:
        Local cache path.

    :param params:
        Query parameters, e.g. an API key.

    :param max_age:
        Reuse the cached file if it is younger than this.

    :param timeout:
        HTTP connect/read timeout in seconds.

    :return:
        Path to the downloaded file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        modified_at = datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.UTC).replace(tzinfo=None)
        age = native_datetime_utc_now() - modified_at
        if age < max_age:
            logger.info("Using cached %s, age %s", path, age)
            return path

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    logger.info("Downloading %s to %s", url, path)
    try:
        with requests.get(url, params=params, stream=True, timeout=timeout) as resp:
            # Do not use raise_for_status(): its message contains the full URL with query parameters
            if resp.status_code != 200:
                raise RuntimeError(f"Downloading {url} failed: HTTP {resp.status_code}")
            total = int(resp.headers.get("content-length", 0)) or None
            with open(tmp_path, "wb") as out, tqdm(total=total, unit="B", unit_scale=True, desc=f"Downloading {path.name}") as progress:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    out.write(chunk)
                    progress.update(len(chunk))
    except requests.RequestException as e:
        # Connection error messages contain the full URL with query parameters
        raise RuntimeError(f"Downloading {url} failed: {type(e).__name__}") from None

    tmp_path.replace(path)
    logger.info("Downloaded %s, %d bytes", path, path.stat().st_size)
    return path


def _pick_net(df: pd.DataFrame, column: str) -> pd.Series:
    """Prefer the net (after fees) value of a metric, fall back to the gross value.

    :param df:
        Vault metrics with ``{column}`` and ``{column}_net`` columns.

    :param column:
        Gross metric column name.

    :return:
        Net values where known, gross values otherwise.
    """
    # All-null JSON columns come through as object dtype
    return pd.to_numeric(df[f"{column}_net"], errors="coerce").fillna(pd.to_numeric(df[column], errors="coerce"))


def _get_three_months_drawdown(period_results: list[dict] | None) -> float:
    """Read the three-month maximum drawdown from a vault's period results.

    :param period_results:
        ``period_results`` list of a top vaults JSON record.

    :return:
        Drawdown as a negative fraction, or ``NaN`` if missing.
    """
    three_months = next((p for p in (period_results or []) if p.get("period") == "3M"), None)
    value = three_months.get("max_drawdown") if three_months else None
    return float(value) if value is not None else float("nan")


def prepare_vault_metrics(vaults: list[dict]) -> pd.DataFrame:
    """Turn top vaults JSON records into a report DataFrame.

    Adds helper columns on top of the exported fields
    (see :py:class:`eth_defi.research.vault_metrics.VaultMetricsRecord`):

    - ``one_month_cagr_best``: net annualised one-month return when fee data
      is known, otherwise gross
    - ``three_months_cagr_best``: the same for the annualised three-month return
    - ``three_months_sharpe_best``: net Sharpe, falling back to gross
    - ``is_perp_dex``: perpetual DEX native trading vault
    - ``three_months_max_drawdown``: maximum drawdown of the three-month period
      as a negative fraction, from ``period_results``
    - ``group``: lending, perpetual futures DEX, tokenised fund or other, see
      :py:func:`eth_defi.vault_report.sections.classify_vault`
    - ``end_date``, ``start_date``: parsed as naive UTC timestamps

    TVL values above :py:data:`~eth_defi.research.vault_metrics.MAX_VALID_NAV`
    come from broken share tokens and are set to ``NaN``.

    :param vaults:
        ``vaults`` list of the top vaults JSON export.

    :return:
        DataFrame indexed by vault id.
    """
    df = pd.DataFrame(vaults)
    assert len(df) > 0, "Top vaults JSON contained no vaults"
    df.index = df["id"].to_numpy()

    for column in ("start_date", "end_date"):
        df[column] = pd.to_datetime(df[column])

    df["one_month_cagr_best"] = _pick_net(df, "one_month_cagr")
    df["three_months_cagr_best"] = _pick_net(df, "three_months_cagr")
    df["three_months_sharpe_best"] = _pick_net(df, "three_months_sharpe")
    df["is_perp_dex"] = df["flags"].apply(lambda flags: PERP_DEX_TRADING_VAULT_FLAG in (flags or []))
    df["three_months_max_drawdown"] = df["period_results"].apply(_get_three_months_drawdown) if "period_results" in df.columns else float("nan")

    for column in ("current_nav", "peak_nav"):
        df[column] = df[column].where(df[column] <= MAX_VALID_NAV)
    df["group"] = df.apply(classify_vault, axis=1)
    return df


def fetch_vault_report_data(
    cache_dir: Path,
    top_vaults_json_path: Path | None = None,
    prices_path: Path | None = None,
    api_key: str | None = None,
    max_age: datetime.timedelta = DEFAULT_CACHE_MAX_AGE,
) -> VaultReportData:
    """Download, or read from local files, all input data for the report.

    :param cache_dir:
        Where to store downloaded files.

    :param top_vaults_json_path:
        Use a local top vaults JSON instead of downloading :py:data:`TOP_VAULTS_JSON_URL`.

    :param prices_path:
        Use a local cleaned price Parquet instead of downloading :py:data:`VAULT_PRICES_DOWNLOAD_URL`.

    :param api_key:
        Pro vault data API key, needed when ``prices_path`` is not given.

    :param max_age:
        Reuse downloaded files younger than this.

    :return:
        Loaded report data.
    """
    if top_vaults_json_path is None:
        top_vaults_json_path = fetch_file(TOP_VAULTS_JSON_URL, cache_dir / "top_vaults_by_chain.json", max_age=max_age)

    if prices_path is None:
        if not api_key:
            raise RuntimeError("Give either a local vault price Parquet path or a Pro vault data API key to download it")
        prices_path = fetch_file(VAULT_PRICES_DOWNLOAD_URL, cache_dir / "vault-historical.parquet", params={"api-key": api_key}, max_age=max_age)

    with open(top_vaults_json_path, "rb") as inp:
        data = json.load(inp)
    vaults_df = prepare_vault_metrics(data["vaults"])
    logger.info("Loaded %d vaults from %s, generated at %s", len(vaults_df), top_vaults_json_path, data["generated_at"])
    return VaultReportData(vaults_df=vaults_df, prices_path=prices_path, categories=data.get("categories", {}))


def read_vault_share_prices(
    prices_path: Path,
    vault_ids: list[str],
    start_at: datetime.datetime | None = None,
) -> pd.DataFrame:
    """Read share price history for selected vaults.

    Only reads the columns and rows needed, so this is fast and low-memory
    even though the full price file has millions of rows.

    :param prices_path:
        Cleaned vault price Parquet with ``id``, ``timestamp`` and ``share_price`` columns.

    :param vault_ids:
        ``{chain_id}-{address}`` vault ids to read.

    :param start_at:
        Skip rows before this timestamp.

    :return:
        Long-format DataFrame with ``id``, ``timestamp`` and ``share_price`` columns.
    """
    expression = pc.field("id").isin(vault_ids)
    if start_at is not None:
        expression &= pc.field("timestamp") >= pd.Timestamp(start_at)

    table = pq.read_table(prices_path, columns=["id", "timestamp", "share_price"], filters=expression)
    # The production file stores pandas metadata that would restore timestamp as the index
    df = table.to_pandas(ignore_metadata=True)
    logger.info("Read %d price rows for %d vaults from %s", len(df), df["id"].nunique(), prices_path)
    return df


def calculate_daily_share_prices(prices_df: pd.DataFrame, interpolate: bool = True) -> pd.DataFrame:
    """Resample share prices to a daily wide table.

    Days without an observation are interpolated in time between the vault's
    first and last data point only, so sparsely updated vaults draw a straight
    accrual line instead of a staircase, and a vault does not appear to exist
    before it launched or after it stopped reporting.

    :param prices_df:
        Output of :py:func:`read_vault_share_prices`.

    :param interpolate:
        Interpolate missing days for drawing equity curves. Set ``False`` to
        forward fill instead, like the exported metrics do before calculating
        Sharpe ratios, see :py:func:`eth_defi.vault_report.charts.calculate_rolling_sharpe`.

    :return:
        DataFrame indexed by daily ``DatetimeIndex`` with one column of
        share prices per vault id.
    """
    if len(prices_df) == 0:
        return pd.DataFrame()
    daily = prices_df.pivot_table(index="timestamp", columns="id", values="share_price", aggfunc="last").resample("D").last()
    if interpolate:
        return daily.interpolate(method="time", limit_area="inside")
    return daily.ffill().where(daily.bfill().notna())


#: TVL points above this are broken share tokens; the same threshold as the
#: website historical TVL charts (``src/lib/echarts/tvl-outliers.ts`` in the frontend)
TVL_OUTLIER_THRESHOLD: USDollarAmount = 50_000_000_000

#: Extra history read before the chart start, so vaults with sparse updates
#: already have a value in the first week
TVL_LOOKBACK_BUFFER = datetime.timedelta(days=35)


def read_vault_tvl_history(
    prices_path: Path,
    vault_ids: list[str],
    start_at: datetime.datetime,
) -> pd.DataFrame:
    """Read weekly TVL history for vaults.

    Mirrors the website's historical TVL query
    (``src/lib/echarts/historical-tvl-server.ts`` in the frontend): the last
    ``total_assets`` value of each vault in each week, with values above
    :py:data:`TVL_OUTLIER_THRESHOLD` dropped. Each vault is forward filled
    between its first and last week only. ``total_assets`` in the cleaned
    price Parquet is already in USD, also for EUR-denominated vaults.

    The aggregation runs in an in-memory DuckDB connection, so only one row
    per vault and week is loaded into pandas.

    :param prices_path:
        Cleaned vault price Parquet with ``id``, ``timestamp`` and ``total_assets`` columns.

    :param vault_ids:
        Vault ids to read.

    :param start_at:
        First week to include.

    :return:
        DataFrame indexed by week start with one TVL column per vault id, in USD.
    """
    query = """
        SELECT id, date_trunc('week', "timestamp") AS week, arg_max(total_assets, "timestamp") AS tvl
        FROM read_parquet(?)
        WHERE "timestamp" >= ? AND total_assets >= 0 AND total_assets <= ? AND id IN (SELECT unnest(?))
        GROUP BY id, week
    """
    read_from = pd.Timestamp(start_at - TVL_LOOKBACK_BUFFER)
    with duckdb.connect() as connection:
        weekly = connection.execute(query, [str(prices_path), read_from, TVL_OUTLIER_THRESHOLD, vault_ids]).df()
    logger.info("Read %d weekly TVL rows for %d vaults from %s", len(weekly), weekly["id"].nunique(), prices_path)
    if len(weekly) == 0:
        return pd.DataFrame()
    periodic = weekly.pivot(index="week", columns="id", values="tvl").sort_index()
    periodic = periodic.ffill().where(periodic.bfill().notna())
    return periodic.loc[periodic.index >= pd.Timestamp(start_at).to_period("W").start_time]


def fetch_available_sparklines(vault_ids: list[str], max_workers: int = 16, timeout: float = 20.0) -> set[str]:
    """Check which vaults have a published 90-day sparkline image.

    Low-TVL vaults are rendered on a slower cadence and may not have a sparkline.

    :param vault_ids:
        Vault ids to check.

    :param max_workers:
        Parallel HTTP HEAD requests.

    :param timeout:
        HTTP timeout in seconds.

    :return:
        Vault ids with a sparkline.
    """

    def _exists(vault_id: str) -> bool:
        try:
            return requests.head(SPARKLINE_URL.format(vault_id=vault_id), timeout=timeout).status_code == 200
        except requests.RequestException as e:
            logger.warning("Could not check sparkline for %s: %s", vault_id, e)
            return False

    unique_ids = sorted(set(vault_ids))
    results = Parallel(n_jobs=max_workers, backend="threading")(delayed(_exists)(vault_id) for vault_id in tqdm(unique_ids, desc="Checking sparklines"))
    available = {vault_id for vault_id, exists in zip(unique_ids, results, strict=True) if exists}
    logger.info("Sparklines available for %d of %d vaults", len(available), len(unique_ids))
    return available
