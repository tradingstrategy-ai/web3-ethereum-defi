"""Unit tests for the low-TVL vault metrics freshness gate.

The freshness gate recalculates low-TVL vaults only every
``LOW_TVL_METRICS_MAX_AGE`` while keeping every export-relevant vault fresh
on every run. These tests cover the due/skip partitioning, the staggered
expiry, the state file self-healing and the crypto previous-record patch
validation.
"""

import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from eth_defi.research.metrics_freshness import (
    LOW_TVL_METRICS_MAX_AGE,
    LOW_TVL_THRESHOLD_ETH,
    LOW_TVL_THRESHOLD_USD,
    VAULT_METRICS_STATE_SCHEMA_VERSION,
    _stagger_offset,  # noqa: PLC2701
    clear_period_rankings,
    compute_vault_tvl_observations,
    load_metrics_state,
    load_valid_previous_crypto_records,
    make_empty_metrics_state,
    partition_due_vault_ids,
    refresh_metrics_state,
    resolve_metrics_state_path,
    save_metrics_state,
)
from eth_defi.vault import crypto_vaults
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


def _make_state(now: datetime.datetime, entries: dict[str, str | None]) -> dict:
    """Build a freshness state document with per-vault update timestamps.

    :param now:
        Current naive UTC datetime.
    :param entries:
        Vault id to ``metrics_updated_at`` ISO value (``None`` allowed).
    :return:
        State mapping accepted by :func:`partition_due_vault_ids`.
    """
    state = make_empty_metrics_state(now)
    for vault_id, updated_at in entries.items():
        state["vaults"][vault_id] = {
            "metrics_updated_at": updated_at,
            "current_tvl": 1.0,
            "peak_tvl": 1.0,
            "denomination_family": "stablecoin",
        }
    return state


def test_partition_due_vault_ids_rules() -> None:
    """Due rules force recomputation for every unsafe freshness case.

    1. A new vault (no state entry) is due.
    2. A low-TVL vault with fresh metrics is skipped.
    3. A low-TVL vault older than the max age is due.
    4. Current TVL at or above the family threshold is due even when fresh.
    5. Peak TVL at or above the export threshold is due even when fresh.
    6. Null/non-finite current TVL is due (safe default).
    7. Malformed or future ``metrics_updated_at`` is due.
    8. Without a validated previous record (crypto patch precondition) the
       vault is due even when fresh.
    """
    now = datetime.datetime(2026, 9, 22, 12, 0, 0)
    fresh = (now - datetime.timedelta(hours=1)).isoformat()
    stale = (now - LOW_TVL_METRICS_MAX_AGE - datetime.timedelta(days=1)).isoformat()
    family_by_id = dict.fromkeys(("new", "fresh", "stale", "promoted", "peak", "null-tvl", "bad-ts", "future-ts", "unpatchable"), "stablecoin")
    export_threshold_by_id = dict.fromkeys(family_by_id, LOW_TVL_THRESHOLD_USD)
    state = _make_state(
        now,
        {
            "fresh": fresh,
            "stale": stale,
            "promoted": fresh,
            "peak": fresh,
            "null-tvl": fresh,
            "bad-ts": "not-a-timestamp",
            "future-ts": (now + datetime.timedelta(days=1)).isoformat(),
            "unpatchable": fresh,
        },
    )
    current_tvl_by_id = {
        "new": 100.0,
        "fresh": 100.0,
        "stale": 100.0,
        "promoted": LOW_TVL_THRESHOLD_USD,
        "peak": 100.0,
        "null-tvl": None,
        "bad-ts": 100.0,
        "future-ts": 100.0,
        "unpatchable": 100.0,
    }
    peak_tvl_by_id = {
        "new": 100.0,
        "fresh": 100.0,
        "stale": 100.0,
        "promoted": 100.0,
        "peak": LOW_TVL_THRESHOLD_USD,
        "null-tvl": 100.0,
        "bad-ts": 100.0,
        "future-ts": 100.0,
        "unpatchable": 100.0,
    }

    # 1-7: patch replay is unconditional for the stablecoin bundle, so a
    # fresh low-TVL vault is skipped regardless of patch availability
    due_ids, skipped_ids = partition_due_vault_ids(
        set(family_by_id),
        state,
        current_tvl_by_id,
        peak_tvl_by_id,
        family_by_id,
        export_threshold_by_id,
        now,
    )
    assert due_ids == {"new", "stale", "promoted", "peak", "null-tvl", "bad-ts", "future-ts"}
    assert skipped_ids == {"fresh", "unpatchable"}

    # 8: crypto bundle requires a validated previous record to patch
    due_ids, skipped_ids = partition_due_vault_ids(
        {"unpatchable"},
        state,
        current_tvl_by_id,
        peak_tvl_by_id,
        family_by_id,
        export_threshold_by_id,
        now,
        patchable_ids=set(),
    )
    assert due_ids == {"unpatchable"}
    assert skipped_ids == set()


