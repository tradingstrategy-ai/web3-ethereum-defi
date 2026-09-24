"""Constants for the native Derive v3 vault dataset."""

from pathlib import Path

#: Synthetic dataset partition for Derive v3 native vaults. Not an EVM chain.
DERIVE_V3_CHAIN_ID = 9993

#: Canonical production reader state under the scanner's persistent mount.
DERIVE_V3_MAINNET_DATABASE = Path.home() / ".tradingstrategy" / "vaults" / "derive-v3-mainnet-vaults.duckdb"

#: Isolated testnet observations; never merged into the production catalogue.
DERIVE_V3_TESTNET_DATABASE = Path.home() / ".tradingstrategy" / "vaults" / "derive-v3-testnet-vaults.duckdb"


def make_derive_v3_vault_address(subaccount_id: int) -> str:
    """Return the stable synthetic address for a native vault.

    The native subaccount ID is the common key in DuckDB, shared metadata,
    raw prices and JSON. It is scoped to the chosen Derive deployment; only
    mainnet addresses enter the shared dataset.

    :param subaccount_id: Derive v3 native vault subaccount ID.
    :return: Synthetic dataset address.
    """
    if subaccount_id < 0:
        message = "Derive v3 subaccount ID cannot be negative"
        raise ValueError(message)
    return f"derive-v3-vault-{subaccount_id}"
