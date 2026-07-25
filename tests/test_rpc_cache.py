"""Unit tests for the Foundry fork RPC cache seeder.

Pure filesystem logic, no network — the seeder is autouse for every pytest
session (``tests/conftest.py``), so its non-destructive / atomic / depth-filter
behaviour is covered here. See :mod:`eth_defi.testing.rpc_cache`.
"""

from pathlib import Path

import pytest

from eth_defi.testing.rpc_cache import seed_default_foundry_rpc_cache, seed_foundry_rpc_cache


def _make_seed(tmp_path: Path) -> Path:
    """Create a seed dir mirroring Foundry's ``<network>/<block>/`` layout."""
    seed = tmp_path / "seed"
    (seed / "mainnet" / "25598869").mkdir(parents=True)
    (seed / "mainnet" / "25598869" / "storage.json").write_text("{}")
    (seed / "arbitrum" / "487039644").mkdir(parents=True)
    (seed / "arbitrum" / "487039644" / "storage.json").write_text("[]")
    # Root-level docs must be skipped (relative depth < 3).
    (seed / "README.md").write_text("docs")
    return seed


def test_seed_copies_tree_and_skips_docs(tmp_path: Path) -> None:
    """Mirror ``<network>/<block>/`` files; skip root docs like README.md."""
    seed = _make_seed(tmp_path)
    cache = tmp_path / "cache"
    copied = seed_foundry_rpc_cache(seed, cache)
    assert copied == 2
    assert (cache / "mainnet" / "25598869" / "storage.json").read_text() == "{}"
    assert (cache / "arbitrum" / "487039644" / "storage.json").read_text() == "[]"
    assert not (cache / "README.md").exists()


def test_seed_is_non_destructive(tmp_path: Path) -> None:
    """An existing (warmer) live cache file is not overwritten by default."""
    seed = _make_seed(tmp_path)
    cache = tmp_path / "cache"
    seed_foundry_rpc_cache(seed, cache)
    (cache / "mainnet" / "25598869" / "storage.json").write_text("WARM")
    copied = seed_foundry_rpc_cache(seed, cache)
    assert copied == 0
    assert (cache / "mainnet" / "25598869" / "storage.json").read_text() == "WARM"


def test_seed_overwrite(tmp_path: Path) -> None:
    """``overwrite=True`` replaces existing live cache files."""
    seed = _make_seed(tmp_path)
    cache = tmp_path / "cache"
    seed_foundry_rpc_cache(seed, cache)
    (cache / "mainnet" / "25598869" / "storage.json").write_text("WARM")
    copied = seed_foundry_rpc_cache(seed, cache, overwrite=True)
    assert copied == 2
    assert (cache / "mainnet" / "25598869" / "storage.json").read_text() == "{}"


def test_seed_missing_dir_is_noop(tmp_path: Path) -> None:
    """A missing seed directory returns 0 and creates nothing."""
    cache = tmp_path / "cache"
    assert seed_foundry_rpc_cache(tmp_path / "nope", cache) == 0
    assert not cache.exists()


def test_seed_default_uses_env_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``seed_default_foundry_rpc_cache`` also applies ``$ETH_DEFI_RPC_CACHE_SEED_DIR``."""
    seed = _make_seed(tmp_path)
    cache = tmp_path / "cache"
    monkeypatch.setenv("ETH_DEFI_RPC_CACHE_SEED_DIR", str(seed))
    # Repo DEFAULT_SEED_DIR has no cache files (only README), so all copies come
    # from the env-supplied dir.
    assert seed_default_foundry_rpc_cache(cache) == 2
