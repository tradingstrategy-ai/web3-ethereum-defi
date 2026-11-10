"""Reviewed human-selection classifications and their persistence boundaries."""

import copy
import datetime
import json
from dataclasses import replace
from pathlib import Path

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner
from eth_defi.vault_protocols.repairs.strategy_tags import resolve_strategy_tags

SCRIPT = "062-curator-discretionary-strategy-tags.py"
EXPECTED_CHANGES = 9

#: Frozen review expectations: chain, address, protocol, previous tags and current tags.
REVIEWED = (
    (1, "0x10dbaf26e61db145c9af764fcd3bdd3993e3ce8a", "Lagoon Finance", None, None),
    (1, "0x2746f31096f23670caf4043f8b30d8d02405a257", "Lagoon Finance", "amm arbitrage carry_trade lending multistrategy", "amm arbitrage carry_trade lending multistrategy"),
    (1, "0x401550bc1ce4fb0ffdd86015f49142bbaad29ad8", "Lagoon Finance", "algorithmic_trading directional_trading discretionary_trading perpetual_futures", "algorithmic_trading directional_trading discretionary_trading perpetual_futures"),
    (1, "0x47491305479635357df29fe0cb6f1af10e51d4da", "Lagoon Finance", None, None),
    (1, "0x4cb280e63251b9ab24a54def74bf5995d82ff398", "Lagoon Finance", "delta_neutral liquidity_provider market_making multistrategy", "delta_neutral liquidity_provider market_making multistrategy"),
    (1, "0x59b7942f7d2afd085691ce65c152e0d38d4eff22", "Lagoon Finance", "lending liquidity_provider multistrategy", "lending liquidity_provider multistrategy"),
    (1, "0x66dcb62da5430d800a1c807822c25be17138fda8", "Lagoon Finance", None, None),
    (1, "0x71652d4898de9a7dd35e472a5fe4577ec69d82f2", "Lagoon Finance", None, None),
    (1, "0x7d798ce99ee2adc183a865d76b112810d5395ea2", "Lagoon Finance", None, None),
    (1, "0x7d2c2f54792ad72cb834d298f542145b06b703cb", "Lagoon Finance", "algorithmic_trading lending lending_optimisation", "algorithmic_trading discretionary_trading lending lending_optimisation"),
    (1, "0x8595f5ee036e1f7c4f3ca66327ba5f38c21cb7fb", "Lagoon Finance", None, None),
    (1, "0x9a458370556a68bdc130a80c35b294d0ad947a6a", "Lagoon Finance", None, None),
    (1, "0xca790385506b790554571cbc9da73f0130cdcfd5", "Lagoon Finance", "amm lending liquidity_provider multistrategy", "amm lending liquidity_provider multistrategy"),
    (1, "0xf02030ab0d7385ce4cc2f7f64b7b44430fb44c89", "Lagoon Finance", "algorithmic_trading lending lending_optimisation", "algorithmic_trading lending lending_optimisation"),
    (143, "0xe95074a86fead8647edff36af2f3f1dd7e8f7adb", "Lagoon Finance", "algorithmic_trading", "algorithmic_trading"),
    (999, "0x7dd73a986f6188da1baaf68541fd6ebd455abd1d", "Lagoon Finance", "delta_neutral lending multistrategy options", "delta_neutral lending multistrategy options"),
    (8453, "0x0a63471da694fc822f5a0a0b65978dda8c8edbe5", "Lagoon Finance", "amm liquidity_provider", "amm liquidity_provider"),
    (8453, "0x4efc07dca8697792119484af33549f33ab11bf3c", "Lagoon Finance", "algorithmic_trading amm liquidity_provider market_making market_making_amm multistrategy", "algorithmic_trading amm liquidity_provider market_making market_making_amm multistrategy"),
    (8453, "0x8092ca384d44260ea4feaf7457b629b8dc6f88f0", "Lagoon Finance", "amm carry_trade lending lending_looping lending_optimisation liquidity_provider multistrategy", "amm carry_trade discretionary_trading lending lending_looping lending_optimisation liquidity_provider multistrategy"),
    (8453, "0x9b97bfdfe44d1b113ecd4bf2f243ed36aca34523", "Lagoon Finance", "amm carry_trade lending lending_looping liquidity_provider multistrategy", "amm carry_trade discretionary_trading lending lending_looping liquidity_provider multistrategy"),
    (8453, "0xb09f761cb13baca8ec087ac476647361b6314f98", "Lagoon Finance", "arbitrage delta_neutral lending lending_looping liquidity_provider multistrategy", "arbitrage delta_neutral discretionary_trading lending lending_looping liquidity_provider multistrategy"),
    (8453, "0xb725f2277f3f62ad017a6dc418433b326a9f6334", "Lagoon Finance", "directional_trading discretionary_trading multistrategy", "directional_trading discretionary_trading multistrategy"),
    (8453, "0xbe7db44f4ce20dac83b578b94fd35087f66e9754", "Lagoon Finance", "rwa rwa_credit rwa_lending", "discretionary_trading rwa rwa_credit rwa_lending"),
    (8453, "0xd4401d8bea82e4e6c40bb26ae3a04d2fb7ca4550", "Lagoon Finance", "amm carry_trade delta_neutral lending lending_looping liquidity_provider multistrategy", "amm carry_trade delta_neutral discretionary_trading lending lending_looping liquidity_provider multistrategy"),
    (8453, "0xd5c22fa3f7ee979ed7c28e36669b29797ab277e4", "Lagoon Finance", "delta_neutral lending liquidity_provider multistrategy", "delta_neutral lending liquidity_provider multistrategy"),
    (8453, "0xf56bfe07b8d6e6d74258cdb6969a633629b06b08", "Lagoon Finance", "directional_trading discretionary_trading multistrategy", "directional_trading discretionary_trading multistrategy"),
    (8453, "0xfce2064b4221c54651b21c868064a23695e78f09", "Lagoon Finance", "delta_neutral", "delta_neutral"),
    (42161, "0x99cd0b8b32b15922f0754fddc21323b5278c5261", "Lagoon Finance", None, None),
    (42161, "0x9e6de1f581d63129af453fe1ea7253b280b4dca8", "Lagoon Finance", "algorithmic_trading directional_trading discretionary_trading perpetual_futures", "algorithmic_trading directional_trading discretionary_trading perpetual_futures"),
    (1, "0x845339546d90e6e21fdb9638cc7d8d15ebb5e4ac", "Morpho", "lending", "algorithmic_trading discretionary_trading lending lending_optimisation"),
    (1, "0x29ca54dd3ae9e43abb10880f5e08da439bf25f99", "Morpho", "lending", "algorithmic_trading discretionary_trading lending lending_optimisation"),
    (8453, "0xcda5dbf58a016158ecd4a226109752cc3a627ec8", "Morpho", "lending", "algorithmic_trading discretionary_trading lending lending_optimisation"),
    (143, "0x9f209b43559b13b783eaa0a6686921c7b174f269", "Morpho", "lending", "lending"),
    (999, "0x3f78bdd48b363ec927dc227d6c80a6e4821c7e10", "Morpho", "lending", "lending"),
)


