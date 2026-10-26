"""Regression coverage for RPC credentials in real diagnostic output."""

import html
import io
import json
import logging
import re
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from requests import ConnectionError as RequestsConnectionError
from requests import Request

from eth_defi import rpc_url
from eth_defi.compat import create_http_provider
from eth_defi.gmx.freqtrade.sensitive_filter import patch_logging, unpatch_logging
from eth_defi.provider import anvil
from eth_defi.provider.multi_provider import MultiProviderConfigurationError, create_multi_provider_web3
from eth_defi.utils import get_url_domain, setup_console_logging, shutdown_hard

RPC = "https://rpc-user:rpc-password@rpc.example:8545/v3/path-secret?key=query-secret#fragment-secret"
DOMAIN = "rpc.example:8545"
LINK = "https://docs.example/guide?section=rpc"


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep synthetic endpoints and record factories isolated between tests.

    The production registry intentionally retains endpoints for late diagnostics.

    :param monkeypatch: Pytest's reversible attribute patching.
    :return: Yield control with an empty endpoint registry.
    """
    factory = logging.getLogRecordFactory()
    monkeypatch.setattr(rpc_url, "_endpoints", set())
    monkeypatch.setattr(rpc_url, "_url_rules", (None, {}))
    monkeypatch.setattr(rpc_url, "_target_rules", (None, {}))
    monkeypatch.setattr(rpc_url, "_installed_factory", None)
    try:
        yield
    finally:
        logging.setLogRecordFactory(factory)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (RPC, DOMAIN),
        ("mev+" + RPC, DOMAIN),
        ("wss://user:pass@socket.example:8443/key?secret=yes", "socket.example:8443"),
        ("ws://socket.example:80/key", "socket.example"),
        ("https://rpc.example:443/path", "rpc.example"),
        ("http://localhost:8545", "localhost:8545"),
        ("http://[::1]:8545/key", "[::1]:8545"),
        ("https://[::1]:443/key", "[::1]"),
        ("https://rpc.example:bad/secret", "[redacted RPC URL]"),
        ("https://[broken/secret", "[redacted RPC URL]"),
        ("not-a-url", "[redacted RPC URL]"),
        ("mev+https://primary.example/key https://backup.example/token", "primary.example, backup.example"),
    ],
)
def test_url_domain(url: str, expected: str) -> None:
    """Only the host and non-default port survive endpoint display."""
    assert get_url_domain(url) == expected


def test_redact_variants_and_preserve_links() -> None:
    """Fallbacks, escaped URLs and punctuation redact without damaging links."""
    second = "https://backup.example/v2/backup-secret"
    rpc_url.register_rpc_url(f"mev+{RPC} {second}")
    variants = [RPC, "mev+" + RPC, RPC.replace("/", r"\/"), json.dumps(RPC)[1:-1], html.escape(RPC), quote(RPC, safe="")]
    for variant in variants:
        assert rpc_url.redact_rpc_urls(f"Endpoint '{variant}'. {LINK}") == f"Endpoint '{DOMAIN}'. {LINK}"
    assert rpc_url.redact_rpc_urls(f"{second}. Next") == "backup.example. Next"
    # A different resource on the same host is still a non-RPC link.
    other = second + "/documentation"
    assert rpc_url.redact_rpc_urls(other) == other


def test_transport_request_targets(caplog: pytest.LogCaptureFixture) -> None:
    """urllib3 path-only debug logs and exceptions cannot reveal path keys."""
    rpc_url.register_rpc_url(RPC)
    target = "/v3/path-secret?key=query-secret"
    logger = logging.getLogger("urllib3.connectionpool")
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        logger.debug('"POST %s HTTP/1.1" 200', target)
        logging.getLogger("eth_defi.provider.test").warning("%s", RequestsConnectionError(f"HTTPSConnectionPool(host='rpc.example', port=8545): Max retries exceeded with url: {target}"))
    assert "path-secret" not in caplog.text
    assert "query-secret" not in caplog.text
    assert "rpc.example" in caplog.text
    assert rpc_url.redact_rpc_urls(LINK) == LINK
    assert rpc_url.redact_rpc_urls(f"https://docs.example{target}") == f"https://docs.example{target}"


def test_prepared_request_urls() -> None:
    """Transport normalisation cannot bypass registered endpoint redaction."""
    rpc_url.register_rpc_url(RPC)
    prepared = Request("POST", RPC).prepare()
    for value in (prepared.url, prepared.url.replace("rpc-user:rpc-password@", ""), prepared.url.split("#")[0]):
        assert rpc_url.redact_rpc_urls(value) == DOMAIN


@pytest.mark.parametrize("colour", [False, True])
def test_logging_output_and_chained_tracebacks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, colour: bool) -> None:
    """Console and file handlers redact containers, objects and exception chains."""
    monkeypatch.setenv("JSON_RPC_REDACTION_TEST", RPC)
    monkeypatch.setenv("NO_COLOR", "" if colour else "1")
    monkeypatch.setenv("FORCE_COLOR", "1" if colour else "0")
    stream = io.StringIO()
    file = tmp_path / "rpc.log"
    root = setup_console_logging(default_log_level="debug", stream=stream, log_file=file)
    logger = logging.getLogger("rpc-output-test")
    provider = create_http_provider(RPC)
    logger.info("Dictionary %s; list %s; provider %s; number %.2f; link %s", {"rpc": RPC}, [RPC], provider, 1.25, LINK)
    logger.info("Mapping %(rpc)s %(number)d", {"rpc": RPC, "number": 42})
    try:
        try:
            raise RequestsConnectionError(f"Failed to connect to {RPC}")
        except RequestsConnectionError as cause:
            raise RuntimeError(f"RPC failed: {RPC}") from cause
    except RuntimeError:
        logger.exception("Logged chain %s", RPC)
    for handler in root.handlers:
        handler.flush()
    for raw_text in (stream.getvalue(), file.read_text()):
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw_text)
        for secret in ("rpc-user", "rpc-password", "path-secret", "query-secret", "fragment-secret"):
            assert secret not in text
        assert DOMAIN in text
        assert LINK in text
        assert "1.25" in text
        assert "42" in text
        assert "ConnectionError" in text
        assert "RuntimeError" in text
    assert provider.endpoint_uri == RPC


def test_existing_and_future_handlers_and_cached_tracebacks() -> None:
    """Record creation protects independent handlers and preformatted exceptions."""
    stream = io.StringIO()
    logger = logging.getLogger("independent-rpc-test")
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        rpc_url.register_rpc_url(RPC)
        logger.warning("Existing handler %s", RPC)
        record = logging.LogRecord(logger.name, logging.WARNING, __file__, 1, "Cached %s", (RPC,), None)
        record.exc_text = f"ConnectionError: {RPC}"
        record.endpoint = RPC
        handler.addFilter(rpc_url.RPCURLFilter())
        handler.setFormatter(logging.Formatter("%(message)s %(endpoint)s"))
        handler.handle(record)
        assert RPC not in stream.getvalue()
        assert "path-secret" not in stream.getvalue()
        assert record.endpoint == DOMAIN
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_registration_is_thread_safe_and_preserves_factory() -> None:
    """Concurrent registration keeps every endpoint and chains custom factories."""
    original = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        """Add an application-specific field to each record."""
        record = original(*args, **kwargs)
        record.application_field = "retained"
        return record

    logging.setLogRecordFactory(factory)
    endpoints = [f"https://rpc{i}.example/v3/secret-{i}" for i in range(20)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        tuple(pool.map(rpc_url.register_rpc_url, endpoints))
    installed = logging.getLogRecordFactory()
    rpc_url.install_rpc_logging()
    assert logging.getLogRecordFactory() is installed
    record = logging.getLogger("rpc-factory-test").makeRecord("rpc-factory-test", logging.WARNING, __file__, 1, "RPC %s", (endpoints,), None)
    assert record.application_field == "retained"
    assert "secret-" not in record.getMessage()
    assert "rpc19.example" in record.getMessage()


@pytest.mark.parametrize("configuration", [f"{RPC} {RPC}", f"mev+{RPC}", "https://[broken/path-secret", f"mev+{RPC} mev+https://backup.example/key"])
def test_configuration_errors_are_safe(configuration: str) -> None:
    """Invalid configurations fail without exposing their endpoint strings."""
    with pytest.raises(MultiProviderConfigurationError) as caught:
        create_multi_provider_web3(configuration)
    assert "path-secret" not in str(caught.value)
    assert "query-secret" not in str(caught.value)


def test_archive_failure_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Archive preflight exceptions retain operational details without credentials."""

    def fail(*_args: Any, **_kwargs: Any) -> None:
        """Simulate a transport error which repeats the upstream URL."""
        raise RequestsConnectionError(f"HTTP request failed: {RPC}")

    monkeypatch.setattr(anvil.requests, "post", fail)
    with pytest.raises(anvil.ArchiveNodeRequired) as caught:
        anvil._verify_archive_node_access(None, RPC, 100, 200)
    assert RPC not in str(caught.value)
    assert "path-secret" not in str(caught.value)
    assert DOMAIN in str(caught.value)
    assert caught.value.rpc_url == RPC


