"""Test all-chain vault scanner configuration."""

from eth_defi.chain import POA_MIDDLEWARE_NEEDED_CHAIN_IDS
from eth_defi.vault.scan_all_chains import build_chain_configs

LINEA_CHAIN_ID = 59144


def test_robinhood_chain_is_scheduled_for_vault_scans():
    """Robinhood is available as an EVM vault scanner target."""

    configs = {config.name: config for config in build_chain_configs()}

    robinhood = configs["Robinhood"]
    assert robinhood.env_var == "JSON_RPC_ROBINHOOD"
    assert robinhood.scan_vaults is True


def test_linea_uses_poa_middleware_for_historical_settlement_reads():
    """Linea historical settlement backfills need PoA extra-data handling."""

    assert LINEA_CHAIN_ID in POA_MIDDLEWARE_NEEDED_CHAIN_IDS
