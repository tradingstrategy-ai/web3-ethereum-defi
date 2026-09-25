"""Persist public Derive v3 vault observations in DuckDB.

The collector keeps testnet and mainnet in separate databases. It uses
Derive's public `vault listing
<https://docs.derive.xyz/api-reference/vault-shareholders/publicget_vaults>`__
and `performance history
<https://docs.derive.xyz/api-reference/vault-shareholders/publicget_vault_performance_history>`__.
"""

import json
import logging
from pathlib import Path

import duckdb
import pandas as pd
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.derive.tags import get_strategy_tags
from eth_defi.derive.v3_constants import DERIVE_V3_CHAIN_ID, make_derive_v3_vault_address
from eth_defi.derive.v3_vaults import DeriveV3Vault, DeriveV3VaultClient, DeriveV3VaultPrice
from eth_defi.perp_dex.metrics import PerpVaultIdentity, SourcePositionDataStatus, create_unavailable_perp_vault_observation_bundle
from eth_defi.perp_dex.storage import initialise_perp_vault_observation_schema, write_perp_vault_observation_bundle
from eth_defi.vault.strategy_tag import StrategyTag

logger = logging.getLogger(__name__)


class DeriveV3VaultDatabase:
    """File-backed public vault metadata and daily performance observations.

    Metadata is keyed by ``(network, subaccount_id)``; prices also include
    ``timestamp``. Amounts are stored as decimal strings. Ingestion handles
    duplicate keys without unique constraints because large ART indexes have
    caused native DuckDB crashes under Python 3.14 in this repository.

    :param path: Destination DuckDB file.
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(path))
        self.con.execute("SET wal_autocheckpoint = '1TB'")
        initialise_perp_vault_observation_schema(self.con)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS vault_metadata (
                network VARCHAR NOT NULL,
                subaccount_id BIGINT NOT NULL,
                name VARCHAR NOT NULL,
                description VARCHAR NOT NULL,
                curator VARCHAR NOT NULL,
                deposit_spot_asset VARCHAR NOT NULL,
                nav_usd VARCHAR,
                share_price_usd VARCHAR,
                total_shares VARCHAR NOT NULL,
                management_fee_bps INTEGER NOT NULL,
                performance_fee_bps INTEGER NOT NULL,
                cooldown_sec BIGINT NOT NULL,
                whitelist_only BOOLEAN NOT NULL,
                closed BOOLEAN NOT NULL,
                observed_at TIMESTAMP NOT NULL,
                deposit_symbol VARCHAR,
                deposit_decimals INTEGER,
                raw_metadata_json VARCHAR
            )
        """)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS vault_prices (
                network VARCHAR NOT NULL,
                subaccount_id BIGINT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                share_price VARCHAR NOT NULL,
                nav_usd VARCHAR,
                total_shares VARCHAR NOT NULL,
                written_at TIMESTAMP NOT NULL
            )
        """)

    def store_vault(self, network: str, vault: DeriveV3Vault, prices: list[DeriveV3VaultPrice]) -> None:
        """Replace one vault's metadata and fetched history transactionally.

        A fetched point replaces the stored point at the same timestamp.
        All other timestamps survive, including gaps within the fetched range.
        ``observed_at`` retains the first scan time when metadata is refreshed.

        :param network: Source deployment name.
        :param vault: Fresh public vault metadata.
        :param prices: Fetched performance points.
        :return: ``None``.
        """
        observed_at = native_datetime_utc_now()
        self.con.execute("BEGIN TRANSACTION")
        try:
            existing = self.con.execute("SELECT MIN(observed_at) FROM vault_metadata WHERE network = ? AND subaccount_id = ?", [network, vault.subaccount_id]).fetchone()[0]
            first_seen_at = existing or observed_at
            self.con.execute("DELETE FROM vault_metadata WHERE network = ? AND subaccount_id = ?", [network, vault.subaccount_id])
            self.con.execute(
                """INSERT INTO vault_metadata (
                    network, subaccount_id, name, description, curator,
                    deposit_spot_asset, nav_usd, share_price_usd, total_shares,
                    management_fee_bps, performance_fee_bps, cooldown_sec,
                    whitelist_only, closed, observed_at, deposit_symbol,
                    deposit_decimals, raw_metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [network, vault.subaccount_id, vault.name, vault.description, vault.curator, vault.deposit_spot_asset, str(vault.nav_usd) if vault.nav_usd is not None else None, str(vault.share_price_usd) if vault.share_price_usd is not None else None, str(vault.total_shares), vault.management_fee_bps, vault.performance_fee_bps, vault.cooldown_sec, vault.whitelist_only, vault.closed, first_seen_at, vault.deposit_symbol, vault.deposit_decimals, json.dumps(vault.raw_metadata) if vault.raw_metadata is not None else None],
            )
            if prices:
                unique_prices = list({point.timestamp: point for point in prices}.values())
                timestamps = [point.timestamp for point in unique_prices]
                self.con.execute("DELETE FROM vault_prices WHERE network = ? AND subaccount_id = ? AND timestamp = ANY(?)", [network, vault.subaccount_id, timestamps])
                self.con.executemany(
                    "INSERT INTO vault_prices VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(network, vault.subaccount_id, point.timestamp, str(point.share_price), str(point.nav_usd) if point.nav_usd is not None else None, str(point.total_shares), observed_at) for point in unique_prices],
                )
            self.con.execute("COMMIT")
        except (duckdb.Error, ValueError, TypeError):
            self.con.execute("ROLLBACK")
            raise

        # Only mainnet vaults tagged as perpetual strategies belong in the
        # perp DEX tables. Store public USD equity with positions unavailable.
        address = make_derive_v3_vault_address(vault.subaccount_id)
        tags = get_strategy_tags(address)
        if network == "mainnet" and tags is not None and StrategyTag.perpetual_futures in tags:
            bundle = create_unavailable_perp_vault_observation_bundle(
                identity=PerpVaultIdentity(
                    protocol_slug="derive",
                    deployment_slug="mainnet",
                    vault_id=str(vault.subaccount_id),
                    dataset_chain_id=DERIVE_V3_CHAIN_ID,
                    dataset_address=address,
                ),
                observed_at=observed_at,
                total_equity=vault.nav_usd,
                quote_asset="USD",
                status=SourcePositionDataStatus.authentication_required,
                reason="Derive v3 positions require authenticated access to the curator's vault subaccount",
                source_endpoint="public/get_vaults",
            )
            write_perp_vault_observation_bundle(
                self.con,
                bundle,
                {"subaccount_id": vault.subaccount_id, "nav_usd": str(vault.nav_usd) if vault.nav_usd is not None else None},
            )

    def get_vault_metadata(self, network: str) -> pd.DataFrame:
        """Read current vault metadata for one deployment.

        Includes previously stored vaults that no longer appear in the API
        listing. ``first_price_at`` comes from the earliest stored price.

        :param network: ``testnet`` or ``mainnet``.
        :return: DataFrame with the ``vault_metadata`` columns and nullable
            ``first_price_at`` as a naive UTC timestamp. One row per vault.
        """
        return self.con.execute(
            """SELECT metadata.*, prices.first_price_at
            FROM vault_metadata AS metadata
            LEFT JOIN (
                SELECT network, subaccount_id, MIN(timestamp) AS first_price_at
                FROM vault_prices GROUP BY network, subaccount_id
            ) AS prices USING (network, subaccount_id)
            WHERE metadata.network = ? ORDER BY metadata.subaccount_id""",
            [network],
        ).df()

    def get_vault_prices(self, network: str) -> pd.DataFrame:
        """Read sampled vault prices for one deployment.

        Rows are ordered by subaccount ID, then observation timestamp.

        :param network: ``testnet`` or ``mainnet``.
        :return: DataFrame with integer ``subaccount_id``, naive UTC
            ``timestamp`` and ``written_at``, string ``network``, and decimal
            strings ``share_price``, ``nav_usd`` (nullable) and ``total_shares``.
        """
        return self.con.execute("SELECT * FROM vault_prices WHERE network = ? ORDER BY subaccount_id, timestamp", [network]).df()

    def close(self) -> None:
        """Close the file-backed database connection.

        :return: ``None``.
        """
        if self.con is not None:
            self.con.close()
            self.con = None