def test_partition_due_vault_ids_respects_family_thresholds() -> None:
    """Family thresholds gate skipping in the family's own unit.

    1. An ETH-family vault at 2.0 ETH (below the 2.5 ETH freshness threshold)
       with fresh metrics is skipped.
    2. The same vault at 2.5 ETH is due (promotion must be immediate).
    """
    now = datetime.datetime(2026, 9, 22, 12, 0, 0)
    fresh = (now - datetime.timedelta(hours=1)).isoformat()
    state = _make_state(now, {"eth-low": fresh, "eth-at-threshold": fresh})
    due_ids, skipped_ids = partition_due_vault_ids(
        {"eth-low", "eth-at-threshold"},
        state,
        {"eth-low": 2.0, "eth-at-threshold": LOW_TVL_THRESHOLD_ETH},
        {"eth-low": 2.0, "eth-at-threshold": 2.0},
        {"eth-low": "eth", "eth-at-threshold": "eth"},
        {"eth-low": 10_000.0, "eth-at-threshold": 10_000.0},
        now,
    )
    assert due_ids == {"eth-at-threshold"}
    assert skipped_ids == {"eth-low"}


def test_stagger_offset_is_deterministic_and_bounded() -> None:
    """Stagger spreads low-TVL expiry without extra state.

    1. The same vault id always resolves to the same offset.
    2. The offset is bounded by ``[0, LOW_TVL_METRICS_MAX_AGE)``.
    3. A vault expires exactly when ``now - updated_at - offset`` exceeds the
       max age, not before.
    """
    vault_id = "1-0x0000000000000000000000000000000000000001"
    offset = _stagger_offset(vault_id)
    assert _stagger_offset(vault_id) == offset
    assert datetime.timedelta(0) <= offset < LOW_TVL_METRICS_MAX_AGE

    now = datetime.datetime(2026, 9, 22, 12, 0, 0)
    state = make_empty_metrics_state(now)
    just_before = now - LOW_TVL_METRICS_MAX_AGE - offset
    state["vaults"][vault_id] = {
        "metrics_updated_at": just_before.isoformat(),
        "current_tvl": 100.0,
        "peak_tvl": 100.0,
        "denomination_family": "stablecoin",
    }
    due_ids, _ = partition_due_vault_ids({vault_id}, state, {vault_id: 100.0}, {vault_id: 100.0}, {vault_id: "stablecoin"}, {vault_id: LOW_TVL_THRESHOLD_USD}, now)
    assert due_ids == set()

    state["vaults"][vault_id]["metrics_updated_at"] = (just_before - datetime.timedelta(seconds=1)).isoformat()
    due_ids, _ = partition_due_vault_ids({vault_id}, state, {vault_id: 100.0}, {vault_id: 100.0}, {vault_id: "stablecoin"}, {vault_id: LOW_TVL_THRESHOLD_USD}, now)
    assert due_ids == {vault_id}


