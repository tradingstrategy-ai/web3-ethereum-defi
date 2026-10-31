"""Repair Upshift admission without touching historical or unrelated data."""

import copy
import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner
from eth_defi.vault_protocols.repairs import upshift

USPC = VaultSpec(1, "0x0a152c5b8d48c26619ff3ae85ef1b54a79e20870")
RESTRICTED_OUTSIDE_017 = VaultSpec(1, "0x3299a525986d2e94b3fc6c641c158f5e12db912d")
PUBLIC = VaultSpec(1, "0x74ad2f789ed583dbd141bbdafc673fe1f033718b")
LEGACY = VaultSpec(1, "0xe9b725010a9e419412ed67d0fa5f3a5f40159d32")
UNRELATED = VaultSpec(1, "0x0000000000000000000000000000000000000001")


@pytest.fixture
def permission_database() -> VaultDatabase:
    """Create stale public labels for restricted and public Upshift deployments.

    Detection and other row fields are real persisted values, so preservation
    checks include discovery identity and capability independence.

    :return: Isolated database with both Upshift generations and an unrelated row.
    """
    rows = {}
    for spec in (USPC, RESTRICTED_OUTSIDE_017, PUBLIC, LEGACY):
        features = {ERC4626Feature.upshift_like}
        if spec != LEGACY:
            features.add(ERC4626Feature.upshift_multi_asset_like)
        detection = ERC4262VaultDetection(1, spec.vault_address, 42, datetime.datetime(2026, 9, 1), features, datetime.datetime(2026, 10, 1), 27, 1)
        rows[spec] = {"Address": spec.vault_address, "_detection_data": detection, "features": features, "_deposit_permission": "permissionless", "_deposit_manager": {"can_deposit": True}, "_notes": "Keep existing warning", "_whitelist_notes": None}
    rows[UNRELATED] = {"_deposit_permission": "whitelisted", "_notes": "Unrelated policy"}
    return VaultDatabase(rows=rows, last_scanned_block={1: 1234})


