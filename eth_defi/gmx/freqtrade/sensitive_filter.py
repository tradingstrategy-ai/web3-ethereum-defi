"""Logging filter to redact sensitive data from log messages.

Monkeypatched into Freqtrade's logging system for GMX integration.

Handles a subtle Python logging behaviour: when you call
``logger.info("Config: %s", some_dict)``, the dict is stored as
``record.args`` and converted to string *after* filters run.  This
filter converts dicts to strings *before* applying redaction patterns.

.. note::

    ``_sanitise_any`` coerces dicts and lists to ``str`` so that
    redaction patterns can match.  This changes the type of the log
    argument from the original container to a string, which is
    acceptable because the value is only used for string formatting.
"""

import logging
import re
from typing import Any

from eth_defi.utils import get_url_domain

logger = logging.getLogger(__name__)


class SensitiveDataFilter(logging.Filter):
    """Logging filter to redact sensitive data (private keys, API keys, etc.)
    from log messages.

    Redacts:

    - ``apiKey``, ``secret``, ``password`` (16+ char values, except
      ``password`` which redacts any non-empty value)
    - ``privateKey``, ``private_key`` (64+ char hex with ``0x`` prefix)
    - ``accountId``, ``walletAddress`` (40+ char hex addresses)
    - ``signature`` (64+ char hex)
    - bare hex strings preceded by ``key``, ``private``, or ``secret``
    - all URLs (replaced with domain-only via :func:`get_url_domain`)
    """

    # Consolidated patterns using ['\"] to match both Python repr and JSON.
    # Each pattern handles single-quoted *and* double-quoted variants.
    PATTERNS: list[tuple[str, str]] = [
        # API keys (16+ char strings)
        (r"""['"]apiKey['"]:\s*['"]([^'"]{16,})['"]""", r"'apiKey': '[REDACTED]'"),
        # Secrets (16+ char strings)
        (r"""['"]secret['"]:\s*['"]([^'"]{16,})['"]""", r"'secret': '[REDACTED]'"),
        # Passwords (any non-empty value — more aggressive than other fields
        # because passwords can be short)
        (r"""['"]password['"]:\s*['"]([^'"]+)['"]""", r"'password': '[REDACTED]'"),
        # JWT secret keys (any non-empty value)
        (r"""['"]jwt_secret_key['"]:\s*['"]([^'"]+)['"]""", r"'jwt_secret_key': '[REDACTED]'"),
        # Private keys (64+ char hex with 0x prefix)
        (r"""['"]privateKey['"]:\s*['"](0x[0-9a-fA-F]{64,})['"]""", r"'privateKey': '[REDACTED]'"),
        (r"""['"]private_key['"]:\s*['"](0x[0-9a-fA-F]{64,})['"]""", r"'private_key': '[REDACTED]'"),
        # Account IDs (hex addresses, 40+ chars)
        (r"""['"]accountId['"]:\s*['"](0x[0-9a-fA-F]{40,})['"]""", r"'accountId': '[REDACTED]'"),
        # Wallet addresses (hex, 40+ chars)
        (r"""['"]walletAddress['"]:\s*['"](0x[0-9a-fA-F]{40,})['"]""", r"'walletAddress': '[REDACTED]'"),
        # Signatures (64+ char hex)
        (r"""['"]signature['"]:\s*['"](0x[0-9a-fA-F]{64,})['"]""", r"'signature': '[REDACTED]'"),
        # Bare hex private keys preceded by context keywords
        (r"(?:key|private|secret)\W*(0x[0-9a-fA-F]{64,})\b", "[REDACTED_HEX]"),
    ]

    # Matches any http/https/ws/wss URL — used by _redact_urls()
    _URL_PATTERN = re.compile(r"(?:https?|wss?)://[^\s'\"<>]+")

    def __init__(self, name: str = ""):
        super().__init__(name)
        self._compiled_patterns = [(re.compile(pattern, re.IGNORECASE), replacement) for pattern, replacement in self.PATTERNS]

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter and sanitise the log record message."""
        # Sanitise the message itself
        if record.msg and isinstance(record.msg, str):
            record.msg = self._sanitise(record.msg)

        # Sanitise arguments.
        #
        # ``record.args`` can be:
        # - tuple: positional args for ``%s`` formatting
        # - dict: EITHER named args for ``%(name)s`` OR a single dict
        #   passed to ``%s``
        #
        # When ``logger.info("msg: %s", some_dict)`` is called, Python
        # stores the dict directly as ``record.args`` (not wrapped in a
        # tuple).  The dict is converted to a string *after* filters run,
        # so we must convert it to a sanitised string now.
        #
        # The heuristic below (``%s`` without ``%(`` ) is imperfect: a
        # format string could legitimately contain both, but this covers
        # all practical Freqtrade logging patterns.
        if record.args:
            if isinstance(record.args, dict):
                msg = record.msg if record.msg else ""
                if "%s" in msg and "%(" not in msg:
                    # Single dict arg for %s — convert to sanitised string
                    record.args = (self._sanitise(str(record.args)),)
                else:
                    # Named args for %(name)s — sanitise each value
                    record.args = {k: self._sanitise_any(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._sanitise_any(arg) for arg in record.args)

        return True  # Always return True to keep the record

    def _sanitise_any(self, value: Any) -> str | Any:
        """Sanitise any value, converting non-strings to string first.

        .. note::

            This coerces dicts/lists to ``str`` so redaction patterns can
            match inside them.  The type change is acceptable because the
            value is only ever used for log-message string formatting.
        """
        if isinstance(value, str):
            return self._sanitise(value)
        elif isinstance(value, (dict, list)):
            return self._sanitise(str(value))
        return value

    def _redact_urls(self, text: str) -> str:
        """Replace all URLs with their domain-only form.

        Uses :func:`eth_defi.utils.get_url_domain` so that path
        components (which may contain API keys, e.g. Infura) and
        embedded ``user:pass@host`` credentials are stripped.
        """

        def _replace(match: re.Match) -> str:
            url = match.group(0)
            try:
                return get_url_domain(url)
            except Exception:
                return "[REDACTED_URL]"

        return self._URL_PATTERN.sub(_replace, text)

    def _sanitise(self, text: str) -> str:
        """Apply all redaction patterns to the text, then redact URLs."""
        for pattern, replacement in self._compiled_patterns:
            text = pattern.sub(replacement, text)
        text = self._redact_urls(text)
        return text


# Module-level state for patch tracking
_LOGGING_PATCHED = False
_SENSITIVE_FILTER = None
_ORIGINAL_HANDLER_INIT = None


def patch_logging():
    """Add :class:`SensitiveDataFilter` to all existing and future log handlers.

    This function:

    1. Creates a single :class:`SensitiveDataFilter` instance.
    2. Adds it to all existing handlers on the root logger.
    3. Monkeypatches :meth:`logging.Handler.__init__` so future handlers
       get the filter automatically.

    Safe to call multiple times — only applies once.
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
    """Remove the logging monkeypatch.  Mainly useful for testing."""
    global _LOGGING_PATCHED, _SENSITIVE_FILTER, _ORIGINAL_HANDLER_INIT

    if not _LOGGING_PATCHED:
        return

    if _ORIGINAL_HANDLER_INIT is not None:
        logging.Handler.__init__ = _ORIGINAL_HANDLER_INIT

    # Remove filter from all handlers — root and non-root loggers
    if _SENSITIVE_FILTER is not None:
        for handler in logging.root.handlers:
            handler.removeFilter(_SENSITIVE_FILTER)

        for logger_ref in logging.Logger.manager.loggerDict.values():
            if isinstance(logger_ref, logging.Logger):
                for handler in logger_ref.handlers:
                    handler.removeFilter(_SENSITIVE_FILTER)

    _LOGGING_PATCHED = False
    _SENSITIVE_FILTER = None
    _ORIGINAL_HANDLER_INIT = None


def is_logging_patched() -> bool:
    """Check if logging has been patched with :class:`SensitiveDataFilter`."""
    return _LOGGING_PATCHED
