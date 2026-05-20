"""GMX symbol normalisation constants.

Freqtrade's pair validation regex (``^[A-Za-z0-9:/-]+$``) rejects dots, so
versioned symbols like ``XAUT.v2`` must be normalised to a canonical name
before they are exposed through the CCXT adapter.

Both the legacy and the versioned symbol map to the same canonical name so
whitelists remain stable across GMX token migrations.  The deprecated variant
is already excluded via ``isListed=false`` in the REST API, so there is no
ambiguity at runtime — only one active market exists per canonical name.

GMX uses a ``k`` prefix for 1000x-denomination tokens (e.g. ``kBONK`` is the
token where 1 unit represents 1000 BONK).  The ``k``-prefixed symbol is the
raw on-chain token symbol; the GMX SDK and the rest of the trading stack
(``OrderArgumentParser``, ``GMXTrading``) expect the bare symbol (``BONK``).
These are also mapped here so the CCXT adapter and close/open order flows work
without callers having to strip the prefix themselves.
"""

#: Maps raw GMX token symbols to their canonical (Freqtrade-safe) equivalents.
#:
#: Covers:
#: - Versioned symbols (``XAUT.v2`` → ``XAUT``): dots rejected by Freqtrade.
#: - 1000x ``k``-prefix tokens (``kBONK`` → ``BONK``, ``kSHIB`` → ``SHIB``):
#:   the GMX SDK token registry and ``OrderArgumentParser`` use the bare symbol.
SYMBOL_NORMALISE: dict[str, str] = {
    # Versioned markets
    "XAUT.v2": "XAUT",
    # 1000x denomination tokens (k-prefix)
    "kBONK": "BONK",
    "kSHIB": "SHIB",
    "kPEPE": "PEPE",
    "kFLOKI": "FLOKI",
}

#: Known deprecated GMX market token addresses (lowercase).
#:
#: These market tokens have been superseded and are disabled on-chain.
#: Any loading path (GraphQL, REST API, RPC, disk cache) must skip markets
#: whose ``market_token`` address appears in this set — even if the REST API
#: still reports ``isListed=true``.
#:
#: Example: XAUT deprecated pool ``0xAbDb...`` was replaced by XAUT.v2
#: ``0xeb28aD...``.  Sending an order to the old pool reverts with
#: ``DisabledMarket()``.
DEPRECATED_MARKET_TOKENS: frozenset[str] = frozenset(
    {
        # XAUT deprecated pool — superseded by XAUT.v2 (0xeb28aD1a2e497f4acc5d9b87e7b496623c93061e)
        "0xabdb2530e24f0736dfbf6da2600b52bd6455acdd",
    }
)
