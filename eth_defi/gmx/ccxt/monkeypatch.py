"""Monkeypatch CCXT to add GMX exchange support without forking.

This module dynamically injects the GMX exchange class into the CCXT library,
making it available as if it were a built-in CCXT exchange.

Usage::

    # Import and apply the monkeypatch
    from eth_defi.gmx.ccxt.monkeypatch import patch_ccxt

    patch_ccxt()

    # Now GMX is available in CCXT
    import ccxt

    # Use GMX like any other CCXT exchange
    exchange = ccxt.gmx(
        {
            "rpcUrl": "https://arb1.arbitrum.io/rpc",
            "privateKey": "0x...",  # Optional, only needed for trading
        }
    )

    # Or access the class directly
    GMX = ccxt.gmx
    exchange = GMX({"rpcUrl": "..."})

The monkeypatch adds:

1. ``ccxt.gmx`` - The GMX exchange class
2. ``ccxt.exchanges`` - Adds 'gmx' to the list of supported exchanges
3. Auto-discovery - GMX appears in ``ccxt.exchanges`` list

Example with context manager (auto-unpatch)::

    from eth_defi.gmx.ccxt.monkeypatch import gmx_ccxt_patch

    with gmx_ccxt_patch():
        import ccxt

        exchange = ccxt.gmx({"rpcUrl": "..."})
        markets = exchange.fetch_markets()

    # GMX is removed from ccxt after the context exits

Advanced usage - manual patching::

    from eth_defi.gmx.ccxt.monkeypatch import patch_ccxt, unpatch_ccxt

    # Apply patch
    patch_ccxt()

    # Use GMX
    import ccxt

    exchange = ccxt.gmx({"rpcUrl": "..."})

    # Remove patch (optional)
    unpatch_ccxt()

.. note::
    The monkeypatch is safe to apply multiple times. If GMX is already
    registered, subsequent calls will update the registration.

.. warning::
    If you unpatch and then import new modules that expect GMX to be in CCXT,
    they will fail. Unpatching is primarily useful for testing.
"""

import logging
import sys
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)


_PATCHED = False
_ORIGINAL_EXCHANGES: Optional[list] = None


def patch_ccxt(force: bool = False) -> bool:
    """Inject GMX exchange into the CCXT library.

    This function modifies the CCXT module at runtime to add GMX as a supported
    exchange. After calling this function, you can use GMX like any other CCXT
    exchange.

    The patch adds:
    - ``ccxt.gmx`` - The GMX exchange class
    - Adds 'gmx' to ``ccxt.exchanges`` list
    - Updates ``ccxt.__all__`` if it exists

    Args:
        force: If True, re-apply the patch even if already applied.
               If False (default), skip if already patched.

    Returns:
        True if patch was applied, False if already patched and not forced.

    Raises:
        ImportError: If CCXT is not installed or GMX class cannot be imported.

    Example::

        from eth_defi.gmx.ccxt.monkeypatch import patch_ccxt

        # Apply the patch once at startup
        patch_ccxt()

        # Now use GMX through CCXT
        import ccxt

        exchange = ccxt.gmx({"rpcUrl": "https://arb1.arbitrum.io/rpc"})
        markets = exchange.fetch_markets()
    """
    global _PATCHED, _ORIGINAL_EXCHANGES

    if _PATCHED and not force:
        logger.debug("CCXT is already patched with GMX support")
        return False

    try:
        import ccxt

        # Force import of async_support and pro modules so they exist before we patch them
        import ccxt.async_support
        import ccxt.pro
    except ImportError as e:
        raise ImportError("CCXT library is not installed. Install it with: pip install ccxt") from e

    try:
        from eth_defi.gmx.ccxt.exchange import GMX
        from eth_defi.gmx.ccxt.async_support.exchange import GMX as AsyncGMX
    except ImportError as e:
        raise ImportError("Could not import GMX exchange class. Make sure eth_defi is properly installed.") from e

    # Store original exchanges list for potential unpatch
    if not _PATCHED:
        _ORIGINAL_EXCHANGES = ccxt.exchanges.copy() if hasattr(ccxt, "exchanges") else []

    # Add GMX class to ccxt module (sync version)
    ccxt.gmx = GMX

    # Add 'gmx' to the exchanges list if it exists
    if hasattr(ccxt, "exchanges"):
        if "gmx" not in ccxt.exchanges:
            ccxt.exchanges.append("gmx")
            # Keep the list sorted like CCXT does
            ccxt.exchanges.sort()
    else:
        # Create exchanges list if it doesn't exist
        ccxt.exchanges = ["gmx"]

    # Update __all__ if it exists (for proper module exports)
    if hasattr(ccxt, "__all__"):
        if "gmx" not in ccxt.__all__:
            ccxt.__all__.append("gmx")

    # Patch ccxt.async_support with the ASYNC version
    if hasattr(ccxt, "async_support"):
        ccxt.async_support.gmx = AsyncGMX  # Use async version, not sync
        if hasattr(ccxt.async_support, "exchanges"):
            if "gmx" not in ccxt.async_support.exchanges:
                ccxt.async_support.exchanges.append("gmx")
                ccxt.async_support.exchanges.sort()
        logger.debug("Patched ccxt.async_support with AsyncGMX")

    # Patch ccxt.pro with ASYNC version (ccxt.pro is async-only)
    # Freqtrade checks ccxt.pro first before falling back to ccxt.async_support
    if hasattr(ccxt, "pro"):
        ccxt.pro.gmx = AsyncGMX  # ccxt.pro should use async version!
        if hasattr(ccxt.pro, "exchanges"):
            if "gmx" not in ccxt.pro.exchanges:
                ccxt.pro.exchanges.append("gmx")
                ccxt.pro.exchanges.sort()
        logger.debug("Patched ccxt.pro with AsyncGMX (ccxt.pro is async-only)")

    # Mark as patched
    _PATCHED = True

    logger.info("Successfully patched CCXT with GMX exchange support")
    logger.debug(f"CCXT now has {len(ccxt.exchanges)} exchanges including GMX")

    return True


