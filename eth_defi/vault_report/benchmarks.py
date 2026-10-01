"""Performance benchmarks for the monthly vault report: US Treasury bills, BTC and ETH.

Like the vault pages on the website, each vault is compared with the
benchmark that matches its activity:

- Calm yield vaults (lending, credit) are compared with the 3-month US
  Treasury bill, the risk-free alternative for stablecoin holders.
- Perpetual futures and GMX pools, and other volatile vaults, are compared with
  BTC and ETH, the market their trading returns depend on.

The rules follow ``src/lib/top-vaults/vault-price-benchmarks.ts`` and
``isPerpetualFuturesVault.ts`` in the frontend, extended with a volatility and
drawdown rule for volatile stablecoin vaults on EVM chains, which the website
compares with the Treasury bill.

Data sources:

- `FRED DGS3MO <https://fred.stlouisfed.org/series/DGS3MO>`__: the daily
  3-month Treasury constant maturity yield on an investment basis, the same
  series as the website. No API key.
- `Coinbase Exchange candles <https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductcandles>`__:
  daily BTC-USD and ETH-USD closes, the same source as the website. No API key.

Benchmarks are optional: an outage leaves them out of the charts. Each
download falls back to an older cached copy before giving up, so a FRED or
Coinbase outage on report day rarely matters.

Use in the post: :py:func:`fetch_benchmark_indices` gives the grey benchmark
lines of the performance charts, and :py:func:`get_latest_yield` the T-bill
reference line of the average yield and risk and return charts.
:py:func:`select_benchmarks` picks per vault, and a performance chart draws
the benchmarks used by at least half of its vaults.

Units: yields are fractions (0.04 = 4%), dates are naive UTC.
"""

import datetime
import io
import logging
from pathlib import Path

import pandas as pd
import requests

from eth_defi.types import Percent
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault_report.data import fetch_file, get_cache_age

logger = logging.getLogger(__name__)

#: FRED series id of the 3-month Treasury constant maturity yield
TREASURY_SERIES_ID = "DGS3MO"

#: FRED CSV export of :py:data:`TREASURY_SERIES_ID`, the full daily history in percent,
#: with ``.`` for days without a quote
TREASURY_CSV_URL = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={TREASURY_SERIES_ID}"

#: Coinbase Exchange public candles endpoint
COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/{product}/candles"

#: Coinbase returns at most this many candles per request, so longer ranges are split into chunks
COINBASE_MAX_CANDLES = 300

#: Benchmark names, used as series keys, chart labels and benchmark logo keys
TREASURY_BILL = "US 3M T-bill"
BTC = "BTC"
ETH = "ETH"

#: Coinbase product of each crypto benchmark
CRYPTO_PRODUCTS = {BTC: "BTC-USD", ETH: "ETH-USD"}

#: Synthetic chain ids of perpetual futures DEXes, from ``isPerpetualFuturesVault.ts``.
#: Native perp DEX vaults are exported under these made-up chain ids, e.g. 9999 for
#: Hypercore. HyperEVM (999) is a regular EVM chain and is not included.
PERP_CHAIN_IDS = frozenset({9999, 325, 9998, 9997, 9995})

#: Stablecoin symbols of GMX GM pools, lower case, from ``vault-price-benchmarks.ts``.
#: Keep in sync with the frontend, so the report and the website pick the same benchmark for a GM pool.
GMX_STABLECOIN_SYMBOLS = frozenset({"usdc", "usdc.e", "usdt", "usdt.e", "usdt0", "usde", "susde", "dai", "usdg", "usds", "usdf", "usd1", "gho", "frax", "pyusd", "fdusd", "usdb", "usdx", "usda", "crvusd", "usdd", "lusd", "dola", "usdp", "usd0", "usdn", "usdr", "usdy"})


def fetch_treasury_bill_yields(cache_dir: Path, max_age: datetime.timedelta = datetime.timedelta(days=1)) -> pd.Series | None:
    """Fetch daily 3-month Treasury bill yields.

    The risk-free alternative for a stablecoin holder: the yield charts draw
    it as a reference line, and the performance charts accrue it into a value
    index, see :py:func:`calculate_treasury_bill_index`. The
    `FRED DGS3MO series <https://fred.stlouisfed.org/series/DGS3MO>`__ is the
    same one the website uses and needs no API key.

    Downloads the FRED :py:data:`TREASURY_SERIES_ID` CSV at most once per
    ``max_age``; the series updates once per US business day, so a day old
    copy is current enough. On a download failure the older cached copy is
    used, and a parse failure returns ``None``, which leaves the T-bill out
    of the charts instead of stopping the report.

    :param cache_dir:
        Download cache directory.

    :param max_age:
        Re-download the CSV when the cached copy is older than this.

    :return:
        Daily yields as fractions (0.04 = 4%), indexed by naive UTC date, or
        ``None`` when no data is available.
    """
    path = cache_dir / f"fred-{TREASURY_SERIES_ID.lower()}.csv"
    try:
        fetch_file(TREASURY_CSV_URL, path, max_age=max_age, timeout=60)
    except RuntimeError as e:
        logger.warning("Could not download US Treasury bill rates, using cached data if available: %s", e)

    if not path.exists():
        return None

    try:
        # The date column is read by position, because its header has changed between FRED export versions
        df = pd.read_csv(io.StringIO(path.read_text()), na_values=["."])
        rates = df.set_index(pd.to_datetime(df.iloc[:, 0]))[TREASURY_SERIES_ID].dropna() / 100
    except (ValueError, KeyError, IndexError) as e:
        # A missing series column means FRED changed the export format or series
        logger.warning("Could not parse US Treasury bill rates in %s: %s", path, e)
        return None

    if rates.empty:
        return None
    return rates


