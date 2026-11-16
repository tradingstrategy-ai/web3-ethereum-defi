"""Manual capacity scan: incomplete reads stay red and failures preserve rows."""

import importlib.util
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from eth_defi.vault.withdrawal_capacity import assess_withdrawal_capacity
from eth_defi.erc_4626.core import ERC4626Feature

SCRIPT = Path(__file__).parents[2] / "scripts/erc-4626/check-withdrawal-capacity.py"
spec = importlib.util.spec_from_file_location("capacity_scan", SCRIPT)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)
ADDRESS = "0x0000000000000000000000000000000000000001"


@pytest.mark.parametrize(
    "checks,expected",
    [((False, False, False), "clear"), ((False, None, False), "unknown"), ((True, False, False), "warning"), ((True, None, True), "warning + unknown")],
)
def test_status(checks, expected):
    limit = dict(zip(("capacity_warning", "pause_warning", "tiny_tvl"), checks, strict=True))
    assert scanner.assessment_status(limit) == expected


def test_input_list_and_duplicates(tmp_path):
    path = tmp_path / "vaults.json"
    path.write_text(json.dumps([{"chain": 1, "address": ADDRESS, "features": []}]))
    assert scanner.load_vaults(path, ["9998-lighter-pool-123"])[1]["id"] == "9998-lighter-pool-123"
    with pytest.raises(ValueError, match="Duplicate"):
        scanner.load_vaults(path, [f"1-{ADDRESS}"])
    with pytest.raises(ValueError, match="Malformed"):
        scanner.load_vaults(None, ["1-0xinvalid"])


def test_address_normalisation_and_type_validation(tmp_path):
    with pytest.raises(ValueError, match="Duplicate"):
        scanner.load_vaults(None, [f"1-{ADDRESS[2:]}", f"1-{ADDRESS}"])
    path = tmp_path / "vaults.json"
    path.write_text(json.dumps([{"chain_id": 1, "address": None}]))
    with pytest.raises(ValueError, match="addresses must be strings"):
        scanner.load_vaults(path, [])


def test_settings_error_is_actionable_before_rpc(monkeypatch, tmp_path):
    path = tmp_path / "vaults.json"
    path.write_text(json.dumps([{"chain_id": 1, "address": ADDRESS, "features": None}]))
    monkeypatch.setenv("VAULT_LIST_FILE", str(path))
    monkeypatch.setenv("VAULTS", "")
    monkeypatch.setattr(scanner, "read_json_rpc_url", lambda _chain: pytest.fail("Invalid settings attempted RPC"))
    with pytest.raises(SystemExit, match="Invalid scan settings: features must be a list"):
        scanner.main()


def test_native_addresses_do_not_use_rpc(monkeypatch):
    def no_rpc(chain):
        pytest.fail("Native vault attempted an EVM connection")

    monkeypatch.setattr(scanner, "read_json_rpc_url", no_rpc)
    rows = scanner.load_vaults(None, [f"9999-{ADDRESS}", "9998-lighter-pool-123"])
    report = scanner.fetch_capacity_report(rows, Decimal("0.15"), 1)
    assert report["summary"] == {"unknown": 2}
    assert all(row["withdrawal_capacity_observation"]["status"] == "unsupported" for row in report["rows"])


@pytest.mark.parametrize("name", [feature.name for feature in ERC4626Feature if feature.name.endswith("_native")])
def test_native_feature_does_not_use_rpc(name, monkeypatch, tmp_path):
    def no_rpc(_chain):
        pytest.fail("Native feature attempted EVM connection")

    path = tmp_path / "vaults.json"
    path.write_text(json.dumps([{"chain_id": 12345, "address": ADDRESS, "features": [name]}]))
    monkeypatch.setattr(scanner, "read_json_rpc_url", no_rpc)
    report = scanner.fetch_capacity_report(scanner.load_vaults(path, []), Decimal("0.15"), 1)
    assert report["rows"][0]["withdrawal_capacity_observation"]["status"] == "unsupported"


