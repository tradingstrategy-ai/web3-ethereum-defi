"""
Logging filter to redact sensitive data from log messages.
Monkeypatched into Freqtrade's logging system for GMX integration.

This filter handles a subtle bug in Python logging: when you call
logger.info("Config: %s", some_dict), the dict is stored as record.args
and converted to string AFTER filters run. This filter converts dicts
to strings BEFORE applying redaction patterns.
"""
import logging
import re
from typing import Any


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter to redact sensitive data (private keys, API keys, etc.)
    from log messages.

    Redacts:
    - apiKey, secret, password (16+ char values)
    - privateKey, private_key (64+ char hex with 0x prefix)
    - accountId, walletAddress (40+ char hex addresses)
    - httpsProxy, httpProxy (URLs with embedded user:pass@host credentials)
    - signature (64+ char hex)
    """

    # Patterns match Python dict repr format: 'key': 'value' or "key": "value"
    PATTERNS: list[tuple[str, str]] = [
        # API keys (16+ char strings)
        (r"'apiKey':\s*'([^']{16,})'", r"'apiKey': '[REDACTED]'"),
        (r'"apiKey":\s*"([^"]{16,})"', r'"apiKey": "[REDACTED]"'),
        # Secrets (16+ char strings)
        (r"'secret':\s*'([^']{16,})'", r"'secret': '[REDACTED]'"),
        (r'"secret":\s*"([^"]{16,})"', r'"secret": "[REDACTED]"'),
        # Passwords (any non-empty value)
        (r"'password':\s*'([^']+)'", r"'password': '[REDACTED]'"),
        (r'"password":\s*"([^"]+)"', r'"password": "[REDACTED]"'),
        # Private keys (64+ char hex with 0x prefix)
        (r"'privateKey':\s*'(0x[0-9a-fA-F]{64,})'", r"'privateKey': '[REDACTED]'"),
        (r'"privateKey":\s*"(0x[0-9a-fA-F]{64,})"', r'"privateKey": "[REDACTED]"'),
        (r"'private_key':\s*'(0x[0-9a-fA-F]{64,})'", r"'private_key': '[REDACTED]'"),
        (r'"private_key":\s*"(0x[0-9a-fA-F]{64,})"', r'"private_key": "[REDACTED]"'),
        # Account IDs (hex addresses, 40+ chars)
        (r"'accountId':\s*'(0x[0-9a-fA-F]{40,})'", r"'accountId': '[REDACTED]'"),
        (r'"accountId":\s*"(0x[0-9a-fA-F]{40,})"', r'"accountId": "[REDACTED]"'),
        # Wallet addresses (hex, 40+ chars)
        (r"'walletAddress':\s*'(0x[0-9a-fA-F]{40,})'", r"'walletAddress': '[REDACTED]'"),
        (r'"walletAddress":\s*"(0x[0-9a-fA-F]{40,})"', r'"walletAddress": "[REDACTED]"'),
        # Proxy URLs with embedded credentials: http://user:pass@host:port
        (r"'httpsProxy':\s*'(https?://[^:]+:[^@]+@[^']+)'", r"'httpsProxy': '[REDACTED]'"),
        (r'"httpsProxy":\s*"(https?://[^:]+:[^@]+@[^"]+)"', r'"httpsProxy": "[REDACTED]"'),
        (r"'httpProxy':\s*'(https?://[^:]+:[^@]+@[^']+)'", r"'httpProxy': '[REDACTED]'"),
        (r'"httpProxy":\s*"(https?://[^:]+:[^@]+@[^"]+)"', r'"httpProxy": "[REDACTED]"'),
        # Signatures (64+ char hex)
        (r"'signature':\s*'(0x[0-9a-fA-F]{64,})'", r"'signature': '[REDACTED]'"),
        (r'"signature":\s*"(0x[0-9a-fA-F]{64,})"', r'"signature": "[REDACTED]"'),
    ]

    def __init__(self, name: str = ""):
        super().__init__(name)
        self._compiled_patterns = [
            (re.compile(pattern, re.IGNORECASE), replacement)
            for pattern, replacement in self.PATTERNS
        ]

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter and sanitise the log record message."""
        # Sanitise the message itself
        if record.msg and isinstance(record.msg, str):
            record.msg = self._sanitise(record.msg)

        # Sanitise arguments
        # record.args can be:
        # - tuple: positional args for %s formatting
        # - dict: EITHER named args for %(name)s OR a single dict passed to %s
        #
        # CRITICAL: When logger.info("msg: %s", some_dict) is called, Python
        # stores the dict directly as record.args (not as a tuple). The dict
        # gets converted to string AFTER filters run, so we must convert it
        # to a sanitised string NOW.
        if record.args:
            if isinstance(record.args, dict):
                # Check if this is a single dict arg for %s formatting
                # vs named args for %(name)s formatting
                msg = record.msg if record.msg else ""
                if "%s" in msg and "%(" not in msg:
                    # Single dict arg for %s - convert to sanitised string tuple
                    record.args = (self._sanitise(str(record.args)),)
                else:
                    # Named args for %(name)s - sanitise each value
                    record.args = {
                        k: self._sanitise_any(v) for k, v in record.args.items()
                    }
            elif isinstance(record.args, tuple):
                record.args = tuple(self._sanitise_any(arg) for arg in record.args)

        return True  # Always return True to keep the record

    def _sanitise_any(self, value: Any) -> str | Any:
        """Sanitise any value, converting non-strings to string first if needed."""
        if isinstance(value, str):
            return self._sanitise(value)
        elif isinstance(value, (dict, list)):
            # Convert to string repr, then sanitise
            return self._sanitise(str(value))
        return value

    def _sanitise(self, text: str) -> str:
        """Apply all redaction patterns to the text."""
        for pattern, replacement in self._compiled_patterns:
            text = pattern.sub(replacement, text)
        return text


