"""Tests for deterministic GMX contract address resolution.

GMX publishes new deployments to the ``updates`` branch of ``gmx-io/gmx-synthetics``.
Resolving addresses from that branch on every call meant the ExchangeRouter a bot
traded through could change because of an upstream push, or flip back to the previous
deployment when GitHub replied HTTP 429. Against a Lagoon vault guard's fixed address
allowlist that turned every order — including exits — into a reverted transaction.

These tests lock in that address resolution is pinned, cached, and overridable.
"""

import dataclasses
import json
import logging
from pathlib import Path

import pytest

from eth_defi.gmx import contracts as contracts_module
from eth_defi.gmx.contracts import (
    GMX_CONTRACT_RELEASE_ENV_VAR,
    GMX_CONTRACT_RELEASE_REMOTE,
    GMX_DEFAULT_CONTRACT_RELEASE,
    PINNED_CONTRACTS,
    ContractAddresses,
    clear_contract_address_cache,
    get_contract_addresses,
    get_pinned_contract_release,
)

#: v2.2c ExchangeRouter on Arbitrum — the router GMX rotated to.
V22C_EXCHANGE_ROUTER = "0x7dE39FF2e232A2203196788d37e234cF8F1b83f1"

#: v2.2b ExchangeRouter on Arbitrum — the router the vault guard already allowed.
V22B_EXCHANGE_ROUTER = "0x1C3fa76e6E1088bCE750f23a5BFcffa1efEF6A41"

#: SyntheticsRouter on Arbitrum, unchanged across v2.2b and v2.2c.
SYNTHETICS_ROUTER = "0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6"

#: OrderVault on Arbitrum, unchanged across v2.2b and v2.2c.
ORDER_VAULT = "0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5"


@pytest.fixture(autouse=True)
def _clean_resolution_state(monkeypatch):
    """Isolate each test from ambient pin overrides and cached fetches."""
    monkeypatch.delenv(GMX_CONTRACT_RELEASE_ENV_VAR, raising=False)
    clear_contract_address_cache()
    yield
    clear_contract_address_cache()


@pytest.fixture
def no_network(monkeypatch):
    """Fail loudly if a code path tries to reach GMX over HTTP."""

    def _boom(*args, **kwargs):
        raise AssertionError("Pinned resolution must not perform a network fetch")

    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", _boom)


def test_default_release_is_v22c():
    """The shipped default is the current GMX release."""
    assert GMX_DEFAULT_CONTRACT_RELEASE == "v2.2c"
    assert get_pinned_contract_release() == "v2.2c"


def test_arbitrum_resolves_to_pinned_v22c(no_network):
    """Arbitrum resolves to the v2.2c address set without touching the network."""
    addresses = get_contract_addresses("arbitrum")
    assert addresses.exchangerouter == V22C_EXCHANGE_ROUTER
    assert addresses.syntheticsreader == "0xfA26cBb46e2614609406de08CA1Dc7f70a684184"
    assert addresses.glvreader == "0x85fcBD684D08053f1efAB302dCb04F22E20E65B1"


def test_resolution_is_stable_when_remote_changes(monkeypatch):
    """A push to gmx-synthetics must not move the resolved router.

    This is the regression: the live bot's ExchangeRouter changed under it when GMX
    published v2.2c, with no code change and no restart.
    """
    rogue = ContractAddresses(
        datastore="0x" + "11" * 20,
        eventemitter="0x" + "22" * 20,
        exchangerouter="0x" + "33" * 20,
        depositvault="0x" + "44" * 20,
        withdrawalvault="0x" + "55" * 20,
        ordervault="0x" + "66" * 20,
        syntheticsreader="0x" + "77" * 20,
        syntheticsrouter="0x" + "88" * 20,
        glvreader="0x" + "99" * 20,
    )
    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", lambda chain: rogue)

    assert get_contract_addresses("arbitrum").exchangerouter == V22C_EXCHANGE_ROUTER


def test_env_var_overrides_the_pin(monkeypatch, no_network):
    """Operators can roll back to a previous release without a code change."""
    monkeypatch.setenv(GMX_CONTRACT_RELEASE_ENV_VAR, "v2.2b")
    assert get_contract_addresses("arbitrum").exchangerouter == V22B_EXCHANGE_ROUTER


def test_release_argument_overrides_env(monkeypatch, no_network):
    """An explicit argument beats the environment variable."""
    monkeypatch.setenv(GMX_CONTRACT_RELEASE_ENV_VAR, "v2.2b")
    assert get_contract_addresses("arbitrum", release="v2.2c").exchangerouter == V22C_EXCHANGE_ROUTER


