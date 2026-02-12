"""Tests for SensitiveDataFilter logging monkeypatch.

Verifies that after calling :func:`patch_logging` (the entrypoint),
sensitive data does not leak into log output, while non-sensitive data
passes through unchanged.
"""

import logging

import pytest

from eth_defi.gmx.freqtrade.sensitive_filter import (
    SensitiveDataFilter,
    is_logging_patched,
    patch_logging,
    unpatch_logging,
)

# ---------------------------------------------------------------------------
# Realistic fake sensitive values
# ---------------------------------------------------------------------------

FAKE_API_KEY = "aBcDeFgHiJkLmNoP1234"  # 20 chars, >16 threshold
FAKE_SECRET = "sEcReTvAlUeThAtIsLong1234"  # 25 chars, >16 threshold
FAKE_PASSWORD = "p@ssw0rd"
FAKE_PRIVATE_KEY = "0x" + "a1b2c3d4e5f6" * 11 + "ab"  # 0x + 68 hex
FAKE_WALLET_ADDR = "0x" + "deadbeefcafe1234" * 3  # 0x + 48 hex
FAKE_INFURA_URL = "https://mainnet.infura.io/v3/abc123secretkey456def"


# =========================================================================
# Entrypoint integration tests — the actual contract
# =========================================================================


def test_patch_redacts_api_key_from_dict_log(patched_logging, caplog):
    """After patch_logging(), apiKey in a dict arg must not appear in output."""
    logger = logging.getLogger("test.sensitive.apikey")
    config = {"apiKey": FAKE_API_KEY, "exchange": "gmx"}

    with caplog.at_level(logging.INFO):
        logger.info("Config: %s", config)

    assert FAKE_API_KEY not in caplog.text
    assert "gmx" in caplog.text


def test_patch_redacts_multiple_secrets(patched_logging, caplog):
    """All sensitive fields in a single log call are redacted."""
    logger = logging.getLogger("test.sensitive.multi")
    config = {
        "apiKey": FAKE_API_KEY,
        "secret": FAKE_SECRET,
        "password": FAKE_PASSWORD,
        "privateKey": FAKE_PRIVATE_KEY,
    }

    with caplog.at_level(logging.INFO):
        logger.info("Full config: %s", config)

    assert FAKE_API_KEY not in caplog.text
    assert FAKE_SECRET not in caplog.text
    assert FAKE_PASSWORD not in caplog.text
    assert FAKE_PRIVATE_KEY not in caplog.text


def test_patch_redacts_private_key_in_dict(patched_logging, caplog):
    """A private key logged as a dict value is redacted."""
    logger = logging.getLogger("test.sensitive.privkey")
    wallet = {"private_key": FAKE_PRIVATE_KEY, "chain": "arbitrum"}

    with caplog.at_level(logging.INFO):
        logger.info("Wallet: %s", wallet)

    assert FAKE_PRIVATE_KEY not in caplog.text
    assert "arbitrum" in caplog.text


def test_patch_redacts_url_path_key(patched_logging, caplog):
    """Infura-style URL path (containing API key) is stripped, domain preserved."""
    logger = logging.getLogger("test.sensitive.url")

    with caplog.at_level(logging.INFO):
        logger.info("Connecting to %s", FAKE_INFURA_URL)

    assert "abc123secretkey456def" not in caplog.text
    assert "mainnet.infura.io" in caplog.text


def test_patch_redacts_url_with_credentials(patched_logging, caplog):
    """user:pass@host credentials in URLs are stripped."""
    logger = logging.getLogger("test.sensitive.urlcreds")
    # fancy string
    url = "https://myuser:mypassword@rpc.example.com/v1/key123"

    with caplog.at_level(logging.INFO):
        logger.info("RPC: %s", url)

    assert "myuser" not in caplog.text
    assert "mypassword" not in caplog.text
    assert "rpc.example.com" in caplog.text