@pytest.mark.parametrize(("chain", "address", "protocol", "before", "after"), REVIEWED)
def test_reviewed_strategy_tags(chain: int, address: str, protocol: str, before: str | None, after: str | None) -> None:
    """Exercise every reviewed vault through its real accessor and repair resolver.

    Fixed-universe, automated and undocumented products remain distinct from
    human-selected investments. Mixed execution keeps both supported tags.

    :param chain: Exact deployment chain.
    :param address: Reviewed contract address.
    :param protocol: Protocol identity used to select the real accessor.
    :param before: Frozen prior classifications, used for migration fixtures.
    :param after: Frozen expected canonical identifiers, or missing information.
    :return: None after checking accessors, migration agreement and copy isolation.
    """
    adapters = (MorphoV1Vault, MorphoV2Vault) if protocol == "Morpho" else (LagoonVault,)
    features = (ERC4626Feature.morpho_like, ERC4626Feature.morpho_v2_like) if protocol == "Morpho" else (ERC4626Feature.lagoon_like,)
    spec = VaultSpec(chain, HexAddress(address))
    expected = {StrategyTag(tag) for tag in after.split()} if after else None
    for adapter, feature in zip(adapters, features, strict=True):
        vault = object.__new__(adapter)
        vault.spec = VaultSpec(chain, HexAddress(address.upper()))
        assert vault.get_strategy_tags() == expected
        resolved = resolve_strategy_tags(spec, _row(spec, protocol, {feature}, before))
        assert (resolved[0] if resolved else None) == expected
        tags = vault.get_strategy_tags()
        if tags is not None:
            tags.clear()
            assert vault.get_strategy_tags() == expected
        if before != after:
            vault.spec = VaultSpec(31337, HexAddress(address))
            assert vault.get_strategy_tags() == ({StrategyTag.lending} if protocol == "Morpho" else None)


