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
# Regex bypass attempts
# =========================================================================


def test_bypass_nested_dict(patched_logging, caplog):
    """Secrets inside a nested dict are still redacted."""
    logger = logging.getLogger("test.bypass.nested")
    outer = {"exchange": {"apiKey": FAKE_API_KEY, "secret": FAKE_SECRET}}

    with caplog.at_level(logging.INFO):
        logger.info("Nested: %s", outer)

    assert FAKE_API_KEY not in caplog.text
    assert FAKE_SECRET not in caplog.text


def test_bypass_list_of_dicts(patched_logging, caplog):
    """Secrets inside a list of dicts are still redacted."""
    logger = logging.getLogger("test.bypass.list")
    configs = [{"apiKey": FAKE_API_KEY}, {"secret": FAKE_SECRET}]

    with caplog.at_level(logging.INFO):
        logger.info("Configs: %s", configs)

    assert FAKE_API_KEY not in caplog.text
    assert FAKE_SECRET not in caplog.text


def test_bypass_extra_whitespace(patched_logging, caplog):
    """Extra whitespace between key and value does not bypass redaction."""
    logger = logging.getLogger("test.bypass.whitespace")
    # Manually craft string with extra spaces (simulates unusual repr)
    crafted = "'apiKey':   '" + FAKE_API_KEY + "'"

    with caplog.at_level(logging.INFO):
        logger.info("Spaced: %s", crafted)

    assert FAKE_API_KEY not in caplog.text


def test_bypass_json_double_quotes(patched_logging, caplog):
    """JSON-style double-quoted keys/values are still redacted."""
    logger = logging.getLogger("test.bypass.json")
    import json

    config = {"apiKey": FAKE_API_KEY, "password": FAKE_PASSWORD}

    with caplog.at_level(logging.INFO):
        logger.info("JSON config: %s", json.dumps(config))

    assert FAKE_API_KEY not in caplog.text
    assert FAKE_PASSWORD not in caplog.text


def test_bypass_mixed_quotes(patched_logging, caplog):
    """Mixed quote styles (double key, single value) are still caught."""
    logger = logging.getLogger("test.bypass.mixed")
    # Python repr of a dict uses single quotes, but simulate mixed
    crafted = '"apiKey": \'' + FAKE_API_KEY + "'"

    with caplog.at_level(logging.INFO):
        logger.info("Mixed: %s", crafted)

    assert FAKE_API_KEY not in caplog.text


def test_bypass_multiple_urls_with_keys(patched_logging, caplog):
    """Multiple RPC URLs with embedded keys are all redacted."""
    logger = logging.getLogger("test.bypass.multiurl")
    text = f"primary={FAKE_INFURA_URL} fallback=https://eth-mainnet.alchemyapi.io/v2/secretAlchemyKey789"

    with caplog.at_level(logging.INFO):
        logger.info("RPCs: %s", text)

    assert "abc123secretkey456def" not in caplog.text
    assert "secretAlchemyKey789" not in caplog.text
    assert "mainnet.infura.io" in caplog.text
    assert "eth-mainnet.alchemyapi.io" in caplog.text


def test_bypass_websocket_url(patched_logging, caplog):
    """wss:// URLs with embedded keys are redacted."""
    logger = logging.getLogger("test.bypass.wss")
    url = "wss://mainnet.infura.io/ws/v3/secretWsKey123456"

    with caplog.at_level(logging.INFO):
        logger.info("WS: %s", url)

    assert "secretWsKey123456" not in caplog.text
    assert "mainnet.infura.io" in caplog.text


def test_bypass_space_separated_rpcs(patched_logging, caplog):
    """Space-separated RPC URLs (multi-provider format) are all redacted."""
    logger = logging.getLogger("test.bypass.spacerpcs")
    rpcs = "https://mainnet.infura.io/v3/key111aaa https://eth-mainnet.alchemyapi.io/v2/key222bbb https://user:pass@rpc.ankr.com/eth/key333ccc"

    with caplog.at_level(logging.INFO):
        logger.info("JSON_RPC_ETHEREUM=%s", rpcs)

    assert "key111aaa" not in caplog.text
    assert "key222bbb" not in caplog.text
    assert "key333ccc" not in caplog.text
    assert "user:pass@" not in caplog.text
    assert "mainnet.infura.io" in caplog.text
    assert "eth-mainnet.alchemyapi.io" in caplog.text
    assert "rpc.ankr.com" in caplog.text


def test_bypass_f_string_interpolation(patched_logging, caplog):
    """Secrets injected via f-string into the message are still caught."""
    logger = logging.getLogger("test.bypass.fstring")
    config = {"apiKey": FAKE_API_KEY, "secret": FAKE_SECRET}

    with caplog.at_level(logging.INFO):
        logger.info(f"Config dump: {config}")

    assert FAKE_API_KEY not in caplog.text
    assert FAKE_SECRET not in caplog.text


# =========================================================================
# Entrypoint lifecycle tests
# =========================================================================


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
