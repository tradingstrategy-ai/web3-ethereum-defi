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
from pathlib import Path

import pytest
import requests

from eth_defi.gmx import contracts as contracts_module
from eth_defi.gmx.contracts import (
    GMX_CONTRACT_RELEASE_ENV_VAR,
    GMX_CONTRACT_RELEASE_REMOTE,
    GMX_DEFAULT_CONTRACT_RELEASE,
    GMX_VENDORED_ABI_RELEASE,
    NETWORK_CONTRACTS,
    PINNED_CONTRACTS,
    ContractAddresses,
    GMXReleaseMismatchError,
    clear_contract_address_cache,
    get_contract_addresses,
    get_pinned_contract_release,
    get_reader_contract,
)

#: v2.2.1 ExchangeRouter on Arbitrum — the current router.
V221_EXCHANGE_ROUTER = "0x6776c2b52179BC737193DE3bc75262681006AA27"

#: v2.2c ExchangeRouter on Arbitrum — the previous router.
V22C_EXCHANGE_ROUTER = "0x7dE39FF2e232A2203196788d37e234cF8F1b83f1"

#: v2.2b ExchangeRouter on Arbitrum — the router the vault guard already allowed.
V22B_EXCHANGE_ROUTER = "0x1C3fa76e6E1088bCE750f23a5BFcffa1efEF6A41"

#: SyntheticsRouter on Arbitrum, unchanged across v2.2b and v2.2c.
SYNTHETICS_ROUTER = "0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6"

#: OrderVault on Arbitrum, unchanged across v2.2b and v2.2c.
ORDER_VAULT = "0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5"

#: Current Arbitrum Sepolia ExchangeRouter, from
#: ``gmx-io/gmx-synthetics@updates:deployments/arbitrumSepolia/``.
SEPOLIA_EXCHANGE_ROUTER = "0x6B489dD5bB1AAE8df246359d59aA7316760a75d2"

#: Arbitrum Sepolia Reader address, retained for address-only inspection. Its
#: older tuple layout is incompatible with the vendored v2.2.1 ABI.
SEPOLIA_READER = "0x92659fEf40582ceCC3CBa4D096d28291C238D358"


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


def test_default_release_is_v221():
    """The shipped default is the current GMX release, and the vendored ABIs' release."""
    assert GMX_DEFAULT_CONTRACT_RELEASE == "v2.2.1"
    assert GMX_VENDORED_ABI_RELEASE == GMX_DEFAULT_CONTRACT_RELEASE
    assert get_pinned_contract_release() == "v2.2.1"


def test_arbitrum_resolves_to_pinned_v221(no_network):
    """Arbitrum resolves to the v2.2.1 address set without touching the network."""
    addresses = get_contract_addresses("arbitrum")
    assert addresses.exchangerouter == V221_EXCHANGE_ROUTER
    assert addresses.syntheticsreader == "0xAda708aFf0f1D784D28cd8Ff4d6D977fF9599e5D"
    assert addresses.glvreader == "0x51fe0b7919e1208a717E9B16a097C1C3D70eFbf6"
    assert addresses.orderhandler == "0x2dC2315039bf3617e815969c613428F36e9Baf98"
    assert addresses.oracle == "0x42F3642F3F82CBAdE4CFA32961a57568055Dd10E"
    assert addresses.chainlinkdatastreamprovider == "0x204CC947Fddd11c90e302db2A5ac3865021D1618"


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

    assert get_contract_addresses("arbitrum").exchangerouter == V221_EXCHANGE_ROUTER


@pytest.mark.parametrize("old_release", ["v2.2b", "v2.2c"])
def test_env_var_cannot_pair_old_addresses_with_new_abis(monkeypatch, no_network, old_release):
    """Rollback is by image tag only: an env override to an older release is refused.

    The vendored ABIs decode v2.2.1 only; an older Reader decoded with them
    mis-aligns struct tuples silently.
    """
    monkeypatch.setenv(GMX_CONTRACT_RELEASE_ENV_VAR, old_release)
    with pytest.raises(GMXReleaseMismatchError, match="does not match the vendored ABIs"):
        get_contract_addresses("arbitrum")


@pytest.mark.parametrize("old_release", ["v2.2b", "v2.2c"])
def test_release_argument_cannot_pair_old_addresses_with_new_abis(no_network, old_release):
    """An explicit older ``release`` argument is refused too."""
    with pytest.raises(GMXReleaseMismatchError):
        get_contract_addresses("arbitrum", release=old_release)


