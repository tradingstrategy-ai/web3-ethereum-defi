"""Unit tests for protocol withdrawal-period reporting."""

import datetime
from types import SimpleNamespace

import pytest

from eth_defi.erc_4626.vault_protocol.aave.vault import AaveVault
from eth_defi.erc_4626.vault_protocol.brink.vault import BrinkVault
from eth_defi.erc_4626.vault_protocol.curvance.vault import CurvanceVault
from eth_defi.erc_4626.vault_protocol.d2.vault import D2Vault
from eth_defi.erc_4626.vault_protocol.dolomite.vault import DolomiteVault
from eth_defi.erc_4626.vault_protocol.foxify.vault import FoxifyVault
from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault, OstiumVault, OstiumVersion
from eth_defi.erc_4626.vault_protocol.gearbox.vault import GearboxVault
from eth_defi.erc_4626.vault_protocol.kiloex.vault import KiloExVault
from eth_defi.erc_4626.vault_protocol.nara.vault import NaraVault
from eth_defi.erc_4626.vault_protocol.sentiment.vault import SentimentVault
from eth_defi.erc_4626.vault_protocol.singularity.vault import SingularityVault
from eth_defi.erc_4626.vault_protocol.term_finance.vault import TermFinanceVault
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault
from eth_defi.erc_4626.vault_protocol.usdai.vault import StakedUSDaiVault
from eth_defi.vault.base import WithdrawalDelayType


def _call(value: int) -> SimpleNamespace:
    """Build a Web3-like no-argument call result."""
    return SimpleNamespace(call=lambda: value)


def test_gains_withdrawal_period_covers_collateralisation_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gains exports its documented one-to-three epoch withdrawal range."""
    vault = object.__new__(GainsVault)
    monkeypatch.setattr(GainsVault, "fetch_epoch_duration", lambda _vault: datetime.timedelta(hours=6))

    period = vault.get_withdrawal_period()

    assert period.min_period == datetime.timedelta(hours=6)
    assert period.max_period == datetime.timedelta(hours=18)
    assert period.delay_type is WithdrawalDelayType.delay


def test_ostium_v15_withdrawal_period_uses_settlement_configuration() -> None:
    """Ostium V1.5 includes its onchain delay and scheduling interval."""
    vault = object.__new__(OstiumVault)
    vault.__dict__["version"] = OstiumVersion.v1_5
    vault.__dict__["vault_contract"] = SimpleNamespace(
        functions=SimpleNamespace(
            maxSettlementInterval=lambda: _call(86_400),
            withdrawSettlementDelay=lambda: _call(2),
        )
    )

    period = vault.get_withdrawal_period()

    assert period.min_period == datetime.timedelta(days=2)
    assert period.max_period == datetime.timedelta(days=3)
    assert period.delay_type is WithdrawalDelayType.delay


def test_d2_withdrawal_period_is_epoch_based(monkeypatch: pytest.MonkeyPatch) -> None:
    """D2 exports an immediately available withdrawal window after an epoch."""
    vault = object.__new__(D2Vault)
    monkeypatch.setattr(D2Vault, "get_estimated_lock_up", lambda _vault: datetime.timedelta(days=30))

    period = vault.get_withdrawal_period()

    assert period.min_period == datetime.timedelta(0)
    assert period.max_period == datetime.timedelta(days=30)
    assert period.delay_type is WithdrawalDelayType.epoch


def test_upshift_withdrawal_period_uses_configured_lag_duration() -> None:
    """Upshift reads the vault-specific delay rather than assuming one day."""
    vault = object.__new__(UpshiftVault)
    vault.__dict__["vault_contract"] = SimpleNamespace(functions=SimpleNamespace(lagDuration=lambda: _call(259_200)))

    period = vault.get_withdrawal_period()

    assert period.min_period == datetime.timedelta(days=3)
    assert period.max_period == datetime.timedelta(days=3)
    assert period.delay_type is WithdrawalDelayType.delay


def test_kiloex_withdrawal_period_covers_epoch_range() -> None:
    """KiloEx exports its documented one-to-three epoch range."""
    vault = object.__new__(KiloExVault)

    period = vault.get_withdrawal_period()

    assert period.min_period == datetime.timedelta(days=3)
    assert period.max_period == datetime.timedelta(days=9)
    assert period.delay_type is WithdrawalDelayType.epoch


def test_nara_withdrawal_period_uses_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nara exports the live cooldown as a fixed delay."""
    vault = object.__new__(NaraVault)
    monkeypatch.setattr(NaraVault, "get_estimated_lock_up", lambda _vault: datetime.timedelta(days=7))

    period = vault.get_withdrawal_period()

    assert period.min_period == datetime.timedelta(days=7)
    assert period.max_period == datetime.timedelta(days=7)
    assert period.delay_type is WithdrawalDelayType.delay


def test_usdai_withdrawal_period_is_redemption_window() -> None:
    """USDai exports its thirty-day epoch window."""
    vault = object.__new__(StakedUSDaiVault)

    period = vault.get_withdrawal_period()

    assert period.min_period == datetime.timedelta(0)
    assert period.max_period == datetime.timedelta(days=30)
    assert period.delay_type is WithdrawalDelayType.epoch


@pytest.mark.parametrize(
    "vault_class",
    [
        AaveVault,
        BrinkVault,
        CurvanceVault,
        DolomiteVault,
        FoxifyVault,
        GearboxVault,
        SentimentVault,
        SingularityVault,
        TermFinanceVault,
    ],
)
def test_direct_redemption_vaults_export_instant_period(vault_class: type) -> None:
    """Direct ERC-4626 adapters do not conflate a liquidity limit with a delay."""
    vault = object.__new__(vault_class)

    period = vault.get_withdrawal_period()

    assert period.min_period == datetime.timedelta(0)
    assert period.max_period == datetime.timedelta(0)
    assert period.delay_type is WithdrawalDelayType.instant
