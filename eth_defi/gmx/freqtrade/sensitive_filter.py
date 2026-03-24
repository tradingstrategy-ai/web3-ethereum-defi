"""Logging filter to redact sensitive data from log messages.

Monkeypatched into Freqtrade's logging system for GMX integration.

Handles a subtle Python logging behaviour: when you call
``logger.info("Config: %s", some_dict)``, the dict is stored as
``record.args`` and converted to string *after* filters run.  This
filter converts dicts to strings *before* applying redaction patterns.

Also provides notebook output sanitisation via ``patch_notebook()`` and
reusable helpers ``sanitise_text()`` / ``contains_secret()`` for use by
git hooks and notebook save guards.

.. note::

    ``_sanitise_any`` coerces dicts and lists to ``str`` so that
    redaction patterns can match.  This changes the type of the log
    argument from the original container to a string, which is
    acceptable because the value is only used for string formatting.
"""

import logging
import re
import sys
from typing import Any

from eth_defi.utils import get_url_domain

logger = logging.getLogger(__name__)


# Bare private key pattern: optionally 0x-prefixed, exactly 64 hex chars (256-bit key).
_BARE_HEX_KEY_RE = re.compile(r"\b(?:0x)?[0-9a-fA-F]{64}\b")

# PEM private key blocks
_PEM_KEY_RE = re.compile(
    r"-----BEGIN (?:EC |RSA |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
    r"[\s\S]*?"
    r"-----END (?:EC |RSA |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
    re.MULTILINE,
)