@pytest.mark.parametrize("inherit_stdio", [False, True])
def test_anvil_subprocess_output(tmp_path: Path, capfd: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture, *, inherit_stdio: bool) -> None:
    """A real child receives the full URL but emits only safe diagnostics."""
    executable = tmp_path / "fake-anvil"
    executable.write_text(f"#!{sys.executable}\nimport sys\nprint(sys.argv[2], flush=True)\nprint(sys.argv[2], file=sys.stderr, flush=True)\n")
    executable.chmod(0o755)
    with caplog.at_level(logging.INFO):
        process, command = anvil._launch(str(executable), fork=RPC, inherit_stdio=inherit_stdio)
        assert command[-1] == RPC
        process.wait(timeout=10)
        stdout, stderr = shutdown_hard(process, log_level=logging.INFO, block=False)
    output = capfd.readouterr()
    assert "path-secret" not in caplog.text + output.out + output.err
    assert DOMAIN in caplog.text
    if inherit_stdio:
        assert DOMAIN in output.out
        assert DOMAIN in output.err
        assert (stdout, stderr) == (b"", b"")
    else:
        # Raw data remains available to programmatic consumers.
        assert RPC.encode() in stdout
        assert RPC.encode() in stderr


def test_gmx_filter_coexists(caplog: pytest.LogCaptureFixture) -> None:
    """The broader GMX policy remains in effect alongside RPC-only redaction."""
    rpc_url.register_rpc_url(RPC)
    patch_logging()
    try:
        rpc_url.register_rpc_url("https://backup.example/key")
        logging.getLogger("gmx-coexistence").warning("Endpoint %s; config %s", RPC, {"password": "another-secret"})
        assert "path-secret" not in caplog.text
        assert "another-secret" not in caplog.text
        assert DOMAIN in caplog.text
    finally:
        unpatch_logging()
