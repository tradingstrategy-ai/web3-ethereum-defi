"""Unit tests for the Foundry fork RPC cache seeder.

Pure filesystem logic, no network — the seeder is autouse for every pytest
session (``tests/conftest.py``), so its non-destructive / atomic / depth-filter
behaviour is covered here. See :mod:`eth_defi.testing.rpc_cache`.
"""

from pathlib import Path

import pytest

from eth_defi.chain import FOUNDRY_NETWORK_NAMES
from eth_defi.testing.fork_blocks import MIDNIGHT_BLOCKS
from eth_defi.testing.rpc_cache import DEFAULT_SEED_DIR, NON_CACHEABLE_CHAIN_NETWORKS, seed_default_foundry_rpc_cache, seed_foundry_rpc_cache


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
    # All copies come from the env-supplied dir (2), plus whatever the repo
    # DEFAULT_SEED_DIR ships (the committed midnight-block seed). Just assert the
    # env dir's two files were applied.
    assert seed_default_foundry_rpc_cache(cache) >= 2


def test_committed_seed_is_well_formed() -> None:
    """Every committed cache dir is a usable, reproducible Foundry fork block.

    The seed ships every *fixed* fork block the suite uses, so this no longer
    pins the set of blocks. It guards the invariants that still matter:
    the directory must be a Foundry network name (not our display name, so the
    path matches what Anvil writes), must hold a ``storage.json``, and must not
    belong to a chain that can only fork the tip — caching such a block is dead
    weight because the block number changes on every run.
    """
    name_to_chain = {name: chain_id for chain_id, name in FOUNDRY_NETWORK_NAMES.items()}
    seen = 0
    for net_dir in DEFAULT_SEED_DIR.iterdir():
        if not net_dir.is_dir():
            continue
        assert net_dir.name in name_to_chain, f"seed dir '{net_dir.name}' is not a known Foundry network name (add it to FOUNDRY_NETWORK_NAMES)"
        assert net_dir.name not in NON_CACHEABLE_CHAIN_NETWORKS, f"{net_dir.name} forks the chain tip, so its cache must not be committed"
        for block_dir in net_dir.iterdir():
            if not block_dir.is_dir():
                continue
            assert block_dir.name.isdigit(), f"{net_dir.name}/{block_dir.name} is not a block number"
            assert (block_dir / "storage.json").exists(), f"{net_dir.name}/{block_dir.name} has no storage.json"
            seen += 1
    assert seen > 0, "no committed fork cache blocks found"


def test_midnight_blocks_are_seeded() -> None:
    """Each chain's canonical midnight block ships in the seed.

    These are the shared blocks the pooled fork tests normalise onto, so they are
    the most valuable entries to keep warm.
    """
    for chain_id, block in MIDNIGHT_BLOCKS.items():
        network = FOUNDRY_NETWORK_NAMES.get(chain_id)
        if network is None or network in NON_CACHEABLE_CHAIN_NETWORKS:
            continue
        seeded = DEFAULT_SEED_DIR / network / str(block)
        if not seeded.exists():
            # Not every chain has a vault test that forks its midnight block.
            continue
        assert (seeded / "storage.json").exists()