def test_metrics_state_round_trip_and_corrupt_quarantine(tmp_path: Path) -> None:
    """State persists atomically and self-heals from corruption.

    1. A saved state round-trips through load unchanged.
    2. A corrupt state file is moved aside and an empty state is returned, so
       the next run fully recomputes instead of trusting garbage.
    """
    now = datetime.datetime(2026, 9, 22, 12, 0, 0)
    state_path = resolve_metrics_state_path(tmp_path)
    state = _make_state(now, {"1-0xvault": now.isoformat()})

    # 1
    save_metrics_state(state, state_path)
    assert load_metrics_state(state_path, now) == state

    # 2
    state_path.write_text("{not json", encoding="utf-8")
    loaded = load_metrics_state(state_path, now)
    assert loaded == make_empty_metrics_state(now)
    quarantined = list(tmp_path.glob("vault-metrics-state.corrupt-*"))
    assert len(quarantined) == 1


def test_refresh_metrics_state_advances_only_computed_ids() -> None:
    """A vault that failed metric calculation stays due for the next run.

    1. ``metrics_updated_at`` advances only for ids in ``computed_ids``.
    2. TVL observations refresh for every seen vault, computed or not.
    3. Vaults absent from the price data are dropped from the state.
    """
    now = datetime.datetime(2026, 9, 22, 12, 0, 0)
    old = (now - datetime.timedelta(days=10)).isoformat()
    state = _make_state(now, {"computed": old, "failed": old, "gone": old})

    refresh_metrics_state(
        state,
        seen_ids={"computed", "failed"},
        computed_ids={"computed"},
        current_tvl_by_id={"computed": 111.0, "failed": 222.0},
        peak_tvl_by_id={"computed": 333.0, "failed": 444.0},
        family_by_id={"computed": "stablecoin", "failed": "stablecoin"},
        now=now,
    )

    assert state["vaults"]["computed"]["metrics_updated_at"] == now.isoformat()
    assert state["vaults"]["failed"]["metrics_updated_at"] == old
    assert state["vaults"]["failed"]["current_tvl"] == pytest.approx(222.0)
    assert "gone" not in state["vaults"]
    assert state["schema_version"] == VAULT_METRICS_STATE_SCHEMA_VERSION

    # The failed vault is still due on the next run
    due_ids, _ = partition_due_vault_ids(
        {"failed"},
        state,
        {"failed": 222.0},
        {"failed": 444.0},
        {"failed": "stablecoin"},
        {"failed": LOW_TVL_THRESHOLD_USD},
        now,
    )
    assert due_ids == {"failed"}


def test_compute_vault_tvl_observations() -> None:
    """Cheap TVL pass reads current (last) and peak (max) assets per vault.

    1. Current TVL is the last observation, peak TVL the maximum.
    2. Non-finite observations become ``None`` so the gate falls back to due.
    """
    prices_df = pd.DataFrame(
        {
            "id": ["1-0xa", "1-0xa", "1-0xb"],
            "total_assets": [500.0, 100.0, float("inf")],
        }
    )
    current_by_id, peak_by_id = compute_vault_tvl_observations(prices_df)
    assert current_by_id["1-0xa"] == pytest.approx(100.0)
    assert peak_by_id["1-0xa"] == pytest.approx(500.0)
    assert current_by_id["1-0xb"] is None
    assert peak_by_id["1-0xb"] is None

    empty_current, empty_peak = compute_vault_tvl_observations(pd.DataFrame({"id": ["1-0xa"]}))
    assert empty_current == {}
    assert empty_peak == {}


