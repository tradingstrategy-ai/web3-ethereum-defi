"""Load vault metrics and share price history for the monthly vault report.

The report uses the same data as the live
`vault dashboard <https://tradingstrategy.ai/vaults>`__, so that
the numbers in a blog post match what readers see on the website:

- Vault metrics (returns, TVL, Sharpe, risk, flags) come from the production
  top vaults JSON export produced by :py:mod:`eth_defi.vault.top_vaults_json`.
- Share price history for the charts comes from the cleaned vault price
  Parquet. The script downloads both files directly from the private R2
  bucket with :py:func:`fetch_vault_report_data_from_r2`, using a one-day cache.
  A failed production refresh aborts generation instead of using stale files.

On the production scanner host both files already exist in the pipeline data
directory and can be passed as local paths instead of downloading them.

Other inputs read here:

- Weekly TVL history for the stacked TVL charts, aggregated from the same
  price Parquet with DuckDB the way the website's historical TVL charts do,
  see :py:func:`read_vault_tvl_history`.
- Which vaults have a published sparkline image for the tables' "3M
  history" column, see :py:func:`fetch_available_sparklines`.

Production downloads are cached in the report cache directory and reused for
:py:data:`DEFAULT_R2_CACHE_MAX_AGE`, so rerunning the report while editing does
not download the ~250 MB price file again, and the investability check's
decision files, tied to the input data, stay reusable between reruns.

:py:func:`fetch_vault_report_data` also supports explicit local paths and
public/Pro API downloads for library callers. Their HTTP cache is separate
from the script's production cache.
"""

import datetime
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.cloudflare_r2 import create_r2_client
from eth_defi.research.vault_metrics import MAX_VALID_NAV, USDollarAmount, _get_trading_strategy_vault_link
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault_report.sections import OTHER_PROTOCOL, SPARKLINE_URL, canonical_vault_urls, classify_vault, find_period, is_identified_protocol

logger = logging.getLogger(__name__)

#: Public top vaults JSON export used by the tradingstrategy.ai vault pages, written by
#: :py:mod:`eth_defi.vault.top_vaults_json`. No API key needed.
TOP_VAULTS_JSON_URL = "https://top-defi-vaults.tradingstrategy.ai/top_vaults_by_chain.json"

#: Pro dataset download endpoint for the cleaned vault price Parquet.
#:
#: Needs ``?api-key=`` query parameter with a Pro subscription API key,
#: ``VAULT_PRO_API_KEY`` in the report script. The file is about 250 MB.
VAULT_PRICES_DOWNLOAD_URL = "https://tradingstrategy.ai/vaults/datasets/download/vault-prices"

#: Re-download cached files older than this.
#:
#: Reruns while editing the report reuse the same data, so the selections and
#: the investability check's saved decisions stay valid; a rerun after the
#: refresh usually needs the check again.
DEFAULT_CACHE_MAX_AGE = datetime.timedelta(hours=6)

#: Production report inputs are refreshed from the private bucket daily.
DEFAULT_R2_CACHE_MAX_AGE = datetime.timedelta(days=1)

#: Vault flag for perpetual DEX native trading vaults (Hyperliquid, GRVT, Lighter...),
#: precomputed into the ``is_perp_dex`` column
PERP_DEX_TRADING_VAULT_FLAG = VaultFlag.perp_dex_trading_vault.value


