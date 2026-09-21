"""Shared health record for GMX API hosts.

The sync driver, the async driver and the oracle query each fail over from tier
to tier per request. This record is what lets one request's discovery that a
host is dead spare every other request the same discovery.
"""

from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from eth_defi.gmx import tier_health
from eth_defi.gmx.tier_health import (
    TIER_DOWN_COOLDOWN_SECONDS,
    clear_tier_health,
    healthy_first,
    is_tier_down,
    mark_tier_down,
)

PRIMARY = "https://arbitrum-api.gmxinfra.io/prices/candles?tokenSymbol=BTC&period=1h"
BACKUP = "https://arbitrum-api.gmxinfra2.io/prices/candles?tokenSymbol=BTC&period=1h"


@pytest.fixture(autouse=True)
def _clean_health() -> Iterator[None]:
    """Isolate the module-level health record."""
    clear_tier_health()
    yield
    clear_tier_health()


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """A hand-driven monotonic clock for the health module only."""
    state = {"now": 1000.0}
    monkeypatch.setattr(tier_health, "time", SimpleNamespace(monotonic=lambda: state["now"]))
    return state


def test_a_tier_marked_down_is_usable_again_after_the_cooldown(clock: dict[str, float]) -> None:
    """Catches: a tier that stays skipped forever after a single blip."""
    mark_tier_down(PRIMARY)
    assert is_tier_down(PRIMARY)

    clock["now"] += TIER_DOWN_COOLDOWN_SECONDS + 1

    assert not is_tier_down(PRIMARY)


@pytest.mark.usefixtures("clock")
def test_health_is_kept_per_host_not_per_endpoint() -> None:
    """A host found dead through one endpoint is dead for every endpoint, and its sibling host is unaffected.

    Catches: keying by full URL, where each endpoint (and each of 100 token
    symbols) would have to rediscover the dead host on its own.
    """
    mark_tier_down("https://arbitrum-api.gmxinfra.io/prices/candles?tokenSymbol=BTC")

    assert is_tier_down("https://arbitrum-api.gmxinfra.io/signed_prices/latest")
    assert is_tier_down("https://arbitrum-api.gmxinfra.io/prices/candles?tokenSymbol=ETH")
    assert not is_tier_down("https://arbitrum-api.gmxinfra2.io/signed_prices/latest")


@pytest.mark.usefixtures("clock")
def test_healthy_first_moves_down_tiers_to_the_back_and_keeps_the_rest_in_order() -> None:
    """Down tiers are reordered, never dropped.

    Catches: dropping down tiers (a total outage would then have nothing left to
    try) and scrambling the configured order of the healthy ones.
    """
    tiers = ["https://a.example/x", "https://b.example/x", "https://c.example/x", "https://d.example/x"]
    mark_tier_down(tiers[0])
    mark_tier_down(tiers[2])

    assert healthy_first(tiers) == [tiers[1], tiers[3], tiers[0], tiers[2]]


@pytest.mark.usefixtures("clock")
def test_healthy_first_keeps_the_configured_order_when_all_tiers_are_up_or_all_are_down() -> None:
    """Catches: reordering when there is nothing to prefer."""
    tiers = ["https://a.example/x", "https://b.example/x", "https://c.example/x"]
    assert healthy_first(tiers) == tiers

    for tier in tiers:
        mark_tier_down(tier)

    assert healthy_first(tiers) == tiers


@pytest.mark.usefixtures("clock")
def test_healthy_first_works_on_tuples_through_a_key_function() -> None:
    """The sync driver keeps ``(name, url)`` pairs. Catches: ignoring the ``key`` argument."""
    tiers = [("primary", "https://a.example/x"), ("backup", "https://b.example/x")]
    mark_tier_down("https://a.example/x")

    assert healthy_first(tiers, key=lambda tier: tier[1]) == [("backup", "https://b.example/x"), ("primary", "https://a.example/x")]


def test_mark_tier_down_reports_only_the_up_to_down_transition(clock: dict[str, float]) -> None:
    """Callers log one WARNING per transition. Catches: reporting every repeat, which brings back the log flood."""
    assert mark_tier_down(PRIMARY) is True
    assert mark_tier_down(PRIMARY) is False

    clock["now"] += TIER_DOWN_COOLDOWN_SECONDS + 1

    assert mark_tier_down(PRIMARY) is True


@pytest.mark.usefixtures("clock")
def test_marking_one_host_down_leaves_the_backup_host_up() -> None:
    """Catches: a mark that leaks across hosts."""
    mark_tier_down(PRIMARY)

    assert not is_tier_down(BACKUP)
