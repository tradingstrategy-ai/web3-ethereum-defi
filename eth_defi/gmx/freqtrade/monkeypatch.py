"""Monkeypatch Freqtrade to add GMX exchange support without forking.

This module dynamically injects the GMX exchange class into Freqtrade,
making it available as if it were a built-in Freqtrade exchange.

The monkeypatch works by:
1. First patching CCXT to add GMX (using eth_defi.gmx.ccxt.monkeypatch)
2. Then registering the GMX Freqtrade exchange class in Freqtrade's exchange module
3. Adding GMX to Freqtrade's supported exchanges list

Usage::

    # Import and apply the monkeypatch
    from eth_defi.gmx.freqtrade.monkeypatch import patch_freqtrade

    patch_freqtrade()

    # Now GMX is available in Freqtrade
    from freqtrade.exchange import Exchange
    from freqtrade.resolvers import ExchangeResolver

    # Create GMX exchange instance
    config = {
        "exchange": {
            "name": "gmx",
            "rpc_url": "https://arb1.arbitrum.io/rpc",
            "private_key": "0x...",
        },
        "stake_currency": "USD",
        "trading_mode": "futures",
        "margin_mode": "isolated",
    }

    exchange = ExchangeResolver.load_exchange(config)

The monkeypatch adds:

1. ``ccxt.gmx`` - The GMX CCXT exchange class (via CCXT monkeypatch)
2. ``freqtrade.exchange.gmx`` - The GMX Freqtrade exchange class
3. Adds 'gmx' to Freqtrade's SUPPORTED_EXCHANGES list
4. Auto-discovery - GMX can be loaded via ExchangeResolver

Context manager usage::

    from eth_defi.gmx.freqtrade.monkeypatch import gmx_freqtrade_patch

    with gmx_freqtrade_patch():
        from freqtrade.exchange import Exchange
        # Use Freqtrade with GMX support

    # GMX is removed after context exits

.. note::
    The monkeypatch is safe to apply multiple times. If GMX is already
    registered, subsequent calls will update the registration.

.. warning::
    You must patch BEFORE importing freqtrade modules that will use GMX.
    The recommended approach is to patch at the very start of your script.
"""

import logging
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)


_PATCHED = False
_ORIGINAL_SUPPORTED_EXCHANGES: Optional[list] = None


def patch_freqtrade(force: bool = False) -> bool:
    """Inject GMX exchange into Freqtrade.

    This function modifies Freqtrade at runtime to add GMX as a supported
    exchange. It performs two main steps:

    1. Patches CCXT to add GMX (using eth_defi.gmx.ccxt.monkeypatch)
    2. Registers GMX in Freqtrade's exchange module

    Args:
        force: If True, re-apply the patch even if already applied.
               If False (default), skip if already patched.

    Returns:
        True if patch was applied, False if already patched and not forced.

    Raises:
        ImportError: If required modules cannot be imported.

    Example::

        from eth_defi.gmx.freqtrade.monkeypatch import patch_freqtrade

        # Apply the patch once at startup
        patch_freqtrade()

        # Now create GMX exchange via Freqtrade
        from freqtrade.resolvers import ExchangeResolver

        config = {
            "exchange": {"name": "gmx", "rpc_url": "https://arb1.arbitrum.io/rpc"},
            "trading_mode": "futures",
            "margin_mode": "isolated",
        }
        exchange = ExchangeResolver.load_exchange(config)
    """
    global _PATCHED, _ORIGINAL_SUPPORTED_EXCHANGES

    if _PATCHED and not force:
        logger.debug("Freqtrade is already patched with GMX support")
        return False

    # Step 1: Patch CCXT first
    try:
        from eth_defi.gmx.ccxt.monkeypatch import patch_ccxt

        patch_ccxt()
    except ImportError as e:
        raise ImportError("Could not import GMX CCXT monkeypatch. Make sure eth_defi is properly installed.") from e

    # Step 2: Patch Freqtrade
    try:
        # Import Freqtrade exchange module
        try:
            import freqtrade.exchange as ft_exchange
        except ImportError as e:
            raise ImportError("Freqtrade is not installed. Install it with: pip install freqtrade") from e

        # Import GMX Freqtrade exchange class
        try:
            from eth_defi.gmx.freqtrade.gmx_exchange import Gmx
        except ImportError as e:
            raise ImportError("Could not import GMX Freqtrade exchange class. Make sure eth_defi is properly installed.") from e

        # Add GMX class to freqtrade.exchange module
        # Freqtrade's ExchangeResolver looks for lowercase name
        ft_exchange.gmx = Gmx
        ft_exchange.Gmx = Gmx  # Also add capitalized for backwards compatibility

        # Try to add to __all__ if it exists
        if hasattr(ft_exchange, "__all__"):
            if "gmx" not in ft_exchange.__all__:
                ft_exchange.__all__.append("gmx")
            if "Gmx" not in ft_exchange.__all__:
                ft_exchange.__all__.append("Gmx")

        # Add to SUPPORTED_EXCHANGES in common.py if available
        try:
            from freqtrade.exchange import common

            if hasattr(common, "SUPPORTED_EXCHANGES"):
                if not _PATCHED:
                    _ORIGINAL_SUPPORTED_EXCHANGES = common.SUPPORTED_EXCHANGES.copy()

                if "gmx" not in common.SUPPORTED_EXCHANGES:
                    common.SUPPORTED_EXCHANGES.append("gmx")
                    # Keep sorted
                    common.SUPPORTED_EXCHANGES.sort()

        except (ImportError, AttributeError) as e:
            logger.debug(f"Could not update SUPPORTED_EXCHANGES list: {e}")

        # Mark as patched
        _PATCHED = True

        logger.info("Successfully patched Freqtrade with GMX exchange support")
        logger.debug("GMX is now available as a Freqtrade exchange")

        return True

    except Exception as e:
        logger.error(f"Failed to patch Freqtrade: {e}")
        raise