@dataclass(slots=True)
class VaultReportData:
    """Input data for the monthly vault report.

    Created by :py:func:`fetch_vault_report_data`. Holds the vault metrics in
    memory, but only the path of the price Parquet: the file has millions of
    rows, and the charts read just the vaults and columns they need, see
    :py:func:`read_vault_share_prices` and :py:func:`read_vault_tvl_history`.
    """

    #: One row per vault, indexed by ``{chain_id}-{address}`` vault id.
    #:
    #: See :py:func:`prepare_vault_metrics` for the columns.
    vaults_df: pd.DataFrame

    #: Path to the cleaned vault price Parquet file used for charts
    prices_path: Path

    #: Strategy category key -> category description, from the top vaults export ``categories``.
    #: The risk and return chart uses the category labels for its legend.
    categories: dict[str, dict] = field(default_factory=dict)

    @property
    def data_end_at(self) -> datetime.datetime:
        """The most recent metrics period end across all vaults.

        Used as the report date everywhere instead of the wall clock: the
        stale data filter, the chart windows and the post's data date are all
        relative to it, so regenerating the report later from the same export
        gives the same selections.

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

    The cache freshness is the file's modification time, see
    :py:func:`get_cache_age`, so no separate metadata file is needed and
    deleting the file forces a download.

    The file is downloaded to a temporary name first and renamed into place,
    so an interrupted download never leaves a truncated cache file behind.
    The body is streamed in 1 MiB chunks, so the large price Parquet never
    sits in memory, with a :py:mod:`tqdm_loggable` progress bar that stays
    visible in non-interactive logs.

    Error messages include ``url`` but never ``params``, so an API key passed in
    ``params`` does not end up in logs or tracebacks.

    Also used by :py:mod:`eth_defi.vault_report.benchmarks` for the FRED CSV.

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

    age = get_cache_age(path)
    if age is not None:
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

    The export leaves ``*_net`` empty when the vault's fees are unknown. The
    website shows the same fallback without a net or gross marker, so the
    report's ``*_best`` columns match the numbers readers see there.

    :param df:
        Vault metrics with ``{column}`` and ``{column}_net`` columns.

    :param column:
        Gross metric column name.

    :return:
        Net values where known, gross values otherwise.
    """
    # All-null JSON columns come through as object dtype
    return pd.to_numeric(df[f"{column}_net"], errors="coerce").fillna(pd.to_numeric(df[column], errors="coerce"))


def get_cache_age(path: Path) -> datetime.timedelta | None:
    """Age of a cached file from its modification time.

    :param path:
        Cached file.

    :return:
        Age, or ``None`` if the file does not exist.
    """
    if not path.exists():
        return None
    return native_datetime_utc_now() - native_datetime_utc_fromtimestamp(path.stat().st_mtime)


def _get_three_months_drawdown(period_results: list[dict] | None) -> float:
    """Read the three-month maximum drawdown from a vault's period results.

    The export has no flat column for it. The benchmark selection uses it to
    compare vaults with a deep drawdown with BTC and ETH, see
    :py:func:`eth_defi.vault_report.benchmarks.select_benchmarks`.

    :param period_results:
        ``period_results`` list of a top vaults JSON record.

    :return:
        Drawdown as a negative fraction, or ``NaN`` if missing.
    """
    value = find_period(period_results, "3M").get("max_drawdown")
    return float(value) if value is not None else float("nan")


