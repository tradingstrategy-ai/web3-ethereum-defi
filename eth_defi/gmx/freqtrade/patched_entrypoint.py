#!/usr/bin/env python3
"""
GMX Freqtrade Patched Entrypoint

This script applies the GMX monkeypatch to Freqtrade before starting the bot.
It should be used as the Docker entrypoint or imported before running Freqtrade.

Usage:
    # As a script wrapper
    python -m eth_defi.gmx.freqtrade.patched_entrypoint freqtrade trade --config config.json

    # Or as a direct script
    python patched_entrypoint.py freqtrade trade --config config.json

    # Or import in your strategy/config
    import eth_defi.gmx.freqtrade.patched_entrypoint  # Just importing applies the patch
"""

import logging
import re
import sys

logger = logging.getLogger(__name__)

_patched_at_module_level = False


def _patch_status_table_profit_format() -> None:
    """Patch freqtrade's ``_rpc_status_table`` to display profit with 3 decimal places.

    Freqtrade hardcodes ``:.5g`` (5 significant digits) for the per-trade profit
    column, which produces values like ``-0.0039503`` for small USD amounts.
    This patch replaces the format with ``:.3f`` (3 fixed decimal places) so the
    same value displays as ``-0.004``.
    """
    try:
        from freqtrade.rpc.rpc import RPC

        _original = RPC._rpc_status_table

        def _patched(self, stake_currency: str, fiat_display_currency: str):
            trades_list, columns, fiat_profit_sum, fiat_total_profit_sum = _original(self, stake_currency, fiat_display_currency)
            # Profit string is the last element of each row: e.g. "0.60% (-0.0039503)"
            # Replace the parenthesised number with a :.3f formatted version.
            _pat = re.compile(r"\((-?[\d.]+(?:e[+-]?\d+)?)\)")
            for row in trades_list:
                if row and isinstance(row[-1], str):
                    row[-1] = _pat.sub(
                        lambda m: f"({float(m.group(1)):.3f})",
                        row[-1],
                    )
            return trades_list, columns, fiat_profit_sum, fiat_total_profit_sum

        RPC._rpc_status_table = _patched
        logger.debug("Patched freqtrade _rpc_status_table: profit display → :.3f")
    except Exception as exc:
        logger.debug("Could not patch _rpc_status_table: %s", exc)


def apply_patch():
    """Apply the GMX monkeypatch to Freqtrade.

    This function patches CCXT (including async_support and pro modules)
    and Freqtrade to recognize GMX as a supported exchange.
    It also adds sensitive data filtering to all log handlers.
    """
    global _patched_at_module_level
    print("Applying GMX monkeypatch to Freqtrade...", flush=True)
    from eth_defi.gmx.freqtrade.monkeypatch import patch_freqtrade
    from eth_defi.gmx.freqtrade.sensitive_filter import patch_logging, patch_notebook

    patch_freqtrade()
    patch_logging()  # Add sensitive data filtering to all log handlers
    patch_notebook()  # Add sensitive data filtering to notebook output channels
    _patch_status_table_profit_format()  # Show profit with 3 decimal places instead of 5 sig figs

    # Register custom pairlist plugins so schema validation accepts them.
    # Must happen before freqtrade.config_schema is imported.
    from freqtrade.constants import AVAILABLE_PAIRLISTS

    for name in ("HistoricalVolumePairList", "GMXLiquidityFilter"):
        if name not in AVAILABLE_PAIRLISTS:
            AVAILABLE_PAIRLISTS.append(name)

    # Verify the patch worked correctly
    print("Verifying GMX monkeypatch...", flush=True)
    import ccxt.async_support
    import inspect

    if not hasattr(ccxt.async_support, "gmx"):
        raise RuntimeError("GMX monkeypatch failed: ccxt.async_support.gmx not found")

    gmx_class = ccxt.async_support.gmx
    print(f"  ccxt.async_support.gmx = {gmx_class}", flush=True)
    print(f"  Class module: {gmx_class.__module__}", flush=True)

    # Check if load_markets is async
    if not inspect.iscoroutinefunction(gmx_class.load_markets):
        raise RuntimeError(f"GMX monkeypatch failed: load_markets is not async! Class: {gmx_class}")

    print("  ✓ load_markets is async", flush=True)
    print("GMX support enabled successfully!", flush=True)
    _patched_at_module_level = True


def main():
    """Main entrypoint function for running Freqtrade with GMX support."""
    # Apply the patch if not already done at module level
    if not _patched_at_module_level:
        apply_patch()

    if len(sys.argv) > 1:
        # CRITICAL: Import freqtrade AFTER patching to ensure resolvers see our GMX class
        # The patch must be applied before freqtrade.resolvers.exchange_resolver is imported
        # because it caches the reference to freqtrade.exchange at module load time
        from freqtrade.main import main as freqtrade_main

        # Remove this script name from argv so freqtrade gets clean arguments
        sys.argv = sys.argv[1:]

        # Run freqtrade
        sys.exit(freqtrade_main())
    else:
        print("\nNo command provided. Patch applied successfully.")
        print("Usage: python -m eth_defi.gmx.freqtrade.patched_entrypoint <freqtrade-command>")


# Apply patch when module is imported
apply_patch()

# Run main if executed as script
if __name__ == "__main__":
    main()