@pytest.mark.parametrize("features", [None, "euler_like", ["unknown_feature"], [1]])
def test_invalid_features_rejected_before_scan(features, tmp_path):
    path = tmp_path / "vaults.json"
    path.write_text(json.dumps([{"chain_id": 1, "address": ADDRESS, "features": features}]))
    with pytest.raises(ValueError, match="features must"):
        scanner.load_vaults(path, [])


@pytest.mark.parametrize("mismatch", [False, True])
def test_chain_failure_preserves_rows_and_structural_warning(mismatch, monkeypatch):
    def rpc(_chain):
        if not mismatch:
            raise ValueError("https://secret-provider-token")
        return "private"

    monkeypatch.setattr(scanner, "read_json_rpc_url", rpc)
    monkeypatch.setattr(scanner, "create_multi_provider_web3", lambda *_args, **_kwargs: SimpleNamespace(eth=SimpleNamespace(chain_id=2)))
    rows = scanner.load_vaults(None, [f"1-{ADDRESS}", f"1-{ADDRESS[:-1]}2"])
    rows[1]["features"] = ["forty_acres_like"]
    report = scanner.fetch_capacity_report(rows, Decimal("0.15"), 1)
    assert [row["status"] for row in report["rows"]] == ["unknown", "warning + unknown"]
    assert all(row["withdrawal_capacity_observation"]["status"] == "failed" for row in report["rows"])
    assert "secret-provider-token" not in json.dumps(report)


def test_pinned_api_and_failure_isolation(monkeypatch):
    queried = []

    class Eth:
        chain_id = 1
        block_number = 103

        def get_block(self, number):
            assert number == 100
            return {"number": 100, "hash": bytes(32), "timestamp": 1700000000}

    def factory(web3, address, **kwargs):
        assert kwargs["default_block_identifier"] == 100
        if address.endswith("2"):
            raise RuntimeError("Sensitive provider URL must not reach output")
        assert kwargs["features"] == {ERC4626Feature.euler_like}

        def fetch(**kwargs):
            queried.append(kwargs)
            return assess_withdrawal_capacity({"status": "measured", "current_capacity": "15000.000000000000000001", "current_tvl": "100000", "current_tvl_usd": "100000", "deposits_paused": False, "withdrawals_paused": False, "observed_at": datetime.now(UTC).isoformat()}, kwargs["capacity_threshold"])

        return SimpleNamespace(fetch_withdrawal_capacity_limit=fetch)

    monkeypatch.setattr(scanner, "read_json_rpc_url", lambda chain: "private")
    monkeypatch.setattr(scanner, "create_multi_provider_web3", lambda *args, **kwargs: SimpleNamespace(eth=Eth()))
    monkeypatch.setattr(scanner, "create_vault_instance", factory)
    rows = scanner.load_vaults(None, [f"1-{ADDRESS}", f"1-{ADDRESS[:-1]}2"])
    for row in rows:
        row["features"] = ["euler_like"]
    rows[1]["features"] = ["forty_acres_like"]
    report = scanner.fetch_capacity_report(rows, Decimal("0.2"), 2)
    assert queried == [{"block_identifier": 100, "capacity_threshold": Decimal("0.2")}]
    assert len(report["rows"]) == 2
    assert report["rows"][1]["withdrawal_capacity_observation"]["status"] == "failed"
    assert "Sensitive" not in json.dumps(report)
    assert report["summary"] == {"warning": 1, "warning + unknown": 1}
    assert '"current_capacity": "15000.000000000000000001"' in json.dumps(report)


def test_leftmost_colours():
    rows = []
    for capacity in ("15000.000000000001", "14999.999999999999"):
        limit = {"capacity_warning": Decimal(capacity) < Decimal("15000"), "pause_warning": False, "tiny_tvl": False, "current_capacity": capacity, "current_tvl": "100000", "capacity_reason": "Example"}
        rows.append({"id": "1-example", "status": scanner.assessment_status(limit), "withdrawal_capacity_limit": limit, "withdrawal_capacity_observation": {"status": "measured", "denomination_symbol": "USDC"}})
    output = scanner.render_table({"rows": rows})
    assert "\033[32m✓\033[0m" in output
    assert "\033[31m✗\033[0m" in output
    assert output.index("✓") < output.index("1-example")
