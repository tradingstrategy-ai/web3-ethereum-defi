"""DuckDB persistence for protocol-independent perp vault observations."""

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from typing import Any

import duckdb
import pandas as pd

from eth_defi.perp_dex.metrics import PerpVaultObservationBundle, validate_perp_vault_observation_bundle

#: Ordered account-observation storage contract.  The read path validates the
#: live DuckDB schema before projecting these columns so a future migration
#: cannot silently omit a new field from correction semantics.
PERP_VAULT_ACCOUNT_OBSERVATION_COLUMNS = (
    "snapshot_id",
    "protocol_slug",
    "deployment_slug",
    "vault_id",
    "dataset_chain_id",
    "dataset_address",
    "observed_at",
    "written_at",
    "equity_effective_at",
    "position_effective_at",
    "total_equity",
    "quote_asset",
    "position_data_status",
    "position_data_reason",
    "position_set_complete",
    "source_endpoint",
    "raw_payload_reference",
    "collector_version",
)

#: Ordered position-observation storage contract used by correction signature
#: comparison and exposure aggregation.
PERP_VAULT_POSITION_OBSERVATION_COLUMNS = (
    "snapshot_id",
    "source_market_id",
    "signed_notional",
    "quote_asset",
    "valuation_basis",
    "valuation_observed_at",
    "source_endpoint",
)


def initialise_perp_vault_observation_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Create common append-only observation tables in a protocol's DuckDB file.

    The caller owns the database and transaction boundary; no central database
    is created.  The source-status constraint intentionally excludes pipeline
    statuses such as ``stale`` and ``not_collected``.

    :param connection:
        Open DuckDB connection from the protocol's metrics database.
    :return:
        ``None``.
    """
    connection.execute("""
        CREATE TABLE IF NOT EXISTS perp_vault_source_payloads (
            payload_hash VARCHAR PRIMARY KEY,
            protocol_slug VARCHAR NOT NULL,
            deployment_slug VARCHAR NOT NULL,
            captured_at TIMESTAMP NOT NULL,
            payload_json VARCHAR NOT NULL,
            payload_schema_version INTEGER NOT NULL,
            collector_version VARCHAR NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS perp_vault_account_observations (
            snapshot_id VARCHAR PRIMARY KEY,
            protocol_slug VARCHAR NOT NULL,
            deployment_slug VARCHAR NOT NULL,
            vault_id VARCHAR NOT NULL,
            dataset_chain_id UINTEGER NOT NULL,
            dataset_address VARCHAR NOT NULL,
            observed_at TIMESTAMP NOT NULL,
            written_at TIMESTAMP NOT NULL,
            equity_effective_at TIMESTAMP,
            position_effective_at TIMESTAMP NOT NULL,
            total_equity DECIMAL(38, 18),
            quote_asset VARCHAR,
            position_data_status VARCHAR NOT NULL CHECK (position_data_status IN ('available', 'not_public', 'authentication_required', 'source_error', 'not_implemented')),
            position_data_reason VARCHAR NOT NULL,
            position_set_complete BOOLEAN NOT NULL,
            source_endpoint VARCHAR NOT NULL,
            raw_payload_reference VARCHAR REFERENCES perp_vault_source_payloads(payload_hash),
            collector_version VARCHAR NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS perp_vault_position_observations (
            snapshot_id VARCHAR NOT NULL REFERENCES perp_vault_account_observations(snapshot_id),
            source_market_id VARCHAR NOT NULL,
            signed_notional DECIMAL(38, 18) NOT NULL CHECK (signed_notional != 0),
            quote_asset VARCHAR NOT NULL,
            valuation_basis VARCHAR NOT NULL,
            valuation_observed_at TIMESTAMP NOT NULL,
            source_endpoint VARCHAR NOT NULL,
            PRIMARY KEY (snapshot_id, source_market_id)
        )
    """)


def canonicalise_perp_source_payload(payload: Mapping[str, Any]) -> tuple[str, str]:
    """Serialise a trimmed source payload deterministically and hash it.

    :param payload:
        Whitelisted protocol-native source inputs used for one bundle.
    :return:
        ``(sha256_hex, canonical_json)``.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), encoded


def write_perp_vault_observation_bundle(
    connection: duckdb.DuckDBPyConnection,
    bundle: PerpVaultObservationBundle,
    payload: Mapping[str, Any],
    payload_schema_version: int = 1,
) -> str:
    """Atomically store one validated observation bundle and its payload.

    The immutable snapshot ID makes corrections append-only.  A payload is
    content-addressed and deduplicated before its account row references it.

    :param connection:
        Open protocol-owned DuckDB connection.
    :param bundle:
        Validated observation bundle.  Its payload reference is filled here.
    :param payload:
        Trimmed protocol-native source fields used for the normalisation.
    :param payload_schema_version:
        Version of the protocol payload whitelist.
    :return:
        SHA-256 payload reference written to the account observation.
    """
    validate_perp_vault_observation_bundle(bundle)
    payload_hash, payload_json = canonicalise_perp_source_payload(payload)
    account = dataclasses.replace(bundle.account, raw_payload_reference=payload_hash)

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            """
            INSERT INTO perp_vault_source_payloads VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (payload_hash) DO NOTHING
            """,
            [
                payload_hash,
                account.identity.protocol_slug,
                account.identity.deployment_slug,
                account.observed_at,
                payload_json,
                payload_schema_version,
                account.collector_version,
            ],
        )
        connection.execute(
            """
            INSERT INTO perp_vault_account_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                account.snapshot_id,
                account.identity.protocol_slug,
                account.identity.deployment_slug,
                account.identity.vault_id,
                account.identity.dataset_chain_id,
                account.identity.dataset_address,
                account.observed_at,
                account.written_at,
                account.equity_effective_at,
                account.position_effective_at,
                account.total_equity,
                account.quote_asset,
                account.position_data_status.value,
                account.position_data_reason,
                account.position_set_complete,
                account.source_endpoint,
                payload_hash,
                account.collector_version,
            ],
        )
        if bundle.positions:
            connection.executemany(
                """
                INSERT INTO perp_vault_position_observations VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    [
                        position.snapshot_id,
                        position.source_market_id,
                        position.signed_notional,
                        position.quote_asset,
                        position.valuation_basis.value,
                        position.valuation_observed_at,
                        position.source_endpoint,
                    ]
                    for position in bundle.positions
                ],
            )
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    return payload_hash


