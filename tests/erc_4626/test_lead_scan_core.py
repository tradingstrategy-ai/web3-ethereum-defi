"""Unit tests for ERC-4626 lead scanner terminal output."""

import logging
from decimal import Decimal

import pandas as pd
import pytest

from eth_defi.erc_4626.lead_scan_core import MASS_BROKEN_RECLASSIFICATION_MIN_VAULTS, check_mass_broken_reclassification, display_vaults_table


def test_display_vaults_table_limits_columns_and_entries(caplog: pytest.LogCaptureFixture):
    """The all-chains scanner can keep its vault results log compact."""

    df = pd.DataFrame(
        {
            "Name": ["Vault one", "Vault two", "Vault three"],
            "Protocol": ["Protocol"] * 3,
            "Share token": ["SHARE"] * 3,
            "NAV": [Decimal("1001")] * 3,
            "Address": ["0x1", "0x2", "0x3"],
            "Features": ["verbose"] * 3,
        }
    )

    caplog.set_level(logging.INFO, logger="eth_defi.erc_4626.lead_scan_core")
    display_vaults_table(df, max_entries=2)

    assert "Vault one" in caplog.text
    assert "Vault two" in caplog.text
    assert "Vault three" not in caplog.text
    assert "Name" in caplog.text
    assert "Protocol" in caplog.text
    assert "Share token" in caplog.text
    assert "NAV" in caplog.text
    assert "Address" not in caplog.text
    assert "Features" not in caplog.text


def _entry(*features: str) -> dict:
    """Build a minimal classification cache entry."""
    return {"features": list(features)}


def test_mass_broken_reclassification_aborts() -> None:
    """Most previously working vaults turning broken at once is refused.

    Mirrors the 2026-11-14 Arc incident, where 118 of 119 vaults were cached as broken.
    """
    previous = {f"0x{index:040x}": _entry("morpho_like") for index in range(119)}
    fresh = {address: _entry("broken") for address in list(previous)[:118]}
    with pytest.raises(RuntimeError, match="118 of 118 previously working vaults"):
        check_mass_broken_reclassification(5042, previous, fresh)


@pytest.mark.parametrize(
    ("previously_working", "newly_broken"),
    [
        (100, MASS_BROKEN_RECLASSIFICATION_MIN_VAULTS - 1),  # Below the absolute floor.
        (1000, 100),  # Below the 20 % share.
    ],
)
def test_mass_broken_reclassification_allows_ordinary_breakage(previously_working: int, newly_broken: int) -> None:
    """A few genuinely broken contracts, or a small share of a large chain, are persisted."""
    previous = {f"0x{index:040x}": _entry("morpho_like") for index in range(previously_working)}
    previous["0x" + "ff" * 20] = _entry("broken")
    fresh = {address: _entry("broken") if index < newly_broken else _entry("morpho_like") for index, address in enumerate(previous)}
    fresh["0x" + "ee" * 20] = _entry("broken")
    check_mass_broken_reclassification(1, previous, fresh)
