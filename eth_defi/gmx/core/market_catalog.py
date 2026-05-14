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

from dataclasses import dataclass


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