def test_load_valid_previous_crypto_records_validation(tmp_path: Path) -> None:
    """Previous crypto records are only reused when the document is compatible.

    1. A matching document yields its records keyed by vault id.
    2. A schema version or whitelist digest mismatch yields nothing, forcing
       recomputation of every vault.
    3. Corrupt or missing files yield nothing instead of raising.
    """
    metadata_path = tmp_path / "crypto-vault-metadata.json"
    document = {
        "schema_version": 1,
        "denomination_whitelist_sha256": "abc123",
        "vaults": [{"id": "1-0xa", "denomination_family": "eth"}],
    }

    # 1
    metadata_path.write_text(json.dumps(document), encoding="utf-8")
    records = load_valid_previous_crypto_records(metadata_path, schema_version=1, whitelist_sha256="abc123")
    assert records == {"1-0xa": {"id": "1-0xa", "denomination_family": "eth"}}

    # 2
    assert load_valid_previous_crypto_records(metadata_path, schema_version=2, whitelist_sha256="abc123") == {}
    assert load_valid_previous_crypto_records(metadata_path, schema_version=1, whitelist_sha256="def456") == {}

    # 3
    metadata_path.write_text("{broken", encoding="utf-8")
    assert load_valid_previous_crypto_records(metadata_path, schema_version=1, whitelist_sha256="abc123") == {}
    assert load_valid_previous_crypto_records(tmp_path / "missing.json", schema_version=1, whitelist_sha256="abc123") == {}


def test_clear_period_rankings() -> None:
    """Replayed records must not carry ranks from a different vault universe."""
    record = {
        "id": "1-0xa",
        "period_results": [
            {"period": "1W", "ranking_overall": 3, "ranking_chain": 1, "ranking_protocol": 2, "ranking_curator": 5, "cagr_net": 0.1},
            "not-a-dict",
        ],
    }
    clear_period_rankings(record)
    cleared = record["period_results"][0]
    assert cleared["ranking_overall"] is None
    assert cleared["ranking_chain"] is None
    assert cleared["ranking_protocol"] is None
    assert cleared["ranking_curator"] is None
    assert cleared["cagr_net"] == pytest.approx(0.1)
    assert record["period_results"][1] == "not-a-dict"

    assert clear_period_rankings({"id": "1-0xb"}) == {"id": "1-0xb"}