def scan_derive_v3_vaults(client: DeriveV3VaultClient, db_path: Path, vault_ids: set[int] | None = None) -> tuple[int, int]:
    """Fetch all public v3 vaults and persist their daily price history.

    Fetches the full available daily history on every run. Each vault commits
    separately; a fetch failure stops the scan and leaves earlier commits
    intact. An empty listing succeeds without deleting stored observations.

    :param client: Public API client with a selected deployment.
    :param db_path: Destination DuckDB file.
    :param vault_ids: Optional native subaccount ID filter.
    :return: Vaults processed and performance points fetched this run. These
        counts include replacements of previously stored records.
    """
    vaults = list(client.fetch_vaults())
    if vault_ids is not None:
        vaults = [vault for vault in vaults if vault.subaccount_id in vault_ids]
    logger.info("Discovered %d Derive v3 %s vaults", len(vaults), client.network)
    if vaults:
        spot_assets = client.fetch_spot_assets()
        for vault in vaults:
            asset = spot_assets.get(vault.deposit_spot_asset.lower())
            if asset is not None:
                vault.deposit_symbol, vault.deposit_decimals = asset
    db = DeriveV3VaultDatabase(db_path)
    price_count = 0
    try:
        for vault in tqdm(vaults, desc=f"Derive v3 {client.network} vault history"):
            prices = list(client.fetch_vault_performance(vault.subaccount_id))
            if not prices:
                logger.warning("Derive v3 %s vault %d has no public performance points", client.network, vault.subaccount_id)
            db.store_vault(client.network, vault, prices)
            price_count += len(prices)
            logger.info("Stored Derive v3 %s vault %d with %d prices", client.network, vault.subaccount_id, len(prices))
    finally:
        db.close()
    logger.info("Derive v3 %s scan complete: %d vaults, %d prices", client.network, len(vaults), price_count)
    return len(vaults), price_count
