"""Mellow ABI filenames.

The actual ABI definitions live under :mod:`eth_defi.abi` in JSON files, like
the rest of the project protocol integrations. Keep this module as a small
named access point for Mellow-specific ABI paths.
"""

FACTORY_ABI_FILENAME = "mellow/Factory.json"
"""Mellow Core Vault factory ABI file."""

VAULT_ABI_FILENAME = "mellow/Vault.json"
"""Mellow Core Vault ABI file."""

ERC20_ABI_FILENAME = "mellow/ERC20.json"
"""Minimal ERC-20 ABI file for tokenised Mellow ShareManagers."""

ORACLE_ABI_FILENAME = "mellow/Oracle.json"
"""Mellow Core Vault oracle ABI file."""

FEE_MANAGER_ABI_FILENAME = "mellow/FeeManager.json"
"""Mellow Core Vault FeeManager ABI file."""