def unpatch_ccxt() -> bool:
    """Remove GMX exchange from the CCXT library.

    This function reverses the monkeypatch applied by ``patch_ccxt()``,
    removing GMX from CCXT's namespace and exchange list.

    Returns:
        True if unpatch was performed, False if not currently patched.

    .. warning::
        This function is primarily for testing. In production code, you typically
        don't need to unpatch. If you unpatch and other modules have already
        imported ``ccxt.gmx``, they will retain their references.

    Example::

        from eth_defi.gmx.ccxt.monkeypatch import patch_ccxt, unpatch_ccxt

        # Apply patch
        patch_ccxt()

        # Use GMX
        import ccxt

        exchange = ccxt.gmx({"rpcUrl": "..."})

        # Clean up (usually not necessary)
        unpatch_ccxt()
    """
    global _PATCHED, _ORIGINAL_EXCHANGES

    if not _PATCHED:
        logger.debug("CCXT is not currently patched with GMX")
        return False

    try:
        import ccxt
    except ImportError:
        # If ccxt is not available, nothing to unpatch
        _PATCHED = False
        return False

    # Remove GMX class from ccxt module
    if hasattr(ccxt, "gmx"):
        delattr(ccxt, "gmx")

    # Restore original exchanges list
    if _ORIGINAL_EXCHANGES is not None and hasattr(ccxt, "exchanges"):
        ccxt.exchanges[:] = _ORIGINAL_EXCHANGES
    elif hasattr(ccxt, "exchanges") and "gmx" in ccxt.exchanges:
        ccxt.exchanges.remove("gmx")

    # Remove from __all__ if it exists
    if hasattr(ccxt, "__all__") and "gmx" in ccxt.__all__:
        ccxt.__all__.remove("gmx")

    # Unpatch ccxt.async_support
    if hasattr(ccxt, "async_support"):
        if hasattr(ccxt.async_support, "gmx"):
            delattr(ccxt.async_support, "gmx")
        if hasattr(ccxt.async_support, "exchanges") and "gmx" in ccxt.async_support.exchanges:
            ccxt.async_support.exchanges.remove("gmx")
        logger.debug("Unpatched ccxt.async_support")

    # Unpatch ccxt.pro
    if hasattr(ccxt, "pro"):
        if hasattr(ccxt.pro, "gmx"):
            delattr(ccxt.pro, "gmx")
        if hasattr(ccxt.pro, "exchanges") and "gmx" in ccxt.pro.exchanges:
            ccxt.pro.exchanges.remove("gmx")
        logger.debug("Unpatched ccxt.pro")

    # Mark as unpatched
    _PATCHED = False
    _ORIGINAL_EXCHANGES = None

    logger.info("Successfully removed GMX from CCXT")

    return True


def is_patched() -> bool:
    """Check if CCXT is currently patched with GMX support.

    Returns:
        True if GMX is currently registered in CCXT, False otherwise.

    Example::

        from eth_defi.gmx.ccxt.monkeypatch import patch_ccxt, is_patched

        print(is_patched())  # False
        patch_ccxt()
        print(is_patched())  # True
    """
    return _PATCHED


@contextmanager
def gmx_ccxt_patch():
    """Context manager for temporary CCXT patching.

    This context manager applies the GMX monkeypatch for the duration of the
    context and automatically removes it when exiting. Useful for testing or
    when you only need GMX support temporarily.

    Yields:
        None

    Example::

        from eth_defi.gmx.ccxt.monkeypatch import gmx_ccxt_patch

        # GMX only available inside the context
        with gmx_ccxt_patch():
            import ccxt

            exchange = ccxt.gmx({"rpcUrl": "..."})
            markets = exchange.fetch_markets()

        # GMX is removed from ccxt after exiting

    Example with exception handling::

        from eth_defi.gmx.ccxt.monkeypatch import gmx_ccxt_patch

        try:
            with gmx_ccxt_patch():
                import ccxt

                exchange = ccxt.gmx({"rpcUrl": "..."})
                # Do trading...
        except Exception as e:
            print(f"Error: {e}")
        # GMX is still properly removed even if exception occurred
    """
    was_patched = _PATCHED

    try:
        # Apply patch if not already applied
        if not was_patched:
            patch_ccxt()
        yield
    finally:
        # Only unpatch if we applied it in this context
        if not was_patched:
            unpatch_ccxt()


def ensure_patched():
    """Ensure CCXT is patched with GMX support.

    This is a convenience function that patches CCXT if not already patched.
    Safe to call multiple times. Useful at module import time or in functions
    that require GMX to be available in CCXT.

    Example::

        from eth_defi.gmx.ccxt.monkeypatch import ensure_patched


        def my_trading_function():
            # Make sure GMX is available
            ensure_patched()

            import ccxt

            exchange = ccxt.gmx({"rpcUrl": "..."})
            return exchange.fetch_ticker("ETH/USD")
    """
    patch_ccxt(force=False)
