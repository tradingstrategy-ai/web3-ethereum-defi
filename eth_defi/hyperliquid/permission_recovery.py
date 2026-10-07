"""Backed-up, transactional recovery of HyperCore prices and permission evidence.

Old price-row flags have no independent response receipts. Recover their
historical state from explicitly selected backups using the price timestamp
as an inferred permission clock. Preserve provenance so an approximation is
never labelled as an authenticated API receipt. See issue
https://github.com/tradingstrategy-ai/web3-ethereum-defi/issues/1628.
"""

import datetime
from pathlib import Path

import duckdb
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.permission import initialise_permission_schema
from eth_defi.vault.backup import backup_database, file_sha256, observe_database_operation
from eth_defi.vault.backup import write_json_atomic as write_json_atomic  # noqa: PLC0414 - compatibility export for saved recovery tooling.

PRICE_TABLES = {"vault_daily_prices": "date", "vault_high_freq_prices": "timestamp"}


def prepare_price_timestamp_permissions(connection: duckdb.DuckDBPyConnection, table: str, time_column: str, archives: list[tuple[str, str, str, str | None, str | None]], parquets: list[tuple[str, str, str, set[str]]]) -> None:
    """Stage legacy permission snapshots with an explicitly inferred price clock.

    Only operator-selected backup sources supply flags. Current target flags
    are excluded because this repair addresses their known corruption. Archive
    order resolves matching-key conflicts, with raw Parquet last. A source row
    with no flags supplies no permission evidence. HLP identity comes from its
    source metadata; capacity receives no inferred freshness clock.

    :param connection: Caller-owned connection; only TEMP tables are written.
    :param table: Allowlisted scanner price table name.
    :param time_column: Source price clock column, date or timestamp.
    :param archives: Price alias, immutable path, SHA-256, optional metadata and permission aliases.
    :param parquets: Raw view alias, immutable path, SHA-256 and available columns.
    :return: ``None``; prepares legacy_permission_candidates for dry/apply paths.
    """
    parts = []
    parameters = []
    scanner_keys = " UNION ALL ".join(f"SELECT vault_address,CAST({time_column} AS TIMESTAMP) AS permission_time FROM {alias}" for alias, _, _, _, _ in archives)
    if scanner_keys:
        connection.execute(f"CREATE TEMP TABLE scanner_permission_price_keys AS SELECT DISTINCT * FROM ({scanner_keys})")
    else:
        connection.execute("CREATE TEMP TABLE scanner_permission_price_keys (vault_address VARCHAR,permission_time TIMESTAMP)")
    for priority, (alias, path, digest, metadata, permission_alias) in enumerate(archives):
        relationship = "coalesce(m.relationship_type,'normal')" if metadata else "'normal'"
        metadata_join = f"LEFT JOIN {metadata} m USING(vault_address)" if metadata else ""
        parts.append(f"SELECT a.vault_address,CAST(a.{time_column} AS TIMESTAMP) AS permission_time,a.written_at AS original_written_at,a.is_closed,a.allow_deposits,{relationship} AS relationship_type,to_json(a) AS payload_json,{priority * 2 + 1} AS priority,? AS source_path,? AS source_sha256 FROM {alias} a {metadata_join} WHERE a.is_closed IS NOT NULL OR a.allow_deposits IS NOT NULL")
        parameters.extend((path, digest))
        if permission_alias:
            # A repaired backup has NULL compatibility fields but still retains
            # its inferred evidence. Include it in the canonical replacement.
            parts.append(f"SELECT vault_address,permission_observed_at AS permission_time,TRY_CAST(json_extract_string(payload_json,'$.written_at') AS TIMESTAMP) AS original_written_at,is_closed,allow_deposits,coalesce(relationship_type,'normal') AS relationship_type,payload_json,{priority * 2} AS priority,? AS source_path,? AS source_sha256 FROM {permission_alias} WHERE provenance='legacy_price_timestamp' AND source_endpoint='archive price rows/{table}' AND permission_observed_at IS NOT NULL AND (is_closed IS NOT NULL OR allow_deposits IS NOT NULL)")
            parameters.extend((path, digest))
    for priority, (alias, path, digest, columns) in enumerate(parquets, start=len(archives) * 2):
        if "deposits_open" not in columns:
            continue
        reason = "coalesce(deposit_closed_reason,'')" if "deposit_closed_reason" in columns else "''"
        written = "written_at" if "written_at" in columns else "CAST(NULL AS TIMESTAMP)"
        # A raw export records classified access, not the original API pair.
        # Preserve that classification and retain the full row as its payload.
        # Never re-date a scanner's carried flag using the export's later price
        # rows. Raw-only keys can use the operator-approved inferred fallback.
        parts.append(f"SELECT lower(address) AS vault_address,CAST(timestamp AS TIMESTAMP) AS permission_time,{written} AS original_written_at,({reason}='Vault is permanently closed') AS is_closed,(lower(CAST(deposits_open AS VARCHAR))='true') AS allow_deposits,'normal' AS relationship_type,to_json(r) AS payload_json,{priority} AS priority,? AS source_path,? AS source_sha256 FROM {alias} r WHERE chain=9999 AND hypercore_source=? AND lower(CAST(deposits_open AS VARCHAR)) IN ('true','false') AND NOT EXISTS (SELECT 1 FROM scanner_permission_price_keys k WHERE k.vault_address=lower(r.address) AND k.permission_time=CAST(r.timestamp AS TIMESTAMP))")
        parameters.extend((path, digest, "daily" if time_column == "date" else "hf"))
    if parts:
        connection.execute(f"CREATE TEMP TABLE legacy_permission_candidates AS SELECT * EXCLUDE(priority),sha256('price-clock:{table}:' || vault_address || CAST(permission_time AS VARCHAR) || payload_json || relationship_type) AS observation_id FROM ({' UNION ALL '.join(parts)}) QUALIFY row_number() OVER (PARTITION BY vault_address,permission_time ORDER BY priority)=1", parameters)
    else:
        connection.execute("CREATE TEMP TABLE legacy_permission_candidates (vault_address VARCHAR,permission_time TIMESTAMP,original_written_at TIMESTAMP,is_closed BOOLEAN,allow_deposits BOOLEAN,relationship_type VARCHAR,payload_json VARCHAR,source_path VARCHAR,source_sha256 VARCHAR,observation_id VARCHAR)")