def test_crypto_two_run_patch_keeps_previous_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0914
    """Skipped low-TVL crypto vaults are patched from the previous metadata JSON.

    ``calculate_lifetime_metrics`` is mocked because the metric mathematics
    itself is covered by its own extensive tests; this test exercises the
    freshness wiring around it (due gate, previous-record patch, state
    commit). The mock returns one minimal exportable row per vault in the
    frame it receives, with a fixed ``generated_at`` we can trace. Both
    vaults are USDC-denominated: native ETH/BTC vaults are excluded upstream
    by the lifetime-peak admission and never reach the freshness gate.

    1. Run 1: vault A (peak 6,000 USD, above the 5,000 USD export threshold,
       current 100 USD) is computed and exported; vault B (peak and current
       100 USD) is computed but never exported and gets no sticky entry.
    2. Run 2: history is trimmed so vault A's peak drops below the export
       threshold; vault A is skipped and patched from the previous metadata
       JSON with the original ``generated_at``, while vault B is skipped
       without a record because it provably cannot enter the export.
    3. Run 3: the freshness state file is deleted; both vaults become due
       again and are recomputed (self-healing from state loss).
    """
    vault_address = "0x00000000000000000000000000000000000000aa"
    vault_id = f"1-{vault_address}"
    small_vault_address = "0x00000000000000000000000000000000000000bb"
    small_vault_id = f"1-{small_vault_address}"
    fixed_generated_at = pd.Timestamp("2026-09-22T00:00:00")

    vault_row = {
        "Denomination": "USDC",
        "_detection_data": SimpleNamespace(chain=1, address=vault_address),
        "_denomination_token": {"address": "0x00000000000000000000000000000000000000bb", "decimals": 6},
    }
    small_vault_row = {
        "Denomination": "USDC",
        "_detection_data": SimpleNamespace(chain=1, address=small_vault_address),
        "_denomination_token": {"address": "0x00000000000000000000000000000000000000cc", "decimals": 6},
    }
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(
        rows={
            VaultSpec(1, vault_address): vault_row,
            VaultSpec(1, small_vault_address): small_vault_row,
        }
    ).write(vault_db_path)

    def write_prices(path: Path, rows: list[dict]) -> None:
        frame = pd.DataFrame(rows)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        frame.set_index("timestamp", inplace=True)
        frame.to_parquet(path)

    full_history_path = tmp_path / "full-history.parquet"
    write_prices(
        full_history_path,
        [
            {"id": vault_id, "chain": 1, "address": vault_address, "share_price": 1.0, "total_assets": 6000.0, "timestamp": "2026-09-01 00:00:00"},
            {"id": vault_id, "chain": 1, "address": vault_address, "share_price": 1.0, "total_assets": 100.0, "timestamp": "2026-09-21 00:00:00"},
            {"id": small_vault_id, "chain": 1, "address": small_vault_address, "share_price": 1.0, "total_assets": 100.0, "timestamp": "2026-09-01 00:00:00"},
            {"id": small_vault_id, "chain": 1, "address": small_vault_address, "share_price": 1.0, "total_assets": 100.0, "timestamp": "2026-09-21 00:00:00"},
        ],
    )
    trimmed_history_path = tmp_path / "trimmed-history.parquet"
    write_prices(
        trimmed_history_path,
        [
            {"id": vault_id, "chain": 1, "address": vault_address, "share_price": 1.0, "total_assets": 100.0, "timestamp": "2026-09-21 00:00:00"},
            {"id": small_vault_id, "chain": 1, "address": small_vault_address, "share_price": 1.0, "total_assets": 100.0, "timestamp": "2026-09-21 00:00:00"},
        ],
    )

    mock_calls: list[set[str]] = []

    def fake_calculate_lifetime_metrics(daily_prices_df, vault_db, stablecoin_rate_feeder=None, crypto_usd_conversion_context=None):  # noqa: ARG001
        vault_ids = set(daily_prices_df["id"].astype(str))
        mock_calls.append(vault_ids)
        records = [
            {
                "id": vault_id_,
                "current_nav": 100.0,
                "peak_nav": 6000.0 if vault_id_ == vault_id else 100.0,
                "period_results": [],
                "generated_at": fixed_generated_at,
            }
            for vault_id_ in sorted(vault_ids)
        ]
        return pd.DataFrame(records)

    monkeypatch.setattr(crypto_vaults, "calculate_lifetime_metrics", fake_calculate_lifetime_metrics)

    metadata_path = tmp_path / "crypto-vaults" / "crypto-vault-metadata.json"
    sticky_state_path = tmp_path / "crypto-vaults" / "crypto-vault-export-state.json"
    metrics_state_path = tmp_path / "crypto-vaults" / "crypto-vault-metrics-state.json"

    # 1: first run computes both vaults; only vault A is exported and gets a
    # sticky entry, vault B is never exported
    metadata = crypto_vaults.build_crypto_vault_metadata(
        vault_db_path=vault_db_path,
        cleaned_price_path=full_history_path,
        metadata_path=metadata_path,
        sticky_state_path=sticky_state_path,
    )
    assert mock_calls[-1] == {vault_id, small_vault_id}
    assert [record["id"] for record in metadata["vaults"]] == [vault_id]
    assert metadata["vaults"][0]["sticky_export"] is False
    assert metadata_path.exists()
    assert metrics_state_path.exists()
    sticky_after_run1 = json.loads(sticky_state_path.read_text(encoding="utf-8"))["vaults"]
    assert vault_id in sticky_after_run1
    assert small_vault_id not in sticky_after_run1
    first_run_updated_at = json.loads(metrics_state_path.read_text(encoding="utf-8"))["vaults"][vault_id]["metrics_updated_at"]

    # 2: trimmed history drops the peak below the export threshold; vault A
    # is skipped and patched from the previous metadata JSON, vault B is
    # skipped without a record because it cannot enter the export
    metadata = crypto_vaults.build_crypto_vault_metadata(
        vault_db_path=vault_db_path,
        cleaned_price_path=trimmed_history_path,
        metadata_path=metadata_path,
        sticky_state_path=sticky_state_path,
    )
    # No new metrics calculation happened: the mocked calculator was not
    # called again for either skipped vault
    assert len(mock_calls) == 1
    assert [record["id"] for record in metadata["vaults"]] == [vault_id]
    assert metadata["vaults"][0]["generated_at"] == fixed_generated_at.isoformat()
    # Patched crypto rows keep their original record verbatim (accepted
    # staleness): no scrubbing or extra markers, unlike the stablecoin
    # bundle's sticky fallback.
    assert metadata["vaults"][0]["sticky_export"] is False
    state_after_run2 = json.loads(metrics_state_path.read_text(encoding="utf-8"))["vaults"]
    assert state_after_run2[vault_id]["metrics_updated_at"] == first_run_updated_at
    # The never-exported vault was skipped without a record, but its
    # freshness timestamp still advanced so it is not due again next run
    assert state_after_run2[small_vault_id]["metrics_updated_at"] is not None
    assert vault_id in json.loads(sticky_state_path.read_text(encoding="utf-8"))["vaults"]

    # 3: state loss makes both vaults due again (self-healing full recompute)
    metrics_state_path.unlink()
    metadata = crypto_vaults.build_crypto_vault_metadata(
        vault_db_path=vault_db_path,
        cleaned_price_path=trimmed_history_path,
        metadata_path=metadata_path,
        sticky_state_path=sticky_state_path,
    )
    assert len(mock_calls) == 2
    assert mock_calls[-1] == {vault_id, small_vault_id}
    assert [record["id"] for record in metadata["vaults"]] == [vault_id]
    assert metrics_state_path.exists()