def calculate_treasury_bill_index(yields: pd.Series, end_at: datetime.datetime) -> pd.Series:
    """Turn daily Treasury bill yields into a value index.

    Lets the performance charts draw the T-bill as an equity curve next to
    vault share prices, measured from the chart start like the vaults.

    Accrues each calendar day at the day's yield (``1 + y / 365``), forward
    filling weekends and holidays, which matches how vault share prices accrue
    every day.

    :param yields:
        Output of :py:func:`fetch_treasury_bill_yields`.

    :param end_at:
        Last day of the index.

    :return:
        Daily index indexed by naive UTC date, from the first FRED date to
        ``end_at``, starting near 1.0 and compounding upwards.
    """
    days = pd.date_range(yields.index.min(), pd.Timestamp(end_at).normalize(), freq="D")
    daily = yields.resample("D").last().reindex(days).ffill()
    return (1 + daily / 365).cumprod()


def get_latest_yield(yields: pd.Series) -> Percent:
    """Get the most recent Treasury bill yield.

    The reference line of the average yield charts and the risk and return
    chart, and the "difference to the T-bill" column of the yield charts.

    :param yields:
        Output of :py:func:`fetch_treasury_bill_yields`.

    :return:
        Latest yield as a fraction.
    """
    return float(yields.iloc[-1])


