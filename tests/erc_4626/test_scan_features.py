"""Tests for ERC-4626 scan-record feature persistence."""

import datetime
from decimal import Decimal

import pytest

import eth_defi.erc_4626.scan as scan_module
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import INSTANT_WITHDRAWAL_PERIOD, VaultSpec, WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.price_source import PriceSource
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase


class _FakeToken:
    """Minimal token object for scan-record tests."""

    symbol = "USDC"

    def export(self) -> dict:
        """Return token metadata."""
        return {"symbol": self.symbol}


class _FakeVault:
    """Minimal vault object for scan-record tests."""

    address = "0x0000000000000000000000000000000000000001"
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
    def get_withdrawal_period() -> WithdrawalPeriod:
        """Return withdrawal period metadata."""
        return WithdrawalPeriod(
            min_period=datetime.timedelta(days=1),
            max_period=datetime.timedelta(days=2),
            delay_type=WithdrawalDelayType.delay,
        )

    @staticmethod
    def get_flags() -> set:
        """Return no flags."""
        return set()

    @staticmethod
    def get_strategy_tags() -> None:
        """Report missing strategy classification."""
        return None

    @staticmethod
    def get_link() -> str:
        """Return vault link."""
        return "https://example.com/vault"

    @staticmethod
    def get_notes() -> None:
        """Return no vault notes."""
        return None

    @staticmethod
    def get_share_price_source() -> PriceSource:
        """Return the standard contract-state source."""
        return PriceSource.smart_contract_state

    @staticmethod
    def fetch_minimum_deposit(_block_identifier: int) -> Decimal:
        """Return a source-proven denomination-token minimum."""
        return Decimal("12.5")

    @staticmethod
    def fetch_minimum_redemption(_block_identifier: int) -> Decimal:
        """Return a source-proven absence of a redemption minimum."""
        return Decimal(0)

    @staticmethod
    def fetch_scan_record_extra_data() -> dict:
        """Return no protocol-specific scan fields."""
        return {}

    @staticmethod
    def is_whitelisted_deposit() -> bool:
        """Report that this generic fake has no permission-policy accessor."""
        raise NotImplementedError()


class _PermissionedFakeVault(_FakeVault):
    """Minimal vault with an explicit refusing manager capability."""

    @staticmethod
    def get_deposit_manager_capability() -> VaultDepositManagerCapability:
        """Return a manager capability with both public actions disabled."""
        return VaultDepositManagerCapability(can_deposit=False, can_redeem=False)

    @staticmethod
    def is_whitelisted_deposit() -> bool:
        """Report that deposits require account permission."""
        return True

    @staticmethod
    def get_whitelist_notes() -> str:
        """Return a classification caveat."""
        return "No permissioned hook checks were performed"


class _TaggedFakeVault(_FakeVault):
    """Minimal vault exposing the optional strategy-tag hook."""

    @staticmethod
    def get_strategy_tags() -> set[StrategyTag]:
        """Return one maintained strategy tag."""
        return {StrategyTag.algorithmic_trading}


class _BrokenStrategyTagFakeVault(_FakeVault):
    """Minimal vault whose optional strategy-tag hook fails."""

    @staticmethod
    def get_strategy_tags() -> set[StrategyTag]:
        """Raise a representative lookup error."""
        error_message = "classification mapping is unavailable"
        raise KeyError(error_message)


class _LegacyInstantFakeVault(_FakeVault):
    """Legacy adapter that explicitly reported a zero lock-up."""

    @staticmethod
    def get_estimated_lock_up() -> datetime.timedelta:
        """Return the old zero-duration lock-up representation."""
        return datetime.timedelta(0)

    @staticmethod
    def get_withdrawal_period() -> None:
        """Report no structured withdrawal timing metadata."""
        return None


