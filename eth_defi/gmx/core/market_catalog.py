"""Liquidity-aware GMX V2 market catalog.

Single source of truth for resolving ``base_symbol`` + ``collateral_symbol``
to an on-chain GMX market.  Replaces the legacy first-match-by-pool-type sort
that could land a BTC/USDC order on the synthetic ``tBTC-tBTC`` pool and then
fail collateral validation (issue #67 follow-up, 2026-05-14).

The catalog builds in three layers (subsequent tasks fill in 2 and 3):

1. **Enumeration** — REST ``/markets`` primary, on-chain Reader fallback.
2. **Augmentation** — Subsquid (with graceful 404/timeout degradation) for
   ``liquidity_usd``, ``oi_long_usd``, ``oi_short_usd``.
3. **Persistence** — disk + memory cache with TTL, single-flight refresh.

Selection (:meth:`MarketCatalog.pick_market`) ranks survivors by
``liquidity_usd`` descending and falls back when the requested collateral
isn't supported by any pool for the base symbol.

This task (1.1) covers the :class:`MarketEntry` row type only.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from eth_defi.gmx.config import GMXConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class MarketEntry:
    """A single GMX V2 market with liquidity-aware metadata.

    :param market_key: 0x-prefixed market token address (case preserved as
        provided — comparisons use :meth:`str.lower`).
    :param index_token_symbol: Normalised index token symbol (``SYMBOL_NORMALISE``
        already applied — callers see ``BONK`` not ``kBONK``).
    :param index_token_address: Index token contract address.
    :param long_token_symbol: Symbol of the long-side collateral token.
    :param long_token_address: Address of the long-side collateral token.
    :param short_token_symbol: Symbol of the short-side collateral token.
    :param short_token_address: Address of the short-side collateral token.
    :param liquidity_usd: Pool TVL in USD at :attr:`refreshed_at`.  ``0.0``
        when augmentation failed (acceptable degradation).
    :param oi_long_usd: Open interest on the long side in USD.  ``0.0`` when
        unknown.
    :param oi_short_usd: Open interest on the short side in USD.  ``0.0`` when
        unknown.
    :param refreshed_at: Unix seconds when this row was last refreshed.
    """

    #: 0x-prefixed market token address.
    market_key: str
    #: Normalised index token symbol (``BONK`` not ``kBONK``).
    index_token_symbol: str
    #: Index token contract address.
    index_token_address: str
    #: Long-side collateral symbol.
    long_token_symbol: str
    #: Long-side collateral address.
    long_token_address: str
    #: Short-side collateral symbol.
    short_token_symbol: str
    #: Short-side collateral address.
    short_token_address: str
    #: Pool TVL in USD at refresh time.  0.0 when augmentation failed.
    liquidity_usd: float
    #: Long-side OI in USD.  0.0 when unknown.
    oi_long_usd: float
    #: Short-side OI in USD.  0.0 when unknown.
    oi_short_usd: float
    #: Unix seconds when this entry was last refreshed.
    refreshed_at: int

    @property
    def is_synthetic(self) -> bool:
        """Whether this is a single-sided synthetic market.

        A synthetic GMX V2 market has ``long_token == short_token`` (for
        example, the ``tBTC-tBTC`` BTC tracker).  Such markets only accept
        their long/short token as collateral — USDC orders against them
        fail validation.

        :returns: ``True`` when ``long_token_address`` equals
            ``short_token_address`` (case-insensitive comparison).
        """
        return self.long_token_address.lower() == self.short_token_address.lower()

    def accepts_collateral(self, collateral_symbol: str) -> bool:
        """Whether this market accepts the given collateral symbol.

        Compared case-insensitively against :attr:`long_token_symbol` and
        :attr:`short_token_symbol`.  For synthetic markets the two are equal
        so only the single supported token returns ``True``.

        :param collateral_symbol: Candidate collateral symbol (case-insensitive).
        :returns: ``True`` when the symbol matches either side of the pool.
        """
        candidate = collateral_symbol.upper()
        return candidate in {
            self.long_token_symbol.upper(),
            self.short_token_symbol.upper(),
        }


def _load_raw_markets(config: "GMXConfig") -> dict[str, dict[str, Any]]:
    """Fetch the chain's listed GMX markets via the existing pipeline.

    Delegates to :meth:`eth_defi.gmx.core.markets.Markets.get_available_markets`
    which already implements REST ``/markets`` primary + on-chain Reader
    fallback + ``SYMBOL_NORMALISE`` on the market symbol.  Indirection through
    this private helper exists purely so unit tests can monkeypatch the loader
    without spinning up RPC infra.

    :param config: GMX configuration carrying chain + Web3 references.
    :returns: Dict keyed by checksummed market address, values as produced by
        :class:`Markets`.
    """
    from eth_defi.gmx.core.markets import Markets

    return Markets(config).get_available_markets()


def enumerate_markets(
    config: "GMXConfig",
    *,
    now_ts: int | None = None,
) -> list[MarketEntry]:
    """Enumerate every listed GMX V2 market on the configured chain.

    Wraps the existing ``Markets`` pipeline (REST primary, on-chain Reader
    fallback) and projects each entry into a :class:`MarketEntry`.  Liquidity
    and OI fields are not populated here — that is layered in by
    ``augment_with_liquidity`` (Task 1.3).  Each row carries ``refreshed_at``
    so the catalog cache can compute TTLs.

    :class:`SYMBOL_NORMALISE` is applied defensively to **every** token symbol
    (index, long, short) so downstream consumers never see a ``k`` prefix —
    e.g. ``kBONK → BONK``.  The upstream pipeline already normalises
    ``market_symbol``, but raw ``long_token_metadata["symbol"]`` and
    ``short_token_metadata["symbol"]`` are not normalised there, so the
    defensive pass closes that gap.

    Entries with missing or malformed metadata are skipped with a DEBUG log;
    they never abort the enumeration.

    :param config: GMX configuration for the target chain.
    :param now_ts: Override the ``refreshed_at`` timestamp.  Defaults to the
        current ``time.time()`` (Unix seconds).  Tests pass a fixed value for
        determinism.
    :returns: List of :class:`MarketEntry` — one per listed market, in
        insertion order of the underlying ``Markets`` dict.
    """
    from eth_defi.gmx.symbols import SYMBOL_NORMALISE

    raw_markets = _load_raw_markets(config)
    refreshed_at = now_ts if now_ts is not None else int(time.time())

    entries: list[MarketEntry] = []
    for market_address, data in raw_markets.items():
        try:
            index_meta = data["market_metadata"]
            long_meta = data["long_token_metadata"]
            short_meta = data["short_token_metadata"]

            raw_index_symbol = data.get("market_symbol") or index_meta["symbol"]
            raw_long_symbol = long_meta["symbol"]
            raw_short_symbol = short_meta["symbol"]
        except KeyError as exc:
            logger.debug(
                "Skipping market %s: missing required metadata key %s",
                market_address,
                exc,
            )
            continue

        if not raw_index_symbol:
            logger.debug("Skipping market %s: empty index symbol", market_address)
            continue

        entries.append(
            MarketEntry(
                market_key=market_address,
                index_token_symbol=SYMBOL_NORMALISE.get(raw_index_symbol, raw_index_symbol),
                index_token_address=data["index_token_address"],
                long_token_symbol=SYMBOL_NORMALISE.get(raw_long_symbol, raw_long_symbol),
                long_token_address=data["long_token_address"],
                short_token_symbol=SYMBOL_NORMALISE.get(raw_short_symbol, raw_short_symbol),
                short_token_address=data["short_token_address"],
                liquidity_usd=0.0,
                oi_long_usd=0.0,
                oi_short_usd=0.0,
                refreshed_at=refreshed_at,
            )
        )

    logger.debug("enumerate_markets: produced %d entries", len(entries))
    return entries


def _fetch_open_interest(config: "GMXConfig") -> dict[str, dict[str, float]] | None:
    """Fetch GMX open interest via on-chain Reader (no Subsquid).

    Wraps :class:`eth_defi.gmx.core.open_interest.GetOpenInterest`, which uses
    DataStore reads under the hood — independent of Subsquid availability.
    Indirection exists so tests can monkeypatch the fetch without standing up
    a Web3 connection.

    :param config: GMX configuration with a live ``web3`` reference.
    :returns: Output of ``GetOpenInterest.get_data()`` — a dict with ``long``
        and ``short`` sub-dicts keyed by ``market_symbol`` (``BTC``, ``BTC2``,
        ``BONK``...) plus a ``parameter`` tag.  ``None`` when the fetch fails
        for any reason — caller treats this as "no OI data available".
    """
    try:
        from eth_defi.gmx.core.open_interest import GetOpenInterest

        return GetOpenInterest(config).get_data()
    except Exception as exc:  # noqa: BLE001 — broad catch; we never want catalog
        # augmentation to abort the whole catalog build.
        logger.warning("OI fetch failed, market_catalog will use 0.0 OI: %s", exc)
        return None


def _fetch_available_liquidity(config: "GMXConfig") -> dict[str, dict[str, float]] | None:
    """Fetch GMX available liquidity via Multicall3 (no Subsquid).

    Wraps :class:`eth_defi.gmx.core.available_liquidity.GetAvailableLiquidity`,
    which queries pool amounts and reserve factors on-chain.  Same indirection
    + degradation contract as :func:`_fetch_open_interest`.

    :param config: GMX configuration with a live ``web3`` reference.
    :returns: Output of ``GetAvailableLiquidity.get_data()`` — ``{'long': {sym:
        usd}, 'short': {sym: usd}, 'parameter': 'available_liquidity'}``.
        ``None`` on failure.
    """
    try:
        from eth_defi.gmx.core.available_liquidity import GetAvailableLiquidity

        return GetAvailableLiquidity(config).get_data()
    except Exception as exc:  # noqa: BLE001 — broad catch as above.
        logger.warning(
            "Available liquidity fetch failed, market_catalog will use 0.0 liquidity: %s",
            exc,
        )
        return None


def _resolve_market_symbol(config: "GMXConfig", market_key: str) -> str | None:
    """Resolve a market_key → GMX ``market_symbol`` (e.g. ``BTC``, ``BTC2``).

    The catalog uses ``index_token_symbol`` for selection, but ``GetOpenInterest``
    and ``GetAvailableLiquidity`` both key by ``market_symbol`` — the GMX-internal
    name that distinguishes pools sharing an index (``BTC`` for WBTC-USDC,
    ``BTC2`` for tBTC-tBTC).  This helper provides the bridge.

    :param config: GMX configuration.
    :param market_key: Checksummed market token address.
    :returns: The ``market_symbol`` or ``None`` if the markets dict doesn't
        contain this key.
    """
    try:
        from eth_defi.gmx.core.markets import Markets

        symbol = Markets(config).get_market_symbol(market_key)
        return symbol if symbol else None
    except Exception as exc:  # noqa: BLE001 — same broad-catch rationale.
        logger.debug("market_symbol resolve failed for %s: %s", market_key, exc)
        return None


def augment_with_liquidity(
    entries: list[MarketEntry],
    config: "GMXConfig",
) -> list[MarketEntry]:
    """Populate ``liquidity_usd``, ``oi_long_usd``, ``oi_short_usd`` on each entry.

    Sourcing (Subsquid never touched on this path):

    1. :func:`_fetch_available_liquidity` — on-chain multicall via the existing
       ``GetAvailableLiquidity`` pipeline.
    2. :func:`_fetch_open_interest` — on-chain reads via ``GetOpenInterest``.
    3. :func:`_resolve_market_symbol` — local ``Markets`` cache, no network.

    ``liquidity_usd`` is the sum of the ``long`` and ``short`` available
    liquidity for the market — the total tradable depth the catalog uses to
    rank pools.  ``oi_long_usd`` / ``oi_short_usd`` are pass-throughs from
    open-interest data.

    **Degradation contract:** any source returning ``None`` (failure) is
    silently skipped for that source — the affected field stays at ``0.0``
    and a WARNING is logged once.  Partial data (e.g. one side missing) is
    used as available — the missing side stays at ``0.0``.  This function
    never raises; the catalog must always rebuild even with stale prices,
    flaky RPC, or temporary endpoint outages.

    :param entries: The bare entries from :func:`enumerate_markets`.
    :param config: GMX configuration.
    :returns: A **new** list of :class:`MarketEntry` — input is not mutated
        (entries are frozen dataclasses anyway).
    """
    liquidity = _fetch_available_liquidity(config)
    open_interest = _fetch_open_interest(config)

    if liquidity is None:
        logger.warning("market_catalog augmentation: no liquidity data; entries will report 0.0 liquidity_usd")
    if open_interest is None:
        logger.warning("market_catalog augmentation: no open-interest data; entries will report 0.0 OI")

    long_liq: dict[str, float] = (liquidity or {}).get("long", {})
    short_liq: dict[str, float] = (liquidity or {}).get("short", {})
    long_oi: dict[str, float] = (open_interest or {}).get("long", {})
    short_oi: dict[str, float] = (open_interest or {}).get("short", {})

    augmented: list[MarketEntry] = []
    for entry in entries:
        market_symbol = _resolve_market_symbol(config, entry.market_key)
        if not market_symbol:
            augmented.append(entry)
            continue

        liq_long = float(long_liq.get(market_symbol, 0.0))
        liq_short = float(short_liq.get(market_symbol, 0.0))
        oi_long = float(long_oi.get(market_symbol, 0.0))
        oi_short = float(short_oi.get(market_symbol, 0.0))

        augmented.append(
            MarketEntry(
                market_key=entry.market_key,
                index_token_symbol=entry.index_token_symbol,
                index_token_address=entry.index_token_address,
                long_token_symbol=entry.long_token_symbol,
                long_token_address=entry.long_token_address,
                short_token_symbol=entry.short_token_symbol,
                short_token_address=entry.short_token_address,
                liquidity_usd=liq_long + liq_short,
                oi_long_usd=oi_long,
                oi_short_usd=oi_short,
                refreshed_at=entry.refreshed_at,
            )
        )

    return augmented