# Module-level state for patch tracking
_LOGGING_PATCHED = False
_SENSITIVE_FILTER = None
_ORIGINAL_HANDLER_INIT = None


def patch_logging():
    """
    Add SensitiveDataFilter to all existing and future log handlers.

    This function:
    1. Creates a single SensitiveDataFilter instance
    2. Adds it to all existing handlers on the root logger
    3. Monkeypatches logging.Handler.__init__ so future handlers get the filter

    Safe to call multiple times - will only apply once.
    """
    global _LOGGING_PATCHED, _SENSITIVE_FILTER, _ORIGINAL_HANDLER_INIT

    if _LOGGING_PATCHED:
        return

    _SENSITIVE_FILTER = SensitiveDataFilter()

    # Add filter to all existing handlers
    for handler in logging.root.handlers:
        handler.addFilter(_SENSITIVE_FILTER)

    # Monkeypatch logging.Handler.__init__ to add filter to future handlers
    _ORIGINAL_HANDLER_INIT = logging.Handler.__init__

    def patched_handler_init(self, level=logging.NOTSET):
        _ORIGINAL_HANDLER_INIT(self, level)
        if _SENSITIVE_FILTER is not None:
            self.addFilter(_SENSITIVE_FILTER)

    logging.Handler.__init__ = patched_handler_init

    _LOGGING_PATCHED = True


def unpatch_logging():
    """Remove the logging monkeypatch. Mainly useful for testing."""
    global _LOGGING_PATCHED, _SENSITIVE_FILTER, _ORIGINAL_HANDLER_INIT

    if not _LOGGING_PATCHED:
        return

    if _ORIGINAL_HANDLER_INIT is not None:
        logging.Handler.__init__ = _ORIGINAL_HANDLER_INIT

    # Remove filter from existing handlers
    if _SENSITIVE_FILTER is not None:
        for handler in logging.root.handlers:
            handler.removeFilter(_SENSITIVE_FILTER)

    _LOGGING_PATCHED = False
    _SENSITIVE_FILTER = None
    _ORIGINAL_HANDLER_INIT = None


def is_logging_patched() -> bool:
    """Check if logging has been patched with SensitiveDataFilter."""
    return _LOGGING_PATCHED
