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

import sys


_patched_at_module_level = False


def apply_patch():
    """Apply the GMX monkeypatch to Freqtrade.

    This function patches CCXT (including async_support and pro modules)
    and Freqtrade to recognize GMX as a supported exchange.
    """
    global _patched_at_module_level
    print("Applying GMX monkeypatch to Freqtrade...", flush=True)
    from eth_defi.gmx.freqtrade.monkeypatch import patch_freqtrade
    patch_freqtrade()
    print("GMX support enabled successfully!", flush=True)
    _patched_at_module_level = True


def main():
    """Main entrypoint function for running Freqtrade with GMX support."""
    # Apply the patch if not already done at module level
    if not _patched_at_module_level:
        apply_patch()

    if len(sys.argv) > 1:
        # Import and run freqtrade CLI
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