def test_explicit_vendored_release_is_accepted(monkeypatch, no_network):
    """Setting the env var to the vendored release is a no-op, not an error."""
    monkeypatch.setenv(GMX_CONTRACT_RELEASE_ENV_VAR, GMX_VENDORED_ABI_RELEASE)
    assert get_contract_addresses("arbitrum").exchangerouter == V221_EXCHANGE_ROUTER


def test_avalanche_is_not_pinned_for_vendored_release(no_network):
    """v2.2.1 is Arbitrum only; Avalanche has no vendored-ABI-compatible pin."""
    with pytest.raises(GMXReleaseMismatchError, match="Arbitrum only"):
        get_contract_addresses("avalanche")


def test_unknown_release_raises(no_network):
    """A typo in the pin fails loudly rather than silently falling back."""
    with pytest.raises(ValueError, match="Unknown GMX contract release"):
        get_contract_addresses("arbitrum", release="v9.9z")


def test_unsupported_chain_raises(no_network):
    with pytest.raises(ValueError, match="Unsupported chain"):
        get_contract_addresses("dogechain")


def test_sepolia_cannot_use_v221_abis(no_network):
    """Sepolia's older Reader layout must not be paired with v2.2.1 ABIs."""
    for release in ("v2.2b", None):
        assert get_contract_addresses("arbitrum_sepolia", release=release).exchangerouter == SEPOLIA_EXCHANGE_ROUTER
    with pytest.raises(GMXReleaseMismatchError, match="Sepolia contracts have not migrated"):
        get_reader_contract(None, "arbitrum_sepolia")


@pytest.mark.parametrize("chain", ["arbitrum", "avalanche"])
def test_approval_critical_addresses_unchanged_across_releases(chain, no_network):
    """SyntheticsRouter and OrderVault must match across v2.2b and v2.2c.

    The Safe's ERC-20 approval targets the SyntheticsRouter and the guard maps each
    ExchangeRouter to an OrderVault. If either had rotated, the migration would have
    needed a re-approval and a guard remap rather than a single whitelist entry.
    """
    old = PINNED_CONTRACTS["v2.2b"][chain]
    new = PINNED_CONTRACTS["v2.2c"][chain]
    assert old.syntheticsrouter == new.syntheticsrouter
    assert old.ordervault == new.ordervault
    assert old.datastore == new.datastore


def test_arbitrum_pinned_addresses_are_the_documented_ones(no_network):
    """Guard-relevant Arbitrum addresses match the migration plan."""
    v221 = PINNED_CONTRACTS["v2.2.1"]["arbitrum"]
    assert v221.exchangerouter == V221_EXCHANGE_ROUTER
    assert v221.syntheticsrouter == SYNTHETICS_ROUTER
    assert v221.ordervault == ORDER_VAULT
    assert PINNED_CONTRACTS["v2.2c"]["arbitrum"].exchangerouter == V22C_EXCHANGE_ROUTER


def test_remote_release_is_cached(monkeypatch):
    """Opting into live resolution must not cost an HTTP round trip per order."""
    calls = []
    sentinel = PINNED_CONTRACTS["v2.2.1"]["arbitrum"]

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
    sentinel = PINNED_CONTRACTS["v2.2.1"]["arbitrum"]
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
    sentinel = PINNED_CONTRACTS["v2.2.1"]["arbitrum"]
    calls = []

    def _fetch_then_die(chain):
        calls.append(chain)
        if len(calls) == 1:
            return sentinel
        raise requests.exceptions.ConnectionError("GitHub unreachable")

    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", _fetch_then_die)
    monkeypatch.setattr(contracts_module, "GMX_REMOTE_CACHE_TTL_SECONDS", -1.0)

    assert get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE) is sentinel
    assert get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE) is sentinel
    assert len(calls) == 2, "the second call should have attempted a refresh"


def test_remote_release_raises_when_the_fetch_raises_with_nothing_cached(monkeypatch):
    """With no cache to fall back on, the failure is still reported as ValueError."""

    def _die(chain):
        raise requests.exceptions.ConnectionError("GitHub unreachable")

    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", _die)
    with pytest.raises(ValueError, match="Failed to fetch contract addresses"):
        get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE)