def restore_price_timestamp_permissions(connection: duckdb.DuckDBPyConnection, table: str) -> None:
    """Apply inferred legacy snapshots without rewriting prices or genuine receipts.

    The endpoint's inferred annotations are rebuilt transactionally from the
    explicitly selected backup preference. Stable IDs retain their original annotation write clock,
    making reapplication idempotent. Raw source payloads and references remain
    auditable; permission_observed_at carries the inferred price clock only for
    records explicitly labelled legacy_price_timestamp.

    :param connection: Caller-owned connection inside the recovery transaction.
    :param table: Allowlisted price table defining this annotation scope.
    :return: ``None`` after applying prepared candidates.
    """
    endpoint = f"archive price rows/{table}"
    connection.execute("CREATE TEMP TABLE prior_price_permission_annotations AS SELECT * FROM vault_permission_observations WHERE provenance='legacy_price_timestamp' AND source_endpoint=?", [endpoint])
    connection.execute("DELETE FROM vault_permission_sources WHERE observation_id IN (SELECT observation_id FROM prior_price_permission_annotations)")
    connection.execute("DELETE FROM vault_permission_observations WHERE provenance='legacy_price_timestamp' AND source_endpoint=?", [endpoint])
    connection.execute(
        """
        INSERT INTO vault_permission_observations
            (observation_id,vault_address,record_kind,permission_observed_at,written_at,is_closed,allow_deposits,relationship_type,provenance,source_endpoint,collector_version,payload_json,payload_sha256,source_row_key,reason,leader_fraction,max_deposit)
        SELECT c.observation_id,c.vault_address,'observation',c.permission_time,coalesce(p.written_at,?),c.is_closed,c.allow_deposits,c.relationship_type,'legacy_price_timestamp',?,'2',c.payload_json,sha256(c.payload_json),sha256(c.payload_json),'Permission clock inferred from the backup price row timestamp; not an API receipt',
            TRY_CAST(json_extract_string(c.payload_json,'$.leader_fraction') AS DOUBLE),
            coalesce(TRY_CAST(json_extract_string(c.payload_json,'$.max_deposit') AS DOUBLE),
                CASE WHEN c.is_closed=false AND c.allow_deposits=true AND c.relationship_type='normal'
                    AND TRY_CAST(json_extract_string(c.payload_json,'$.leader_fraction') AS DOUBLE)<0.055 THEN 0.0 END)
        FROM legacy_permission_candidates c LEFT JOIN prior_price_permission_annotations p USING(observation_id)
        WHERE NOT EXISTS (SELECT 1 FROM vault_permission_observations o WHERE o.observation_id=c.observation_id)
    """,
        [native_datetime_utc_now(), endpoint],
    )
    connection.execute("INSERT INTO vault_permission_sources SELECT observation_id,source_sha256,source_path FROM legacy_permission_candidates c WHERE NOT EXISTS (SELECT 1 FROM vault_permission_sources s WHERE s.observation_id=c.observation_id AND s.source_sha256=c.source_sha256)")


