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
        # Retired pools with maxOpenInterestLong == maxOpenInterestShort == 0
        # (Subsquid marketInfos, 2026-11-04). The replacements are single-token
        # USDG-collateral markets, loaded as ZEC2/HYPE2/XRP2/NEAR2, so without
        # these entries ZEC/USDC:USDC etc. resolved to the retired pool and every
        # open was keeper-cancelled with MaxOpenInterestExceeded(maxAllowed: 0).
        # Listing them removes the unusable USDC pairs instead of paying for
        # orders that cannot fill.
        # ZEC — replacement 0x2CeEafcA180B07C9d1d2f1086f8f59AAFd282832 (USDG)
        "0x587759c237acca739bce3911647bacf56c876e60",
        # HYPE — replacement 0x162C091263fBb3dfAf1D55DCC979533EB341DA5f (USDG)
        "0xbcb8fe13d02b023e8f94f6881cc0192fd918a5c0",
        # XRP — replacement 0xc9ED746D613eEfC0F196658f6030e90482593004 (USDG)
        "0x0ccb4faa6f1f1b30911619f1184082ab4e25813c",
        # NEAR — replacement 0x54bc430A788DFFD4ae35e751E10899489f5a953f (USDG)
        "0x63dc80ee90f26363b3fcd609007cc9e14c8991be",
    }
)
