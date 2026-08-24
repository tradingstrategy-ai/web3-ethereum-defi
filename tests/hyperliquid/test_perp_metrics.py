"""Unit tests for Hyperliquid perpetual-position metric collection."""

from decimal import Decimal
from types import SimpleNamespace

from eth_defi.hyperliquid import perp_metrics
from eth_defi.hyperliquid.perp_metrics import CLEARINGHOUSE_STATE_REQUESTS_PER_SECOND, collect_hyperliquid_vault_observations
from eth_defi.hyperliquid.vault import VaultSummary


def _make_summary(index: int) -> VaultSummary:
    """Create a minimal public vault summary for threaded collector tests."""
    return VaultSummary(
        name=f"Test vault {index}",
        vault_address=f"0x{index:040x}",
        leader=f"0x{index + 100:040x}",
        tvl=Decimal("10000"),
        is_closed=False,
        relationship_type="normal",
    )


def test_collect_positions_uses_one_limiter_per_proxy(monkeypatch) -> None:
    """Position reads must use the fast endpoint-specific rate limit per proxy IP."""
    clones: list[tuple[int, float]] = []
    sessions_seen: list[int] = []

    class DummySession:
        """Record the clones requested by the collector."""

        proxy_enabled = True
        proxy_count = 2

        @staticmethod
        def clone_for_worker(proxy_start_index: int, requests_per_second: float) -> SimpleNamespace:
            """Return a uniquely identifiable worker session."""
            clones.append((proxy_start_index, requests_per_second))
            return SimpleNamespace(worker_index=proxy_start_index)

    def fake_fetch(session: SimpleNamespace, summary: VaultSummary, _timeout: float) -> tuple[object, dict[str, str]]:
        """Avoid HTTP reads while recording the worker session used."""
        sessions_seen.append(session.worker_index)
        return object(), {"vault_address": summary.vault_address}

    monkeypatch.setattr(perp_metrics, "_fetch_hyperliquid_vault_bundle", fake_fetch)

    def fake_write(*_args: object) -> None:
        """Avoid DuckDB writes in this session-allocation test."""

    monkeypatch.setattr(perp_metrics, "write_perp_vault_observation_bundle", fake_write)

    summaries = [_make_summary(index) for index in range(5)]

    attempts = collect_hyperliquid_vault_observations(
        DummySession(),
        object(),
        summaries,
        max_workers=10,
        timeout=30.0,
    )

    assert attempts == len(summaries)
    assert clones == [(0, CLEARINGHOUSE_STATE_REQUESTS_PER_SECOND), (1, CLEARINGHOUSE_STATE_REQUESTS_PER_SECOND)]
    assert len(sessions_seen) == len(summaries)


def test_collect_positions_uses_one_limiter_without_proxies(monkeypatch) -> None:
    """A direct-IP scan must not multiply its endpoint-specific rate limit."""
    clones: list[tuple[int, float]] = []

    class DummySession:
        """Record the sole direct-IP worker requested by the collector."""

        proxy_enabled = False
        proxy_count = 0

        @staticmethod
        def clone_for_worker(proxy_start_index: int, requests_per_second: float) -> SimpleNamespace:
            """Return the uniquely identifiable direct-IP worker session."""
            clones.append((proxy_start_index, requests_per_second))
            return SimpleNamespace()

    def fake_fetch(_session: SimpleNamespace, _summary: VaultSummary, _timeout: float) -> tuple[object, dict[str, str]]:
        """Avoid HTTP reads in this direct-IP allocation test."""
        return object(), {}

    def fake_write(*_args: object) -> None:
        """Avoid DuckDB writes in this session-allocation test."""

    monkeypatch.setattr(perp_metrics, "_fetch_hyperliquid_vault_bundle", fake_fetch)
    monkeypatch.setattr(perp_metrics, "write_perp_vault_observation_bundle", fake_write)

    summaries = [_make_summary(index) for index in range(3)]
    attempts = collect_hyperliquid_vault_observations(
        DummySession(),
        object(),
        summaries,
        max_workers=10,
        timeout=30.0,
    )

    assert attempts == len(summaries)
    assert clones == [(0, CLEARINGHOUSE_STATE_REQUESTS_PER_SECOND)]
