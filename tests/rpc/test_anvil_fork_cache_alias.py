"""Network-free tests for the Anvil 1.8 fork cache alias and merge helpers."""

import json
from pathlib import Path

import pytest
import zstandard
from web3 import Web3

from eth_defi.provider.anvil import _fork_cache_paths, _fork_source_id, _merge_fork_cache_alias, _read_fork_cache, _seed_fork_cache_alias

#: Proxy URL string exactly as it would be passed to ``--fork-url``.
FORK_URL = "http://127.0.0.1:41401"

#: Canonical Base fork block used by the shared fixtures.
BLOCK = 49_030_926


def _cache(accounts: dict, storage: dict, hosts: list[str]) -> dict:
    """Build a minimal Foundry fork cache document.

    :param accounts: Account entries keyed by address.
    :param storage: Storage slots keyed by address then slot.
    :param hosts: Upstream hosts recorded in ``meta``.
    :return: Cache document.
    """
    return {"meta": {"block_env": {"number": hex(BLOCK)}, "hosts": hosts}, "accounts": accounts, "storage": storage, "block_hashes": {}}


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the Foundry fork cache at a temporary directory.

    :return: The ``<network>/<block>`` directory for the Base fork block.
    """
    monkeypatch.setenv("FOUNDRY_RPC_CACHE_DIR", str(tmp_path))
    block_dir = tmp_path / "base" / str(BLOCK)
    block_dir.mkdir(parents=True)
    return block_dir


def test_fork_cache_paths_use_keccak_of_fork_url(cache_dir: Path) -> None:
    """The endpoint file name is Anvil 1.8's keccak256 of the exact fork URL."""
    shared, endpoint = _fork_cache_paths(FORK_URL, BLOCK, 8453)
    assert shared == cache_dir / "storage.json"
    assert endpoint.name == f"storage-{Web3.keccak(text=FORK_URL).hex().removeprefix('0x')}.json"
    assert _fork_cache_paths(None, BLOCK, 8453) is None
    assert _fork_cache_paths(FORK_URL, None, 8453) is None
    assert _fork_cache_paths(FORK_URL, BLOCK, None) is None


def test_seed_and_merge_fork_cache_alias(cache_dir: Path) -> None:
    """The shared seed is aliased before launch and unioned back after shutdown.

    1. A compressed shared seed is copied to the endpoint file name with this launch's source id.
    2. Anvil's session-only endpoint file is merged as a union, then removed.
    """
    shared, endpoint = _fork_cache_paths(FORK_URL, BLOCK, 8453)
    seed = _cache({"0xaa": {"balance": "0x1"}, "0xbb": {"balance": "0x2"}}, {"0xaa": {"0x1": "0x10", "0x2": "0x20"}}, ["lb.drpc.org"])
    seed["meta"]["source_id"] = _fork_source_id("http://127.0.0.1:40000")
    shared.write_bytes(zstandard.ZstdCompressor().compress(json.dumps(seed).encode()))

    # 1. A compressed shared seed is copied to the endpoint file name with this launch's source id.
    _seed_fork_cache_alias(FORK_URL, BLOCK, 8453)
    aliased = _read_fork_cache(endpoint)
    assert aliased["meta"]["source_id"] == _fork_source_id(FORK_URL)
    assert aliased["storage"] == seed["storage"]

    # 2. Anvil's session-only endpoint file is merged as a union, then removed.
    session = _cache({"0xaa": {"balance": "0x1"}, "0xcc": {"balance": "0x3"}}, {"0xaa": {"0x3": "0x30"}, "0xcc": {"0x1": "0x11"}}, ["127.0.0.1"])
    endpoint.write_text(json.dumps(session))
    _merge_fork_cache_alias(FORK_URL, BLOCK, 8453)

    assert not endpoint.exists()
    merged = _read_fork_cache(shared)
    assert set(merged["accounts"]) == {"0xaa", "0xbb", "0xcc"}
    assert merged["storage"] == {"0xaa": {"0x1": "0x10", "0x2": "0x20", "0x3": "0x30"}, "0xcc": {"0x1": "0x11"}}
    assert merged["meta"]["hosts"] == ["lb.drpc.org", "127.0.0.1"]
    assert shared.read_bytes()[:4] == bytes.fromhex("28b52ffd")


def test_merge_discards_unreadable_endpoint_file(cache_dir: Path) -> None:
    """A truncated endpoint file is dropped without touching the shared seed."""
    shared, endpoint = _fork_cache_paths(FORK_URL, BLOCK, 8453)
    seed_bytes = zstandard.ZstdCompressor().compress(json.dumps(_cache({"0xaa": {}}, {}, [])).encode())
    shared.write_bytes(seed_bytes)
    endpoint.write_bytes(b"\x28\xb5\x2f\xfdtruncated")

    _merge_fork_cache_alias(FORK_URL, BLOCK, 8453)

    assert not endpoint.exists()
    assert shared.read_bytes() == seed_bytes


def test_fork_source_id_matches_anvil() -> None:
    """The source id is the one Anvil 1.8.6 writes to ``meta.source_id``."""
    # Recorded from the cache file of ``anvil --fork-url http://127.0.0.1:28545 --fork-block-number 2``
    assert _fork_source_id("http://127.0.0.1:28545") == "0x214be996fbd7690cdff89aeb4be3057b8c44dff3ed014c37e70df004d07fd7b1"
    assert _fork_source_id(FORK_URL) != _fork_source_id("http://127.0.0.1:41402")
