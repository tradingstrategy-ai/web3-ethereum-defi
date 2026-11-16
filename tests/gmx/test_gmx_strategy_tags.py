"""Stablecoin AMM classifications for GMX pools, their migration and the GLV [USDG-USDG] copy."""

import copy
import datetime
import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from eth_typing import HexAddress
from eth_utils import to_checksum_address

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.gmx import vault_sync
from eth_defi.gmx.vault import GMXLiquidityVault, GMXMarketVault
from eth_defi.gmx.vault_catalog import GMXVaultProduct
from eth_defi.gmx.vault_sync import fetch_and_sync_gmx_vault_catalogue
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics, export_lifetime_row
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.handwritten_metadata import GMX_VAULT_METADATA, get_handwritten_vault_metadata
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner
from eth_defi.vault_protocols.repairs.strategy_tags import resolve_strategy_tags

SCRIPT = "069-gmx-stablecoin-amm-strategy-tags.py"

#: Frozen expectations for swap-only stablecoin GM pools.
SWAP_TAGS = {StrategyTag.amm_stablecoin, StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.market_making_amm}

#: Frozen expectations for the USDG-backed GLV, which also backs perpetual traders.
GLV_TAGS = SWAP_TAGS | {StrategyTag.perpetual_futures}

#: Reviewed deployments: chain, address, adapter feature and expected tags.
REVIEWED = (
    (42161, "0x4cd5a94a30876320ac65f2e192493ee476f13866", ERC4626Feature.gmx_glv, GLV_TAGS),
    (42161, "0x408e8e83d4b8ac4bdeffdf896b49268a5fe5ef44", ERC4626Feature.gmx_gm, SWAP_TAGS),
    (42161, "0x9c2433dfd71096c435be9465220bb2b189375ea7", ERC4626Feature.gmx_gm, SWAP_TAGS),
    (42161, "0xb686bcb112660343e6d15bdb65297e110c8311c4", ERC4626Feature.gmx_gm, SWAP_TAGS),
    (42161, "0xe2fedb9e6139a182b98e7c2688ccfa3e9a53c665", ERC4626Feature.gmx_gm, SWAP_TAGS),
    (42161, "0x45ad16aaa28fb66ef74d5ca0ab9751f2817c81a4", ERC4626Feature.gmx_gm, SWAP_TAGS),
    (43114, "0x297e71a931c5825867e8fb937ae5cda9891c2e99", ERC4626Feature.gmx_gm, SWAP_TAGS),
    (43114, "0xa7b768d6a1f746fd5a513d440df2970ff099b0fc", ERC4626Feature.gmx_gm, SWAP_TAGS),
    (43114, "0xf3652eba45dc761e7add4091627d5cda21f61613", ERC4626Feature.gmx_gm, SWAP_TAGS),
)

#: GM HYPE [USDG-USDG]: stablecoin-backed, but concentrated single-market trader risk.
EXCLUDED_SINGLE_MARKET = (42161, "0x162c091263fbb3dfaf1d55dcc979533eb341da5f")


def _vault(chain: int, address: str, feature: ERC4626Feature) -> GMXMarketVault | GMXLiquidityVault:
    """Create a GMX adapter without RPC access for accessor checks.

    :param chain: Deployment chain.
    :param address: Share-token address, deliberately upper-cased.
    :param feature: GM or GLV adapter feature.
    :return: Adapter with only its deployment identity set.
    """
    vault = object.__new__(GMXLiquidityVault if feature == ERC4626Feature.gmx_glv else GMXMarketVault)
    vault.spec = VaultSpec(chain, HexAddress(address.upper()))
    return vault


def _row(spec: VaultSpec, feature: ERC4626Feature, tags: set[StrategyTag] | None) -> dict:
    """Create a persisted GMX row with preservation sentinels.

    :param spec: Deployment identity.
    :param feature: GM or GLV adapter feature.
    :param tags: Previously persisted strategy tags.
    :return: A row with independently observable unrelated fields.
    """
    timestamp = datetime.datetime(2026, 11, 12)  # noqa: DTZ001 - Persisted timestamps are naive UTC.
    detection = ERC4262VaultDetection(chain=spec.chain_id, address=spec.vault_address, first_seen_at_block=1, first_seen_at=timestamp, features={feature, ERC4626Feature.amm_pool_like}, updated_at=timestamp, deposit_count=1, redeem_count=1)
    return {"Protocol": "GMX", "_detection_data": detection, "_description": "Preserve description", "_flags": {"liquidity_provision"}, "_strategy_tags": tags}


@pytest.mark.parametrize(("chain", "address", "feature", "expected"), REVIEWED)
def test_reviewed_gmx_stablecoin_tags(chain: int, address: str, feature: ERC4626Feature, expected: set[StrategyTag]) -> None:
    """The adapter and the persisted-tag resolver agree for each reviewed pool.

    :param chain: Deployment chain.
    :param address: Reviewed share-token address.
    :param feature: GM or GLV adapter feature.
    :param expected: Frozen expected tags.
    :return: None after checking accessor, resolver and copy isolation.
    """
    vault = _vault(chain, address, feature)
    assert vault.get_strategy_tags() == expected
    vault.get_strategy_tags().clear()
    assert vault.get_strategy_tags() == expected
    spec = VaultSpec(chain, HexAddress(address))
    tags, _source = resolve_strategy_tags(spec, _row(spec, feature, None))
    assert tags == expected