# Hash joins use fixed table allowlists and validated/escaped paths.
# ruff: noqa: S608


def recover_permissions(target: Path, sources: list[Path], backup_dir: Path, *, dry_run: bool = True, source_available_at: dict[str, datetime.datetime] | None = None, parquet_sources: list[Path] | None = None) -> dict:  # noqa: PLR0914
    """Restore missing price keys and retain uncertain legacy permission evidence.

    Current target values win matching-key conflicts; every differing archive
    row is retained in an audit table. Sources must be immutable scanner DuckDB
    files. Never pass a live production database as a local rehearsal target.
    Recovered backup flags use their price timestamp as an explicitly inferred
    permission clock when no genuine receipt is available. Missing backup flags
    remain unknown. The corrupted target's flags never supply this fallback.
    Mutation is backed up and committed atomically, including ART index removal.

    :param target: Existing target database; stop its owner before applying.
    :param sources: Earlier scanner database snapshots in preference order.
    :param backup_dir: Directory for verified pre-mutation backups.
    :param dry_run: Read-only analysis; default ``True``.
    :param parquet_sources: Optional raw published Parquets, restored after DuckDB sources. Cleaned Parquets are refused.
    :param source_available_at: Optional verified archive availability bounds, keyed by absolute source filename. These are never API receipt clocks.
    :return: Counts, archive hashes, conflicts and optional own-backup receipt.
    """
    target = target.resolve()
    sources = [path.resolve() for path in sources]
    parquet_sources = [path.resolve() for path in (parquet_sources or [])]
    if target in sources or len(sources) != len(set(sources)):
        msg = "Recovery sources must be distinct from the target and each other"
        raise ValueError(msg)
    report = {"schema_version": 1, "target": str(target), "dry_run": dry_run, "sources": [], "tables": {}}
    # Hash before acquiring the target writer connection. Sources are immutable.
    if any(Path(str(path) + ".wal").exists() for path in sources):
        msg = "Archive sources must be checkpointed immutable databases without WALs"
        raise ValueError(msg)
    evidence = [(path, file_sha256(path)) for path in sources]
    parquet_evidence = [(path, file_sha256(path)) for path in parquet_sources]
    connection = duckdb.connect(str(target), read_only=dry_run)
    transaction = False
    try:
        connection.execute("SET wal_autocheckpoint = '1TB'")
        connection.execute("SET TimeZone = 'UTC'")
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        selected = [(table, time) for table, time in PRICE_TABLES.items() if table in tables]
        if len(selected) != 1:
            msg = "Expected exactly one recognised Hyperliquid price table"
            raise ValueError(msg)
        table, time = selected[0]
        columns = connection.execute(f"DESCRIBE {table}").fetchall()
        required = {"vault_address", time, "share_price", "tvl", "is_closed", "allow_deposits", "leader_fraction", "written_at"}
        if any(not row[0].isidentifier() or row[1] not in {"VARCHAR", "DATE", "TIMESTAMP", "DOUBLE", "INTEGER", "BOOLEAN", "BIGINT"} for row in columns):
            msg = "Unsupported price identifiers or types"
            raise ValueError(msg)
        if not required.issubset({row[0] for row in columns}):
            msg = "Unsupported price schema; refusing a lossy recovery"
            raise ValueError(msg)
        if connection.execute(f"SELECT count(*) FROM (SELECT vault_address,{time} FROM {table} GROUP BY ALL HAVING count(*) > 1)").fetchone()[0]:
            msg = "Target already contains duplicate price keys"
            raise ValueError(msg)
        already_recovered = "vault_permission_observations" in tables and connection.execute("SELECT count(*) FROM vault_permission_observations WHERE record_kind='uncertainty_boundary' AND provenance='corrupted_unknown'").fetchone()[0] > 0
        exact_permission_archives = []
        fallback_archives = []
        fallback_parquets = []
        for index, (path, digest) in enumerate(evidence):
            # ATTACH does not accept bind parameters; escape SQL string quotes.
            quoted_path = str(path).replace("'", "''")
            connection.execute(f"ATTACH '{quoted_path}' AS archive_{index} (READ_ONLY)")
            schema = connection.execute(f"DESCRIBE archive_{index}.{table}").fetchall()
            if [(r[0], r[1]) for r in schema if r[0] != "max_deposit"] != [(r[0], r[1]) for r in columns if r[0] != "max_deposit"]:
                msg = f"Archive schema drift in {path}; refusing a lossy recovery"
                raise ValueError(msg)
            if connection.execute(f"SELECT count(*) FROM (SELECT vault_address,{time} FROM archive_{index}.{table} GROUP BY ALL HAVING count(*)>1)").fetchone()[0]:
                msg = "Scanner archive contains duplicate price keys; source precedence is ambiguous"
                raise ValueError(msg)
            report["sources"].append({"path": str(path), "sha256": digest})
            archive_tables = {row[0] for row in connection.execute(f"SELECT table_name FROM information_schema.tables WHERE table_catalog='archive_{index}'").fetchall()}
            metadata_alias = f"archive_{index}.vault_metadata" if "vault_metadata" in archive_tables else None
            if metadata_alias and "relationship_type" not in {row[0] for row in connection.execute(f"DESCRIBE {metadata_alias}").fetchall()}:
                metadata_alias = None
            permission_alias = f"archive_{index}.vault_permission_observations" if "vault_permission_observations" in archive_tables else None
            if "max_deposit" in {r[0] for r in schema} and "max_deposit" not in {r[0] for r in columns}:
                raise ValueError("Target must be opened by the updated scanner before importing a newer price schema")
            price_alias = f"archive_{index}.{table}"
            if "max_deposit" in {r[0] for r in columns} and "max_deposit" not in {r[0] for r in schema}:
                price_alias = f"archive_prices_{index}"
                connection.execute(f"CREATE TEMP VIEW {price_alias} AS SELECT *,CAST(NULL AS DOUBLE) AS max_deposit FROM archive_{index}.{table}")
            fallback_archives.append((price_alias, str(path), digest, metadata_alias, permission_alias))
            if "vault_permission_observations" in archive_tables:
                exact_permission_archives.append((f"archive_{index}.vault_permission_observations", str(path), digest))
        aliases = [entry[0] for entry in fallback_archives]
        comparison_by_alias = {}
        # Raw published files are archive-only evidence. Cleaned/resampled
        # prices cannot be promoted back to exact scanner observations.
        mapped = {"vault_address": "lower(address)", time: "CAST(timestamp AS " + ("DATE" if time == "date" else "TIMESTAMP") + ")", "share_price": "share_price", "tvl": "total_assets", "cumulative_pnl": "account_pnl", "follower_count": "follower_count", "cumulative_volume": "cumulative_volume", "leader_fraction": "leader_fraction", "max_deposit": "max_deposit", "leader_commission": "leader_commission", "epoch_reset": "epoch_reset", "written_at": "written_at", "data_source": "'archive_raw_parquet'"}
        flow_names = ("deposit_count", "withdrawal_count", "deposit_usd", "withdrawal_usd")
        for name in flow_names:
            mapped[name if time == "timestamp" else "daily_" + name] = "daily_" + name
        for index, (path, digest) in enumerate(parquet_evidence):
            if path.name != "vault-prices-1h.parquet":
                msg = "Only original raw vault-prices-1h.parquet may restore scanner prices"
                raise ValueError(msg)
            parquet_path_literal = str(path).replace("'", "''")
            connection.execute(f"CREATE TEMP VIEW raw_archive_{index} AS SELECT * FROM read_parquet('{parquet_path_literal}')")
            raw_columns = {row[0] for row in connection.execute(f"DESCRIBE raw_archive_{index}").fetchall()}
            fallback_parquets.append((f"raw_archive_{index}", str(path), digest, raw_columns))
            if not {"chain", "address", "timestamp", "share_price", "total_assets", "hypercore_source"}.issubset(raw_columns):
                msg = "Raw Parquet lacks precise scanner source provenance"
                raise ValueError(msg)
            kind = "daily" if time == "date" else "hf"
            if connection.execute(f"SELECT count(*) FROM (SELECT lower(address),timestamp FROM raw_archive_{index} WHERE chain=9999 AND hypercore_source=? GROUP BY ALL HAVING count(*)>1)", [kind]).fetchone()[0]:
                msg = "Raw Parquet contains duplicate scanner keys; source precedence is ambiguous"
                raise ValueError(msg)
            expressions = []
            for name, sql_type, *_ in columns:
                expression = mapped.get(name, "NULL")
                if expression not in {"NULL", "'archive_raw_parquet'"} and name not in {"vault_address", time} and expression not in raw_columns:
                    expression = "NULL"
                expressions.append(f'CAST({expression} AS {sql_type}) AS "{name}"')
            alias = f"parquet_archive_{index}"
            connection.execute(f"CREATE TEMP TABLE {alias} AS SELECT {','.join(expressions)} FROM raw_archive_{index} WHERE chain=9999 AND hypercore_source=? AND share_price IS NOT NULL AND total_assets IS NOT NULL", [kind])
            aliases.append(alias)
            compared = [name for name in ("share_price", "tvl", "cumulative_pnl") if name in {row[0] for row in columns} and mapped[name] in raw_columns]
            comparison_by_alias[alias] = " OR ".join(f'p."{name}" IS DISTINCT FROM a."{name}"' for name in compared)
            report["sources"].append({"path": str(path), "sha256": digest, "kind": "archive_raw_parquet"})
        key = f"vault_address,{time}"
        join = f"p.vault_address=a.vault_address AND p.{time}=a.{time}"
        union = " UNION ALL ".join(f"SELECT *,{i} AS source_priority FROM {alias}" for i, alias in enumerate(aliases))
        # TEMP tables are safe in a read-only database and do not modify its file.
        if union:
            connection.execute(f"CREATE TEMP TABLE recovery_candidates AS SELECT * EXCLUDE(source_priority) FROM ({union}) QUALIFY row_number() OVER (PARTITION BY {key} ORDER BY source_priority) = 1")
        else:
            connection.execute(f"CREATE TEMP TABLE recovery_candidates AS SELECT * FROM {table} WHERE false")
        before = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        missing = connection.execute(f"SELECT count(*) FROM recovery_candidates a WHERE NOT EXISTS (SELECT 1 FROM {table} p WHERE {join})").fetchone()[0]
        comparison = " OR ".join(f'p."{r[0]}" IS DISTINCT FROM a."{r[0]}"' for r in columns if r[0] not in {"vault_address", time})
        conflict_union = " UNION ALL ".join(f"SELECT a.vault_address,a.{time} FROM {alias} a JOIN {table} p ON {join} WHERE {comparison_by_alias.get(alias, comparison)}" for alias in aliases)
        conflicts = connection.execute(f"SELECT count(*) FROM (SELECT DISTINCT * FROM ({conflict_union}))").fetchone()[0] if conflict_union else 0
        constraints = connection.execute("SELECT constraint_type FROM duckdb_constraints() WHERE database_name = current_database() AND table_name = ? AND constraint_type IN ('PRIMARY KEY','UNIQUE')", [table]).fetchall()
        report["tables"][table] = {"rows_before": before, "restore_missing": missing, "matching_conflicts_current_wins": conflicts, "remove_art_constraints": len(constraints)}
        with observe_database_operation("Preparing backup flags with inferred price clocks"):
            prepare_price_timestamp_permissions(connection, table, time, fallback_archives, fallback_parquets)
        report["legacy_price_timestamp_permissions"] = connection.execute("SELECT count(*) FROM legacy_permission_candidates").fetchone()[0]
        if dry_run:
            return report
        report["backup"] = backup_database(connection, target, backup_dir)
        connection.execute("BEGIN TRANSACTION")
        transaction = True
        # Rebuild only when constrained, preserving all columns, nulls and rows.
        if constraints:
            definition = ",".join(f'"{r[0]}" {r[1]}' + (" NOT NULL" if r[2] == "NO" else "") for r in columns)
            connection.execute(f"CREATE TABLE recovery_price_table ({definition})")
            connection.execute(f"INSERT INTO recovery_price_table SELECT * FROM {table}")
            connection.execute(f"DROP TABLE {table}")
            connection.execute(f"ALTER TABLE recovery_price_table RENAME TO {table}")
        initialise_permission_schema(connection)
        for alias, path_string, digest in exact_permission_archives:
            expected = connection.execute("DESCRIBE vault_permission_observations").fetchall()
            source_schema = connection.execute(f"DESCRIBE {alias}").fetchall()
            if [(r[0], r[1]) for r in source_schema if r[0] != "max_deposit"] != [(r[0], r[1]) for r in expected if r[0] != "max_deposit"]:
                msg = "Permission archive schema differs; refusing a lossy import"
                raise ValueError(msg)
            if "max_deposit" not in {r[0] for r in source_schema}:
                adapted_alias = "adapted_" + alias.replace(".", "_")
                connection.execute(f"CREATE TEMP VIEW {adapted_alias} AS SELECT *,CAST(NULL AS DOUBLE) AS max_deposit FROM {alias}")
                alias = adapted_alias
            differences = " OR ".join(f'o."{r[0]}" IS DISTINCT FROM a."{r[0]}"' for r in expected if r[0] not in {"observation_id", "max_deposit"})
            differences += " OR (o.max_deposit IS NOT NULL AND a.max_deposit IS NOT NULL AND o.max_deposit IS DISTINCT FROM a.max_deposit)"
            if connection.execute(f"SELECT count(*) FROM {alias} a JOIN vault_permission_observations o USING(observation_id) WHERE {differences}").fetchone()[0]:
                msg = "Conflicting immutable observation ID in permission archive"
                raise ValueError(msg)
            connection.execute(f"UPDATE vault_permission_observations o SET max_deposit=a.max_deposit FROM {alias} a WHERE o.observation_id=a.observation_id AND o.max_deposit IS NULL AND a.max_deposit IS NOT NULL")
            connection.execute(f"INSERT INTO vault_permission_observations SELECT a.* FROM {alias} a WHERE NOT EXISTS (SELECT 1 FROM vault_permission_observations o WHERE o.observation_id=a.observation_id)")
            connection.execute(f"INSERT INTO vault_permission_sources SELECT observation_id,?,? FROM {alias} a WHERE NOT EXISTS (SELECT 1 FROM vault_permission_sources s WHERE s.observation_id=a.observation_id AND s.source_sha256=?)", [digest, path_string, digest])
        connection.execute("CREATE TABLE IF NOT EXISTS hypercore_recovery_sources (source_sha256 VARCHAR, source_path VARCHAR, imported_at TIMESTAMP)")
        connection.execute("CREATE TABLE IF NOT EXISTS hypercore_legacy_price_evidence (evidence_id VARCHAR, vault_address VARCHAR, source_sha256 VARCHAR, source_path VARCHAR, source_timestamp TIMESTAMP, original_written_at TIMESTAMP, is_closed BOOLEAN, allow_deposits BOOLEAN, leader_fraction DOUBLE, original_row_json VARCHAR)")
        connection.execute(f"CREATE TABLE IF NOT EXISTS hypercore_price_conflicts AS SELECT *,CAST(NULL AS VARCHAR) AS source_sha256 FROM {table} WHERE false")
        if "max_deposit" in {row[0] for row in columns}:
            connection.execute("ALTER TABLE hypercore_price_conflicts ADD COLUMN IF NOT EXISTS max_deposit DOUBLE")
        connection.execute("CREATE TABLE IF NOT EXISTS hypercore_raw_parquet_evidence (source_sha256 VARCHAR, vault_address VARCHAR, source_timestamp TIMESTAMP, original_row_json VARCHAR)")
        for index, (_path, digest) in enumerate(parquet_evidence):
            if not connection.execute("SELECT count(*) FROM hypercore_recovery_sources WHERE source_sha256=?", [digest]).fetchone()[0]:
                connection.execute(f"INSERT INTO hypercore_raw_parquet_evidence SELECT ?,lower(address),timestamp,to_json(r) FROM raw_archive_{index} r WHERE chain=9999", [digest])
        # Include target evidence before any clearing; stable row hashes exclude
        # archive file identity and migration publication time.
        all_sources = [(table, report["backup"]["backup"], report["backup"]["sha256"], None)] + [(alias, str(path), digest, path) for alias, (path, digest) in zip(aliases, evidence + parquet_evidence)]
        for alias, path_string, digest, path in tqdm(all_sources, desc="Importing archived evidence"):
            if path is None and already_recovered:
                continue
            if connection.execute("SELECT count(*) FROM hypercore_recovery_sources WHERE source_sha256=?", [digest]).fetchone()[0]:
                continue
            connection.execute(
                f"""
                INSERT INTO hypercore_legacy_price_evidence
                SELECT sha256(to_json(a)),vault_address,?, ?,CAST({time} AS TIMESTAMP),written_at,is_closed,allow_deposits,leader_fraction,to_json(a)
                FROM {alias} a WHERE (is_closed IS NOT NULL OR allow_deposits IS NOT NULL OR leader_fraction IS NOT NULL)
                AND NOT EXISTS (SELECT 1 FROM hypercore_legacy_price_evidence e WHERE e.evidence_id=sha256(to_json(a)) AND e.source_sha256=?)
            """,
                [digest, path_string, digest],
            )
            if path is None:
                connection.execute(f"INSERT INTO {table} SELECT a.* FROM recovery_candidates a WHERE NOT EXISTS (SELECT 1 FROM {table} p WHERE {join})")
                available_at = datetime.datetime.fromisoformat(report["backup"]["created_at"])
            if path is not None:
                archive_comparison = comparison_by_alias.get(alias, comparison)
                connection.execute(f"INSERT INTO hypercore_price_conflicts ({','.join(row[0] for row in columns)},source_sha256) SELECT a.*,? FROM {alias} a JOIN {table} p ON {join} WHERE ({archive_comparison}) AND NOT EXISTS (SELECT 1 FROM hypercore_price_conflicts e WHERE e.vault_address=a.vault_address AND e.{time}=a.{time} AND e.source_sha256=?)", [digest, digest])
                connection.execute("INSERT INTO hypercore_recovery_sources VALUES (?,?,?)", [digest, path_string, native_datetime_utc_now()])
                available_at = (source_available_at or {}).get(path_string)
            if available_at is not None:
                if available_at.tzinfo is not None:
                    msg = "Archive availability bound must be naive UTC"
                    raise ValueError(msg)
                # Deny-only archive evidence keeps raw inputs for audit but
                # cannot certify openness, capacity freshness or end a gap.
                connection.execute(
                    """
                    INSERT INTO vault_permission_observations
                        (observation_id,vault_address,record_kind,written_at,evidence_available_at,is_closed,allow_deposits,provenance,source_endpoint,collector_version,payload_json,payload_sha256,source_row_key,reason)
                    SELECT sha256('bounded-closure:' || evidence_id || CAST(? AS VARCHAR)),vault_address,'observation',?, ?,is_closed,allow_deposits,'legacy_closure_bounded','archive migration','2',original_row_json,sha256(original_row_json),evidence_id,'Explicit closure available by archive bound; API receipt and freshness unknown'
                    FROM hypercore_legacy_price_evidence e
                    WHERE source_sha256=? AND (is_closed=true OR allow_deposits=false)
                    AND NOT EXISTS (SELECT 1 FROM vault_permission_observations o WHERE o.source_row_key=e.evidence_id AND o.provenance='legacy_closure_bounded' AND o.evidence_available_at<=?)
                """,
                    [available_at, native_datetime_utc_now(), available_at, digest, available_at],
                )

        connection.execute(f"INSERT INTO {table} SELECT a.* FROM recovery_candidates a WHERE NOT EXISTS (SELECT 1 FROM {table} p WHERE {join})")

        # Retain recorded leader shares while clearing corrupted permission flags.
        # Backup flags are restored separately and projected at export.
        connection.execute(f"UPDATE {table} SET is_closed=NULL,allow_deposits=NULL")
        if "vault_metadata" in tables:
            connection.execute("CREATE TABLE IF NOT EXISTS hypercore_legacy_metadata_evidence AS SELECT * FROM vault_metadata")
            metadata_columns = connection.execute("PRAGMA table_info('vault_metadata')").fetchall()
            for name in ("is_closed", "allow_deposits"):
                connection.execute(f"ALTER TABLE vault_metadata ALTER COLUMN {name} DROP DEFAULT")
                if any(row[1] == name and row[3] for row in metadata_columns):
                    connection.execute(f"ALTER TABLE vault_metadata ALTER COLUMN {name} DROP NOT NULL")
            connection.execute("UPDATE vault_metadata SET is_closed=NULL,allow_deposits=NULL")
            connection.execute("""
                UPDATE vault_metadata m SET is_closed=o.is_closed,allow_deposits=o.allow_deposits
                FROM (SELECT * FROM vault_permission_observations WHERE record_kind='observation' AND provenance IN ('observed','observed_unknown','restored') AND permission_observed_at IS NOT NULL QUALIFY row_number() OVER (PARTITION BY vault_address ORDER BY permission_observed_at DESC,observation_id DESC)=1) o
                WHERE m.vault_address=o.vault_address
            """)

        connection.execute(f"""
            INSERT INTO vault_permission_observations (observation_id,vault_address,record_kind,provenance,source_endpoint,collector_version,effective_from,reason)
            SELECT sha256('legacy-uncertainty:{table}:' || vault_address || CAST(first_time AS VARCHAR)),vault_address,'uncertainty_boundary','corrupted_unknown','archive migration','2',first_time,'Legacy price flags have no independently verified response receipt'
            FROM (SELECT vault_address,MIN(CAST({time} AS TIMESTAMP)) AS first_time FROM {table} GROUP BY vault_address) p
            WHERE NOT EXISTS (SELECT 1 FROM vault_permission_observations o WHERE o.observation_id=sha256('legacy-uncertainty:{table}:' || p.vault_address || CAST(p.first_time AS VARCHAR)))
        """)
        if evidence or parquet_evidence:
            with observe_database_operation("Restoring inferred backup permission snapshots"):
                restore_price_timestamp_permissions(connection, table)
        after = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if after != before + missing:
            msg = "Recovery row-count invariant failed"
            raise ValueError(msg)
        connection.execute("COMMIT")
        transaction = False
        connection.execute("CHECKPOINT")
        report["tables"][table]["rows_after"] = after
        report["legacy_evidence_rows"] = connection.execute("SELECT count(*) FROM hypercore_legacy_price_evidence").fetchone()[0]
        report["permission_records"] = connection.execute("SELECT count(*) FROM vault_permission_observations").fetchone()[0]
        return report
    finally:
        if transaction:
            connection.execute("ROLLBACK")
        connection.close()
