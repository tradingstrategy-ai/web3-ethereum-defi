"""Restore recorded HyperCore leader shares and deposit caps without changing prices.

Archived scanner rows and raw exports remain valid value sources without a
separate capacity clock. See `issue #1633
<https://github.com/tradingstrategy-ai/web3-ethereum-defi/issues/1633>`__.
"""

# ruff: noqa: S608 - SQL identifiers are validated and archive filenames are escaped.

import logging
from pathlib import Path

import duckdb

from eth_defi.hyperliquid.permission_recovery import PRICE_TABLES
from eth_defi.vault.backup import backup_database, file_sha256, observe_database_operation

logger = logging.getLogger(__name__)

#: Pre-permission-migration archives, in newest-first preference order.
BACKUP_DATES = ("2026-10-05", "2026-10-04", "2026-10-03", "2026-10-02", "2026-10-01", "2026-09-30", "2026-09-29")


def recover_leader_shares(target: Path, sources: list[Path], backup_dir: Path, *, parquet_sources: list[Path] | None = None, dry_run: bool = True) -> dict:  # noqa: PLR0914 - One transaction keeps the recovery and invariants together.
    """Restore missing policy values from backups and retained migration evidence.

    Scanner values precede raw Parquet values; source order resolves conflicting
    shares. Explicit caps are restored only when their accompanying share agrees
    with the retained share, or the cap has no share input. Existing non-null
    values win. Genuine responses and explicit unknowns retain export precedence.
    The migration never changes economics, permission flags or original clocks.
    Apply takes a verified backup before its transaction; dry run is read-only.

    :param target: Stopped daily or HF scanner database; use a copy for rehearsals.
    :param sources: Immutable scanner backups in preference order.
    :param backup_dir: Directory for automatic verified pre-mutation backups.
    :param parquet_sources: Original raw price exports, after scanner sources.
    :param dry_run: Report prospective changes without persistent writes.
    :return: Source hashes, restored-value counts, conflicts and backup receipt.
    """
    target = target.resolve()
    sources = [path.resolve() for path in sources]
    parquets = [path.resolve() for path in (parquet_sources or [])]
    if target in sources or len(sources) != len(set(sources)) or len(parquets) != len(set(parquets)):
        msg = "Recovery sources must be distinct immutable archives"
        raise ValueError(msg)
    if any(Path(str(path) + ".wal").exists() for path in sources):
        msg = "Scanner archives must be checkpointed without WAL files"
        raise ValueError(msg)
    report = {"target": str(target), "dry_run": dry_run, "sources": []}
    evidence = [(path, file_sha256(path)) for path in sources + parquets]
    connection = duckdb.connect(str(target), read_only=dry_run)
    transaction = False
    try:
        connection.execute("SET wal_autocheckpoint='1TB'")
        connection.execute("SET TimeZone='UTC'")
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        selected = [(table, clock) for table, clock in PRICE_TABLES.items() if table in tables]
        if len(selected) != 1:
            msg = "Expected one recognised Hyperliquid price table"
            raise ValueError(msg)
        table, clock = selected[0]
        columns = [row[0] for row in connection.execute(f"DESCRIBE {table}").fetchall()]
        if not all(name.isidentifier() for name in columns):
            msg = "Unsupported price column identifiers"
            raise ValueError(msg)
        if connection.execute(f"SELECT count(*) FROM (SELECT vault_address,{clock} FROM {table} GROUP BY ALL HAVING count(*)>1)").fetchone()[0]:
            msg = "Duplicate target price keys; refusing ambiguous recovery"
            raise ValueError(msg)
        unchanged_columns = ",".join(name for name in columns if name not in {"leader_fraction", "max_deposit"})
        invariant_sql = f"SELECT count(*),sum(hash({unchanged_columns})) FROM {table}"
        before = connection.execute(invariant_sql).fetchone()
        parts = []
        parameters = []
        for index, (path, digest) in enumerate(evidence):
            literal = str(path).replace("'", "''")
            if index < len(sources):
                connection.execute(f"ATTACH '{literal}' AS policy_archive_{index} (READ_ONLY)")
                alias = f"policy_archive_{index}.{table}"
                available = {row[0] for row in connection.execute(f"DESCRIBE {alias}").fetchall()}
                cap = "max_deposit" if "max_deposit" in available else "CAST(NULL AS DOUBLE)"
                parts.append(f"SELECT lower(vault_address) AS vault_address,CAST({clock} AS TIMESTAMP) AS source_timestamp,leader_fraction,{cap} AS max_deposit,? AS source_path,? AS source_sha256,{index} AS priority FROM {alias}")
            else:
                if path.name != "vault-prices-1h.parquet":
                    msg = "Only original raw vault-prices-1h.parquet is accepted"
                    raise ValueError(msg)
                alias = f"raw_policy_archive_{index}"
                connection.execute(f"CREATE TEMP VIEW {alias} AS SELECT * FROM read_parquet('{literal}')")
                available = {row[0] for row in connection.execute(f"DESCRIBE {alias}").fetchall()}
                if not {"chain", "address", "timestamp", "hypercore_source"}.issubset(available):
                    msg = "Raw archive lacks precise scanner source keys"
                    raise ValueError(msg)
                share = "leader_fraction" if "leader_fraction" in available else "CAST(NULL AS DOUBLE)"
                cap = "max_deposit" if "max_deposit" in available else "CAST(NULL AS DOUBLE)"
                kind = "daily" if clock == "date" else "hf"
                parts.append(f"SELECT lower(address) AS vault_address,CAST(timestamp AS TIMESTAMP) AS source_timestamp,{share} AS leader_fraction,{cap} AS max_deposit,? AS source_path,? AS source_sha256,{index + 1000} AS priority FROM {alias} WHERE chain=9999 AND hypercore_source='{kind}'")
            parameters.extend((str(path), digest))
            report["sources"].append({"path": str(path), "sha256": digest})
        # The previous repair saved these raw values before clearing them.
        # Retained scanner evidence precedes raw projected evidence.
        if "hypercore_legacy_price_evidence" in tables:
            parts.append("SELECT lower(vault_address) AS vault_address,source_timestamp,leader_fraction,TRY_CAST(json_extract_string(original_row_json,'$.max_deposit') AS DOUBLE) AS max_deposit,source_path,source_sha256,500 AS priority FROM hypercore_legacy_price_evidence")
        if "hypercore_raw_parquet_evidence" in tables:
            kind = "daily" if clock == "date" else "hf"
            parts.append(f"SELECT lower(vault_address) AS vault_address,source_timestamp,TRY_CAST(json_extract_string(original_row_json,'$.leader_fraction') AS DOUBLE) AS leader_fraction,TRY_CAST(json_extract_string(original_row_json,'$.max_deposit') AS DOUBLE) AS max_deposit,'retained raw Parquet evidence' AS source_path,source_sha256,2000 AS priority FROM hypercore_raw_parquet_evidence WHERE json_extract_string(original_row_json,'$.hypercore_source')='{kind}'")
        union = " UNION ALL ".join(parts) if parts else "SELECT CAST(NULL AS VARCHAR) AS vault_address,CAST(NULL AS TIMESTAMP) AS source_timestamp,CAST(NULL AS DOUBLE) AS leader_fraction,CAST(NULL AS DOUBLE) AS max_deposit,CAST(NULL AS VARCHAR) AS source_path,CAST(NULL AS VARCHAR) AS source_sha256,0 AS priority WHERE false"
        with observe_database_operation("Selecting archived leader shares and deposit caps"):
            connection.execute(f"CREATE TEMP TABLE policy_candidates AS SELECT DISTINCT * FROM ({union}) WHERE leader_fraction IS NOT NULL OR max_deposit IS NOT NULL", parameters)
            # Archived ratios can exceed one by floating-point rounding; retain their exact values.
            invalid = connection.execute("SELECT count(*) FROM policy_candidates WHERE NOT isfinite(leader_fraction) OR leader_fraction<0 OR leader_fraction>1.000001 OR NOT isfinite(max_deposit) OR max_deposit<0").fetchone()[0]
            if invalid:
                raise ValueError(f"Archive contains {invalid} invalid policy values")
            connection.execute("CREATE TEMP TABLE recovered_shares AS SELECT * FROM policy_candidates WHERE leader_fraction IS NOT NULL QUALIFY row_number() OVER (PARTITION BY vault_address,source_timestamp ORDER BY priority,source_path DESC,source_sha256,leader_fraction)=1")
            connection.execute(f"CREATE TEMP TABLE recovered_policy AS SELECT p.vault_address,CAST(p.{clock} AS TIMESTAMP) AS source_timestamp,coalesce(p.leader_fraction,s.leader_fraction) AS leader_fraction,{('p.max_deposit' if 'max_deposit' in columns else 'CAST(NULL AS DOUBLE)')} AS original_cap FROM {table} p LEFT JOIN recovered_shares s ON p.vault_address=s.vault_address AND CAST(p.{clock} AS TIMESTAMP)=s.source_timestamp")
            connection.execute("CREATE TEMP TABLE recovered_caps AS SELECT c.* FROM policy_candidates c JOIN recovered_policy p USING(vault_address,source_timestamp) WHERE c.max_deposit IS NOT NULL AND (c.leader_fraction IS NULL OR p.leader_fraction IS NULL OR c.leader_fraction=p.leader_fraction) QUALIFY row_number() OVER (PARTITION BY c.vault_address,c.source_timestamp ORDER BY c.priority,c.source_path DESC,c.source_sha256,c.max_deposit)=1")
        report["rows_before"] = before[0]
        report["restore_leader_fraction"] = connection.execute(f"SELECT count(*) FROM {table} p JOIN recovered_shares s ON p.vault_address=s.vault_address AND CAST(p.{clock} AS TIMESTAMP)=s.source_timestamp WHERE p.leader_fraction IS NULL").fetchone()[0]
        report["restore_max_deposit"] = connection.execute("SELECT count(*) FROM recovered_policy p JOIN recovered_caps c USING(vault_address,source_timestamp) WHERE p.original_cap IS NULL").fetchone()[0]
        report["conflicting_share_keys"] = connection.execute("SELECT count(*) FROM (SELECT vault_address,source_timestamp FROM policy_candidates WHERE leader_fraction IS NOT NULL GROUP BY ALL HAVING count(DISTINCT leader_fraction)>1)").fetchone()[0]
        constraints = connection.execute("SELECT count(*) FROM duckdb_constraints() WHERE database_name=current_database() AND table_name=? AND constraint_type IN ('PRIMARY KEY','UNIQUE')", [table]).fetchone()[0]
        report["remove_art_constraints"] = constraints
        has_observations = "vault_permission_observations" in tables
        if has_observations:
            observation_columns = {row[0] for row in connection.execute("DESCRIBE vault_permission_observations").fetchall()}
            cap = "max_deposit" if "max_deposit" in observation_columns else "CAST(NULL AS DOUBLE)"
            connection.execute(f"""
                CREATE TEMP TABLE restored_policy_observations AS
                SELECT observation_id,
                    coalesce(leader_fraction,CASE WHEN provenance='legacy_price_timestamp' THEN TRY_CAST(json_extract_string(payload_json,'$.leader_fraction') AS DOUBLE) END) AS restored_share,
                    coalesce({cap},CASE WHEN provenance='legacy_price_timestamp' THEN TRY_CAST(json_extract_string(payload_json,'$.max_deposit') AS DOUBLE) END) AS restored_cap,
                    is_closed,allow_deposits,relationship_type
                FROM vault_permission_observations WHERE record_kind='observation'
            """)
            connection.execute("UPDATE restored_policy_observations SET restored_cap=0.0 WHERE restored_cap IS NULL AND is_closed=false AND allow_deposits=true AND coalesce(relationship_type,'normal')='normal' AND restored_share<0.055")
            old_cap = "o.max_deposit" if "max_deposit" in observation_columns else "CAST(NULL AS DOUBLE)"
            report["restore_observation_inputs"] = connection.execute(f"SELECT count(*) FROM vault_permission_observations o JOIN restored_policy_observations r USING(observation_id) WHERE o.leader_fraction IS DISTINCT FROM r.restored_share OR {old_cap} IS DISTINCT FROM r.restored_cap").fetchone()[0]
            if connection.execute("SELECT count(*) FROM restored_policy_observations WHERE NOT isfinite(restored_share) OR restored_share<0 OR restored_share>1.000001 OR NOT isfinite(restored_cap) OR restored_cap<0").fetchone()[0]:
                msg = "Retained observation payload contains invalid policy values"
                raise ValueError(msg)
        if dry_run:
            return report
        report["backup"] = backup_database(connection, target, backup_dir)
        connection.execute("BEGIN TRANSACTION")
        transaction = True
        if constraints:
            schema = connection.execute(f"DESCRIBE {table}").fetchall()
            definition = ",".join(f'"{name}" {kind}' + (" NOT NULL" if nullable == "NO" else "") for name, kind, nullable, *_ in schema)
            connection.execute(f"CREATE TABLE policy_recovery_prices ({definition})")
            connection.execute(f"INSERT INTO policy_recovery_prices SELECT * FROM {table}")
            connection.execute(f"DROP TABLE {table}")
            connection.execute(f"ALTER TABLE policy_recovery_prices RENAME TO {table}")
        # Only add nullable columns; no ART indexes or rewritten price history.
        connection.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS max_deposit DOUBLE")
        connection.execute(f"UPDATE {table} p SET leader_fraction=s.leader_fraction FROM recovered_shares s WHERE p.vault_address=s.vault_address AND CAST(p.{clock} AS TIMESTAMP)=s.source_timestamp AND p.leader_fraction IS NULL")
        connection.execute(f"UPDATE {table} p SET max_deposit=c.max_deposit FROM recovered_caps c WHERE p.vault_address=c.vault_address AND CAST(p.{clock} AS TIMESTAMP)=c.source_timestamp AND p.max_deposit IS NULL")
        if has_observations:
            connection.execute("ALTER TABLE vault_permission_observations ADD COLUMN IF NOT EXISTS max_deposit DOUBLE")
            connection.execute("UPDATE vault_permission_observations o SET leader_fraction=r.restored_share,max_deposit=r.restored_cap FROM restored_policy_observations r WHERE o.observation_id=r.observation_id")
        connection.execute("CREATE TABLE IF NOT EXISTS hypercore_leader_share_recovery (vault_address VARCHAR,source_timestamp TIMESTAMP,leader_fraction DOUBLE,max_deposit DOUBLE,source_path VARCHAR,source_sha256 VARCHAR,priority INTEGER)")
        connection.execute("INSERT INTO hypercore_leader_share_recovery SELECT c.* FROM (SELECT * FROM recovered_shares UNION SELECT * FROM recovered_caps) c JOIN recovered_policy p USING(vault_address,source_timestamp) WHERE NOT EXISTS (SELECT 1 FROM hypercore_leader_share_recovery old WHERE old.vault_address=c.vault_address AND old.source_timestamp=c.source_timestamp AND old.source_sha256=c.source_sha256 AND old.leader_fraction IS NOT DISTINCT FROM c.leader_fraction AND old.max_deposit IS NOT DISTINCT FROM c.max_deposit)")
        after = connection.execute(invariant_sql).fetchone()
        if after != before:
            msg = "Non-policy price data changed; rolling back recovery"
            raise ValueError(msg)
        connection.execute("COMMIT")
        transaction = False
        connection.execute("CHECKPOINT")
        report["rows_after"] = after[0]
        return report
    finally:
        if transaction:
            connection.execute("ROLLBACK")
        connection.close()