def test_single_market_usdg_pool_is_not_stablecoin_amm() -> None:
    """A single-market USDG perpetual pool remains unclassified."""
    chain, address = EXCLUDED_SINGLE_MARKET
    assert _vault(chain, address, ERC4626Feature.gmx_gm).get_strategy_tags() is None


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stage the reviewed GMX rows plus an excluded control in an isolated runner.

    :param tmp_path: Test-owned directory.
    :param monkeypatch: Remove legacy production storage overrides.
    :return: Arguments for the real numbered migration runner.
    """
    for key in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = {VaultSpec(chain, HexAddress(address)): _row(VaultSpec(chain, HexAddress(address)), feature, None) for chain, address, feature, _tags in REVIEWED}
    excluded = VaultSpec(*EXCLUDED_SINGLE_MARKET)
    rows[excluded] = _row(excluded, ERC4626Feature.gmx_gm, None)
    VaultDatabase(rows=rows, last_scanned_block={42161: 123}).write(data_dir / "vault-metadata-db.pickle")
    (data_dir / "vault-prices-1h.parquet").write_bytes(b"Preserve historical bytes")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = next(item for item in runner.discover_migrations() if item.name == SCRIPT)
    (scripts / SCRIPT).write_bytes(script.source)
    return {"data_dir": data_dir, "scripts_dir": scripts, "provenance": {"in_docker": False}}


def test_preview_apply_and_repeat(pipeline: dict) -> None:
    """Preview writes nothing; apply tags exactly nine rows and is idempotent.

    :param pipeline: Isolated runner arguments.
    :return: None after checking rows, unrelated files and the journal.
    """
    data_dir = pipeline["data_dir"]
    path = data_dir / "vault-metadata-db.pickle"
    original = VaultDatabase.read(path)
    expected = copy.deepcopy(original.rows)
    for chain, address, _feature, tags in REVIEWED:
        expected[VaultSpec(chain, HexAddress(address))]["_strategy_tags"] = tags
    files = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=True, **pipeline) == [SCRIPT]
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == files
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT]
    actual = VaultDatabase.read(path)
    assert actual.rows == expected
    assert actual.rows[VaultSpec(*EXCLUDED_SINGLE_MARKET)]["_strategy_tags"] is None
    report = json.loads((data_dir / "migration.json").read_text())[SCRIPT]["other_data"]
    assert report["updated_fields"] == len(REVIEWED)
    assert report["missing_specs"] == []
    assert (data_dir / "vault-prices-1h.parquet").read_bytes() == files["vault-prices-1h.parquet"]
    after_bytes = path.read_bytes()
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == []
    (data_dir / "migration.json").unlink()
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT]
    assert path.read_bytes() == after_bytes


def test_wrong_adapter_is_atomic(pipeline: dict) -> None:
    """A GLV target detected as a GM market fails without writing.

    :param pipeline: Isolated runner arguments.
    :return: None after asserting failure and byte preservation.
    """
    path = pipeline["data_dir"] / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    row = database.rows[VaultSpec(42161, "0x4cd5a94a30876320ac65f2e192493ee476f13866")]
    row["_detection_data"] = replace(row["_detection_data"], features={ERC4626Feature.gmx_gm})
    database.write(path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Incompatible adapter"):
        runner.run_pending_migrations(dry_run=False, **pipeline)
    assert path.read_bytes() == before


def test_missing_target_is_reported(pipeline: dict) -> None:
    """A missing target is reported and never inserted.

    :param pipeline: Isolated runner arguments.
    :return: None after checking the reported gap and remaining changes.
    """
    path = pipeline["data_dir"] / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    missing = VaultSpec(43114, "0xf3652eba45dc761e7add4091627d5cda21f61613")
    del database.rows[missing]
    database.write(path)
    runner.run_pending_migrations(dry_run=False, **pipeline)
    assert missing not in VaultDatabase.read(path).rows
    report = json.loads((pipeline["data_dir"] / "migration.json").read_text())[SCRIPT]["other_data"]
    assert report["missing_specs"] == [missing.as_string_id()]
    assert report["updated_fields"] == len(REVIEWED) - 1


#: GLV [USDG-USDG], the only GMX product with reviewed handwritten copy.
GLV_USDG = (42161, "0x4cd5a94a30876320ac65f2e192493ee476f13866")


def test_glv_usdg_handwritten_description_format() -> None:
    """The adapter exposes the reviewed copy in the published field formats.

    :return: None after checking accessors, normalisation and scoping.
    """
    metadata = GMX_VAULT_METADATA[GLV_USDG]
    vault = _vault(*GLV_USDG, ERC4626Feature.gmx_glv)
    assert vault.description == metadata.description
    assert vault.short_description == metadata.short_description
    assert metadata.description.startswith("#### Vault's trading strategy\n\n")
    assert normalise_vault_description(metadata.description) == metadata.description
    assert normalise_vault_short_description(metadata.short_description) == metadata.short_description
    assert metadata.link == "https://tradingstrategy.ai/vaults/glv-usdg-usdg"
    # Each source page is linked once.
    links = re.findall(r"\]\((https://[^)]+)\)", metadata.description)
    assert len(links) == len(set(links))
    # Other GMX products and same-address deployments elsewhere keep generated copy.
    other = _vault(*EXCLUDED_SINGLE_MARKET, ERC4626Feature.gmx_gm)
    assert other.description is None
    assert other.short_description is None
    assert get_handwritten_vault_metadata(43114, GLV_USDG[1]) is None


def test_catalogue_sync_writes_handwritten_glv_usdg_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scanner's catalogue refresh persists the reviewed copy for the GLV only.

    A generated description remains for a GM market synchronised in the same
    run, and a repeat run during a token-registry outage keeps the reviewed copy.

    :param monkeypatch: Replace GMX Reader, token and scan-record reads.
    :return: None after checking both persisted rows.
    """
    usdg = to_checksum_address("0x004b506865409877c9fa29bfb1eba929984b9bbc")
    index = to_checksum_address("0x1000000000000000000000000000000000000001")
    gm_address = to_checksum_address(EXCLUDED_SINGLE_MARKET[1])
    glv_address = to_checksum_address(GLV_USDG[1])
    gm = GMXVaultProduct(chain_id=42161, token_address=gm_address, product_type="gm", symbol="GM", name="GMX market", decimals=18, component_addresses=(gm_address, index, usdg, usdg), accepted_deposit_tokens=(usdg, usdg), is_enabled=True)
    glv = GMXVaultProduct(chain_id=42161, token_address=glv_address, product_type="glv", symbol="GLV", name="GMX Liquidity Vault", decimals=18, component_addresses=(glv_address, usdg, usdg, gm_address), accepted_deposit_tokens=(usdg, usdg), is_enabled=True)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161, block_number=456, get_block=lambda block_number: {"timestamp": 1_700_000_000 + block_number}))
    registry = {index: {"symbol": "HYPE"}}
    monkeypatch.setattr(vault_sync, "fetch_gmx_v2_vault_products", lambda *_args, **_kwargs: iter((gm, glv)))
    monkeypatch.setattr(vault_sync, "_fetch_token_symbol", lambda _web3, _chain_id, _address, _token_cache: "USDG")
    monkeypatch.setattr(vault_sync, "get_tokens_metadata_dict", lambda _chain: registry)
    monkeypatch.setattr(vault_sync, "create_vault_scan_record", lambda _web3, detection, _block_number, _token_cache: {"_detection_data": detection, "features": set(detection.features), "Name": "GMX", "Protocol": "GMX", "Denomination": "USD"})
    metadata = GMX_VAULT_METADATA[GLV_USDG]
    vault_db = VaultDatabase()
    for block_number in (123, 456):
        fetch_and_sync_gmx_vault_catalogue(web3=web3, vault_db=vault_db, token_cache={}, block_number=block_number)
        glv_row = vault_db.rows[VaultSpec(*GLV_USDG)]
        assert glv_row["Name"] == "GLV [USDG-USDG]"
        assert glv_row["_description"] == metadata.description
        assert glv_row["_short_description"] == metadata.short_description
        gm_row = vault_db.rows[VaultSpec(*EXCLUDED_SINGLE_MARKET)]
        assert gm_row["_description"].startswith("Liquidity providers supply USDG")
        assert gm_row["_short_description"] is None
        # The second run simulates an unreachable GMX token registry.
        registry.clear()
    # The individual vault JSON export publishes both reviewed fields unchanged.
    spec = VaultSpec(*GLV_USDG)
    row = vault_db.rows[spec] | {"Address": spec.vault_address, "Symbol": "GLV", "Share token": "GMX Liquidity Vault", "_fees": FeeData(fee_mode=VaultFeeMode.feeless, management=0.0, performance=0.0, deposit=0.0, withdraw=0.0), "_strategy_tags": GLV_TAGS}
    prices = pd.DataFrame({"id": spec.as_string_id(), "address": spec.vault_address, "chain": 42161, "block_number": range(35), "share_price": [1 + i * 0.0001 for i in range(35)], "total_assets": 100_000.0, "total_supply": 100_000.0, "event_count": 20}, index=pd.date_range("2026-10-06", periods=35, freq="D"))
    metrics = calculate_lifetime_metrics(calculate_hourly_returns_for_all_vaults(prices), {spec: row})
    exported = json.loads(json.dumps(export_lifetime_row(metrics.iloc[0]), allow_nan=False))
    assert exported["description"] == metadata.description
    assert exported["short_description"] == metadata.short_description
    assert "amm_stablecoin" in exported["strategy_tags"]