# ed25519 secret format used in this repo
_ED25519_SECRET_RE = re.compile(r"ed25519:[A-Za-z0-9+/=]{32,}")


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
    - bare 0x-prefixed 64-byte hex private keys
    - PEM private key blocks
    - ed25519 secret strings
    """

    # Consolidated patterns using ['\"] to match both Python repr and JSON.
    PATTERNS: list[tuple[str, str]] = [
        # API keys (16+ char strings)
        (r"""['"]apiKey['"]:\s*['"]([^'"]{16,})['"]""", r"'apiKey': '[REDACTED]'"),
        # Secrets (16+ char strings)
        (r"""['"]secret['"]:\s*['"]([^'"]{16,})['"]""", r"'secret': '[REDACTED]'"),
        # Passwords (any non-empty value)
        (r"""['"]password['"]:\s*['"]([^'"]+)['"]""", r"'password': '[REDACTED]'"),
        # JWT secret keys (any non-empty value)
        (r"""['"]jwt_secret_key['"]:\s*['"]([^'"]+)['"]""", r"'jwt_secret_key': '[REDACTED]'"),
        # Private keys (64+ char hex, optionally 0x-prefixed)
        (r"""['"]privateKey['"]:\s*['"](?:0x)?([0-9a-fA-F]{64,})['"]""", r"'privateKey': '[REDACTED]'"),
        (r"""['"]private_key['"]:\s*['"](?:0x)?([0-9a-fA-F]{64,})['"]""", r"'private_key': '[REDACTED]'"),
        # Account IDs (hex addresses, 40+ chars)
        (r"""['"]accountId['"]:\s*['"](0x[0-9a-fA-F]{40,})['"]""", r"'accountId': '[REDACTED]'"),
        # Wallet addresses (hex, 40+ chars)
        (r"""['"]walletAddress['"]:\s*['"](0x[0-9a-fA-F]{40,})['"]""", r"'walletAddress': '[REDACTED]'"),
        # Signatures (64+ char hex)
        (r"""['"]signature['"]:\s*['"](0x[0-9a-fA-F]{64,})['"]""", r"'signature': '[REDACTED]'"),
        # Bare hex private keys preceded by context keywords
        (r"(?:key|private|secret)\W*(?:0x)?([0-9a-fA-F]{64,})\b", "[REDACTED_HEX]"),
    ]

    # Matches any http/https/ws/wss URL — used by _redact_urls()
    _URL_PATTERN = re.compile(r"(?:https?|wss?)://[^\s'\"<>]+")

    def __init__(self, name: str = ""):
        super().__init__(name)
        self._compiled_patterns = [(re.compile(pattern, re.IGNORECASE), replacement) for pattern, replacement in self.PATTERNS]

    def _sanitise(self, text: str) -> str:
        """Apply all redaction patterns to the text, then redact URLs."""
        for pattern, replacement in self._compiled_patterns:
            text = pattern.sub(replacement, text)
        text = self._redact_urls(text)
        text = _BARE_HEX_KEY_RE.sub("[REDACTED-KEY]", text)
        text = _PEM_KEY_RE.sub("[REDACTED-PEM-KEY]", text)
        text = _ED25519_SECRET_RE.sub("[REDACTED-ED25519]", text)
        return text

    _sanitize = _sanitise

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter and sanitise the log record message."""
        if record.msg and isinstance(record.msg, str):
            record.msg = self._sanitise(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                msg = record.msg if record.msg else ""
                if "%s" in msg and "%(" not in msg:
                    record.args = (self._sanitise(str(record.args)),)
                else:
                    record.args = {k: self._sanitise_any(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._sanitise_any(arg) for arg in record.args)

        if record.exc_text and isinstance(record.exc_text, str):
            record.exc_text = self._sanitise(record.exc_text)

        return True

    def _sanitise_any(self, value: Any) -> str | Any:
        """Sanitise any value, converting non-strings to string first."""
        if isinstance(value, str):
            return self._sanitise(value)
        elif isinstance(value, (dict, list)):
            return self._sanitise(str(value))
        return value

    def _redact_urls(self, text: str) -> str:
        """Replace all URLs with their domain-only form."""

        def _replace(match: re.Match) -> str:
            try:
                return get_url_domain(match.group(0))
            except Exception:
                return "[REDACTED_URL]"

        return self._URL_PATTERN.sub(_replace, text)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def sanitise_text(text: str, compiled_patterns: list | None = None) -> str:
    """Apply all redaction patterns to a string."""
    if compiled_patterns is None:
        compiled_patterns = _get_default_compiled_patterns()

    for pattern, replacement in compiled_patterns:
        text = pattern.sub(replacement, text)

    url_re = re.compile(r"(?:https?|wss?)://[^\s'\"<>]+")

    def _replace_url(match: re.Match) -> str:
        try:
            return get_url_domain(match.group(0))
        except Exception:
            return "[REDACTED_URL]"

    text = url_re.sub(_replace_url, text)
    text = _BARE_HEX_KEY_RE.sub("[REDACTED-KEY]", text)
    text = _PEM_KEY_RE.sub("[REDACTED-PEM-KEY]", text)
    text = _ED25519_SECRET_RE.sub("[REDACTED-ED25519]", text)
    return text


sanitize_text = sanitise_text


def contains_secret(text: str) -> bool:
    """Return True if *text* contains anything that looks like a secret."""
    if _BARE_HEX_KEY_RE.search(text):
        return True
    if _PEM_KEY_RE.search(text):
        return True
    if _ED25519_SECRET_RE.search(text):
        return True
    for pattern, _ in _get_default_compiled_patterns():
        if pattern.search(text):
            return True
    return False


def sanitise_mime_bundle(data: dict[str, Any]) -> dict[str, Any]:
    """Sanitise a Jupyter MIME bundle dict."""
    compiled = _get_default_compiled_patterns()
    result = {}
    for mime_type, value in data.items():
        if isinstance(value, str):
            result[mime_type] = sanitise_text(value, compiled)
        elif isinstance(value, list):
            result[mime_type] = [sanitise_text(item, compiled) if isinstance(item, str) else item for item in value]
        else:
            result[mime_type] = value
    return result


sanitize_mime_bundle = sanitise_mime_bundle


# ---------------------------------------------------------------------------
# Notebook runtime patching
# ---------------------------------------------------------------------------

_NOTEBOOK_PATCHED = False
_ORIGINAL_STDOUT_WRITE = None
_ORIGINAL_STDERR_WRITE = None
_ORIGINAL_WRITE_FORMAT_DATA = None
_ORIGINAL_PUBLISH_DISPLAY_DATA = None
_ORIGINAL_SHOWTRACEBACK = None


def patch_notebook() -> None:
    """Extend sensitive data filtering to all Jupyter/IPython output channels.

    Safe to call multiple times. No-op if IPython is not running.
    """
    global _NOTEBOOK_PATCHED
    global _ORIGINAL_STDOUT_WRITE, _ORIGINAL_STDERR_WRITE
    global _ORIGINAL_WRITE_FORMAT_DATA, _ORIGINAL_PUBLISH_DISPLAY_DATA
    global _ORIGINAL_SHOWTRACEBACK

    if _NOTEBOOK_PATCHED:
        return

    try:
        from IPython import get_ipython

        ipython = get_ipython()
        if ipython is None:
            return
    except ImportError:
        return

    compiled = _get_default_compiled_patterns()

    _ORIGINAL_STDOUT_WRITE = sys.stdout.write
    _ORIGINAL_STDERR_WRITE = sys.stderr.write

    def _sanitised_stdout_write(text):
        if isinstance(text, str):
            text = sanitise_text(text, compiled)
        return _ORIGINAL_STDOUT_WRITE(text)

    def _sanitised_stderr_write(text):
        if isinstance(text, str):
            text = sanitise_text(text, compiled)
        return _ORIGINAL_STDERR_WRITE(text)

    sys.stdout.write = _sanitised_stdout_write
    sys.stderr.write = _sanitised_stderr_write

    if hasattr(ipython, "displayhook") and hasattr(ipython.displayhook, "write_format_data"):
        _ORIGINAL_WRITE_FORMAT_DATA = ipython.displayhook.write_format_data

        def _sanitised_write_format_data(format_dict, md_dict=None):
            format_dict = sanitise_mime_bundle(format_dict)
            return _ORIGINAL_WRITE_FORMAT_DATA(format_dict, md_dict)

        ipython.displayhook.write_format_data = _sanitised_write_format_data

    try:
        import IPython.core.display_functions as _display_mod

        _ORIGINAL_PUBLISH_DISPLAY_DATA = _display_mod.publish_display_data

        def _sanitised_publish_display_data(data, metadata=None, *, transient=None, **kwargs):
            if isinstance(data, dict):
                data = sanitise_mime_bundle(data)
            return _ORIGINAL_PUBLISH_DISPLAY_DATA(data, metadata, transient=transient, **kwargs)

        _display_mod.publish_display_data = _sanitised_publish_display_data
    except (ImportError, AttributeError):
        pass

    if hasattr(ipython, "showtraceback"):
        _ORIGINAL_SHOWTRACEBACK = ipython.showtraceback

        def _sanitised_showtraceback(*args, **kwargs):
            import io

            buf = io.StringIO()
            old_stderr = sys.stderr
            sys.stderr = buf
            try:
                _ORIGINAL_SHOWTRACEBACK(*args, **kwargs)
            finally:
                sys.stderr = old_stderr
            sanitised = sanitise_text(buf.getvalue(), compiled)
            if sanitised:
                old_stderr.write(sanitised)

        ipython.showtraceback = _sanitised_showtraceback

    _NOTEBOOK_PATCHED = True


def unpatch_notebook() -> None:
    """Remove all notebook output patches."""
    global _NOTEBOOK_PATCHED
    global _ORIGINAL_STDOUT_WRITE, _ORIGINAL_STDERR_WRITE
    global _ORIGINAL_WRITE_FORMAT_DATA, _ORIGINAL_PUBLISH_DISPLAY_DATA
    global _ORIGINAL_SHOWTRACEBACK

    if not _NOTEBOOK_PATCHED:
        return

    if _ORIGINAL_STDOUT_WRITE is not None:
        sys.stdout.write = _ORIGINAL_STDOUT_WRITE
    if _ORIGINAL_STDERR_WRITE is not None:
        sys.stderr.write = _ORIGINAL_STDERR_WRITE

    try:
        from IPython import get_ipython

        ipython = get_ipython()
        if ipython is not None:
            if _ORIGINAL_WRITE_FORMAT_DATA is not None:
                ipython.displayhook.write_format_data = _ORIGINAL_WRITE_FORMAT_DATA
            if _ORIGINAL_SHOWTRACEBACK is not None:
                ipython.showtraceback = _ORIGINAL_SHOWTRACEBACK
    except ImportError:
        pass

    if _ORIGINAL_PUBLISH_DISPLAY_DATA is not None:
        try:
            import IPython.core.display_functions as _display_mod

            _display_mod.publish_display_data = _ORIGINAL_PUBLISH_DISPLAY_DATA
        except (ImportError, AttributeError):
            pass

    _NOTEBOOK_PATCHED = False
    _ORIGINAL_STDOUT_WRITE = None
    _ORIGINAL_STDERR_WRITE = None
    _ORIGINAL_WRITE_FORMAT_DATA = None
    _ORIGINAL_PUBLISH_DISPLAY_DATA = None
    _ORIGINAL_SHOWTRACEBACK = None


def is_notebook_patched() -> bool:
    """Check if notebook output channels have been patched."""
    return _NOTEBOOK_PATCHED


# ---------------------------------------------------------------------------
# Logging monkeypatch
# ---------------------------------------------------------------------------

_LOGGING_PATCHED = False
_SENSITIVE_FILTER = None
_ORIGINAL_HANDLER_INIT = None
_ORIGINAL_RECORD_FACTORY = None
_ORIGINAL_FORMAT_EXCEPTION = None


def patch_logging():
    """Add :class:`SensitiveDataFilter` to all existing and future log handlers.

    Also installs:

    - A custom ``LogRecordFactory`` for pre-handler sanitisation of msg/args
    - A ``Formatter.formatException`` wrapper so ``exc_text`` is sanitised
      when generated (closes the RPC buffer blind spot)

    Safe to call multiple times — only applies once.
    """
    global _LOGGING_PATCHED, _SENSITIVE_FILTER, _ORIGINAL_HANDLER_INIT
    global _ORIGINAL_RECORD_FACTORY, _ORIGINAL_FORMAT_EXCEPTION

    if _LOGGING_PATCHED:
        return

    _SENSITIVE_FILTER = SensitiveDataFilter()
    compiled = _get_default_compiled_patterns()

    for handler in logging.root.handlers:
        handler.addFilter(_SENSITIVE_FILTER)

    _ORIGINAL_HANDLER_INIT = logging.Handler.__init__

    def patched_handler_init(self, level=logging.NOTSET):
        _ORIGINAL_HANDLER_INIT(self, level)
        if _SENSITIVE_FILTER is not None:
            self.addFilter(_SENSITIVE_FILTER)

    logging.Handler.__init__ = patched_handler_init

    _ORIGINAL_RECORD_FACTORY = logging.getLogRecordFactory()
    _install_sanitising_record_factory(_ORIGINAL_RECORD_FACTORY)

    _ORIGINAL_FORMAT_EXCEPTION = logging.Formatter.formatException

    def sanitised_format_exception(self, ei):
        text = _ORIGINAL_FORMAT_EXCEPTION(self, ei)
        return sanitise_text(text, compiled)

    logging.Formatter.formatException = sanitised_format_exception

    _LOGGING_PATCHED = True


def unpatch_logging():
    """Remove the logging monkeypatch.  Mainly useful for testing."""
    global _LOGGING_PATCHED, _SENSITIVE_FILTER, _ORIGINAL_HANDLER_INIT
    global _ORIGINAL_RECORD_FACTORY, _ORIGINAL_FORMAT_EXCEPTION

    if not _LOGGING_PATCHED:
        return

    if _ORIGINAL_HANDLER_INIT is not None:
        logging.Handler.__init__ = _ORIGINAL_HANDLER_INIT

    if _ORIGINAL_RECORD_FACTORY is not None:
        logging.setLogRecordFactory(_ORIGINAL_RECORD_FACTORY)

    if _ORIGINAL_FORMAT_EXCEPTION is not None:
        logging.Formatter.formatException = _ORIGINAL_FORMAT_EXCEPTION

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
    _ORIGINAL_RECORD_FACTORY = None
    _ORIGINAL_FORMAT_EXCEPTION = None


def is_logging_patched() -> bool:
    """Check if logging has been patched with :class:`SensitiveDataFilter`."""
    return _LOGGING_PATCHED


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DEFAULT_COMPILED_PATTERNS: list | None = None


def _get_default_compiled_patterns() -> list:
    global _DEFAULT_COMPILED_PATTERNS
    if _DEFAULT_COMPILED_PATTERNS is None:
        _DEFAULT_COMPILED_PATTERNS = [(re.compile(pattern, re.IGNORECASE), replacement) for pattern, replacement in SensitiveDataFilter.PATTERNS]
    return _DEFAULT_COMPILED_PATTERNS


def _sanitise_any(value: Any, compiled_patterns: list) -> str | Any:
    if isinstance(value, str):
        return sanitise_text(value, compiled_patterns)
    elif isinstance(value, (dict, list)):
        return sanitise_text(str(value), compiled_patterns)
    return value


def _install_sanitising_record_factory(original_factory) -> None:
    compiled = _get_default_compiled_patterns()

    def sanitising_factory(*args, **kwargs):
        record = original_factory(*args, **kwargs)
        if record.msg and isinstance(record.msg, str):
            record.msg = sanitise_text(record.msg, compiled)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _sanitise_any(v, compiled) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_sanitise_any(a, compiled) for a in record.args)
        if record.exc_text and isinstance(record.exc_text, str):
            record.exc_text = sanitise_text(record.exc_text, compiled)
        return record

    logging.setLogRecordFactory(sanitising_factory)