def unpatch_freqtrade() -> bool:
    """Remove GMX exchange from Freqtrade.

    This function reverses the monkeypatch applied by ``patch_freqtrade()``,
    removing GMX from Freqtrade's namespace and exchange lists.

    Also unpatches CCXT.

    Returns:
        True if unpatch was performed, False if not currently patched.

    .. warning::
        This function is primarily for testing. In production code, you typically
        don't need to unpatch.

    Example::

        from eth_defi.gmx.freqtrade.monkeypatch import patch_freqtrade, unpatch_freqtrade

        # Apply patch
        patch_freqtrade()

        # Use Freqtrade with GMX
        # ...

        # Clean up (usually not necessary)
        unpatch_freqtrade()
    """
    global _PATCHED, _ORIGINAL_SUPPORTED_EXCHANGES

    if not _PATCHED:
        logger.debug("Freqtrade is not currently patched with GMX")
        return False

    # Unpatch Freqtrade
    try:
        import freqtrade.exchange as ft_exchange

        # Remove GMX class from freqtrade.exchange module
        if hasattr(ft_exchange, "Gmx"):
            delattr(ft_exchange, "Gmx")

        # Remove from __all__ if it exists
        if hasattr(ft_exchange, "__all__") and "Gmx" in ft_exchange.__all__:
            ft_exchange.__all__.remove("Gmx")

        # Restore original SUPPORTED_EXCHANGES
        try:
            from freqtrade.exchange import common

            if hasattr(common, "SUPPORTED_EXCHANGES") and _ORIGINAL_SUPPORTED_EXCHANGES is not None:
                common.SUPPORTED_EXCHANGES[:] = _ORIGINAL_SUPPORTED_EXCHANGES

        except (ImportError, AttributeError):
            pass

    except ImportError:
        # Freqtrade not available, nothing to unpatch
        pass

    # Unpatch CCXT
    try:
        from eth_defi.gmx.ccxt.monkeypatch import unpatch_ccxt

        unpatch_ccxt()
    except ImportError:
        pass

    # Mark as unpatched
    _PATCHED = False
    _ORIGINAL_SUPPORTED_EXCHANGES = None

    logger.info("Successfully removed GMX from Freqtrade")

    return True


def is_patched() -> bool:
    """Check if Freqtrade is currently patched with GMX support.

    Returns:
        True if GMX is currently registered in Freqtrade, False otherwise.

    Example::

        from eth_defi.gmx.freqtrade.monkeypatch import patch_freqtrade, is_patched

        print(is_patched())  # False
        patch_freqtrade()
        print(is_patched())  # True
    """
    return _PATCHED


@contextmanager
def gmx_freqtrade_patch():
    """Context manager for temporary Freqtrade patching.

    This context manager applies the GMX monkeypatch for the duration of the
    context and automatically removes it when exiting. Useful for testing or
    when you only need GMX support temporarily.

    Yields:
        None

    Example::

        from eth_defi.gmx.freqtrade.monkeypatch import gmx_freqtrade_patch

        # GMX only available inside the context
        with gmx_freqtrade_patch():
            from freqtrade.resolvers import ExchangeResolver

            config = {
                "exchange": {"name": "gmx", "rpc_url": "..."},
                "trading_mode": "futures",
                "margin_mode": "isolated",
            }
            exchange = ExchangeResolver.load_exchange(config)

        # GMX is removed from Freqtrade after exiting

    Example with exception handling::

        from eth_defi.gmx.freqtrade.monkeypatch import gmx_freqtrade_patch

        try:
            with gmx_freqtrade_patch():
                # Use Freqtrade with GMX
                pass
        except Exception as e:
            print(f"Error: {e}")
        # GMX is still properly removed even if exception occurred
    """
    was_patched = _PATCHED

    try:
        # Apply patch if not already applied
        if not was_patched:
            patch_freqtrade()
        yield
    finally:
        # Only unpatch if we applied it in this context
        if not was_patched:
            unpatch_freqtrade()


def ensure_patched():
    """Ensure Freqtrade is patched with GMX support.

    This is a convenience function that patches Freqtrade if not already patched.
    Safe to call multiple times. Useful at module import time or in functions
    that require GMX to be available in Freqtrade.

    Example::

        from eth_defi.gmx.freqtrade.monkeypatch import ensure_patched

        def my_trading_bot():
            # Make sure GMX is available
            ensure_patched()

            from freqtrade.resolvers import ExchangeResolver

            config = {"exchange": {"name": "gmx", ...}}
            exchange = ExchangeResolver.load_exchange(config)
    """
    patch_freqtrade(force=False)
