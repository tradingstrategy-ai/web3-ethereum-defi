"""Credential-free RPC diagnostics.

Provider constructors register their endpoints before making requests. This also
protects dependency logs and logged exception chains, without shortening links
to unrelated services. Call :func:`register_rpc_url` before using an externally
constructed provider. Registration is process-local and safe across threads.
"""

import html
import json
import logging
import os
import re
import threading
from typing import Any
from urllib.parse import quote, urlsplit

_lock = threading.RLock()
_endpoints: set[str] = set()
_url_rules: tuple[re.Pattern[str] | None, dict[str, str]] = (None, {})
_target_rules: tuple[re.Pattern[str] | None, dict[str, str]] = (None, {})
_installed_factory = None


def get_url_domain(url: str) -> str:
    """Display a URL as its hostname and optional non-default port.

    Credentials, paths, query parameters and fragments are discarded. Invalid
    URLs use a fixed placeholder rather than echoing the invalid input.

    :param url: An endpoint URL or fallback configuration, including ``mev+``.
    :return: Hostname, with a non-default port when supplied.
    """
    endpoints = url.split()
    if len(endpoints) > 1:
        return ", ".join(get_url_domain(endpoint) for endpoint in endpoints)
    try:
        parsed = urlsplit(url.removeprefix("mev+"))
        host = parsed.hostname
        if not host:
            return "[redacted RPC URL]"
        if ":" in host:
            host = f"[{host}]"
        return host if parsed.port in {80, 443, None} else f"{host}:{parsed.port}"
    except ValueError:
        return "[redacted RPC URL]"


def register_rpc_url(configuration_line: str) -> None:
    """Register endpoints whose diagnostic representations must be shortened.

    Registration installs logging protection before any provider network reads.
    Fallback configurations and ``mev+`` endpoints are supported. The original
    strings remain available to callers for real network requests.

    :param configuration_line: A URL or space-separated RPC configuration.
    :return: ``None``.
    """
    global _url_rules, _target_rules  # noqa: PLW0603 - publish immutable process-local registry snapshots
    with _lock:
        additions = set(configuration_line.split()) - _endpoints
        if not additions:
            install_rpc_logging()
            return
        _endpoints.update(additions)
        replacements = dict(_url_rules[1])
        targets = dict(_target_rules[1])
        for endpoint in additions:
            clean = endpoint.removeprefix("mev+")
            domain = get_url_domain(clean)
            if domain == "[redacted RPC URL]" and not any(marker in clean for marker in (":", "/", "?", "@")):
                continue
            values = {endpoint, clean}
            try:
                parsed = urlsplit(clean)
                # HTTP transports remove fragments and may remove userinfo
                # or percent-encode paths before including URLs in errors.
                authority = parsed.netloc.rsplit("@", 1)[-1]
                values.add(parsed._replace(netloc=authority).geturl())
                values.add(parsed._replace(fragment="").geturl())
                values.add(parsed._replace(netloc=authority, fragment="").geturl())
                values.add(parsed._replace(netloc=authority, path=quote(parsed.path, safe="/%:@"), query=quote(parsed.query, safe="=&?/%:@+"), fragment="").geturl())
                target = parsed.path or "/"
                if parsed.query:
                    target += "?" + parsed.query
                if target != "/":
                    for request_target in (target, quote(target, safe="/=&?%:@+")):
                        # A path shared by several providers has no unique
                        # domain. The connection-pool context retains it.
                        prior = targets.get(request_target, domain)
                        targets[request_target] = domain if prior == domain else "[redacted RPC path]"
                    # requests/urllib3 exceptions sometimes omit the scheme.
                    replacements[parsed.netloc + target] = domain
                    replacements[authority + target] = domain
            except ValueError:
                pass
            for value in values:
                for variant in (value, json.dumps(value)[1:-1], value.replace("/", r"\/"), html.escape(value), quote(value, safe="")):
                    replacements[variant] = domain
        # One scan per record rather than one scan per configured endpoint.
        url_pattern = target_pattern = None
        if replacements:
            url_pattern = re.compile("(?:" + "|".join(re.escape(value) for value in sorted(replacements, key=len, reverse=True)) + r")(?![\w~%/?#&=+-]|\.[\w])")
        if targets:
            target_pattern = re.compile(r"(?<![\w/:])(?:" + "|".join(re.escape(value) for value in sorted(targets, key=len, reverse=True)) + r")(?![\w~%/?#&=+-]|\.[\w])")
        _url_rules = (url_pattern, replacements)
        _target_rules = (target_pattern, targets)
        install_rpc_logging()


