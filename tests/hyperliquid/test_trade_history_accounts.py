"""Offline account-classification coverage for the trade-history database."""

from pathlib import Path

from eth_defi.hyperliquid.trade_history_db import HyperliquidTradeHistoryDatabase

ACCOUNT = "0x1e37a337ed460039d1b15bd3bc489de789768d5e"


def test_account_classification_persists_and_cannot_downgrade(tmp_path: Path) -> None:
    """Persist normal accounts and preserve a later vault classification.

    Classification changes storage and filtering, not fill reconstruction. Test
    those actual boundaries without repeating the same real fill download.

    :param tmp_path:
        Isolated file-backed database location.
    :return:
        None; assertions validate persisted classification and filtering.
    """
    path = tmp_path / "accounts.duckdb"
    database = HyperliquidTradeHistoryDatabase(path)
    try:
        database.add_account(ACCOUNT, label="Normal account", is_vault=False)
        assert database.get_accounts(is_vault=False)[0]["address"] == ACCOUNT
        assert database.get_accounts(is_vault=True) == []
        assert database.is_vault_address(ACCOUNT) is False
        database.save()
    finally:
        database.close()

    database = HyperliquidTradeHistoryDatabase(path)
    try:
        assert database.get_accounts()[0]["is_vault"] is False
        database.add_account(ACCOUNT, label="Vault", is_vault=True)
        database.add_account(ACCOUNT, is_vault=False)
        assert database.get_accounts(is_vault=False) == []
        assert database.get_accounts(is_vault=True)[0]["label"] == "Vault"
        assert database.is_vault_address(ACCOUNT) is True
    finally:
        database.close()
