"""Test Hyperliquid vault metadata fetching.

This test module verifies that we can fetch vault metadata from the Hyperliquid
stats-data API and sort the results in a stable order.
"""

from eth_defi.hyperliquid.vault import (
    VaultSummary,
    fetch_all_vaults,
)


def test_hyperliquid_vault_metadata():
    """Fetch vault metadata and verify stable sort order by creation date.

    - Fetches all vaults from Hyperliquid API
    - Sorts them by creation date (oldest first) for stable ordering
    - Takes the first 10 vaults
    - Examines stable variables (name, leader address) of first and last vault
    """
    # Fetch all vaults
    vaults = list(fetch_all_vaults())

    # There should be many vaults available
    assert len(vaults) > 10, f"Expected more than 10 vaults, got {len(vaults)}"

    # Sort by creation time for stable ordering (oldest first)
    # Filter out vaults without create_time before sorting
    vaults_with_time = [v for v in vaults if v.create_time is not None]
    assert len(vaults_with_time) > 10, f"Expected more than 10 vaults with create_time, got {len(vaults_with_time)}"

    sorted_vaults = sorted(vaults_with_time, key=lambda v: v.create_time)

    # Get first 10 vaults (oldest)
    first_ten = sorted_vaults[:10]
    assert len(first_ten) == 10

    # Verify all are VaultSummary instances with required fields
    for vault in first_ten:
        assert isinstance(vault, VaultSummary)
        assert vault.vault_address, "Vault address should not be empty"
        assert vault.name is not None, "Vault name should not be None"
        assert vault.leader, "Vault leader address should not be empty"
        assert vault.create_time is not None, "Vault create_time should not be None"

    # Verify stable sort order - creation times should be in ascending order
    create_times = [v.create_time for v in first_ten]
    assert create_times == sorted(create_times), "Vaults should be sorted by creation time ascending"

    # Examine first vault (oldest)
    first_vault = first_ten[0]
    assert first_vault.vault_address.startswith("0x"), "First vault address should be a hex address"
    assert first_vault.leader.startswith("0x"), "First vault leader should be a hex address"
    assert len(first_vault.vault_address) == 42, "First vault address should be 42 chars (0x + 40 hex)"
    assert len(first_vault.leader) == 42, "First vault leader should be 42 chars (0x + 40 hex)"

    # Examine last of the first 10 vaults
    last_vault = first_ten[-1]
    assert last_vault.vault_address.startswith("0x"), "Last vault address should be a hex address"
    assert last_vault.leader.startswith("0x"), "Last vault leader should be a hex address"
    assert len(last_vault.vault_address) == 42, "Last vault address should be 42 chars (0x + 40 hex)"
    assert len(last_vault.leader) == 42, "Last vault leader should be 42 chars (0x + 40 hex)"

    # First vault should have earlier creation time than last vault
    assert first_vault.create_time < last_vault.create_time, \
        "First vault should have earlier creation time than last vault"