def _row(spec: VaultSpec, protocol: str, features: set[ERC4626Feature], tags: str | None) -> dict:
    """Create a persisted row with realistic detection and preservation sentinels.

    Tests use only isolated metadata files, with no RPC or provider requests.

    :param spec: Deployment identity.
    :param protocol: Persisted protocol name.
    :param features: Adapter detection evidence.
    :param tags: Previous canonical strategy identifiers, or missing tags.
    :return: A row with independently observable unrelated fields.
    """
    timestamp = datetime.datetime(2026, 11, 10)  # noqa: DTZ001 - Persisted timestamps are naive UTC.
    detection = ERC4262VaultDetection(chain=spec.chain_id, address=spec.vault_address, first_seen_at_block=1, first_seen_at=timestamp, features=features, updated_at=timestamp, deposit_count=1, redeem_count=1)
    return {"Protocol": protocol, "_detection_data": detection, "_description": "Preserve description", "_flags": {"unofficial"}, "_strategy_tags": {StrategyTag(tag) for tag in tags.split()} if tags else None}


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stage all reviewed deployments and wrong-chain controls in an isolated runner.

    Previous tag sets make the nine intended changes observable. Unchanged rows
    and historical files detect any expansion of migration scope.

    :param tmp_path: Test-owned directory.
    :param monkeypatch: Remove legacy production storage overrides.
    :return: Arguments for the real numbered migration runner.
    """
    for key in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = {}
    for chain, address, protocol, before, _after in REVIEWED:
        spec = VaultSpec(chain, HexAddress(address))
        features = {ERC4626Feature.morpho_v2_like} if protocol == "Morpho" else {ERC4626Feature.lagoon_like}
        rows[spec] = _row(spec, protocol, features, before)
        wrong_chain = VaultSpec(31337, HexAddress(address))
        rows[wrong_chain] = _row(wrong_chain, protocol, features, "options")
    VaultDatabase(rows=rows, last_scanned_block={1: 123}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical bytes")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = next(item for item in runner.discover_migrations() if item.name == SCRIPT)
    (scripts / SCRIPT).write_bytes(script.source)
    return {"data_dir": data_dir, "scripts_dir": scripts, "provenance": {"in_docker": False}}


def test_preview_apply_and_repeat(pipeline: dict) -> None:
    """Refresh exactly nine tag fields and preserve all other persisted data.

    Preview writes nothing; apply is idempotent both with and without its
    journal. Expected sets are frozen independently of the implementation.

    :param pipeline: Isolated runner arguments.
    :return: None after checking row, byte and journal invariants.
    """
    data_dir = pipeline["data_dir"]
    path = data_dir / "vault-metadata-db.pickle"
    original = VaultDatabase.read(path)
    expected = copy.deepcopy(original.rows)
    for chain, address, _protocol, before, after in REVIEWED:
        if before != after:
            expected[VaultSpec(chain, HexAddress(address))]["_strategy_tags"] = {StrategyTag(tag) for tag in after.split()}
    files = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=True, **pipeline) == [SCRIPT]
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == files
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT]
    actual = VaultDatabase.read(path)
    assert actual.rows == expected
    assert actual.last_scanned_block == original.last_scanned_block
    report = json.loads((data_dir / "migration.json").read_text())[SCRIPT]["other_data"]
    assert report["updated_fields"] == EXPECTED_CHANGES
    assert report["missing_specs"] == []
    for name, content in files.items():
        if name != path.name:
            assert (data_dir / name).read_bytes() == content
    after_bytes = path.read_bytes()
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == []
    (data_dir / "migration.json").unlink()
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT]
    assert path.read_bytes() == after_bytes


@pytest.mark.parametrize("features", [None, {ERC4626Feature.morpho_like}, {ERC4626Feature.mellow_like, ERC4626Feature.lagoon_like}, {ERC4626Feature.hypercore_native, ERC4626Feature.lagoon_like}])
def test_incompatible_detection_is_atomic(pipeline: dict, features: set[ERC4626Feature] | None) -> None:
    """Reject conflicting adapter evidence before any persisted row changes.

    Missing detection and higher-priority or native adapters cannot borrow a
    Lagoon classification solely from a matching contract address.

    :param pipeline: Isolated runner arguments.
    :param features: Corrupt detection features, or absent detection.
    :return: None after asserting failure and byte preservation.
    """
    path = pipeline["data_dir"] / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    row = database.rows[VaultSpec(8453, "0xbe7db44f4ce20dac83b578b94fd35087f66e9754")]
    row["_detection_data"] = replace(row["_detection_data"], features=features) if features else None
    database.write(path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="adapter"):
        runner.run_pending_migrations(dry_run=False, **pipeline)
    assert path.read_bytes() == before


def test_missing_target_is_reported(pipeline: dict) -> None:
    """A missing target stays absent and does not prevent the other repairs.

    The frozen scope does not insert incomplete records for unscanned products.

    :param pipeline: Isolated runner arguments.
    :return: None after checking the reported gap and remaining changes.
    """
    path = pipeline["data_dir"] / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    missing = VaultSpec(8453, "0xbe7db44f4ce20dac83b578b94fd35087f66e9754")
    del database.rows[missing]
    database.write(path)
    runner.run_pending_migrations(dry_run=False, **pipeline)
    assert missing not in VaultDatabase.read(path).rows
    report = json.loads((pipeline["data_dir"] / "migration.json").read_text())[SCRIPT]["other_data"]
    assert report["missing_specs"] == [missing.as_string_id()]
    assert report["updated_fields"] == EXPECTED_CHANGES - 1