def test_remote_release_does_not_mask_programming_errors(monkeypatch):
    """A bug in this library must not be reported as a network failure.

    The stale-serving path exists for transport failures. Swallowing everything
    would turn, say, a ``TypeError`` from constructing ``ContractAddresses`` with a
    renamed field into a "failed to fetch" warning that quietly serves stale
    addresses — hiding a code defect behind a connectivity story.
    """
    sentinel = PINNED_CONTRACTS["v2.2.1"]["arbitrum"]
    calls = []

    def _fetch_then_bug(chain):
        calls.append(chain)
        if len(calls) == 1:
            return sentinel
        raise TypeError("ContractAddresses() got an unexpected keyword argument")

    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", _fetch_then_bug)
    monkeypatch.setattr(contracts_module, "GMX_REMOTE_CACHE_TTL_SECONDS", -1.0)

    # Warm the cache, so a stale entry exists and could have masked the bug.
    get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
        get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE)


def test_remote_release_refuses_an_unrecognised_router(monkeypatch):
    """Unknown remote router addresses cannot be paired with vendored ABIs."""
    unknown = dataclasses.replace(
        PINNED_CONTRACTS["v2.2.1"]["arbitrum"],
        exchangerouter="0x" + "ab" * 20,
    )
    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", lambda chain: unknown)

    with pytest.raises(GMXReleaseMismatchError, match="does not match the pinned"):
        get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE)


@pytest.mark.parametrize("field", ["syntheticsreader", "ordervault", "syntheticsrouter"])
def test_remote_release_refuses_mixed_contract_set(monkeypatch, field):
    """A partial address match cannot route orders through a mixed deployment."""
    mixed = dataclasses.replace(
        PINNED_CONTRACTS["v2.2.1"]["arbitrum"],
        **{field: "0x" + "ab" * 20},
    )
    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", lambda chain: mixed)
    with pytest.raises(GMXReleaseMismatchError, match=field):
        get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE)


@pytest.mark.parametrize("old_release", ["v2.2b", "v2.2c"])
def test_remote_release_refuses_a_superseded_router(monkeypatch, old_release):
    """Live resolution returning an older known release must not be paired with the new ABIs.

    The ``updates``-then-``main`` fetch does not fail when ``updates`` is rate
    limited or still serves the old release — it succeeds and returns it. That
    would pair old addresses with the v2.2.1 vendored ABIs, so it is refused.
    """
    older = PINNED_CONTRACTS[old_release]["arbitrum"]
    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", lambda chain: older)

    with pytest.raises(GMXReleaseMismatchError, match="does not match the pinned"):
        get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE)


def test_remote_release_accepts_the_exact_pinned_contract_set(monkeypatch):
    current = PINNED_CONTRACTS["v2.2.1"]["arbitrum"]
    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", lambda chain: current)

    assert get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE) is current


def test_remote_release_refuses_chain_without_vendored_abi_set(monkeypatch):
    """Remote Avalanche resolution is refused because only Arbitrum is vendored."""
    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", lambda chain: PINNED_CONTRACTS["v2.2.1"]["arbitrum"])
    with pytest.raises(GMXReleaseMismatchError, match="vendored for Arbitrum only"):
        get_contract_addresses("avalanche", release=GMX_CONTRACT_RELEASE_REMOTE)


def test_remote_release_allows_unpublished_optional_gm_oracle_provider(monkeypatch):
    """The upstream contracts file may omit the carried test-helper address."""
    remote = dataclasses.replace(PINNED_CONTRACTS["v2.2.1"]["arbitrum"], gmoracleprovider=None)
    monkeypatch.setattr(contracts_module, "_fetch_contract_addresses_from_url", lambda chain: remote)
    assert get_contract_addresses("arbitrum", release=GMX_CONTRACT_RELEASE_REMOTE).gmoracleprovider == PINNED_CONTRACTS["v2.2.1"]["arbitrum"].gmoracleprovider


@pytest.mark.parametrize("chain", ["arbitrum", "avalanche"])
def test_release_rotation_set_is_documented_v22b_to_v22c(chain):
    """Pin exactly which addresses moved between v2.2b and v2.2c (historical)."""
    old = PINNED_CONTRACTS["v2.2b"][chain]
    new = PINNED_CONTRACTS["v2.2c"][chain]

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


