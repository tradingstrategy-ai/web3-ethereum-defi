"""Cleaning step for currency_api exchange rate data.

Ingestion (:py:mod:`eth_defi.currency_api.scanner`) stores raw source values
verbatim and never fabricates or "corrects" data. This module is the separate,
explicit cleaning pass: it drops a hardcoded allowlist of known-bad datapoints —
upstream source glitches found by manual data review — *without* mutating the
stored raw data. Apply it on read/export when you want a sanitised view.

Keeping cleaning separate means the DuckDB file remains a faithful mirror of the
source (auditable, re-cleanable), while consumers that want trustworthy series
call :py:func:`filter_known_bad_rates`.
"""

import datetime
import logging

import pandas as pd

logger = logging.getLogger(__name__)

#: Hardcoded allowlist of known-bad ``(date, base_currency, quote_currency, source)``
#: cells from the fawazahmed0 Exchange API, identified by manual data review.
#:
#: Each entry documents the observed bad value and why it is dropped. Keep this
#: list small and evidence-based — only add a cell after confirming the bad value
#: is present at the source (i.e. the raw API returns it), not a local bug.
KNOWN_BAD_RATES: tuple[tuple[datetime.date, str, str, str], ...] = (
    # 2025-12-06 — the source returned garbage crypto rates for this single day:
    #   usd.btc = 38.87461377   (should be ~1.5e-5; implies 1 USD ≈ 38.9 BTC)
    #   usd.eth = 49644.07797781 (should be ~5e-4; implies 1 USD ≈ 49,644 ETH)
    # i.e. ~6-7 orders of magnitude wrong. Confirmed at the source — fetching the
    # raw `usd.min.json` for this date returns the same values — so this is an
    # upstream glitch, not a local parsing/scaling bug. Fiat rates (eur/gbp/jpy/aud)
    # for the same day were normal, and the neighbouring days (12-05, 12-07) are
    # normal, so the bad point is isolated to btc/eth on 2025-12-06.
    # A full-history audit (the entire available range 2024-03-02 .. 2026-06-29,
    # rolling-median ratio test) found this to be the ONLY anomaly across all
    # currencies. Found during the data-quality review on PR #1158.
    (datetime.date(2025, 12, 6), "usd", "btc", "fawazahmed0"),
    (datetime.date(2025, 12, 6), "usd", "eth", "fawazahmed0"),
)


def filter_known_bad_rates(df: pd.DataFrame) -> pd.DataFrame:
    """Drop known-bad exchange rate rows from a rates DataFrame.

    Removes the rows whose ``(date, base_currency, quote_currency, source)`` match
    an entry in :py:data:`KNOWN_BAD_RATES`. The input is not mutated; a filtered
    copy is returned. This is a separate cleaning step — the stored DuckDB data is
    left untouched (raw).

    :param df:
        Rates DataFrame as returned by
        :py:meth:`eth_defi.currency_api.database.CurrencyRateDatabase.get_rates_dataframe`,
        i.e. with columns ``date`` (``datetime.date`` or pandas ``Timestamp``),
        ``base_currency``, ``quote_currency``, ``rate``, ``source`` and
        ``written_at``.
    :return:
        A new DataFrame with the known-bad rows removed and the index reset. If
        the input is empty it is returned unchanged (a copy).
    """
    if df.empty:
        return df.copy()

    bad = set(KNOWN_BAD_RATES)

    # Normalise the date column (DuckDB `.df()` yields pandas Timestamp; direct
    # construction may yield datetime.date) so the tuple keys compare correctly.
    dates = pd.to_datetime(df["date"]).dt.date
    keys = zip(dates, df["base_currency"], df["quote_currency"], df["source"])
    mask = [key not in bad for key in keys]

    removed = len(df) - sum(mask)
    if removed:
        logger.info("filter_known_bad_rates: removed %d known-bad row(s)", removed)

    return df.loc[mask].reset_index(drop=True)