def test_unknown_release_raises(no_network):
    """A typo in the pin fails loudly rather than silently falling back."""
    with pytest.raises(ValueError, match="Unknown GMX contract release"):
        get_contract_addresses("arbitrum", release="v9.9z")


def test_unsupported_chain_raises(no_network):
    with pytest.raises(ValueError, match="Unsupported chain"):
        get_contract_addresses("dogechain")


def test_testnet_ignores_release_pinning(no_network):
    """Testnets have a single static deployment and are unaffected by the pin."""
    assert get_contract_addresses("arbitrum_sepolia", release="v2.2b").exchangerouter == "0x657F9215FA1e839FbA15cF44B1C00D95cF71ed10"


@pytest.mark.parametrize("chain", ["arbitrum", "avalanche"])
def test_approval_critical_addresses_unchanged_across_releases(chain, no_network):
    """SyntheticsRouter and OrderVault must match across v2.2b and v2.2c.

    The Safe's ERC-20 approval targets the SyntheticsRouter and the guard maps each
    ExchangeRouter to an OrderVault. If either had rotated, the migration would have
    needed a re-approval and a guard remap rather than a single whitelist entry.
    """
    old = get_contract_addresses(chain, release="v2.2b")
    new = get_contract_addresses(chain, release="v2.2c")
    assert old.syntheticsrouter == new.syntheticsrouter
    assert old.ordervault == new.ordervault
    assert old.datastore == new.datastore


def test_arbitrum_pinned_addresses_are_the_documented_ones(no_network):
    """Guard-relevant Arbitrum addresses match the migration plan."""
    v22c = PINNED_CONTRACTS["v2.2c"]["arbitrum"]
    assert v22c.exchangerouter == V22C_EXCHANGE_ROUTER
    assert v22c.syntheticsrouter == SYNTHETICS_ROUTER
    assert v22c.ordervault == ORDER_VAULT


def test_remote_release_is_cached(monkeypatch):
    """Opting into live resolution must not cost an HTTP round trip per order."""
    calls = []
    sentinel = PINNED_CONTRACTS["v2.2b"]["arbitrum"]

    def _counting_fetch(chain):
        calls.append(chain)
        return sentinel

    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", _counting_fetch)

    for _ in range(5):
        addresses = get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE)
        assert addresses is sentinel

    assert len(calls) == 1, f"expected a single fetch, got {len(calls)}"


def test_remote_release_serves_stale_entry_on_failure(monkeypatch):
    """A GitHub 429 must not flip the resolved router back to the previous release.

    Historically a failed fetch of the ``updates`` branch fell through to ``main``,
    so rate limiting alone could swap a live bot's ExchangeRouter.
    """
    sentinel = PINNED_CONTRACTS["v2.2c"]["arbitrum"]
    responses = [sentinel, None, None]

    def _flaky_fetch(chain):
        return responses.pop(0)

    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", _flaky_fetch)
    monkeypatch.setattr(contracts_module, "GMX_REMOTE_CACHE_TTL_SECONDS", -1.0)

    first = get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE)
    second = get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE)

    assert first is sentinel
    assert second is sentinel, "stale cached addresses should be reused when a refresh fails"


def test_remote_release_raises_when_nothing_cached(monkeypatch):
    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", lambda chain: None)
    with pytest.raises(ValueError, match="Failed to fetch contract addresses"):
        get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE)


def test_remote_release_serves_stale_when_the_fetch_raises(monkeypatch):
    """A connection failure must reach the stale-serving path, not propagate.

    ``_fetch_contract_addresses_from_url`` re-raises instead of returning ``None``
    when the last URL exhausts its retries — which is what a plain network outage
    looks like, the most likely failure of all. Letting that escape would defeat
    the resilience path for the very case it exists to cover.
    """
    sentinel = PINNED_CONTRACTS["v2.2c"]["arbitrum"]
    calls = []

    def _fetch_then_die(chain):
        calls.append(chain)
        if len(calls) == 1:
            return sentinel
        raise ConnectionError("GitHub unreachable")

    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", _fetch_then_die)
    monkeypatch.setattr(contracts_module, "GMX_REMOTE_CACHE_TTL_SECONDS", -1.0)

    assert get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE) is sentinel
    assert get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE) is sentinel
    assert len(calls) == 2, "the second call should have attempted a refresh"


def test_remote_release_raises_when_the_fetch_raises_with_nothing_cached(monkeypatch):
    """With no cache to fall back on, the failure is still reported as ValueError."""

    def _die(chain):
        raise ConnectionError("GitHub unreachable")

    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", _die)
    with pytest.raises(ValueError, match="Failed to fetch contract addresses"):
        get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE)