def test_top_vaults_json_freshness_gate_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The stablecoin export's pruned gate read and due-filtered metrics read.

    ``calculate_lifetime_metrics`` is mocked because the metric mathematics
    itself is covered by its own extensive tests; this test exercises the
    read path and the freshness wiring around it (gate partition, due-only
    metrics input, sticky replay bookkeeping, state commit).

    1. Run 1 (cold): both vaults are due and computed; only vault A (peak
       6,000 USD) passes the export filter.
    2. Run 2: vault A stays due through the export-threshold rule and is
       recomputed; vault B (peak and current 100 USD) is skipped as fresh
       low-TVL and its state timestamp does not advance.
    3. Run 3: the freshness state file is deleted; both vaults become due
       again (self-healing from state loss).
    """
    from eth_defi.compat import native_datetime_utc_now
    from eth_defi.vault import top_vaults_json

    monkeypatch.delenv("VAULT_EXPORT_STATE_PATH", raising=False)
    monkeypatch.delenv("VAULT_METRICS_STATE_PATH", raising=False)

    vault_address = "0x00000000000000000000000000000000000000aa"
    vault_id = f"1-{vault_address}"
    small_vault_address = "0x00000000000000000000000000000000000000bb"
    small_vault_id = f"1-{small_vault_address}"
    fixed_generated_at = pd.Timestamp("2026-09-22T00:00:00")
    last_updated_at = native_datetime_utc_now() - datetime.timedelta(days=1)

    def make_vault_row(address: str) -> dict:
        return {
            "Denomination": "USDC",
            "Protocol": "Sky",
            "_detection_data": SimpleNamespace(chain=1, address=address),
            "_denomination_token": {"address": "0x00000000000000000000000000000000000000cc", "decimals": 6},
        }

    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(
        rows={
            VaultSpec(1, vault_address): make_vault_row(vault_address),
            VaultSpec(1, small_vault_address): make_vault_row(small_vault_address),
        }
    ).write(vault_db_path)

    prices = pd.DataFrame(
        [
            {"id": vault_id, "chain": 1, "address": vault_address, "share_price": 1.0, "total_assets": 6000.0, "timestamp": "2026-09-01 00:00:00"},
            {"id": vault_id, "chain": 1, "address": vault_address, "share_price": 1.0, "total_assets": 100.0, "timestamp": "2026-09-21 00:00:00"},
            {"id": small_vault_id, "chain": 1, "address": small_vault_address, "share_price": 1.0, "total_assets": 100.0, "timestamp": "2026-09-01 00:00:00"},
            {"id": small_vault_id, "chain": 1, "address": small_vault_address, "share_price": 1.0, "total_assets": 100.0, "timestamp": "2026-09-21 00:00:00"},
        ]
    )
    prices["timestamp"] = pd.to_datetime(prices["timestamp"])
    prices.set_index("timestamp", inplace=True)
    parquet_path = tmp_path / "prices.parquet"
    prices.to_parquet(parquet_path)

    mock_calls: list[set[str]] = []

    def fake_calculate_lifetime_metrics(returns_df, vault_db, core3_protocols=None, xerberus_pools=None, xerberus_protocols=None):  # noqa: ARG001
        vault_ids = set(returns_df["id"].astype(str))
        mock_calls.append(vault_ids)
        records = [
            {
                "id": vault_id_,
                "chain_id": 1,
                "address": vault_id_.split("-", 1)[1],
                "name": f"Vault {vault_id_[-4:]}",
                "protocol_slug": "sky",
                "curator_slug": "test-curator",
                "current_nav": 100.0,
                "peak_nav": 6000.0 if vault_id_ == vault_id else 100.0,
                "last_updated_at": last_updated_at,
                "period_results": [],
                "generated_at": fixed_generated_at,
            }
            for vault_id_ in sorted(vault_ids)
        ]
        return pd.DataFrame(records)

    monkeypatch.setattr(top_vaults_json, "calculate_lifetime_metrics", fake_calculate_lifetime_metrics)

    output_path = tmp_path / "stablecoin-vault-metrics.json"

    def run_main() -> dict:
        return top_vaults_json.main(
            data_dir=tmp_path,
            vault_db_path=vault_db_path,
            parquet_path=parquet_path,
            output_path=output_path,
            core3_db_path=Path("/nonexistent"),
            xerberus_db_path=Path("/nonexistent"),
            feed_db_path=Path("/nonexistent"),
        )

    # 1: cold run computes both vaults; only vault A passes the export filter
    output = run_main()
    assert mock_calls[-1] == {vault_id, small_vault_id}
    assert [record["id"] for record in output["vaults"]] == [vault_id]
    sticky_after_run1 = json.loads((tmp_path / "vault-export-state.json").read_text(encoding="utf-8"))["vaults"]
    assert vault_id in sticky_after_run1
    assert small_vault_id not in sticky_after_run1
    state_after_run1 = json.loads((tmp_path / "vault-metrics-state.json").read_text(encoding="utf-8"))["vaults"]
    first_run_updated_at = state_after_run1[vault_id]["metrics_updated_at"]
    assert state_after_run1[small_vault_id]["metrics_updated_at"] is not None

    # 2: vault A is recomputed through the export-threshold rule; vault B is
    # skipped as fresh low-TVL and its timestamp does not advance
    output = run_main()
    assert mock_calls[-1] == {vault_id}
    assert [record["id"] for record in output["vaults"]] == [vault_id]
    state_after_run2 = json.loads((tmp_path / "vault-metrics-state.json").read_text(encoding="utf-8"))["vaults"]
    assert state_after_run2[small_vault_id]["metrics_updated_at"] == state_after_run1[small_vault_id]["metrics_updated_at"]
    assert state_after_run2[vault_id]["metrics_updated_at"] >= first_run_updated_at

    # 3: state loss makes both vaults due again (self-healing full recompute)
    (tmp_path / "vault-metrics-state.json").unlink()
    output = run_main()
    assert mock_calls[-1] == {vault_id, small_vault_id}
    assert [record["id"] for record in output["vaults"]] == [vault_id]