class _ExplicitInstantFakeVault(_LegacyInstantFakeVault):
    """Legacy zero-lockup adapter that explicitly declares direct redemption."""

    @staticmethod
    def get_withdrawal_period() -> WithdrawalPeriod:
        """Return the adapter's explicit direct-redemption timing."""
        return INSTANT_WITHDRAWAL_PERIOD


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
    assert record["_withdrawal_period"] == WithdrawalPeriod(
        min_period=datetime.timedelta(days=1),
        max_period=datetime.timedelta(days=2),
        delay_type=WithdrawalDelayType.delay,
    )
    assert record["_lockup"] == datetime.timedelta(days=2)
    assert record["_share_price_source"] is PriceSource.smart_contract_state
    assert record["_minimum_deposit"] == Decimal("12.5")
    assert record["_minimum_redemption"] == Decimal(0)


def test_create_vault_scan_record_persists_deposit_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persist permission policy beside an explicitly refusing manager."""
    detection = _create_detection({ERC4626Feature.usdai_like})

    monkeypatch.setattr(scan_module, "create_vault_instance", lambda *_args, **_kwargs: _PermissionedFakeVault())

    record = scan_module.create_vault_scan_record(
        web3=None,
        detection=detection,
        block_identifier=1,
        token_cache={},
    )

    assert record["_deposit_manager"] == {
        "can_deposit": False,
        "can_redeem": False,
    }
    assert record["_deposit_permission"] == "whitelisted"
    assert record["_whitelist_notes"] == "No permissioned hook checks were performed"


def test_create_vault_scan_record_persists_strategy_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persist strategy tags returned by the VaultBase strategy hook."""
    detection = _create_detection({ERC4626Feature.usdai_like})
    monkeypatch.setattr(scan_module, "create_vault_instance", lambda *_args, **_kwargs: _TaggedFakeVault())

    record = scan_module.create_vault_scan_record(
        web3=None,
        detection=detection,
        block_identifier=1,
        token_cache={},
    )

    assert record["_strategy_tags"] == {StrategyTag.algorithmic_trading}


def test_create_vault_scan_record_treats_strategy_tag_errors_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken optional hook leaves strategy information explicitly missing."""
    detection = _create_detection({ERC4626Feature.usdai_like})
    monkeypatch.setattr(scan_module, "create_vault_instance", lambda *_args, **_kwargs: _BrokenStrategyTagFakeVault())

    record = scan_module.create_vault_scan_record(
        web3=None,
        detection=detection,
        block_identifier=1,
        token_cache={},
    )

    assert record["_strategy_tags"] is None


def test_vault_database_merge_preserves_existing_strategy_tags() -> None:
    """A rescan without a classification does not erase persisted tags."""
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    database = VaultDatabase(
        rows={
            spec: {
                "Name": "Existing vault",
                "_strategy_tags": {StrategyTag.algorithmic_trading},
            }
        }
    )

    database._merge_rows(
        {
            spec: {
                "Name": "Fresh vault",
                "Denomination": "USDC",
                "_strategy_tags": None,
            }
        }
    )

    assert database.rows[spec]["_strategy_tags"] == {StrategyTag.algorithmic_trading}


def test_create_vault_scan_record_does_not_infer_instant_from_legacy_zero_lockup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A legacy lock-up estimate cannot establish a withdrawal lifecycle."""
    detection = _create_detection({ERC4626Feature.usdai_like})
    monkeypatch.setattr(scan_module, "create_vault_instance", lambda *_args, **_kwargs: _LegacyInstantFakeVault())

    record = scan_module.create_vault_scan_record(
        web3=None,
        detection=detection,
        block_identifier=1,
        token_cache={},
    )

    assert record["_withdrawal_period"] is None
    assert record["_lockup"] == datetime.timedelta(0)


def test_create_vault_scan_record_exports_explicit_instant_period(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only an adapter's explicit direct-redemption declaration exports ``instant``."""
    detection = _create_detection({ERC4626Feature.usdai_like})
    monkeypatch.setattr(scan_module, "create_vault_instance", lambda *_args, **_kwargs: _ExplicitInstantFakeVault())

    record = scan_module.create_vault_scan_record(
        web3=None,
        detection=detection,
        block_identifier=1,
        token_cache={},
    )

    assert record["_withdrawal_period"] == WithdrawalPeriod(
        min_period=datetime.timedelta(0),
        max_period=datetime.timedelta(0),
        delay_type=WithdrawalDelayType.instant,
    )


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
