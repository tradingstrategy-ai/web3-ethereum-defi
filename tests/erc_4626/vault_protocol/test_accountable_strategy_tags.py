"""Check the reviewed Accountable strategy inventory without RPC calls."""

import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Numbered refresh under test.
MIGRATION_NUMBER = 47

#: Chain of the independently reviewed BIMA deployment.
BASE_CHAIN_ID = 8453


@pytest.mark.parametrize(
    ("chain_id", "address", "expected"),
    [
        # RWA Backed Lending by Valos
        (143, "0x8d3f9f9eb2f5e8b48efbb4074440d1e2a34bc365", {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.lending}),
        # Tori Ecosystem Monad
        (143, "0x9e98bda72ced55e562bed1dcaf2b123e95eb181e", {StrategyTag.multistrategy, StrategyTag.liquidity_provider, StrategyTag.amm}),
        # Neutral Trade Autopilot
        (143, "0xaabab7598be3c4fe58c593e73c2f5934b73b573e", {StrategyTag.multistrategy, StrategyTag.delta_neutral, StrategyTag.arbitrage, StrategyTag.funding_rate_arbitrage, StrategyTag.market_making, StrategyTag.perpetual_futures, StrategyTag.options, StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following}),
        # Morini FXArbUSDTRY
        (1, "0x99351baed3d8ab544ccb08af96a105910fda71e7", {StrategyTag.fx, StrategyTag.arbitrage, StrategyTag.delta_neutral, StrategyTag.market_making, StrategyTag.carry_trade}),
        # Neutral Trade Autopilot
        (1, "0x909dadbca7955614a455d9e7447ad4adb4902c8e", {StrategyTag.multistrategy, StrategyTag.delta_neutral, StrategyTag.arbitrage, StrategyTag.funding_rate_arbitrage, StrategyTag.market_making, StrategyTag.perpetual_futures, StrategyTag.options, StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following}),
        # BIMA WBTC/USDC OCT26-27
        (1, "0x934a801d516ef34c519c793fe0606570e732fc6d", {StrategyTag.options}),
        # Noon wcBTC Yield Vault
        (4114, "0x69bf9d488b0cbb9288b25f83ef815c2dc14d76de", {StrategyTag.lending_looping, StrategyTag.carry_trade, StrategyTag.multistrategy, StrategyTag.rwa, StrategyTag.lending, StrategyTag.delta_neutral}),
        # Meridian Liquidity Provider
        (4663, "0x24b84023c8e4da635be228c380c09bfe5271bf9d", {StrategyTag.market_making, StrategyTag.liquidity_provider, StrategyTag.delta_neutral, StrategyTag.perpetual_futures}),
        # BIMA WBNB/USDC OCTNOV26
        (56, "0xe1f7c0b46c02374ef9a77e40a6cec31ca5a15313", {StrategyTag.options}),
        # BIMA cbBTC/USDC OCT26-27
        (8453, "0xe4ee0fe4b09021fcdf76b165aa1e8eaf7d447c35", {StrategyTag.options}),
    ],
)
def test_accountable_reviewed_strategies(chain_id: int, address: str, expected: set[StrategyTag]) -> None:
    """Check published strategies through the adapter used by the scanner.

    Exercise case normalisation and return-value isolation for representative
    reviewed API products, with independent expected categories.

    :param chain_id: Reviewed deployment chain.
    :param address: Reviewed share-token address.
    :param expected: Complete classification supported by the source material.
    :return: ``None`` after validating the classification.
    """
    vault = SimpleNamespace(chain_id=chain_id, address=HexAddress(address.upper()))
    tags = AccountableVault.get_strategy_tags(vault)
    assert tags == expected
    tags.clear()
    assert AccountableVault.get_strategy_tags(vault) == expected


