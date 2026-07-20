"""Backfill OpenEden TBILL metadata and oracle-backed price history.

TBILL uses an issuer-operated Chainlink-compatible NAV oracle.  The historical
reader calls that oracle at sampled blocks; HyperSync supplies cache-aware block
timestamps to the common scanner, so this migration never performs JSON-RPC log
discovery.  It replaces only TBILL rows in the shared price files.
"""

import os
from pathlib import Path

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.research.wrangle_vault_prices import replace_cleaned_vault_histories
from eth_defi.token import TokenDiskCache
from eth_defi.tokenised_fund.openeden.constants import OPENEDEN_CHAIN_ID, OPENEDEN_TBILL_ADDRESS, OPENEDEN_TBILL_FIRST_SEEN_AT, OPENEDEN_TBILL_FIRST_SEEN_AT_BLOCK, OPENEDEN_TBILL_ORACLE_FIRST_SEEN_AT_BLOCK
from eth_defi.tokenised_fund.usyc.backfill import parse_bool_env, parse_path_env, read_reader_states, write_reader_states
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase


def create_openeden_detection() -> ERC4262VaultDetection:
    """Create the address-scoped TBILL detection record.

    :return: Reviewed OpenEden TBILL detection.
    """

    return ERC4262VaultDetection(
        chain=OPENEDEN_CHAIN_ID,
        address=OPENEDEN_TBILL_ADDRESS,
        first_seen_at_block=OPENEDEN_TBILL_FIRST_SEEN_AT_BLOCK,
        first_seen_at=OPENEDEN_TBILL_FIRST_SEEN_AT,
        features={ERC4626Feature.openeden_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def main() -> None:
    """Backfill OpenEden TBILL without disturbing unrelated vault data.

    :return: ``None``.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_bool_env("DRY_RUN")
    frequency = os.environ.get("FREQUENCY", "1d")
    if frequency != "1d":
        raise ValueError(f"OpenEden TBILL backfill supports only FREQUENCY=1d, got: {frequency}")
    start_block = int(os.environ.get("START_BLOCK", OPENEDEN_TBILL_ORACLE_FIRST_SEEN_AT_BLOCK))
    web3_url = read_json_rpc_url(OPENEDEN_CHAIN_ID)
    web3 = create_multi_provider_web3(web3_url)
    end_block = int(os.environ.get("END_BLOCK", web3.eth.block_number))
    if end_block < start_block:
        raise ValueError(f"END_BLOCK {end_block} is before OpenEden oracle deployment {start_block}")

    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    raw_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    cleaned_path = parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    state_path = parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)
    detection = create_openeden_detection()
    token_cache = TokenDiskCache()
    vault = create_vault_instance(web3, OPENEDEN_TBILL_ADDRESS, features=detection.features, token_cache=token_cache)
    if vault is None:
        raise RuntimeError("Could not create OpenEden TBILL adapter")
    vault.first_seen_at_block = start_block
    if dry_run:
        return

    vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
    previous_cursor = vault_db.last_scanned_block.get(OPENEDEN_CHAIN_ID)
    spec = VaultSpec(OPENEDEN_CHAIN_ID, OPENEDEN_TBILL_ADDRESS)
    vault_db.update_leads_and_rows(
        chain_id=OPENEDEN_CHAIN_ID,
        last_scanned_block=end_block,
        leads={OPENEDEN_TBILL_ADDRESS: PotentialVaultMatch(OPENEDEN_CHAIN_ID, OPENEDEN_TBILL_ADDRESS, OPENEDEN_TBILL_FIRST_SEEN_AT_BLOCK, OPENEDEN_TBILL_FIRST_SEEN_AT)},
        rows={spec: create_vault_scan_record(web3, detection, block_identifier=end_block, token_cache=token_cache)},
    )
    if previous_cursor is None:
        vault_db.last_scanned_block.pop(OPENEDEN_CHAIN_ID, None)
    else:
        vault_db.last_scanned_block[OPENEDEN_CHAIN_ID] = previous_cursor
    vault_db.write(vault_db_path)

    hypersync_client = configure_hypersync_from_env(web3).hypersync_client
    if hypersync_client is None:
        raise RuntimeError("OpenEden TBILL price backfill requires HyperSync for block timestamps")
    prior_states = read_reader_states(state_path)
    retained_states = {existing_spec: state for existing_spec, state in prior_states.items() if existing_spec != spec}
    scan_historical_prices_to_parquet(
        output_fname=raw_path,
        web3=web3,
        web3factory=MultiProviderWeb3Factory(web3_url, retries=5),
        vaults=[vault],
        start_block=start_block,
        end_block=end_block,
        max_workers=int(os.environ.get("MAX_WORKERS", "8")),
        chunk_size=32,
        token_cache=token_cache,
        frequency="1d",
        reader_states=None,
        hypersync_client=hypersync_client,
        vault_addresses={OPENEDEN_TBILL_ADDRESS},
    )
    write_reader_states(state_path, retained_states)
    replace_cleaned_vault_histories({spec.as_string_id()}, vault_db_path=vault_db_path, raw_price_df_path=raw_path, cleaned_price_df_path=cleaned_path)
    token_cache.commit()
