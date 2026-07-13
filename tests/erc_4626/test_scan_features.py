"""Tests for ERC-4626 scan-record feature persistence."""

import datetime

import pytest

import eth_defi.erc_4626.scan as scan_module
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.vaultdb import VaultDatabase


class _FakeToken:
    """Minimal token object for scan-record tests."""

    symbol = "USDC"

    def export(self) -> dict:
        """Return token metadata."""
        return {"symbol": self.symbol}


class _FakeVault:
    """Minimal vault object for scan-record tests."""

    symbol = "fvUSDC"
    name = "Feature Vault"
    denomination_token = _FakeToken()
    share_token = _FakeToken()
    description = "Feature vault description"
    short_description = "Feature vault"
    manager_name = "Feature Labs"
    morpho_offchain_data = None

    @staticmethod
    def get_fee_data() -> FeeData:
        """Return fee data."""
        return FeeData(
            fee_mode=VaultFeeMode.internalised_skimming,
            management=0.0,
            performance=0.0,
            deposit=0.0,
            withdraw=0.0,
        )

    @staticmethod
    def fetch_total_assets(_block_identifier: int) -> float:
        """Return a small TVL so open/closed status checks are skipped."""
        return 100.0

    @staticmethod
    def fetch_total_supply(_block_identifier: int) -> float:
        """Return share supply."""
        return 100.0

    @staticmethod
    def get_estimated_lock_up() -> None:
        """Return no lock-up."""
        return None

    @staticmethod
    def get_flags() -> set:
        """Return no flags."""
        return set()

    @staticmethod
    def get_link() -> str:
        """Return vault link."""
        return "https://example.com/vault"

    @staticmethod
    def get_notes() -> None:
        """Return no vault notes."""
        return None

    @staticmethod
    def fetch_scan_record_extra_data() -> dict:
        """Return no protocol-specific scan fields."""
        return {}


def _create_detection(features: set[ERC4626Feature]) -> ERC4262VaultDetection:
    """Create a detection object."""
    timestamp = datetime.datetime(2026, 7, 3, tzinfo=datetime.UTC).replace(tzinfo=None)
    return ERC4262VaultDetection(
        chain=42161,
        address="0x0000000000000000000000000000000000000001",
        first_seen_at_block=1,
        first_seen_at=timestamp,
        features=features,
        updated_at=timestamp,
        deposit_count=1,
        redeem_count=1,
    )


def test_create_vault_scan_record_persists_machine_readable_features(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scan records persist feature flags both for humans and machines."""

    features = {ERC4626Feature.usdai_like, ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}
    detection = _create_detection(features)

    monkeypatch.setattr(scan_module, "create_vault_instance", lambda *_args, **_kwargs: _FakeVault())

    record = scan_module.create_vault_scan_record(
        web3=None,
        detection=detection,
        block_identifier=1,
        token_cache={},
    )

    assert record["features"] == features
    assert record["features"] is not record["_detection_data"].features
    assert record["_detection_data"].features == features
    assert "erc_7575_like" in record["Features"]
    assert record["_deposit_manager"] is None


def test_vault_database_dataframe_falls_back_to_detection_features() -> None:
    """Old pickles without top-level ``features`` still display protocol names."""

    features = {ERC4626Feature.usdai_like, ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}
    detection = _create_detection(features)
    row = {
        "Name": "Staked USDai",
        "Denomination": "USDai",
        "NAV": 100.0,
        "_detection_data": detection,
    }

    df = VaultDatabase.to_dataframe([row])

    assert df.iloc[0]["protocol"] == "USDai"
    assert df.iloc[0]["vault_address"] == detection.address
