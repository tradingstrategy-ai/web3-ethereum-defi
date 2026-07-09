"""Test all-chain vault scanner configuration."""

from eth_defi.vault.scan_all_chains import build_chain_configs


def test_robinhood_chain_is_scheduled_for_vault_scans():
    """Robinhood is available as an EVM vault scanner target."""

    configs = {config.name: config for config in build_chain_configs()}

    robinhood = configs["Robinhood"]
    assert robinhood.env_var == "JSON_RPC_ROBINHOOD"
    assert robinhood.scan_vaults is True
