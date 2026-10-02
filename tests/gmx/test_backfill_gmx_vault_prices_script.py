"""Tests for the zero-configuration GMX historical backfill script."""

import importlib.util
import pickle
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault import scan_all_chains
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

DEFAULT_MAX_WORKERS = 4


def load_backfill_module() -> ModuleType:
    """Load the hyphenated GMX backfill script as a Python module.

    :return:
        Imported script module without invoking its command-line entry point.
    """

    repository_root = Path(__file__).resolve().parents[2]
    script_path = repository_root / "scripts" / "erc-4626" / "backfill-gmx-vault-prices.py"
    spec = importlib.util.spec_from_file_location("backfill_gmx_vault_prices", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fetch_gmx_full_backfill_range_uses_safe_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """The automatic range starts at block one and stops at one resolved safe head."""

    module = load_backfill_module()
    web3 = SimpleNamespace()
    monkeypatch.setattr(module, "get_almost_latest_block_number", lambda _value: 456_789)

    assert module.fetch_gmx_full_backfill_range(web3) == (1, 456_789)


def test_main_backfills_both_chains_without_range_parameters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Default invocation runs full hourly Arbitrum and Avalanche backfills."""

    module = load_backfill_module()
    calls: list[dict[str, object]] = []

    for name in ("CHAIN", "START_BLOCK", "END_BLOCK", "FREQUENCY", "VAULT_ADDRESSES", "DRY_RUN", "MAX_WORKERS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(module, "setup_console_logging", lambda **_kwargs: None)
    monkeypatch.setattr(module, "get_pipeline_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "wait_other_writers", lambda *_args, **_kwargs: nullcontext())

    def record_backfill(**kwargs: object) -> None:
        """Capture one chain invocation without network or filesystem writes."""

        calls.append(kwargs)

    monkeypatch.setattr(module, "_run_backfill", record_backfill)

    module.main()

    assert module.GMX_BACKFILL_FREQUENCY == "1h"
    assert [call["chain_name"] for call in calls] == ["arbitrum", "avalanche"]
    assert all(call["max_workers"] == DEFAULT_MAX_WORKERS for call in calls)
    assert all(call["vault_database"] == tmp_path / "vault-metadata-db.pickle" for call in calls)
    assert all(call["price_database"] == tmp_path / "vault-prices-1h.parquet" for call in calls)
    assert all(call["context_database"] == tmp_path / "vault-historical-context.duckdb" for call in calls)


@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("include_enabled", [False, True])
def test_gmx_blacklist_scopes_source_fetch_and_price_replacement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], scheduled: bool, include_enabled: bool) -> None:
    """Preserve excluded markets across scheduled and manual backfill paths.

    The manual path owns a full historical replacement, while the scheduled
    path merges legacy reader states. Exercise their actual selection logic
    with a genuinely blacklisted market and an eligible enabled one. Both
    source collection and writer addresses must exclude the disabled market;
    an all-excluded catalogue must not invoke either expensive operation.

    :param monkeypatch: Replace transport and price IO, retaining real selection.
    :param tmp_path: Isolated metadata, legacy reader-state and price locations.
    :param capsys: Capture the manual script's existing operator summary.
    :param scheduled: Exercise the all-chain scanner instead of the manual script.
    :param include_enabled: Keep an eligible market beside the excluded product.
    :return: None; request scope, stored history and reader-state merge are checked.
    """
    module = scan_all_chains if scheduled else load_backfill_module()
    disabled = VaultSpec(42161, "0xe2fedb9e6139a182b98e7c2688ccfa3e9a53c665")
    enabled = VaultSpec(42161, "0x9c2433dfd71096c435be9465220bb2b189375ea7")
    specs = [disabled, enabled] if include_enabled else [disabled]
    rows = {spec: {"_detection_data": SimpleNamespace(chain=42161, address=spec.vault_address, features={ERC4626Feature.gmx_gm}, first_seen_at_block=1, deposit_count=100), "_denomination_token": {"symbol": "USDC"}} for spec in specs}
    metadata_path = tmp_path / "vaults.pickle"
    VaultDatabase(rows=rows).write(metadata_path)
    state_path = tmp_path / "readers.pickle"
    # Persist qualification deliberately: checking the blacklist only after
    # admission would otherwise promote this disabled market into the expected
    # live-reader set and incorrectly report it overdue again.
    original_states = {spec: {"last_block": 10, "last_tvl": Decimal(10_000), "max_tvl": Decimal(10_000), "token_symbol": "USDC", "unsupported_token": False} for spec in specs}
    state_path.write_bytes(pickle.dumps(original_states))
    price_path = tmp_path / "prices.parquet"
    price_path.write_bytes(b"existing prices must remain available")
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161, block_number=100))
    monkeypatch.setattr(module, "create_multi_provider_web3", lambda *_args, **_kwargs: web3)
    monkeypatch.setattr(module, "MultiProviderWeb3Factory", lambda *_args, **_kwargs: None)

    def prepare_hypersync(*_args: object, **_kwargs: object) -> SimpleNamespace:
        """Reject needless source setup when the catalogue is entirely excluded.

        Both callers must resolve eligible products before preparing Hypersync,
        independently of whether source fetching would later be a no-op.

        :param _args: Connection arguments from the tested caller.
        :param _kwargs: Source configuration options irrelevant to selection.
        :return: Minimal source client for eligible test products.
        """
        assert include_enabled
        return SimpleNamespace(hypersync_client=object())

    def prepare_token_cache(*_args: object) -> SimpleNamespace:
        """Keep the manual all-excluded path free of token-cache preparation.

        The scheduled scanner opens its shared cache before selection, while
        the stateless manual script can defer this until a product is eligible.

        :param _args: Optional isolated cache path passed by the manual script.
        :return: Minimal cache supporting the manual commit boundary.
        """
        assert include_enabled or scheduled
        return SimpleNamespace(commit=lambda: None)

    monkeypatch.setattr(module, "configure_hypersync_from_env", prepare_hypersync)
    monkeypatch.setattr(module, "TokenDiskCache", prepare_token_cache)
    selected_sources: list[set[str]] = []
    selected_writers: list[set[str]] = []

    def fetch_context(**kwargs: object) -> SimpleNamespace:
        """Capture actual source selection without contacting Hypersync.

        The source API consumes a lazy address iterator, independently of the
        writer's adapters, so inspect its consumed values at this boundary.

        :param kwargs: Prefill arguments, including the selected product iterator.
        :return: Empty successful context receipt.
        """
        selected_sources.append(set(kwargs["product_addresses"]))
        return SimpleNamespace(observations_fetched=0, observations_inserted=0)

    def create_adapter(_web3: object, address: HexAddress, _features: set[ERC4626Feature], **_kwargs: object) -> SimpleNamespace:
        """Fail if blacklist selection leaks into adapter construction.

        A skipped contract must never reach token preparation or its constructor,
        even when persisted qualification would otherwise request fresh prices.

        :param _web3: Unused connection supplied by the tested caller.
        :param address: Selected market token address.
        :param _features: Detection flags supplied by metadata selection.
        :param _kwargs: Adapter options that are outside this selection test.
        :return: Minimal eligible adapter used by both writer call sites.
        """
        assert address == enabled.vault_address
        return SimpleNamespace(address=address, get_spec=lambda: enabled)

    def write_prices(**kwargs: object) -> dict:
        """Capture replacement scope and advance only the eligible reader.

        The scheduled caller must merge this partial progress into the complete
        legacy map, while a manual backfill must leave that map untouched.

        :param kwargs: Historical-writer arguments from the tested entry point.
        :return: Minimal successful writer receipt consumed by the scanner.
        """
        selected_writers.append(kwargs["vault_addresses"])
        assert {vault.address for vault in kwargs["vaults"]} == {enabled.vault_address}
        return {"reader_states": {enabled: {**original_states[enabled], "last_block": 100}}, "rows_written": 0, "freshness_rows_written": 0, "freshness_eligible_vaults": 1, "overdue_vaults": {}, "unknown_conversion_vaults": [], "start_block": 11, "end_block": 100}

    monkeypatch.setattr(module, "fetch_and_store_gmx_historical_share_prices", fetch_context)
    monkeypatch.setattr(module, "create_vault_instance", create_adapter)
    monkeypatch.setattr(module, "scan_historical_prices_to_parquet", write_prices)
    if scheduled:
        ok, metrics = module.scan_prices_for_chain("https://rpc.example", 1, "1h", vault_db_path=metadata_path, uncleaned_price_path=price_path, reader_state_path=state_path, historical_context_path=tmp_path / "context.duckdb")
        assert ok
        assert metrics["blacklisted_vaults"] == 1
        restored = pickle.loads(state_path.read_bytes())
        assert restored[disabled] == original_states[disabled]
        if include_enabled:
            assert restored[enabled]["last_block"] == 100
    else:
        monkeypatch.setattr(module, "read_json_rpc_url", lambda _chain: "https://rpc.example")
        monkeypatch.setattr(module, "fetch_gmx_full_backfill_range", lambda _web3: (1, 100))
        monkeypatch.setattr(module, "pformat_scan_result", lambda _result: "completed")
        module._run_backfill(chain_name="arbitrum", max_workers=1, vault_database=metadata_path, price_database=price_path, context_database=tmp_path / "context.duckdb")
        assert pickle.loads(state_path.read_bytes()) == original_states
    expected = [{enabled.vault_address}] if include_enabled else []
    assert selected_sources == selected_writers == expected
    assert price_path.read_bytes() == b"existing prices must remain available"
    capsys.readouterr()