def test_remote_release_warns_when_it_resolves_a_superseded_router(monkeypatch, caplog):
    """A silent fall-back to the previous release must not pass unnoticed.

    The ``updates``-then-``main`` fetch does not fail when ``updates`` is rate
    limited — it succeeds against ``main`` and returns the older release. That is
    how a 429 alone could swap a running bot's ExchangeRouter.
    """
    older = PINNED_CONTRACTS["v2.2b"]["arbitrum"]
    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", lambda chain: older)

    with caplog.at_level(logging.WARNING, logger=contracts_module.__name__):
        resolved = get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE)

    assert resolved is older
    assert "v2.2b" in caplog.text
    assert V22B_EXCHANGE_ROUTER in caplog.text


def test_remote_release_does_not_warn_on_the_current_router(monkeypatch, caplog):
    current = PINNED_CONTRACTS["v2.2c"]["arbitrum"]
    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", lambda chain: current)

    with caplog.at_level(logging.WARNING, logger=contracts_module.__name__):
        get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE)

    assert "ExchangeRouter" not in caplog.text


@pytest.mark.parametrize("chain", ["arbitrum", "avalanche"])
def test_release_rotation_set_is_documented(chain, no_network):
    """Pin exactly which addresses moved between v2.2b and v2.2c.

    The ``PINNED_CONTRACTS`` docstring makes a claim about what is stable across
    the upgrade, and the whole migration rests on it: the SyntheticsRouter keeps
    an existing ERC-20 approval valid and the OrderVault keeps a guard's
    router-to-vault mapping valid. Assert it rather than trusting the prose.
    """
    old = get_contract_addresses(chain, release="v2.2b")
    new = get_contract_addresses(chain, release="v2.2c")

    rotated = {f.name for f in dataclasses.fields(old) if getattr(old, f.name) != getattr(new, f.name)}

    assert rotated == {
        "exchangerouter",
        "syntheticsreader",
        "glvreader",
        "chainlinkpricefeedprovider",
        "chainlinkdatastreamprovider",
        "orderhandler",
        "oracle",
    }

    # The load-bearing half: these must not move, or the migration would need a
    # re-approval and a guard remap rather than a single whitelist entry.
    for field_name in ("syntheticsrouter", "ordervault", "datastore", "eventemitter", "depositvault", "withdrawalvault"):
        assert getattr(old, field_name) == getattr(new, field_name), f"{field_name} rotated"


def test_whitelist_constants_track_the_pinned_release(no_network):
    """``GMX_ARBITRUM_ADDRESSES`` must not drift from the pinned set.

    It previously held a router from an older GMX release than either pinned entry,
    so anyone whitelisting from the constant allowed an address the bot never uses.
    """
    from eth_defi.gmx.whitelist import GMX_ARBITRUM_ADDRESSES, get_gmx_arbitrum_addresses

    assert GMX_ARBITRUM_ADDRESSES == get_gmx_arbitrum_addresses()


def _load_vendored_abi(name: str) -> list:
    path = Path(contracts_module.__file__).parent.parent / "abi" / "gmx" / name
    return json.loads(path.read_text())


def test_vendored_reader_abi_matches_v22c():
    """The vendored Reader ABI must carry the v2.2c fields.

    ``get_contract_addresses()`` resolves the Reader *address* but decoding uses this
    vendored ABI. v2.2c appended fields to several structs, so a stale ABI decodes a
    v2.2c response into misaligned values instead of failing outright.
    """
    abi = _load_vendored_abi("Reader.json")
    functions = {e["name"]: e for e in abi if e.get("type") == "function"}

    position_info = functions["getPositionInfo"]
    output_fields = [c["name"] for c in position_info["outputs"][0]["components"]]
    assert "positionValueInUsd" in output_fields, "Reader.json is pre-v2.2c"

    order_numbers = next(c for c in functions["getOrder"]["outputs"][0]["components"] if c["name"] == "numbers")
    assert "uiFeeFactor" in [c["name"] for c in order_numbers["components"]]


def test_vendored_exchange_router_abi_matches_v22c():
    """v2.2c moved the simulate* family out of ExchangeRouter into SimulationRouter."""
    abi = _load_vendored_abi("ExchangeRouter.json")
    names = {e["name"] for e in abi if e.get("type") == "function"}

    assert not {n for n in names if n.startswith("simulateExecute")}, "simulate* should be gone in v2.2c"
    assert "createTwapOrder" in names

    # The functions the Lagoon guard's GmxLib decoder validates must still be present,
    # otherwise order calldata could not be decoded on-chain.
    for required in ("createOrder", "multicall", "sendWnt", "sendTokens", "cancelOrder", "updateOrder"):
        assert required in names, f"{required} missing from vendored ExchangeRouter ABI"
