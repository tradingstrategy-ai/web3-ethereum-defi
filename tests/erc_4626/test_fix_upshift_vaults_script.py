"""Tests for the Upshift targeted repair script."""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

UPSHIFT_EVM_SNAPSHOT_VAULT_COUNT = 104
LATEST_EXISTING_BLOCK = 25_300_000
NEXT_BLOCK_AFTER_LATEST_EXISTING = 25_300_001
CHAIN_SCAN_RESULT = {
    "rows_written": 2,
    "rows_deleted": 0,
    "output_fname": Path("upshift-test.parquet"),
    "chain_id": 1,
    "file_size": 1,
    "existing": False,
    "existing_row_count": 0,
    "chunks_done": 1,
    "reader_states": {},
    "start_block": 1,
    "end_block": 2,
}


def load_fix_upshift_vaults_module() -> ModuleType:
    """Load the hyphenated maintenance script as a normal Python module.

    The script lives under ``scripts/`` and is intended to be called from the
    command line, so it cannot be imported with a normal dotted module path.

    :return:
        Loaded script module.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "fix-upshift-vaults.py"
    spec = importlib.util.spec_from_file_location("fix_upshift_vaults_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fix_upshift_vaults_snapshot_contains_all_known_evm_vaults() -> None:
    """Check that the baked Upshift snapshot has the full EVM vault list."""
    module = load_fix_upshift_vaults_module()

    refs = module.parse_snapshot_csv(module.UPSHIFT_VAULT_SNAPSHOT_CSV)
    specs = {ref.get_spec() for ref in refs}

    assert len(refs) == UPSHIFT_EVM_SNAPSHOT_VAULT_COUNT
    assert all(ref.chain_id > 0 for ref in refs)
    assert all(ref.first_seen_at_block >= 1 for ref in refs)
    assert len(specs) == len(refs)
    assert module.VaultSpec(1, "0xcd69123b3fbbfc666e1f6a501da27b564c00de54") in specs
    assert module.VaultSpec(1, "0xc87dbbb8c67e4f19fcd2e297c05937567b2572ce") in specs


def test_fix_upshift_vaults_filters_status_and_visibility(monkeypatch) -> None:
    """Check operator filters for status and visible API rows."""
    module = load_fix_upshift_vaults_module()

    refs = module.parse_snapshot_csv(module.UPSHIFT_VAULT_SNAPSHOT_CSV)
    monkeypatch.setenv("UPSHIFT_STATUS", "active")
    monkeypatch.setenv("UPSHIFT_VISIBLE_ONLY", "true")

    filtered = module.filter_references(refs)

    assert filtered
    assert all(ref.status == "active" for ref in filtered)
    assert all(ref.is_visible is True for ref in filtered)


def test_fix_upshift_vaults_detects_unsupported_chains() -> None:
    """Check that unknown Upshift API chains are treated as unsupported."""
    module = load_fix_upshift_vaults_module()

    assert module.is_supported_chain(1)
    assert not module.is_supported_chain(999_999_999)


def test_fix_upshift_vaults_start_block_uses_existing_price_rows(monkeypatch) -> None:
    """Check the batched scan start block calculation for one target vault."""
    module = load_fix_upshift_vaults_module()
    refs = module.parse_snapshot_csv(module.UPSHIFT_VAULT_SNAPSHOT_CSV)
    tori = next(ref for ref in refs if ref.address.lower() == "0xcd69123b3fbbfc666e1f6a501da27b564c00de54")

    monkeypatch.delenv("START_BLOCK", raising=False)

    assert module.fetch_vault_price_start_block(tori, {tori.address.lower(): LATEST_EXISTING_BLOCK}, rewrite_targeted=False) == NEXT_BLOCK_AFTER_LATEST_EXISTING
    assert module.fetch_vault_price_start_block(tori, {tori.address.lower(): LATEST_EXISTING_BLOCK}, rewrite_targeted=True) == tori.first_seen_at_block


def test_fix_upshift_vaults_scans_chain_once_for_multiple_vaults(monkeypatch, tmp_path) -> None:
    """Check that the batched price scanner calls the historical scanner once."""
    module = load_fix_upshift_vaults_module()
    refs = [ref for ref in module.parse_snapshot_csv(module.UPSHIFT_VAULT_SNAPSHOT_CSV) if ref.chain_id == 1][:2]
    vaults = [object(), object()]
    historical_calls = []

    def fake_fetch_latest_existing_price_blocks(*_args):
        return {}

    def fake_configure_hypersync_from_env(*_args):
        return SimpleNamespace(hypersync_client=None)

    def fake_pformat_scan_result(*_args):
        return "ok"

    monkeypatch.setattr(module, "fetch_latest_existing_price_blocks", fake_fetch_latest_existing_price_blocks)
    monkeypatch.setattr(module, "configure_hypersync_from_env", fake_configure_hypersync_from_env)
    monkeypatch.setattr(module, "pformat_scan_result", fake_pformat_scan_result)

    def fake_scan_historical_prices_to_parquet(**kwargs):
        historical_calls.append(kwargs)
        result = dict(CHAIN_SCAN_RESULT)
        result["output_fname"] = kwargs["output_fname"]
        result["reader_states"] = kwargs["reader_states"]
        result["start_block"] = kwargs["start_block"]
        result["end_block"] = kwargs["end_block"]
        return result

    monkeypatch.setattr(module, "scan_historical_prices_to_parquet", fake_scan_historical_prices_to_parquet)

    result = module.scan_chain_price_history(
        web3=SimpleNamespace(eth=SimpleNamespace(chain_id=1)),
        json_rpc_url="https://example.invalid",
        token_cache=object(),
        reader_states={},
        refs=refs,
        vaults=vaults,
        price_path=tmp_path / "prices.parquet",
        end_block=30_000_000,
        frequency="1h",
        max_workers=1,
        rewrite_targeted=False,
    )

    assert result is not None
    assert len(historical_calls) == 1
    assert historical_calls[0]["vaults"] == vaults
    assert historical_calls[0]["vault_addresses"] == {ref.address.lower() for ref in refs}


def test_fix_upshift_vaults_excludes_caught_up_vaults_from_chain_scan(monkeypatch, tmp_path) -> None:
    """Check that caught-up vaults are not rewritten by an earlier chain start."""
    module = load_fix_upshift_vaults_module()
    refs = [ref for ref in module.parse_snapshot_csv(module.UPSHIFT_VAULT_SNAPSHOT_CSV) if ref.chain_id == 1][:2]
    vaults = [object(), object()]
    historical_calls = []
    end_block = 30_000_000

    def fake_fetch_latest_existing_price_blocks(*_args):
        return {refs[1].address.lower(): end_block}

    def fake_configure_hypersync_from_env(*_args):
        return SimpleNamespace(hypersync_client=None)

    def fake_pformat_scan_result(*_args):
        return "ok"

    def fake_scan_historical_prices_to_parquet(**kwargs):
        historical_calls.append(kwargs)
        result = dict(CHAIN_SCAN_RESULT)
        result["output_fname"] = kwargs["output_fname"]
        result["reader_states"] = kwargs["reader_states"]
        result["start_block"] = kwargs["start_block"]
        result["end_block"] = kwargs["end_block"]
        return result

    monkeypatch.setattr(module, "fetch_latest_existing_price_blocks", fake_fetch_latest_existing_price_blocks)
    monkeypatch.setattr(module, "configure_hypersync_from_env", fake_configure_hypersync_from_env)
    monkeypatch.setattr(module, "pformat_scan_result", fake_pformat_scan_result)
    monkeypatch.setattr(module, "scan_historical_prices_to_parquet", fake_scan_historical_prices_to_parquet)

    result = module.scan_chain_price_history(
        web3=SimpleNamespace(eth=SimpleNamespace(chain_id=1)),
        json_rpc_url="https://example.invalid",
        token_cache=object(),
        reader_states={},
        refs=refs,
        vaults=vaults,
        price_path=tmp_path / "prices.parquet",
        end_block=end_block,
        frequency="1h",
        max_workers=1,
        rewrite_targeted=False,
    )

    assert result is not None
    assert len(historical_calls) == 1
    assert historical_calls[0]["vaults"] == [vaults[0]]
    assert historical_calls[0]["vault_addresses"] == {refs[0].address.lower()}
