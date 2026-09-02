"""Shared launch helpers for Enzyme metadata migrations.

The scripts in :mod:`scripts.enzyme` remain separate operator entry points,
but both delegate to the same resumable historical-migration engine. This
module centralises their temporary environment handling so new migration flags
cannot leak between entry points.
"""

import os
import runpy
from collections.abc import Mapping, MutableMapping
from pathlib import Path

from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE

#: Environment values temporarily overridden while the delegated script runs.
MIGRATION_ENVIRONMENT_VARIABLES = (
    "ENZYME_SCAN_PRICES",
    "ENZYME_CLEAN_PRICES",
    "ENZYME_REFRESH_EXISTING_METADATA",
    "ENZYME_REFRESH_BLUE_FEES",
    "ENZYME_CHECKPOINT_PATH",
)


def configure_enzyme_migration_environment(environment: MutableMapping[str, str], overrides: Mapping[str, str], checkpoint_filename: str) -> None:
    """Apply a metadata-only Enzyme migration configuration in place.

    The caller supplies the explicit refresh flags for its operation. A caller
    may still set a custom checkpoint path, but price scanning and cleaning are
    always disabled by the entry points using this helper.

    :param environment: Process environment mapping to update.
    :param overrides: Explicit migration mode flags to force.
    :param checkpoint_filename: Default checkpoint basename beside the vault
        metadata database.
    :return: None.
    """

    environment.update(overrides)
    vault_db_path = Path(environment.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    environment.setdefault("ENZYME_CHECKPOINT_PATH", str(vault_db_path.with_name(checkpoint_filename)))


def run_enzyme_backfill_with_environment(overrides: Mapping[str, str], checkpoint_filename: str, script_path: Path) -> None:
    """Run the shared backfill entry point with temporary migration settings.

    The previous process environment is restored even when the delegated
    migration fails. This makes the helper safe for unit tests and for callers
    that invoke more than one migration in the same Python process.

    :param overrides: Explicit migration mode flags to force.
    :param checkpoint_filename: Default checkpoint basename beside the vault
        metadata database.
    :param script_path: Path to the delegated ``backfill-history.py`` script.
    :return: None after the delegated migration exits.
    """

    previous_values = {name: os.environ.get(name) for name in MIGRATION_ENVIRONMENT_VARIABLES}
    try:
        configure_enzyme_migration_environment(os.environ, overrides, checkpoint_filename)
        runpy.run_path(str(script_path), run_name="__main__")
    finally:
        for name, value in previous_values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
