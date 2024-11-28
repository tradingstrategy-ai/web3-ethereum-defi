from eth_defi.lagoon.vault import LagoonVault


def test_lagoon_info(lagoon_vault: LagoonVault):
    vault = lagoon_vault
    info = vault.fetch_info()
    assert info["safe_address"] == "0x205e80371f6d1b33dff7603ca8d3e92bebd7dc25"

