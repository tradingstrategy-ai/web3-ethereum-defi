"""Test Royco tranche purge helpers."""

import datetime
import importlib.util
from pathlib import Path

import pandas as pd

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

EXPECTED_REPAIRED_ROWS = 2


def load_purge_module():
    """Load the Royco purge script as a test module.

    The script filename contains dashes, so it cannot be imported using normal
    Python package syntax.

    :return:
        Loaded purge script module.
    """
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "erc-4626" / "purge-royco-tranche-data.py"
    spec = importlib.util.spec_from_file_location("purge_royco_tranche_data", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_detection(
    spec: VaultSpec,
    features: set[ERC4626Feature] | None = None,
) -> ERC4262VaultDetection:
    """Create a minimal vault detection object for tests.

    :param spec:
        Vault spec to use.

    :param features:
        Initial detected feature set.

    :return:
        Detection object suitable for ``VaultDatabase`` rows.
    """
    timestamp = datetime.datetime(2026, 6, 9, tzinfo=datetime.UTC).replace(tzinfo=None)
    return ERC4262VaultDetection(
        chain=spec.chain_id,
        address=spec.vault_address,
        first_seen_at_block=1,
        first_seen_at=timestamp,
        features=set(features or set()),
        updated_at=timestamp,
        deposit_count=1,
        redeem_count=0,
    )


def create_row(
    spec: VaultSpec,
    name: str | None,
    protocol: str,
    features: set[ERC4626Feature] | None = None,
) -> dict:
    """Create a minimal vault metadata row.

    :param spec:
        Vault spec.

    :param name:
        Vault display name.

    :param protocol:
        Stored protocol label.

    :param features:
        Initial detected feature set.

    :return:
        Vault metadata row.
    """
    initial_features = set(features or set())
    return {
        "Name": name,
        "Protocol": protocol,
        "features": set(initial_features),
        "_detection_data": create_detection(spec, initial_features),
    }


def test_repair_royco_tranche_metadata_repairs_stale_features(tmp_path: Path):
    """Royco purge metadata repair updates stale feature flags.

    1. Create a fake vault DB with one existing tranche row, one tranche name
       row, one row only identified by a ``non-standard ABI`` error, and one
       unrelated Royco-named row.
    2. Create a tiny uncleaned price parquet containing the ABI error.
    3. Run metadata repair.
    4. Verify only tranche candidates get ``royco_tranche_like`` and Protocol
       ``Royco``.
    """
    purge = load_purge_module()

    existing_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    name_spec = VaultSpec(42161, "0x0000000000000000000000000000000000000002")
    error_spec = VaultSpec(43114, "0x0000000000000000000000000000000000000003")
    generic_spec = VaultSpec(1, "0x0000000000000000000000000000000000000004")
    null_name_spec = VaultSpec(1, "0x0000000000000000000000000000000000000005")

    vault_db = VaultDatabase(
        rows={
            existing_spec: create_row(
                existing_spec,
                "Royco Senior Tranche eEARN",
                "Royco",
                {ERC4626Feature.royco_tranche_like},
            ),
            name_spec: create_row(
                name_spec,
                "Royco Senior Tranche sUSDai",
                "<protocol not yet identified>",
            ),
            error_spec: create_row(
                error_spec,
                "Senior Royco USDC",
                "<protocol not yet identified>",
            ),
            generic_spec: create_row(
                generic_spec,
                "Senior Royco USDC",
                "<protocol not yet identified>",
            ),
            null_name_spec: create_row(
                null_name_spec,
                None,
                "<protocol not yet identified>",
            ),
        }
    )
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)

    price_path = tmp_path / "vault-prices-1h.parquet"
    pd.DataFrame(
        [
            {
                "chain": error_spec.chain_id,
                "address": error_spec.vault_address,
                "errors": "total_assets returned 96 bytes, expected 32 (non-standard ABI)",
            },
            {
                "chain": generic_spec.chain_id,
                "address": generic_spec.vault_address,
                "errors": "",
            },
        ]
    ).to_parquet(price_path)

    result = purge.repair_royco_tranche_metadata(vault_db_path, price_path, dry_run=False)

    assert result.specs == {existing_spec, name_spec, error_spec}
    assert result.repaired_rows == EXPECTED_REPAIRED_ROWS

    repaired_db = VaultDatabase.read(vault_db_path)
    for spec in {existing_spec, name_spec, error_spec}:
        row = repaired_db.rows[spec]
        assert row["Protocol"] == "Royco"
        assert ERC4626Feature.royco_tranche_like in row["features"]
        assert ERC4626Feature.royco_tranche_like in row["_detection_data"].features

    generic_row = repaired_db.rows[generic_spec]
    assert generic_row["Protocol"] == "<protocol not yet identified>"
    assert ERC4626Feature.royco_tranche_like not in generic_row["features"]
    assert ERC4626Feature.royco_tranche_like not in generic_row["_detection_data"].features

    null_name_row = repaired_db.rows[null_name_spec]
    assert null_name_row["Protocol"] == "<protocol not yet identified>"
    assert ERC4626Feature.royco_tranche_like not in null_name_row["features"]
    assert ERC4626Feature.royco_tranche_like not in null_name_row["_detection_data"].features
