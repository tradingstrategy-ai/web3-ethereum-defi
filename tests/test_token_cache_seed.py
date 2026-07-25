"""Unit tests for the shipped token cache seeding.

Pure filesystem/SQLite logic, no network. The seeding is autouse for every
pytest session (``tests/conftest.py``), so its install / merge behaviour is
covered here. See :mod:`eth_defi.testing.token_cache`.
"""

from pathlib import Path

import pytest

from eth_defi.testing.token_cache import (
    install_token_cache,
    is_token_cache_rebuild_requested,
    is_token_cache_seeding_disabled,
    merge_into_token_cache_seed,
)
from eth_defi.token import TokenDiskCache

TOKEN = {"name": "USD Coin", "symbol": "USDC", "supply": 1_000_000, "decimals": 6}


def test_merge_round_trip(tmp_path: Path) -> None:
    """Entries merged into the seed decode back to the original dict.

    Guards the encoded-vs-decoded trap: ``PersistentKeyValueStore.items()``
    yields raw JSON strings while ``__getitem__`` decodes, so merging via
    ``items()`` would hand a ``str`` to ``encode_value()``.
    """
    source = TokenDiskCache(tmp_path / "source.sqlite")
    source["1-0xaaa"] = dict(TOKEN)
    source.commit()

    seed_path = tmp_path / "seed.sqlite"
    assert merge_into_token_cache_seed(source, seed_path) == 1

    seed = TokenDiskCache(seed_path)
    try:
        stored = seed["1-0xaaa"]
        assert stored["symbol"] == "USDC"
        assert stored["decimals"] == 6
    finally:
        seed.close()
        source.close()


def test_merge_is_non_destructive(tmp_path: Path) -> None:
    """An entry already in the seed is preserved, not overwritten."""
    seed_path = tmp_path / "seed.sqlite"
    seed = TokenDiskCache(seed_path)
    seed["1-0xaaa"] = {"name": "Existing", "symbol": "OLD", "supply": 1, "decimals": 18}
    seed.commit()
    seed.close()

    source = TokenDiskCache(tmp_path / "source.sqlite")
    source["1-0xaaa"] = dict(TOKEN)
    source.commit()

    assert merge_into_token_cache_seed(source, seed_path) == 0
    seed = TokenDiskCache(seed_path)
    try:
        assert seed["1-0xaaa"]["symbol"] == "OLD"
    finally:
        seed.close()
        source.close()


def test_install_token_cache_sets_vault_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installing points the vault default at a TokenDiskCache.

    Vault token-address caching is gated on
    ``isinstance(token_cache, TokenDiskCache)``, so the installed default must be
    a disk cache rather than the library's in-memory LRU.
    """
    from eth_defi.vault import base as vault_base

    original = vault_base.DEFAULT_TOKEN_CACHE
    try:
        cache = install_token_cache("unit-test")
        assert isinstance(cache, TokenDiskCache)
        assert vault_base.DEFAULT_TOKEN_CACHE is cache
    finally:
        vault_base.DEFAULT_TOKEN_CACHE = original


def test_env_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rebuild and disable flags read truthy environment values."""
    monkeypatch.delenv("ETH_DEFI_TOKEN_CACHE_REBUILD", raising=False)
    monkeypatch.delenv("ETH_DEFI_TOKEN_CACHE_DISABLE", raising=False)
    assert is_token_cache_rebuild_requested() is False
    assert is_token_cache_seeding_disabled() is False

    monkeypatch.setenv("ETH_DEFI_TOKEN_CACHE_REBUILD", "1")
    monkeypatch.setenv("ETH_DEFI_TOKEN_CACHE_DISABLE", "true")
    assert is_token_cache_rebuild_requested() is True
    assert is_token_cache_seeding_disabled() is True