def redact_rpc_urls(text: str) -> str:
    """Shorten registered RPC URLs in diagnostic text.

    Other URLs are preserved. Request targets in urllib3-style exception text
    are also shortened, even when the transport omitted the URL's scheme.

    :param text: Diagnostic text, never a value used for network requests.
    :return: Sanitised text.
    """
    return _redact(text, transport="with url:" in text or "ConnectionPool(" in text)


def _redact(text: str, *, transport: bool = False) -> str:
    """Apply the immutable registry snapshot to one diagnostic string.

    Only transport diagnostics receive path-only redaction.

    :param text: Rendered diagnostic text.
    :param transport: Whether this is a transport log or exception.
    :return: Sanitised text.
    """
    transport = transport or "with url:" in text or "ConnectionPool(" in text
    pattern, replacements = _url_rules
    if pattern:
        text = pattern.sub(lambda match: replacements[match.group(0)], text)
    pattern, targets = _target_rules
    if transport and pattern:
        text = pattern.sub(lambda match: targets[match.group(0)], text)
    return text


def _sanitise_record(record: logging.LogRecord) -> None:
    """Sanitise formatted arguments and exception chains before handlers run.

    Rendering first handles mappings, containers and exception objects without
    changing numeric format arguments or the original exception instance.

    :param record: The logging record to sanitise.
    :return: ``None``.
    """
    transport = record.name.startswith(("urllib3", "requests", "web3", "httpx", "httpcore"))
    record.msg = _redact(record.getMessage(), transport=transport)
    record.args = ()
    if record.exc_info and not record.exc_text:
        record.exc_text = logging.Formatter().formatException(record.exc_info)
    if record.exc_text:
        record.exc_text = redact_rpc_urls(record.exc_text)
    if record.stack_info:
        record.stack_info = redact_rpc_urls(record.stack_info)


class RPCURLFilter(logging.Filter):
    """Protect handler output, including cached exception text and extra fields."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: PLR6301 - logging.Filter callback
        """Sanitise a record immediately before formatting.

        This complements record creation for records received from queues and
        fields added with ``extra`` after the record factory has returned.

        :param record: Record about to be emitted.
        :return: Always ``True``.
        """
        _sanitise_record(record)
        for key, value in vars(record).items():
            if isinstance(value, str):
                setattr(record, key, redact_rpc_urls(value))
        return True


def install_rpc_logging(*, from_environment: bool = False) -> None:
    """Install RPC redaction while preserving an existing log record factory.

    Repeated calls are idempotent. Each worker registers its own providers;
    logging setup can additionally seed endpoints from RPC environment values.

    :param from_environment: Register ``JSON_RPC_*`` and ``*_RPC_URL`` values.
    :return: ``None``.
    """
    global _installed_factory  # noqa: PLW0603 - preserve and chain the active logging factory
    with _lock:
        factory = logging.getLogRecordFactory()
        if getattr(factory, "_eth_defi_rpc_redaction", False):
            _installed_factory = factory
        elif factory is not _installed_factory:

            def rpc_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
                """Chain the application's factory and sanitise its record.

                Preserve application fields while rendering diagnostic arguments
                and exception chains before any handler can emit them.

                :param args: Positional record-factory arguments.
                :param kwargs: Keyword record-factory arguments.
                :return: The sanitised record from the original factory.
                """
                record = factory(*args, **kwargs)
                _sanitise_record(record)
                return record

            rpc_record_factory._eth_defi_rpc_redaction = True
            _installed_factory = rpc_record_factory
            logging.setLogRecordFactory(rpc_record_factory)
        if from_environment:
            for name, value in tuple(os.environ.items()):
                if name.startswith("JSON_RPC_") or name.endswith("_RPC_URL"):
                    register_rpc_url(value)