def read_perp_vault_observations(connection: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read account and position observations in derivation-ready order.

    The column lists are deliberately explicit and checked against the live
    table schemas before reading.  Besides documenting the storage contract,
    this prevents an unrelated append-only column from entering the large
    Pandas frames and prevents a future semantic column from being silently
    omitted.  A schema migration must therefore update this contract in the
    same change.  The position projection includes every field used by
    semantic correction signatures; dropping one would turn an equal-rank
    conflict into a false agreement.

    Performance history
    -------------------

    Baseline (2026-09-22, local production-format copy): ApeX account and
    position reads took about 0.35 seconds; Hypercore high-frequency reads
    took about 0.51 seconds.  The explicit projections took 0.44 seconds and
    0.64 seconds respectively in the 2026-09-22 rerun.  This is a small read
    variance rather than a claimed speed-up; the optimisation's durable gain
    is preventing accidental append-only columns from entering the larger
    correction and aggregation frames.  Peak RSS is recorded by the complete
    derivation benchmark rather than this I/O-only helper.

    :param connection:
        Open protocol-owned DuckDB connection.
    :return:
        Account and position DataFrames.
    """
    table_contracts = {
        "perp_vault_account_observations": PERP_VAULT_ACCOUNT_OBSERVATION_COLUMNS,
        "perp_vault_position_observations": PERP_VAULT_POSITION_OBSERVATION_COLUMNS,
    }
    for table_name, expected_columns in table_contracts.items():
        actual_columns = tuple(row[1] for row in connection.execute(f"PRAGMA table_info('{table_name}')").fetchall())
        if actual_columns != expected_columns:
            raise ValueError(f"Unexpected {table_name} schema: expected {expected_columns}, got {actual_columns}")

    account_projection = ", ".join(PERP_VAULT_ACCOUNT_OBSERVATION_COLUMNS)
    position_projection = ", ".join(PERP_VAULT_POSITION_OBSERVATION_COLUMNS)
    # The only interpolated identifiers are immutable module constants, and
    # the live schema was checked against the same constants immediately above.
    accounts = connection.execute(f"SELECT {account_projection} FROM perp_vault_account_observations").fetchdf()  # noqa: S608
    positions = connection.execute(f"SELECT {position_projection} FROM perp_vault_position_observations").fetchdf()  # noqa: S608
    return accounts, positions