def test_patch_preserves_non_sensitive_data(patched_logging, caplog):
    """Normal trading data passes through unchanged."""
    logger = logging.getLogger("test.sensitive.safe")

    with caplog.at_level(logging.INFO):
        logger.info("Trading %s on %s, amount=%s", "ETH/USDC", "gmx", "1.5")

    assert "ETH/USDC" in caplog.text
    assert "gmx" in caplog.text
    assert "1.5" in caplog.text


def test_patch_redacts_wallet_address(patched_logging, caplog):
    """walletAddress hex value is redacted."""
    logger = logging.getLogger("test.sensitive.wallet")
    data = {"walletAddress": FAKE_WALLET_ADDR, "chain": "arbitrum"}

    with caplog.at_level(logging.INFO):
        logger.info("Account: %s", data)

    assert FAKE_WALLET_ADDR not in caplog.text
    assert "arbitrum" in caplog.text


def test_patch_redacts_at_debug_level(patched_logging, caplog):
    """Sensitive data is redacted even at DEBUG log level."""
    logger = logging.getLogger("test.sensitive.debug")
    config = {
        "apiKey": FAKE_API_KEY,
        "secret": FAKE_SECRET,
        "password": FAKE_PASSWORD,
        "privateKey": FAKE_PRIVATE_KEY,
        "walletAddress": FAKE_WALLET_ADDR,
    }

    with caplog.at_level(logging.DEBUG):
        logger.debug("Debug config dump: %s", config)
        logger.debug("Connecting to %s", FAKE_INFURA_URL)

    assert FAKE_API_KEY not in caplog.text
    assert FAKE_SECRET not in caplog.text
    assert FAKE_PASSWORD not in caplog.text
    assert FAKE_PRIVATE_KEY not in caplog.text
    assert FAKE_WALLET_ADDR not in caplog.text
    assert "abc123secretkey456def" not in caplog.text
    assert "mainnet.infura.io" in caplog.text


def test_short_values_not_redacted(patched_logging, caplog):
    """apiKey/secret values shorter than 16 chars are NOT redacted."""
    logger = logging.getLogger("test.sensitive.short")
    config = {"apiKey": "shortKey", "secret": "shortScr"}

    with caplog.at_level(logging.INFO):
        logger.info("Config: %s", config)

    assert "shortKey" in caplog.text
    assert "shortScr" in caplog.text


# =========================================================================
# Entrypoint lifecycle tests
# =========================================================================


def test_patch_is_idempotent():
    """Calling patch_logging multiple times adds only one filter per handler."""
    unpatch_logging()
    try:
        patch_logging()
        patch_logging()
        patch_logging()

        for handler in logging.root.handlers:
            count = sum(1 for f in handler.filters if isinstance(f, SensitiveDataFilter))
            assert count == 1, f"Expected 1 filter on {handler}, got {count}"
    finally:
        unpatch_logging()


def test_unpatch_removes_filter():
    """After unpatch_logging(), no handlers carry the filter."""
    unpatch_logging()
    patch_logging()
    assert is_logging_patched()

    unpatch_logging()
    assert not is_logging_patched()

    for handler in logging.root.handlers:
        filters = [f for f in handler.filters if isinstance(f, SensitiveDataFilter)]
        assert len(filters) == 0


def test_patch_affects_future_handlers():
    """Handlers created after patch_logging() also get the filter."""
    unpatch_logging()
    try:
        patch_logging()
        new_handler = logging.StreamHandler()
        filters = [f for f in new_handler.filters if isinstance(f, SensitiveDataFilter)]
        assert len(filters) == 1
    finally:
        unpatch_logging()


def test_unpatch_stops_affecting_future_handlers():
    """Handlers created after unpatch_logging() do NOT get the filter."""
    unpatch_logging()
    patch_logging()
    unpatch_logging()

    new_handler = logging.StreamHandler()
    filters = [f for f in new_handler.filters if isinstance(f, SensitiveDataFilter)]
    assert len(filters) == 0