def prepare_vault_metrics(vaults: list[dict]) -> pd.DataFrame:
    """Turn top vaults JSON records into a report DataFrame.

    The single place where export fields are normalised, so every selector
    in :py:mod:`eth_defi.vault_report.sections` can rely on the same columns
    and types. Classification runs once here, not in each selector.

    Adds helper columns on top of the exported fields
    (see :py:class:`eth_defi.research.vault_metrics.VaultMetricsRecord`):

    - ``one_month_cagr_best``: net annualised one-month return when fee data
      is known, otherwise gross
    - ``three_months_cagr_best``: the same for the annualised three-month return
    - ``three_months_sharpe_best``: net Sharpe, falling back to gross
    - ``is_perp_dex``: perpetual DEX native trading vault
    - ``three_months_max_drawdown``: maximum drawdown of the three-month period
      as a negative fraction, from ``period_results``
    - ``group``: AMM pool, perpetual futures DEX, tokenised fund, RWA,
      lending or other, see :py:func:`eth_defi.vault_report.sections.classify_vault`
    - ``protocol_identified``: ``False`` for generic ERC-4626, unknown and
      placeholder protocols, see :py:func:`eth_defi.vault_report.sections.is_identified_protocol`
    - ``protocol_label``: the protocol name, or
      :py:data:`~eth_defi.vault_report.sections.OTHER_PROTOCOL` for unidentified
      protocols, used to group the TVL by protocol chart
    - ``end_date``, ``start_date``: parsed as naive UTC timestamps
    - ``trading_strategy_link``: legacy vault page URLs rewritten, see
      :py:func:`~eth_defi.vault_report.sections.canonical_vault_urls`, and a
      missing link built from ``vault_slug``, so every table links the vault name to its page

    TVL values above :py:data:`~eth_defi.research.vault_metrics.MAX_VALID_NAV`
    come from broken share tokens and are set to ``NaN``, which
    :py:func:`eth_defi.vault_report.sections.filter_eligible_vaults` then drops.

    Returns, volatilities and drawdowns are fractions (0.05 = 5%), TVL
    (``current_nav``, ``peak_nav``) is in USD.

    :param vaults:
        ``vaults`` list of the top vaults JSON export.

    :return:
        DataFrame indexed by ``{chain_id}-{address}`` vault id, the export's ``id``.
    """
    df = pd.DataFrame(vaults)
    assert len(df) > 0, "Top vaults JSON contained no vaults"
    df.index = df["id"].to_numpy()

    for column in ("start_date", "end_date"):
        # Naive UTC, also if the export ever adds a time zone suffix
        df[column] = pd.to_datetime(df[column], format="ISO8601", utc=True).dt.tz_localize(None)
    if "trading_strategy_link" not in df.columns:
        df["trading_strategy_link"] = None
    df["trading_strategy_link"] = df["trading_strategy_link"].apply(lambda url: canonical_vault_urls(url) if isinstance(url, str) else url)
    # Every table links the vault name to its page: build a missing link from the vault slug, like the exporter.
    # Anything not https:// counts as missing, because web_link() would render it as plain text.
    missing = ~df["trading_strategy_link"].apply(lambda url: isinstance(url, str) and url.startswith("https://"))
    if missing.any() and "vault_slug" in df.columns:
        df.loc[missing, "trading_strategy_link"] = df.loc[missing, "vault_slug"].apply(lambda slug: _get_trading_strategy_vault_link(slug) if isinstance(slug, str) and slug else None)
        logger.warning("%d vaults had no page link in the export; built from their vault slug", int(missing.sum()))

    df["one_month_cagr_best"] = _pick_net(df, "one_month_cagr")
    df["three_months_cagr_best"] = _pick_net(df, "three_months_cagr")
    df["three_months_sharpe_best"] = _pick_net(df, "three_months_sharpe")
    df["is_perp_dex"] = df["flags"].apply(lambda flags: isinstance(flags, list) and PERP_DEX_TRADING_VAULT_FLAG in flags)
    df["three_months_max_drawdown"] = df["period_results"].apply(_get_three_months_drawdown) if "period_results" in df.columns else float("nan")

    # Broken share tokens report absurd TVL; one such vault would dominate every TVL total and ranking
    for column in ("current_nav", "peak_nav"):
        df[column] = df[column].where(df[column] <= MAX_VALID_NAV)
    df["group"] = df.apply(classify_vault, axis=1)
    # Generic ERC-4626, unknown and placeholder protocols form one "Other" pile until they are mapped,
    # so the TVL by protocol chart never shows them as a protocol of their own
    df["protocol_identified"] = [is_identified_protocol(protocol, slug) for protocol, slug in zip(df["protocol"], df["protocol_slug"], strict=True)]
    df["protocol_label"] = df["protocol"].where(df["protocol_identified"], OTHER_PROTOCOL)
    return df