@pytest.mark.parametrize(
    ("chain_id", "address"),
    (
        (1, "0x899e270df335287f687fdac8a9089059b042044f"),
        (143, "0x99351baed3d8ab544ccb08af96a105910fda71e7"),
        (1, "0xe4ee0fe4b09021fcdf76b165aa1e8eaf7d447c35"),
        (8453, "0xe4ee0fe4b09021fcdf76b165aa1e8eaf7d447c35"),
    ),
)
def test_accountable_cross_chain_isolation(chain_id: int, address: str) -> None:
    """Keep reused addresses scoped to their separately documented products.

    Ethereum's Tauri lending vault and Base's BIMA options vault share an
    address but must never inherit each other's classifications.

    :param chain_id: Candidate chain.
    :param address: Reused or unrelated share address.
    :return: ``None`` after checking isolation.
    """
    vault = SimpleNamespace(chain_id=chain_id, address=HexAddress(address))
    tags = AccountableVault.get_strategy_tags(vault)
    if chain_id == 1 and address.startswith("0xe4ee"):
        assert tags == {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.inventory_financing}
    elif chain_id == BASE_CHAIN_ID:
        assert tags == {StrategyTag.options}
    else:
        assert tags is None


@pytest.mark.parametrize("dry_run", (True, False))
def test_accountable_strategy_migration(tmp_path: Path, *, dry_run: bool) -> None:
    """Refresh tags without changing other fields, unknown vaults or disk in preview.

    Use the production migration context against an isolated persisted database
    and repeat apply to verify idempotence. No network access is needed.

    :param tmp_path: Isolated pipeline directory.
    :param dry_run: Whether changes must remain in memory.
    :return: ``None`` after checking preservation and migration reporting.
    """
    target = VaultSpec(1, "0x99351baed3d8ab544ccb08af96a105910fda71e7")
    unrelated = VaultSpec(143, target.vault_address)
    unknown = VaultSpec(143, "0x0000000000000000000000000000000000000001")
    row = {"Name": "Morini FXArbUSDTRY", "Protocol": "Accountable", "_detection_data": create_detection(target), "_strategy_tags": {StrategyTag.fx}, "_notes": "Preserve this note"}
    other = {"Protocol": "Accountable", "_strategy_tags": {StrategyTag.unknown}}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={target: row, unrelated: other, unknown: other.copy()}).write(path)
    original_bytes = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.number == MIGRATION_NUMBER)
    context = runner.MigrationContext(tmp_path, dry_run, script_name=migration.name)
    runner._execute(migration, context)
    expected = {StrategyTag.fx, StrategyTag.arbitrage, StrategyTag.delta_neutral, StrategyTag.market_making, StrategyTag.carry_trade}
    assert context.metadata().rows[target] == row | {"_strategy_tags": expected}
    assert context.metadata().rows[unrelated] == other
    assert context.metadata().rows[unknown] == other
    assert context.other_data["changed_rows"] == 1
    if dry_run:
        assert path.read_bytes() == original_bytes
    else:
        assert VaultDatabase.read(path).rows[target]["_strategy_tags"] == expected
        repeated = runner.MigrationContext(tmp_path, False, script_name=migration.name)
        runner._execute(migration, repeated)
        assert repeated.other_data["changed_rows"] == 0


def create_detection(spec: VaultSpec) -> ERC4262VaultDetection:
    """Build Accountable discovery evidence for a metadata row.

    The migration verifies the detected protocol before staging tag updates.

    :param spec: Deployment identity.
    :return: Stable Accountable detection record.
    """
    timestamp = datetime.datetime(2026, 11, 9, tzinfo=datetime.UTC).replace(tzinfo=None)
    return ERC4262VaultDetection(spec.chain_id, spec.vault_address, 42, timestamp, {ERC4626Feature.accountable_like}, timestamp, 1, 1)


def test_accountable_migration_rejects_conflicting_protocol(tmp_path: Path) -> None:
    """Abort before changing metadata when a target lacks Accountable evidence.

    A reused address or changed protocol must be reviewed independently rather
    than receiving a classification from the fixed migration inventory.

    :param tmp_path: Isolated metadata directory.
    :return: ``None`` after checking the hard failure and unchanged file.
    """
    target = VaultSpec(1, "0x99351baed3d8ab544ccb08af96a105910fda71e7")
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={target: {"Protocol": "ERC-4626", "_strategy_tags": None}}).write(path)
    original_bytes = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.number == MIGRATION_NUMBER)
    context = runner.MigrationContext(tmp_path, False, script_name=migration.name)
    with pytest.raises(ValueError, match="not an Accountable vault"):
        runner._execute(migration, context)
    assert path.read_bytes() == original_bytes
