"""Backfill only reviewed Libeara CMTAT fund histories.

This migration preserves all unrelated vault metadata, discovery cursors,
reader states and raw/cleaned Parquet histories.  It only replaces CUMIU and
BELIF rows after their known deployment blocks.  It defaults to ``DRY_RUN``;
set ``DRY_RUN=false`` only after reviewing the displayed plan.
"""

import logging
import os
import pickle  # noqa: S403 - reader state is trusted, local operator data.
from pathlib import Path

from atomicwrites import atomic_write
from tabulate import tabulate

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.research.wrangle_vault_prices import replace_cleaned_vault_histories
from eth_defi.token import TokenDiskCache
from eth_defi.tokenised_fund.libeara.backfill_ultra import main as backfill_ultra
from eth_defi.tokenised_fund.libeara.constants import ETHEREUM_CHAIN_ID, LIBEARA_PRODUCTS, LibearaProduct
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def _bool(name: str, *, default: bool) -> bool:
    """Read an environment boolean.

    :param name: Variable name.
    :param default: Value when unset.
    :return: Parsed value.
    """
    value = os.environ.get(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


def _path(name: str, default: Path) -> Path:
    """Read a database path without changing its default.

    :param name: Environment variable name.
    :param default: Production default path.
    :return: Selected path.
    """
    return Path(os.environ[name]).expanduser() if name in os.environ else default


def _detection(product: LibearaProduct) -> ERC4262VaultDetection:
    """Create a scanner detection for a reviewed CMTAT token.

    :param product: Reviewed product registry record.
    :return: Hardcoded feature detection.
    """
    return ERC4262VaultDetection(chain=product.chain_id, address=product.token, first_seen_at_block=product.first_seen_at_block, first_seen_at=product.first_seen_at, features={ERC4626Feature.libeara_like}, updated_at=product.first_seen_at, deposit_count=0, redeem_count=0)


def _lead(product: LibearaProduct) -> PotentialVaultMatch:
    """Create a hardcoded non-ERC-4626 discovery lead.

    :param product: Reviewed product registry record.
    :return: Stable discovery lead.
    """
    return PotentialVaultMatch(chain=product.chain_id, address=product.token, first_seen_at_block=product.first_seen_at_block, first_seen_at=product.first_seen_at)


def _read_states(path: Path) -> dict:
    """Read all existing reader states unchanged.

    :param path: Reader-state pickle path.
    :return: Existing state mapping, or an empty mapping.
    """
    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local operator data.


def _write_states(path: Path, states: dict) -> None:
    """Atomically write the complete state mapping.

    :param path: Reader-state pickle path.
    :param states: Complete state mapping returned by the scanner.
    :return: None.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(path, mode="wb", overwrite=True) as out:
        pickle.dump(states, out)


def upsert_libeara_metadata_preserving_discovery_cursor(vault_db: VaultDatabase, leads: dict, rows: dict) -> None:
    """Upsert reviewed rows without changing the Ethereum discovery cursor.

    ``VaultDatabase.update_leads_and_rows`` is intentionally unsuitable here:
    it advances the chain-wide discovery watermark, which would skip unrelated
    vault discovery after a targeted repair.  Updating these known addresses
    directly preserves both an existing cursor and an absent cursor.

    :param vault_db: Existing vault metadata database.
    :param leads: CUMIU and BELIF hardcoded leads keyed by token address.
    :param rows: Fresh CUMIU and BELIF scan rows keyed by :class:`VaultSpec`.
    :return: None.
    """
    vault_db.leads.update({VaultSpec(ETHEREUM_CHAIN_ID, address): lead for address, lead in leads.items()})
    vault_db._merge_rows(rows)


def backfill_cmtat() -> None:  # noqa: PLR0914
    """Run the address-scoped metadata and price-history migration.

    :return: None.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    products = tuple(product for product in LIBEARA_PRODUCTS.values() if product.chain_id == ETHEREUM_CHAIN_ID)
    dry_run = _bool("DRY_RUN", default=True)
    frequency = os.environ.get("FREQUENCY", "1d")
    if frequency not in {"1h", "1d"}:
        message = "FREQUENCY must be 1h or 1d"
        raise ValueError(message)
    vault_db_path = _path("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    raw_path = _path("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    clean_path = _path("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    state_path = _path("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)
    web3_url = read_json_rpc_url(ETHEREUM_CHAIN_ID)
    web3 = create_multi_provider_web3(web3_url)
    end_block = int(os.environ.get("END_BLOCK", web3.eth.block_number))
    start_block = int(os.environ.get("START_BLOCK", min(p.first_seen_at_block for p in products)))
    plan = [{"symbol": p.symbol, "token": p.token, "first_block": p.first_seen_at_block} for p in products]
    logger.info("Libeara CMTAT backfill plan; blocks %s-%s; dry_run=%s\n%s", start_block, end_block, dry_run, tabulate(plan, headers="keys", tablefmt="github"))
    if dry_run:
        return
    cache = TokenDiskCache()
    vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
    leads = {p.token: _lead(p) for p in products}
    rows = {VaultSpec(p.chain_id, p.token): create_vault_scan_record(web3, _detection(p), block_identifier=end_block, token_cache=cache) for p in products}
    upsert_libeara_metadata_preserving_discovery_cursor(vault_db, leads, rows)
    vault_db.write(vault_db_path)
    addresses = {p.token.lower() for p in products}
    target_specs = {VaultSpec(p.chain_id, p.token) for p in products}
    states = {spec: state for spec, state in _read_states(state_path).items() if spec not in target_specs}
    vaults = []
    for product in products:
        vault = create_vault_instance(web3, product.token, features={ERC4626Feature.libeara_like}, token_cache=cache)
        if vault is None:
            raise RuntimeError(f"Could not create Libeara adapter for {product.token}")
        vault.first_seen_at_block = product.first_seen_at_block
        vaults.append(vault)
    hypersync = configure_hypersync_from_env(web3)
    if hypersync.hypersync_client is None:
        message = "Libeara history backfill requires HyperSync on Ethereum"
        raise RuntimeError(message)
    result = scan_historical_prices_to_parquet(output_fname=raw_path, web3=web3, web3factory=MultiProviderWeb3Factory(web3_url, retries=5), vaults=vaults, start_block=start_block, end_block=end_block, max_workers=int(os.environ.get("MAX_WORKERS", "8")), chunk_size=32, token_cache=cache, frequency=frequency, reader_states=states, hypersync_client=hypersync.hypersync_client, vault_addresses=addresses)
    _write_states(state_path, result["reader_states"])
    replace_cleaned_vault_histories({VaultSpec(p.chain_id, p.token).as_string_id() for p in products}, vault_db_path=vault_db_path, raw_price_df_path=raw_path, cleaned_price_df_path=clean_path)
    cache.commit()


def main() -> None:
    """Backfill all reviewed Libeara products.

    CUMIU and BELIF use their reviewed CMTAT NAV history. ULTRA is registered
    separately because no verified public NAV/share source is available.

    :return: None.
    """

    backfill_cmtat()
    backfill_ultra()


if __name__ == "__main__":
    main()