def fetch_crypto_prices(
    benchmark: str,
    start_at: datetime.datetime,
    end_at: datetime.datetime,
    cache_dir: Path,
    max_age: datetime.timedelta = datetime.timedelta(hours=6),
    timeout: float = 30.0,
) -> pd.Series | None:
    """Fetch daily closing prices of BTC or ETH from Coinbase.

    The crypto benchmarks of perp DEX, GMX and volatile vaults, see
    :py:func:`select_benchmarks`, from the public
    `Coinbase Exchange candles API <https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductcandles>`__,
    the same source as the website's vault pages. No API key.

    Requests are split into chunks of :py:data:`COINBASE_MAX_CANDLES` days, and
    the result is cached as CSV. The cache file name contains the date range,
    so a different report window never reuses a mismatched series. On a
    failure, an older cached copy of the same range is used if one exists,
    otherwise ``None`` is returned and the benchmark is left out of the charts.

    :param benchmark:
        :py:data:`BTC` or :py:data:`ETH`.

    :param start_at:
        First day.

    :param end_at:
        Last day.

    :param cache_dir:
        Download cache directory.

    :param max_age:
        Re-download when the cached copy is older than this.

    :param timeout:
        HTTP timeout in seconds.

    :return:
        Daily USD closing prices indexed by naive UTC date (the candle's UTC
        day start), sorted, or ``None``.
    """
    product = CRYPTO_PRODUCTS[benchmark]
    path = cache_dir / f"coinbase-{product.lower()}-{start_at:%Y%m%d}-{end_at:%Y%m%d}.csv"

    def _read_cache() -> pd.Series:
        return pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]

    age = get_cache_age(path)
    if age is not None and age < max_age:
        return _read_cache()

    candles = []
    chunk_start = pd.Timestamp(start_at).normalize()
    last = pd.Timestamp(end_at).normalize()
    try:
        # Each chunk spans COINBASE_MAX_CANDLES days and the next starts the day after, so no request exceeds the limit
        while chunk_start <= last:
            chunk_end = min(chunk_start + pd.Timedelta(days=COINBASE_MAX_CANDLES - 1), last)
            resp = requests.get(
                COINBASE_CANDLES_URL.format(product=product),
                params={"granularity": 86400, "start": chunk_start.isoformat(), "end": chunk_end.isoformat()},
                timeout=timeout,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            candles.extend(resp.json())
            chunk_start = chunk_end + pd.Timedelta(days=1)
    except (requests.RequestException, RuntimeError, ValueError) as e:
        logger.warning("Could not download %s prices from Coinbase: %s", product, e)
        return _read_cache() if path.exists() else None

    if not candles:
        return None
    # Candle format: [time, low, high, open, close, volume], time in Unix seconds, newest first.
    # Keyed by timestamp, so a candle returned by two chunks counts once.
    closes = pd.Series({pd.Timestamp(candle[0], unit="s"): float(candle[4]) for candle in candles}, name=benchmark).sort_index()
    path.parent.mkdir(parents=True, exist_ok=True)
    closes.to_csv(path)
    return closes


def _is_gmx_stable_stable_pool(name: str, vault_slug: str) -> bool:
    """Whether a GMX GM swap pool trades two stablecoins.

    A stablecoin-only swap pool has no crypto price exposure, so it is a
    yield product and compared with the T-bill, like on the website. The
    pool tokens are parsed from the vault name, as the export has no field
    for them; ``₮`` in names like ``USD₮0`` is normalised to ``t``.

    :param name:
        Vault name, e.g. ``GM swap [USDC-USDT]``.

    :param vault_slug:
        Vault slug.

    :return:
        ``True`` for stablecoin-only swap pools.
    """
    if not vault_slug.startswith("gm-swap-") or not (name.startswith("GM swap [") and name.endswith("]")):
        return False
    pool = name[len("GM swap [") : -1]

    def _stable(token: str) -> bool:
        return token.lower().replace("₮", "t") in GMX_STABLECOIN_SYMBOLS

    # Token symbols may contain hyphens, so every hyphen is tried as the separator
    return any(char == "-" and _stable(pool[:i]) and _stable(pool[i + 1 :]) for i, char in enumerate(pool))


def select_benchmarks(vault: pd.Series, min_volatility: Percent, max_drawdown: Percent) -> tuple[str, ...]:
    """Choose the benchmarks a vault is compared with.

    A benchmark should be the alternative a reader would otherwise hold: the
    T-bill for a stablecoin yield product, BTC and ETH for a vault whose
    returns depend on the crypto market. Called per vault in a performance
    chart; the chart then draws the benchmarks used by at least half of its
    vaults, see :py:func:`eth_defi.vault_report.charts.create_performance_figure`.

    Follows the website's rules (``src/lib/top-vaults/vault-price-benchmarks.ts``
    and ``isPerpetualFuturesVault.ts`` in the frontend), checked in order:

    1. perpetual futures vaults, by flag or synthetic perp DEX chain id: BTC and ETH
    2. GMX: GLV pools BTC and ETH; stablecoin-only swap pools the Treasury
       bill; GM BTC and ETH pools only their own asset; other GM pools BTC and ETH

    For other vaults, the website always uses the T-bill. The report adds an
    activity rule: a vault with three-month volatility at or above
    ``min_volatility``, or a three-month drawdown at or below
    ``max_drawdown``, behaves like a trading strategy and is compared with
    BTC and ETH. Calm yield vaults are compared with the Treasury bill.
    Missing volatility or drawdown does not trigger the rule.

    :param vault:
        Vault metrics row with ``flags``, ``chain_id``, ``protocol_slug``,
        ``vault_slug``, ``name``, ``three_months_volatility`` and
        ``three_months_max_drawdown``.

    :param min_volatility:
        Annualised volatility from which a vault counts as volatile.

    :param max_drawdown:
        Drawdown, a negative fraction, from which a vault counts as volatile.

    :return:
        Benchmark names, e.g. ``("BTC", "ETH")``.
    """
    flags = vault["flags"] if isinstance(vault["flags"], list) else []
    slug = vault["vault_slug"] if isinstance(vault["vault_slug"], str) else ""
    if VaultFlag.perp_dex_trading_vault.value in flags or vault["chain_id"] in PERP_CHAIN_IDS:
        return (BTC, ETH)
    if vault["protocol_slug"] == "gmx":
        if slug.startswith("glv-"):
            return (BTC, ETH)
        if _is_gmx_stable_stable_pool(vault["name"] if isinstance(vault["name"], str) else "", slug):
            return (TREASURY_BILL,)
        if slug.startswith("gm-btc-"):
            return (BTC,)
        if slug.startswith("gm-eth-"):
            return (ETH,)
        return (BTC, ETH)

    volatility = vault["three_months_volatility"]
    drawdown = vault["three_months_max_drawdown"]
    if (pd.notna(volatility) and volatility >= min_volatility) or (pd.notna(drawdown) and drawdown <= max_drawdown):
        return (BTC, ETH)
    return (TREASURY_BILL,)


def fetch_benchmark_indices(start_at: datetime.datetime, end_at: datetime.datetime, cache_dir: Path, treasury_yields: pd.Series | None) -> dict[str, pd.Series]:
    """Collect all benchmark value series for the performance charts.

    Fetches every benchmark once per report, whichever vaults end up using
    it, so the charts can be rendered without further network access.

    :param start_at:
        First day needed.

    :param end_at:
        Last day needed.

    :param cache_dir:
        Download cache directory.

    :param treasury_yields:
        Output of :py:func:`fetch_treasury_bill_yields`, or ``None``.

    :return:
        Benchmark name -> daily value series indexed by naive UTC date: the
        T-bill as a value index, BTC and ETH as USD prices. The charts measure
        each series as a change from its first value in the chart window, so
        the units do not matter. Benchmarks without data are left out.
    """
    indices = {}
    if treasury_yields is not None:
        indices[TREASURY_BILL] = calculate_treasury_bill_index(treasury_yields, end_at)
    for benchmark in CRYPTO_PRODUCTS:
        prices = fetch_crypto_prices(benchmark, start_at, end_at, cache_dir)
        if prices is not None:
            indices[benchmark] = prices
    return indices
