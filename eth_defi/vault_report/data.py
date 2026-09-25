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
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.research.vault_metrics import MAX_VALID_NAV

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
    return df[f"{column}_net"].fillna(df[column])


def prepare_vault_metrics(vaults: list[dict]) -> pd.DataFrame:
    """Turn top vaults JSON records into a report DataFrame.

    Adds helper columns on top of the exported fields
    (see :py:class:`eth_defi.research.vault_metrics.VaultMetricsRecord`):

    - ``one_month_cagr_best``: net annualised one-month return when fee data
      is known, otherwise gross
    - ``three_months_sharpe_best``: net Sharpe, falling back to gross
    - ``is_perp_dex``: perpetual DEX native trading vault
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
    df["three_months_sharpe_best"] = _pick_net(df, "three_months_sharpe")
    df["is_perp_dex"] = df["flags"].apply(lambda flags: PERP_DEX_TRADING_VAULT_FLAG in (flags or []))

    for column in ("current_nav", "peak_nav"):
        df[column] = df[column].where(df[column] <= MAX_VALID_NAV)
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
    return VaultReportData(vaults_df=vaults_df, prices_path=prices_path)


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


def calculate_daily_share_prices(prices_df: pd.DataFrame) -> pd.DataFrame:
    """Resample share prices to a daily wide table.

    Each vault series is forward filled between its first and last data point
    only, so a vault does not appear to exist before it launched or after it
    stopped reporting.

    :param prices_df:
        Output of :py:func:`read_vault_share_prices`.

    :return:
        DataFrame indexed by daily ``DatetimeIndex`` with one column of
        share prices per vault id.
    """
    if len(prices_df) == 0:
        return pd.DataFrame()
    daily = prices_df.pivot_table(index="timestamp", columns="id", values="share_price", aggfunc="last").resample("D").last()
    return daily.ffill().where(daily.bfill().notna())
