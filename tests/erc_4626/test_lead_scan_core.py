"""Unit tests for ERC-4626 lead scanner terminal output."""

import logging
from decimal import Decimal

import pandas as pd
import pytest

from eth_defi.erc_4626.lead_scan_core import display_vaults_table


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