def fetch_vault_report_data(
    cache_dir: Path,
    top_vaults_json_path: Path | None = None,
    prices_path: Path | None = None,
    api_key: str | None = None,
    max_age: datetime.timedelta = DEFAULT_CACHE_MAX_AGE,
) -> VaultReportData:
    """Download, or read from local files, all input data for the report.

    The entry point of the data layer, called once per report run. Local
    paths are for the production scanner host, where the pipeline has just
    written both files, and for tests. The price Parquet is only downloaded
    here, not read: the charts read slices of it later.

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


def fetch_r2_report_file(
    client: Any,
    bucket_name: str,
    object_key: str,
    path: Path,
    max_age: datetime.timedelta = DEFAULT_R2_CACHE_MAX_AGE,
) -> Path:
    """Download a private production object, reusing a cache younger than one day.

    Uses the `R2 S3 API <https://developers.cloudflare.com/r2/api/s3/>`__
    directly, bypassing the website's download caches. Streams to a temporary
    file with progress logging and replaces the cache only after a complete
    download. A failed refresh aborts the report, preserving the old file
    without using it as a fallback.

    :param client:
        Authenticated S3-compatible R2 client.
    :param bucket_name:
        Private production data bucket.
    :param object_key:
        Exact production object key.
    :param path:
        Local cache path, isolated by bucket, endpoint and object prefix.
    :param max_age:
        Maximum time since the local download, rather than the remote modification time.
    :return:
        Path to the complete, fresh cached file.
    """
    age = get_cache_age(path)
    if age is not None and datetime.timedelta() <= age < max_age:
        logger.info("Using cached production %s, download age %s", path, age)
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    logger.info("Downloading production s3://%s/%s to %s", bucket_name, object_key, path)
    response = client.get_object(Bucket=bucket_name, Key=object_key)
    body = response["Body"]
    try:
        with open(tmp_path, "wb") as output, tqdm(total=response["ContentLength"], unit="B", unit_scale=True, desc=f"Downloading {path.name}") as progress:
            for chunk in body.iter_chunks(chunk_size=1 << 20):
                output.write(chunk)
                progress.update(len(chunk))
        if tmp_path.stat().st_size != response["ContentLength"]:
            raise RuntimeError(f"Incomplete production download: {object_key}")
        tmp_path.replace(path)
    finally:
        body.close()
        tmp_path.unlink(missing_ok=True)
    logger.info("Downloaded production %s, %d bytes, last modified %s", object_key, path.stat().st_size, response.get("LastModified"))
    return path


def fetch_vault_report_data_from_r2(
    cache_dir: Path,
    max_age: datetime.timedelta = DEFAULT_R2_CACHE_MAX_AGE,
) -> VaultReportData:
    """Refresh both report inputs from the private production R2 bucket.

    Uses the scanner's private exporter configuration and credential fallback
    order, see :py:mod:`eth_defi.vault.data_file_export`. Both files must be
    available before generation starts. The cache is separate from public/Pro
    API downloads and local scanner files, and changing the source selects a
    different cache. Missing configuration or a failed download is a hard error.

    :param cache_dir:
        Report download cache; never the scanner's persistent state directory.
    :param max_age:
        Reuse production downloads younger than this, default one day.
    :return:
        Metrics and price history from the production bucket.
    :raises RuntimeError:
        Private R2 configuration is incomplete.
    """
    configuration = {
        "R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME": os.environ.get("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME"),
        "R2_DATA_ENDPOINT_URL or R2_VAULT_METADATA_ENDPOINT_URL": os.environ.get("R2_DATA_ENDPOINT_URL") or os.environ.get("R2_VAULT_METADATA_ENDPOINT_URL"),
        "R2_DATA_ACCESS_KEY_ID or R2_VAULT_METADATA_ACCESS_KEY_ID": os.environ.get("R2_DATA_ACCESS_KEY_ID") or os.environ.get("R2_VAULT_METADATA_ACCESS_KEY_ID"),
        "R2_DATA_SECRET_ACCESS_KEY or R2_VAULT_METADATA_SECRET_ACCESS_KEY": os.environ.get("R2_DATA_SECRET_ACCESS_KEY") or os.environ.get("R2_VAULT_METADATA_SECRET_ACCESS_KEY"),
    }
    missing = [name for name, value in configuration.items() if not value]
    if missing:
        raise RuntimeError(f"Production vault report R2 download is not configured: {', '.join(missing)}")
    bucket_name, endpoint_url, access_key_id, secret_access_key = configuration.values()
    client = create_r2_client(endpoint_url=endpoint_url, access_key_id=access_key_id, secret_access_key=secret_access_key)
    prefix = os.environ.get("UPLOAD_PREFIX", "")
    # Old HTTP downloads and different buckets/prefixes must never satisfy this freshness check.
    source_id = hashlib.sha256(json.dumps([endpoint_url, bucket_name, prefix]).encode()).hexdigest()[:16]
    production_cache = cache_dir / "r2" / source_id
    paths = [fetch_r2_report_file(client, bucket_name, f"{prefix}{name}", production_cache / name, max_age=max_age) for name in ("top_vaults_by_chain.json", "cleaned-vault-prices-1h.parquet")]
    return fetch_vault_report_data(cache_dir, top_vaults_json_path=paths[0], prices_path=paths[1])


def read_vault_share_prices(
    prices_path: Path,
    vault_ids: list[str],
    start_at: datetime.datetime | None = None,
) -> pd.DataFrame:
    """Read share price history for selected vaults.

    Feeds the performance charts, the Sharpe ratio chart and the hero image
    sparklines, which together show a few dozen vaults. Only reads the
    columns and rows needed, using a PyArrow filter expression pushed down
    to the Parquet reader, so this is fast and low-memory even though the
    full price file has millions of rows.

    :param prices_path:
        Cleaned vault price Parquet with ``id``, ``timestamp`` and ``share_price`` columns.

    :param vault_ids:
        ``{chain_id}-{address}`` vault ids to read.

    :param start_at:
        Skip rows before this timestamp.

    :return:
        Long-format DataFrame with ``id`` (str), ``timestamp`` (naive UTC)
        and ``share_price`` (float, in the vault's denomination token)
        columns, roughly hourly rows, in file order.
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

    The hourly long-format prices are pivoted to one column per vault, then
    resampled to the last price of each calendar day. Daily resolution is
    enough for 90-day charts and aligns every vault and benchmark on the
    same dates.

    Days without an observation are interpolated in time between the vault's
    first and last data point only (``limit_area="inside"``), so sparsely
    updated vaults draw a straight accrual line instead of a staircase, and
    a vault does not appear to exist before it launched or after it stopped
    reporting.

    The forward fill variant gets the same "inside only" bound from
    ``.where(daily.bfill().notna())``: a value is kept only where some later
    observation exists, which masks the days after the last observation.

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
#: website historical TVL charts (``src/lib/echarts/tvl-outliers.ts`` in the frontend),
#: so the report's TVL totals match the website. Lower than
#: :py:data:`~eth_defi.research.vault_metrics.MAX_VALID_NAV`, which only
#: guards the current TVL.
TVL_OUTLIER_THRESHOLD: USDollarAmount = 50_000_000_000

#: Extra history read before the chart start, so vaults with sparse updates
#: already have a value in the first week, which the forward fill in
#: :py:func:`read_vault_tvl_history` carries into the chart window. Without
#: it, the first weeks would undercount TVL and the chart would show a false
#: ramp-up.
TVL_LOOKBACK_BUFFER = datetime.timedelta(days=35)


def read_vault_tvl_history(
    prices_path: Path,
    vault_ids: list[str],
    start_at: datetime.datetime,
    end_at: datetime.datetime,
) -> pd.DataFrame:
    """Read weekly TVL history for vaults.

    Feeds the stacked *Stablecoin TVL by DeFi vault protocol*, *by
    blockchain* and *NAV by tokenised fund* charts, via
    :py:func:`eth_defi.vault_report.sections.calculate_protocol_tvl_history`
    and friends.

    Mirrors the website's historical TVL query
    (``src/lib/echarts/historical-tvl-server.ts`` in the frontend), so the
    report's totals match the website's TVL charts:

    1. ``daily``: the last ``total_assets`` value of each vault on each day,
       ``arg_max`` by timestamp, ignoring negative values and values above
       :py:data:`TVL_OUTLIER_THRESHOLD`.
    2. The daily values averaged over each ISO week (Monday start,
       ``date_trunc('week')``).
    3. The week containing ``end_at`` is left out, because its average
       would cover only part of the week.

    Each vault is then forward filled between its first and last week only,
    so sparse updaters do not leave holes in the stack, while vaults that
    have not launched or have stopped reporting add nothing.
    ``total_assets`` in the cleaned price Parquet is already in USD, also
    for EUR-denominated vaults.

    The aggregation runs in an in-memory DuckDB connection, so only one row
    per vault and week is loaded into pandas, instead of millions of hourly rows.

    :param prices_path:
        Cleaned vault price Parquet with ``id``, ``timestamp`` and ``total_assets`` columns.

    :param vault_ids:
        Vault ids to read.

    :param start_at:
        First week to include, naive UTC. Data is read from
        :py:data:`TVL_LOOKBACK_BUFFER` earlier to seed the forward fill.

    :param end_at:
        Report data date, naive UTC; its week is incomplete and left out, like on the website.

    :return:
        DataFrame indexed by week start (Monday, naive) with one TVL column
        per vault id, in USD, ``NaN`` outside a vault's lifetime. Empty when
        no vault has data.
    """
    query = """
        WITH daily AS (
            SELECT id, CAST("timestamp" AS DATE) AS day, arg_max(total_assets, "timestamp") AS tvl
            FROM read_parquet(?)
            WHERE "timestamp" >= ? AND total_assets >= 0 AND total_assets <= ? AND id IN (SELECT unnest(?))
            GROUP BY id, day
        )
        SELECT id, date_trunc('week', day) AS week, avg(tvl) AS tvl
        FROM daily
        WHERE date_trunc('week', day) < date_trunc('week', CAST(? AS TIMESTAMP))
        GROUP BY id, week
    """
    read_from = pd.Timestamp(start_at - TVL_LOOKBACK_BUFFER)
    with duckdb.connect() as connection:
        weekly = connection.execute(query, [str(prices_path), read_from, TVL_OUTLIER_THRESHOLD, vault_ids, pd.Timestamp(end_at)]).df()
    logger.info("Read %d weekly TVL rows for %d vaults from %s", len(weekly), weekly["id"].nunique(), prices_path)
    if len(weekly) == 0:
        return pd.DataFrame()
    periodic = weekly.pivot(index="week", columns="id", values="tvl").sort_index()
    # Forward fill only up to each vault's last week: bfill().notna() is False after it
    periodic = periodic.ffill().where(periodic.bfill().notna())
    # Drop the lookback buffer; to_period("W") gives the Monday week start like DuckDB's date_trunc('week')
    return periodic.loc[periodic.index >= pd.Timestamp(start_at).to_period("W").start_time]


def fetch_available_sparklines(vault_ids: list[str], max_workers: int = 16, timeout: float = 20.0) -> set[str]:
    """Check which vaults have a published 90-day sparkline image.

    The tables' "3M history" column embeds the native 4:1 table sparkline
    PNG, :py:data:`~eth_defi.vault_report.sections.SPARKLINE_URL`, rather
    than rendering images of its own, so the post looks like the website and
    uploads nothing. Low-TVL vaults are rendered on a slower cadence and may
    not have a sparkline; a missing image would show as a broken icon in the
    post, so only vaults whose image answers ``HEAD`` with HTTP 200 get one.

    A failed check counts as "no sparkline" and logs a warning: a missing
    thumbnail must not stop the report.

    :param vault_ids:
        Vault ids to check.

    :param max_workers:
        Parallel HTTP HEAD requests, run in threads with :py:class:`joblib.Parallel`.

    :param timeout:
        HTTP timeout in seconds.

    :return:
        Vault ids with a sparkline.
    """
    # One connection pool for all checks instead of a TLS handshake per vault
    session = requests.Session()
    session.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=max_workers))

    def _exists(vault_id: str) -> bool:
        try:
            return session.head(SPARKLINE_URL.format(vault_id=vault_id), timeout=timeout).status_code == 200
        except requests.RequestException as e:
            logger.warning("Could not check sparkline for %s: %s", vault_id, e)
            return False

    unique_ids = sorted(set(vault_ids))
    results = Parallel(n_jobs=max_workers, backend="threading")(delayed(_exists)(vault_id) for vault_id in tqdm(unique_ids, desc="Checking sparklines"))
    available = {vault_id for vault_id, exists in zip(unique_ids, results, strict=True) if exists}
    logger.info("Sparklines available for %d of %d vaults", len(available), len(unique_ids))
    return available
