"""Verify the numbered T3tris settlement estimate migration on isolated state."""

import datetime
from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.t3tris import offchain_metadata
from eth_defi.vault.base import VaultSpec, WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

SCRIPT_NAME = "079-t3tris-settlement-estimate.py"
LISTED = VaultSpec(42161, "0x98e43a491a464f0886bc5e57207c340bbed0d01f")
UNLISTED = VaultSpec(4663, "0x5b93dd3eb7fd224565498045f5e1a2ebda49e672")
OTHER = VaultSpec(42161, "0x0000000000000000000000000000000000000001")

#: ``Date: Sun, 15 Nov 2026 17:59:44 GMT`` as a Unix timestamp.
RESPONSE_TS = 1794765584


class _Response:
    """Minimal ``requests`` response for the T3tris detail endpoint."""

    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.headers = {"Date": "Sun, 15 Nov 2026 17:59:44 GMT"}

    def raise_for_status(self) -> None:
        """Raise for a non-200 status, as ``requests`` does."""
        if self.status_code != 200:
            raise offchain_metadata.requests.HTTPError(str(self.status_code), response=self)

    def json(self) -> dict:
        """Return the payload."""
        return self.payload


def _fake_get(url: str, **_kwargs) -> _Response:
    """Serve an estimate for the listed vault and 404 for the unlisted one."""
    if url.endswith(f"/vaults/{LISTED.chain_id}/{LISTED.vault_address}"):
        return _Response({"vault": {"name": "First - USDC"}, "estimatedRedemptionTs": RESPONSE_TS + 479_171})
    return _Response({"error": "not found"}, status_code=404)


def _row(spec: VaultSpec, features: set[ERC4626Feature]) -> dict:
    """Build a stored row without an estimate and with unrelated enrichment.

    :param spec: Vault identity.
    :param features: Detected protocol features.
    :return: Catalogue row.
    """
    timestamp = datetime.datetime(2026, 1, 1)
    detection = ERC4262VaultDetection(spec.chain_id, spec.vault_address, 1, timestamp, features, timestamp, 10, 20)
    return {"_detection_data": detection, "_withdrawal_period": None, "_lockup": None, "_notes": "Preserve note"}


def test_t3tris_settlement_estimate_preview_apply_and_preservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Preview changes nothing; apply sets only T3tris estimates from fresh responses.

    1. Write a database with a listed and an unlisted T3tris vault and one other row.
    2. Preview leaves every file untouched and never uses the cached fetcher.
    3. Apply sets the listed vault's estimate and preserves everything else.
    """
    # 1. Write a database with a listed and an unlisted T3tris vault and one other row.
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(offchain_metadata.requests, "get", _fake_get)
    monkeypatch.setattr(offchain_metadata, "fetch_t3tris_vault_metadata", lambda *args, **kwargs: pytest.fail("migration must not use the cached fetcher"))
    other_period = WithdrawalPeriod(min_period=None, max_period=None, delay_type=WithdrawalDelayType.delay, estimated_settlement=datetime.timedelta(days=1))
    other = _row(OTHER, {ERC4626Feature.lagoon_like}) | {"_withdrawal_period": other_period}
    database = VaultDatabase(rows={LISTED: _row(LISTED, {ERC4626Feature.t3tris_like}), UNLISTED: _row(UNLISTED, {ERC4626Feature.t3tris_like}), OTHER: other}, last_scanned_block={42161: 123})
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "vault-metadata-db.pickle"
    database.write(path)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(item for item in runner.discover_migrations() if item.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}

    # 2. Preview leaves every file untouched and never uses the cached fetcher.
    original = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=True, **options) == [SCRIPT_NAME]
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == original

    # 3. Apply sets the listed vault's estimate and preserves everything else.
    assert runner.run_pending_migrations(dry_run=False, **options) == [SCRIPT_NAME]
    result = VaultDatabase.read(path)
    period = result.rows[LISTED]["_withdrawal_period"]
    assert period.estimated_settlement == datetime.timedelta(seconds=479_171)
    assert period.max_period is None
    assert result.rows[LISTED]["_lockup"] is None
    assert result.rows[LISTED]["_notes"] == "Preserve note"
    assert result.rows[UNLISTED]["_withdrawal_period"] is None
    assert result.rows[OTHER]["_withdrawal_period"] == other_period
    assert result.last_scanned_block == database.last_scanned_block
    assert runner.run_pending_migrations(dry_run=False, **options) == []


def test_t3tris_settlement_estimate_server_error_fails_and_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient API failure fails the migration instead of storing no estimate.

    1. A 500 from the detail endpoint aborts apply and leaves the database unchanged.
    2. The migration is not journalled, so a later run applies the estimate.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    database = VaultDatabase(rows={LISTED: _row(LISTED, {ERC4626Feature.t3tris_like})}, last_scanned_block={42161: 123})
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "vault-metadata-db.pickle"
    database.write(path)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(item for item in runner.discover_migrations() if item.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}

    # 1. A 500 from the detail endpoint aborts apply and leaves the database unchanged.
    original = path.read_bytes()
    monkeypatch.setattr(offchain_metadata.requests, "get", lambda url, **_kwargs: _Response({}, status_code=500))
    with pytest.raises(offchain_metadata.requests.HTTPError):
        runner.run_pending_migrations(dry_run=False, **options)
    assert path.read_bytes() == original

    # 2. The migration is not journalled, so a later run applies the estimate.
    monkeypatch.setattr(offchain_metadata.requests, "get", _fake_get)
    assert runner.run_pending_migrations(dry_run=False, **options) == [SCRIPT_NAME]
    assert VaultDatabase.read(path).rows[LISTED]["_withdrawal_period"].estimated_settlement == datetime.timedelta(seconds=479_171)
