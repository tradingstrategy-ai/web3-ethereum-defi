"""HyperCore uncertainty boundaries keep their sequential precedence when evaluated together."""

import pandas as pd

from eth_defi.hyperliquid.permission import select_permission_state

ADDRESS = "0x0000000000000000000000000000000000000001"


def _record(kind: str, identity: str, observed_at: str | None, **metadata) -> dict:
    """Create one selector input record with the observation table's columns."""
    return {
        "vault_address": ADDRESS,
        "record_kind": kind,
        "provenance": "observed",
        "observation_id": identity,
        "permission_observed_at": pd.Timestamp(observed_at) if observed_at else pd.NaT,
        "evidence_available_at": pd.NaT,
        "capacity_observed_at": pd.NaT,
        "source_endpoint": "POST /info vaultDetails",
        "is_closed": None,
        "allow_deposits": None,
        "relationship_type": None,
        "leader_fraction": float("nan"),
        "max_deposit": float("nan"),
        "reason": None,
        "effective_from": pd.NaT,
        "effective_to": pd.NaT,
        **metadata,
    }


def test_overlapping_uncertainty_boundaries_keep_sequential_precedence() -> None:
    """Mask decisions and pick reasons exactly as applying boundaries one at a time.

    All uncertainty boundaries of a vault are evaluated together with numpy,
    because the per-boundary loop dominated permission projection time. A masked
    decision loses its clocks, so every later covering boundary masks it again
    and supplies the reason, even one the original receipt was fresh for.

    1. Build an Open receipt, a later-starting boundary A and an earlier-starting overlapping boundary B listed after it.
    2. Select permission state for daily decisions across both intervals.
    3. Verify the receipt survives where only B covers, and A's gap carries B's reason.
    """
    # 1. Build an Open receipt, a later-starting boundary A and an earlier-starting overlapping boundary B listed after it.
    observations = pd.DataFrame(
        [
            _record("observation", "open", "2026-09-10 06:00", is_closed=False, allow_deposits=True, relationship_type="normal", leader_fraction=0.1),
            _record("uncertainty_boundary", "a", None, provenance="corrupted_unknown", effective_from=pd.Timestamp("2026-09-12"), effective_to=pd.Timestamp("2026-09-15"), reason="A"),
            _record("uncertainty_boundary", "b", None, provenance="corrupted_unknown", effective_from=pd.Timestamp("2026-09-10"), effective_to=pd.Timestamp("2026-09-16"), reason="B"),
        ]
    )

    # 2. Select permission state for daily decisions across both intervals.
    decisions = pd.DataFrame({"vault_address": ADDRESS, "timestamp": pd.date_range("2026-09-11", "2026-09-16", freq="D")})
    state = select_permission_state(observations, decisions, "1D").set_index("timestamp")

    # 3. Verify the receipt survives where only B covers, and A's gap carries B's reason.
    for day in ("2026-09-11", "2026-09-15", "2026-09-16"):
        assert state.loc[pd.Timestamp(day), "observation_id"] == "open", day
        assert state.loc[pd.Timestamp(day), "allow_deposits"], day
    for day in ("2026-09-12", "2026-09-13", "2026-09-14"):
        assert state.loc[pd.Timestamp(day), "provenance"] == "corrupted_unknown", day
        assert state.loc[pd.Timestamp(day), "reason"] == "B", day
        assert pd.isna(state.loc[pd.Timestamp(day), "permission_observed_at"]), day