def test_release_rotation_set_is_documented(no_network):
    """Pin exactly which addresses moved between v2.2c and v2.2.1 on Arbitrum.

    The ``PINNED_CONTRACTS`` docstring makes a claim about what is stable across
    the upgrade, and the migration rests on it: the SyntheticsRouter keeps an
    existing ERC-20 approval valid and the OrderVault keeps a guard's
    router-to-vault mapping valid. Assert it rather than trusting the prose.
    """
    old = PINNED_CONTRACTS["v2.2c"]["arbitrum"]
    new = get_contract_addresses("arbitrum")

    rotated = {f.name for f in dataclasses.fields(old) if getattr(old, f.name) != getattr(new, f.name)}

    assert rotated == {
        "exchangerouter",
        "syntheticsreader",
        "glvreader",
        "orderhandler",
        "oracle",
        "chainlinkdatastreamprovider",
    }

    # The load-bearing half: these must not move, or the migration would need a
    # re-approval and a guard remap rather than a single whitelist entry.
    for field_name in ("syntheticsrouter", "ordervault", "datastore", "eventemitter", "depositvault", "withdrawalvault", "chainlinkpricefeedprovider"):
        assert getattr(old, field_name) == getattr(new, field_name), f"{field_name} rotated"


def test_sepolia_addresses_remain_available_for_address_only_use(no_network):
    """Address inspection remains available although Reader ABI binding is refused."""
    # Address-only inspection remains available from the registry; ABI binding
    # helpers reject this Reader's old tuple layout.
    sepolia = NETWORK_CONTRACTS["arbitrum_sepolia"]
    assert sepolia.exchangerouter == SEPOLIA_EXCHANGE_ROUTER
    assert sepolia.syntheticsreader == SEPOLIA_READER
    assert sepolia.glvreader == "0x45356cEE7f8668b7030577c13b4638802D3397Df"
    assert sepolia.orderhandler == "0xC881c2391611829d7bc81c12a285cB0201F08f8c"


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


def test_vendored_reader_abi_matches_v221():
    """The vendored Reader ABI must carry the v2.2c fields and the v2.2.1 additions.

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

    # v2.2.1 additions.
    assert "getMarketTokenPriceForDeposit" in functions
    position = next(c for c in functions["getPosition"]["outputs"][0]["components"] if c["name"] == "numbers")
    position_numbers = [c["name"] for c in position["components"]]
    assert len(position_numbers) == 15, position_numbers
    assert {"pendingImpactAmount", "cappedPnlAllowancePerPrice"} <= set(position_numbers)
    market_info = [c["name"] for c in functions["getMarketInfo"]["outputs"][0]["components"]]
    assert {"borrowingFactorForLongsAvailable", "borrowingFactorForShortsAvailable"} <= set(market_info)


def test_vendored_exchange_router_abi_matches_v221():
    """v2.2c moved the simulate* family out of ExchangeRouter into SimulationRouter."""
    abi = _load_vendored_abi("ExchangeRouter.json")
    names = {e["name"] for e in abi if e.get("type") == "function"}

    assert not {n for n in names if n.startswith("simulateExecute")}, "simulate* should be gone in v2.2c"
    assert "createTwapOrder" in names

    # The functions the Lagoon guard's GmxLib decoder validates must still be present,
    # otherwise order calldata could not be decoded on-chain.
    for required in ("createOrder", "multicall", "sendWnt", "sendTokens", "cancelOrder", "updateOrder"):
        assert required in names, f"{required} missing from vendored ExchangeRouter ABI"


def test_vendored_glv_reader_and_oracle_abis_match_v221():
    """GlvReader and Oracle gained the v2.2.1 deposit-price and raw-price getters."""
    glv = {e["name"] for e in _load_vendored_abi("GlvReader.json") if e.get("type") == "function"}
    assert {"getGlvValueForDeposit", "getGlvTokenPriceForDeposit"} <= glv

    oracle = {e["name"] for e in _load_vendored_abi("Oracle.json") if e.get("type") == "function"}
    assert {"getPrimaryRawPrice", "primaryRawPrices"} <= oracle


def test_vendored_update_order_has_decrease_position_swap_type():
    """v2.2.1 ``updateOrder`` takes ``decreasePositionSwapType`` before ``autoCancel``."""
    abi = _load_vendored_abi("ExchangeRouter.json")
    update_order = next(e for e in abi if e.get("type") == "function" and e["name"] == "updateOrder")
    names = [i["name"] for i in update_order["inputs"]]
    assert names.index("decreasePositionSwapType") == names.index("autoCancel") - 1
