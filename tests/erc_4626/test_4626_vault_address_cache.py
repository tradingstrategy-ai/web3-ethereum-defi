"""Disk caching for ERC-4626 vault share / denomination token address lookups.

Verifies that the ``vault_token`` helper correctly populates, reads, and
avoids poisoning the :py:class:`eth_defi.token.TokenDiskCache`.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_token import (
    _vault_denomination_token_key,
    _vault_share_token_key,
)
from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests")


#: IPOR USDC Fusion vault on Base — same address as :py:mod:`tests.erc_4626.test_4626_read`.
IPOR_USDC_BASE: HexAddress = "0x45aa96f0b3188d47a1dafdbefce1db6b37f58216"

#: Function selectors we watch for on the RPC provider.
SHARE_SELECTOR = "0xa8d5fd65"  # keccak("share()")[:4]
ASSET_SELECTOR = "0x38d52e0f"  # keccak("asset()")[:4]

#: Clearly bogus address used to seed a poison entry in the historical-pinned
#: bypass step. If the guard works, the real on-chain address is returned.
POISON_ADDRESS: HexAddress = "0x000000000000000000000000000000000000dEaD"


@pytest.fixture(scope="module")
def web3() -> Web3:
    return create_multi_provider_web3(JSON_RPC_BASE)


def _make_rpc_recorder(web3: Web3) -> list[dict]:
    """Wrap ``web3.manager.request_blocking`` to record every ``eth_call`` payload.

    Returns the list that will be populated on each call. The wrapper is
    installed for the lifetime of the surrounding ``with`` block via the
    returned context manager style (caller restores manually).
    """
    recorded: list[dict] = []
    original = web3.manager.request_blocking

    def recording_request_blocking(method, params, *args, **kwargs):
        if method == "eth_call" and params:
            call_obj = params[0] if isinstance(params, (list, tuple)) else params
            if isinstance(call_obj, dict):
                recorded.append(call_obj)
        return original(method, params, *args, **kwargs)

    web3.manager.request_blocking = recording_request_blocking
    recorded.append({"__restore__": original})  # sentinel holds the original for teardown
    return recorded


def _restore_rpc_recorder(web3: Web3, recorded: list[dict]) -> None:
    sentinel = recorded.pop(0)
    web3.manager.request_blocking = sentinel["__restore__"]


def test_vault_address_disk_cache(web3: Web3, tmp_path: Path) -> None:
    """Verify ERC-4626 share/asset token address lookups are disk-cached and poison-safe.

    1. First `fetch_share_token_address()` / `fetch_denomination_token_address()` calls
       on a live Base vault populate the disk cache with the correct keys.
    2. A second `ERC4626Vault` instance sharing the same `TokenDiskCache`
       returns the cached values without issuing `share()` / `asset()` eth_calls.
    3. `fetch_share_token()` invokes `fetch_share_token_address()` exactly once
       (regression guard for the removed double-call bug).
    4. A historical-pinned vault instance (`default_block_identifier=<block>`)
       bypasses the cache entirely even when an entry exists.
    5. A transient `ProbablyNodeHasNoBlock` failure does NOT persist the
       fallback to disk (cache-poisoning guard).
    """
    chain_id = web3.eth.chain_id
    spec = VaultSpec(chain_id, IPOR_USDC_BASE)

    # 1. Setup — shared disk cache for the happy-path steps.
    cache = TokenDiskCache(tmp_path / "disk_cache.sqlite")

    # 2. Happy path cache population — default live path, no pinned block.
    vault_a = ERC4626Vault(web3, spec, token_cache=cache)
    share_addr = vault_a.fetch_share_token_address()
    denom_addr = vault_a.fetch_denomination_token_address()

    assert share_addr.startswith("0x") and len(share_addr) == 42
    assert denom_addr is not None and denom_addr.startswith("0x") and len(denom_addr) == 42

    share_key = _vault_share_token_key(chain_id, spec.vault_address)
    denom_key = _vault_denomination_token_key(chain_id, spec.vault_address)
    assert share_key in cache, f"Share token key not persisted: {share_key}"
    assert denom_key in cache, f"Denomination token key not persisted: {denom_key}"
    assert cache[share_key]["address"] == share_addr
    assert cache[denom_key]["address"] == denom_addr

    # 3. Cache-hit verification — second instance sharing the same cache must
    # not issue share() or asset() eth_calls against the vault address.
    vault_b = ERC4626Vault(web3, spec, token_cache=cache)
    recorded = _make_rpc_recorder(web3)
    try:
        cached_share = vault_b.fetch_share_token_address()
        cached_denom = vault_b.fetch_denomination_token_address()
    finally:
        _restore_rpc_recorder(web3, recorded)

    assert cached_share == share_addr
    assert cached_denom == denom_addr
    vault_address_lower = spec.vault_address.lower()
    for call_obj in recorded:
        to_addr = (call_obj.get("to") or "").lower()
        data = (call_obj.get("data") or "").lower()
        if to_addr == vault_address_lower:
            assert not data.startswith(SHARE_SELECTOR), f"share() selector leaked on cache hit: {call_obj}"
            assert not data.startswith(ASSET_SELECTOR), f"asset() selector leaked on cache hit: {call_obj}"

    # 4. Single-RPC regression guard — fresh vault with empty cache, count how
    # many times fetch_share_token_address() is invoked by fetch_share_token().
    fresh_cache = TokenDiskCache(tmp_path / "fresh_cache.sqlite")
    vault_c = ERC4626Vault(web3, spec, token_cache=fresh_cache)
    original_fetch = ERC4626Vault.fetch_share_token_address
    with patch.object(
        ERC4626Vault,
        "fetch_share_token_address",
        autospec=True,
        side_effect=lambda self, *a, **kw: original_fetch(self, *a, **kw),
    ) as wrapped:
        vault_c.fetch_share_token()
        assert wrapped.call_count == 1, f"fetch_share_token_address called {wrapped.call_count} times, expected 1"

    # 5. Historical-pinned bypass — block-pinned instance must ignore the cache
    # even if it contains a (bogus) entry for the same vault.
    pinned_cache = TokenDiskCache(tmp_path / "pinned_cache.sqlite")
    pinned_cache[share_key] = {"address": POISON_ADDRESS}
    assert pinned_cache[share_key]["address"] == POISON_ADDRESS

    vault_pinned = ERC4626Vault(
        web3,
        spec,
        token_cache=pinned_cache,
        default_block_identifier=27975506,
    )
    pinned_result = vault_pinned.fetch_share_token_address()
    assert pinned_result.lower() != POISON_ADDRESS.lower(), "Historical-pinned vault used the live-latest cache"
    assert pinned_result == share_addr, "Historical-pinned vault did not return the real share token address"

    # 6. Cache-poisoning guard — a transient ProbablyNodeHasNoBlock on a fresh
    # cache must NOT persist the vault-address fallback.
    poison_cache = TokenDiskCache(tmp_path / "poison_cache.sqlite")
    vault_d = ERC4626Vault(web3, spec, token_cache=poison_cache)

    call_count = {"n": 0}
    original_call = None
    from eth_defi.event_reader.multicall_batcher import EncodedCall

    original_call = EncodedCall.call

    def raising_call(self, *args, **kwargs):
        if self.func_name == "share" and call_count["n"] == 0:
            call_count["n"] += 1
            raise ProbablyNodeHasNoBlock("Simulated transient node failure")
        return original_call(self, *args, **kwargs)

    with patch.object(EncodedCall, "call", raising_call):
        transient_result = vault_d.fetch_share_token_address()

    # Fallback to vault address is the documented behaviour for transient failures.
    assert transient_result.lower() == spec.vault_address.lower()
    # Critical assertion: the transient failure did NOT write to the cache.
    assert share_key not in poison_cache, "Transient ProbablyNodeHasNoBlock failure poisoned the cache"

    # After the monkey-patch is removed, a second call must perform the real
    # on-chain lookup and persist the correct answer.
    recovered_result = vault_d.fetch_share_token_address()
    assert recovered_result == share_addr
    assert share_key in poison_cache
    assert poison_cache[share_key]["address"] == share_addr