@pytest.fixture
def permission_provider(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock only RPC-owned sender-policy responses around the real adapter.

    :param monkeypatch: Isolated protocol constructor replacement.
    :return: Provider with one head and per-deployment mock contracts.
    """
    provider = MagicMock()
    provider.eth.chain_id = 1
    provider.eth.block_number = 123
    provider.block_tip_latency = 0
    provider.contracts = {}

    def create_vault(web3: Web3, address: HexAddress, **kwargs: object) -> UpshiftVault:
        """Bind the real admission hook to deterministic contract observations.

        :param web3: Supplied provider whose chain/head are validated by the repair.
        :param address: Deployment address selected by the shared factory.
        :param kwargs: Features and block pin forwarded to the real adapter.
        :return: Real vault with only its RPC-bound contract replaced.
        """
        spec = VaultSpec(web3.eth.chain_id, address)
        vault = UpshiftVault(Web3(), spec, **kwargs)
        contract = MagicMock()
        contract.functions.sendersWhitelistAddress.return_value.call.return_value = "0x1111111111111111111111111111111111111111" if spec in {USPC, RESTRICTED_OUTSIDE_017} else ZERO_ADDRESS_STR
        vault.__dict__["vault_contract"] = contract
        provider.contracts[spec] = contract
        return vault

    monkeypatch.setattr(upshift, "create_vault_instance", create_vault)
    return provider


def test_upshift_permission_refresh_preserves_other_fields(permission_database: VaultDatabase, permission_provider: MagicMock) -> None:
    """Correct every restricted deployment and preserve public controls and metadata.

    The target outside migration 017 prevents a USPC-only repair. A repeated
    refresh must leave the already corrected database unchanged.
    """
    before = copy.deepcopy(permission_database)
    result = upshift.refresh_upshift_deposit_permissions(permission_database, lambda chain: permission_provider)
    assert result["inspected_rows"] == 4
    assert result["changed_rows"] == 2
    assert result["permission_counts"] == {"whitelisted": 2, "permissionless": 2, "unknown": 0}
    assert result["read_blocks"] == {"1": 123}
    for spec, row in permission_database.rows.items():
        if spec in {USPC, RESTRICTED_OUTSIDE_017}:
            assert row["_deposit_permission"] == "whitelisted"
            before.rows[spec]["_deposit_permission"] = "whitelisted"
        assert row == before.rows[spec]
    assert permission_database.last_scanned_block == before.last_scanned_block
    for spec in (USPC, RESTRICTED_OUTSIDE_017, PUBLIC):
        permission_provider.contracts[spec].functions.sendersWhitelistAddress.return_value.call.assert_called_once_with(block_identifier=123)
    assert upshift.refresh_upshift_deposit_permissions(permission_database, lambda chain: permission_provider)["changed_rows"] == 0


def test_upshift_permission_refresh_unresolved_read_is_atomic(permission_database: VaultDatabase, permission_provider: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """A later unknown policy leaves every earlier proposed correction unstaged."""
    before = copy.deepcopy(permission_database.rows)
    create_vault = upshift.create_vault_instance

    def fail_later(*args: object, **kwargs: object) -> UpshiftVault:
        """Simulate a provider outage after the USPC policy read succeeded.

        :param args: Existing adapter constructor arguments.
        :param kwargs: Existing block and feature arguments.
        :return: Real adapter with one failed contract read.
        """
        vault = create_vault(*args, **kwargs)
        if vault.spec == RESTRICTED_OUTSIDE_017:
            vault.vault_contract.functions.sendersWhitelistAddress.return_value.call.side_effect = ExtraValueError("RPC unavailable")
        return vault

    monkeypatch.setattr(upshift, "create_vault_instance", fail_later)
    with pytest.raises(ValueError, match="Unresolved Upshift deposit permission"):
        upshift.refresh_upshift_deposit_permissions(permission_database, lambda chain: permission_provider)
    assert permission_database.rows == before


def test_upshift_permission_refresh_conflicting_identity(permission_database: VaultDatabase, permission_provider: MagicMock) -> None:
    """Conflicting cached discovery must fail before proposing policy changes."""
    permission_database.rows[PUBLIC]["Address"] = USPC.vault_address
    before = copy.deepcopy(permission_database.rows)
    with pytest.raises(ValueError, match="Conflicting Upshift row"):
        upshift.refresh_upshift_deposit_permissions(permission_database, lambda chain: permission_provider)
    assert permission_database.rows == before
    assert not permission_provider.contracts


def test_upshift_permission_refresh_empty_database() -> None:
    """An absent protocol population is a no-op without RPC configuration."""
    assert upshift.refresh_upshift_deposit_permissions(VaultDatabase())["inspected_rows"] == 0


def test_upshift_permission_refresh_provider_safe_head(permission_database: VaultDatabase, permission_provider: MagicMock) -> None:
    """Honour provider tip latency without using the old cached metadata head."""
    permission_provider.block_tip_latency = 4
    result = upshift.refresh_upshift_deposit_permissions(permission_database, lambda chain: permission_provider)
    assert result["read_blocks"] == {"1": 119}
    permission_provider.contracts[USPC].functions.sendersWhitelistAddress.return_value.call.assert_called_once_with(block_identifier=119)


def test_upshift_permission_refresh_refuses_another_adapter(permission_database: VaultDatabase, permission_provider: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not overwrite policy if the scanner selects another protocol adapter."""
    before = copy.deepcopy(permission_database.rows)
    monkeypatch.setattr(upshift, "create_vault_instance", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="Scanner does not select an Upshift adapter"):
        upshift.refresh_upshift_deposit_permissions(permission_database, lambda chain: permission_provider)
    assert permission_database.rows == before


@pytest.mark.parametrize("include_lifecycle_repair", [False, True], ids=("018-alone", "017-then-018"))
def test_upshift_permission_migration_preview_apply(tmp_path: Path, permission_database: VaultDatabase, permission_provider: MagicMock, monkeypatch: pytest.MonkeyPatch, include_lifecycle_repair: bool) -> None:
    """Exercise guarded preview, backup, apply and journalled rerun semantics.

    Existing price and reader-state files must remain byte-identical. Also
    exercise 017 correcting USPC before 018 repairs the out-of-inventory row.
    Resource selection is a test seam; production runs the ordered batch.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(upshift, "read_json_rpc_url", lambda chain: "unused")
    monkeypatch.setattr(upshift, "create_multi_provider_web3", lambda url: permission_provider)
    if include_lifecycle_repair:

        def refresh_lifecycle_metadata(database: VaultDatabase) -> dict[str, int]:
            """Supply 017's already-tested USPC permission correction.

            :param database: Shared staged metadata passed on to migration 018.
            :return: Number of simulated live lifecycle updates.
            """
            database.rows[USPC]["_deposit_permission"] = "whitelisted"
            return {"changed_rows": 1}

        monkeypatch.setattr(upshift, "refresh_upshift_metadata", refresh_lifecycle_metadata)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    metadata = data_dir / "vault-metadata-db.pickle"
    permission_database.write(metadata)
    pq.write_table(pa.table({"chain": [1], "address": [UNRELATED.vault_address], "share_price": [1.25], "available_liquidity": [123.0]}), data_dir / "vault-prices-1h.parquet")
    (data_dir / "vault-reader-state-1h.pickle").write_bytes(b"preserve existing history")
    before = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    resources = tmp_path / "scripts"
    resources.mkdir()
    scripts = [spec for spec in runner.discover_migrations() if spec.number == 18 or (include_lifecycle_repair and spec.number == 17)]
    for script in scripts:
        (resources / script.name).write_bytes(script.source)
    expected_names = [script.name for script in scripts]
    assert runner.run_pending_migrations(data_dir=data_dir, scripts_dir=resources, dry_run=True) == expected_names
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == before
    assert runner.run_pending_migrations(data_dir=data_dir, scripts_dir=resources, dry_run=False) == expected_names
    after = VaultDatabase.read(metadata)
    assert after.rows[USPC]["_deposit_permission"] == "whitelisted"
    assert after.rows[RESTRICTED_OUTSIDE_017]["_deposit_permission"] == "whitelisted"
    backups = list(data_dir.glob("vault-metadata-db.pickle.bak-*"))
    assert len(backups) == len(scripts)
    assert any(backup.read_bytes() == before[metadata.name] for backup in backups)
    journal = runner.load_state(data_dir / "migration.json")
    assert journal[scripts[-1].name]["other_data"]["changed_rows"] == (1 if include_lifecycle_repair else 2)
    for name in ("vault-prices-1h.parquet", "vault-reader-state-1h.pickle"):
        assert (data_dir / name).read_bytes() == before[name]
    applied = metadata.read_bytes()
    assert runner.run_pending_migrations(data_dir=data_dir, scripts_dir=resources, dry_run=False) == []
    assert metadata.read_bytes() == applied
