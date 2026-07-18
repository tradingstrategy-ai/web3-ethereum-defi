"""Backfill Circle USYC metadata and oracle-backed price history.

This targeted migration touches only the Ethereum USYC identifier. It preserves
unrelated vault metadata, reader-state entries and raw/cleaned Parquet history
rows. USYC price scans begin at the current official oracle deployment because
the prior token deployment has no compatible oracle history.

Run with::

    source .local-test.env && PROTOCOLS=usyc poetry run python scripts/backfill-tokenised-funds.py

Set ``DRY_RUN=true`` to validate the plan without writes. Optional overrides:
``START_BLOCK``, ``END_BLOCK``, ``FREQUENCY`` (``1d`` only), ``MAX_WORKERS``,
``VAULT_DB_PATH``, ``UNCLEANED_PRICE_DATABASE``, ``CLEANED_PRICE_DATABASE``
and ``READER_STATE_DATABASE``.
"""

# ruff: noqa: PLR0914

import logging
import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
from pathlib import Path
from typing import Literal, cast

from atomicwrites import atomic_write

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
from eth_defi.tokenised_fund.usyc.constants import USYC_CHAIN_ID, USYC_FIRST_SEEN_AT, USYC_FIRST_SEEN_AT_BLOCK, USYC_ORACLE_FIRST_SEEN_AT_BLOCK, USYC_TOKEN_ADDRESS
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    :param name: Variable name.
    :param default: Value used when the variable is absent.
    :return: Parsed boolean value.
    """
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_path_env(name: str, default: Path) -> Path:
    """Read an optional path override.

    :param name: Environment variable name.
    :param default: Default production location.
    :return: Selected expanded path.
    """
    return Path(os.environ[name]).expanduser() if os.environ.get(name) else default.expanduser()


def resolve_frequency() -> Literal["1h", "1d"]:
    """Require daily samples because USYC publishes one reconciled daily NAV.

    :return: Daily scanner frequency.
    :raise ValueError: If a non-daily frequency is requested.
    """
    frequency = os.environ.get("FREQUENCY", "1d")
    if frequency != "1d":
        raise ValueError(f"USYC backfill supports only FREQUENCY=1d, got: {frequency}")
    return cast(Literal["1h", "1d"], "1d")


def resolve_start_block() -> int:
    """Select the first block with the official current USYC oracle.

    :return: Explicit override or the oracle deployment block.
    """
    return int(os.environ["START_BLOCK"]) if os.environ.get("START_BLOCK") else USYC_ORACLE_FIRST_SEEN_AT_BLOCK


def create_usyc_detection() -> ERC4262VaultDetection:
    """Create the synthetic discovery record for USYC.

    :return: USYC classification record.
    """
    return ERC4262VaultDetection(
        chain=USYC_CHAIN_ID,
        address=USYC_TOKEN_ADDRESS,
        first_seen_at_block=USYC_FIRST_SEEN_AT_BLOCK,
        first_seen_at=USYC_FIRST_SEEN_AT,
        features={ERC4626Feature.usyc_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def read_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Read trusted local reader state when it exists.

    :param path: Shared reader-state pickle path.
    :return: Existing states or an empty mapping.
    """
    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Atomically persist merged reader state.

    :param path: Shared reader-state pickle path.
    :param states: Merged reader states.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as out:
        pickle.dump(states, out)


def main() -> None:
    """Run the USYC-only historical migration."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"), log_file=Path("logs/usyc-backfill-history.log"))
    dry_run = parse_bool_env("DRY_RUN")
    clean_prices = parse_bool_env("USYC_CLEAN_PRICES", default=True)
    frequency = resolve_frequency()
    start_block = resolve_start_block()
    json_rpc_url = read_json_rpc_url(USYC_CHAIN_ID)
    web3 = create_multi_provider_web3(json_rpc_url)
    end_block = int(os.environ["END_BLOCK"]) if os.environ.get("END_BLOCK") else web3.eth.block_number
    if end_block < start_block:
        raise ValueError(f"END_BLOCK {end_block} is before USYC oracle deployment {start_block}")

    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    uncleaned_price_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    cleaned_price_path = parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    reader_state_path = parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)
    token_cache = TokenDiskCache()
    detection = create_usyc_detection()
    vault = create_vault_instance(web3, USYC_TOKEN_ADDRESS, features=detection.features, token_cache=token_cache)
    if vault is None:
        message = "Could not create Circle USYC adapter"
        raise RuntimeError(message)
    vault.first_seen_at_block = start_block

    logger.info("USYC backfill: %s blocks %d..%d; dry-run=%s", USYC_TOKEN_ADDRESS, start_block, end_block, dry_run)
    logger.info("Vault DB: %s", vault_db_path)
    logger.info("Raw prices: %s", uncleaned_price_path)
    logger.info("Cleaned prices: %s", cleaned_price_path)
    if dry_run:
        return

    vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
    prior_chain_watermark = vault_db.last_scanned_block.get(USYC_CHAIN_ID)
    lead = PotentialVaultMatch(USYC_CHAIN_ID, USYC_TOKEN_ADDRESS, USYC_FIRST_SEEN_AT_BLOCK, USYC_FIRST_SEEN_AT)
    vault_db.update_leads_and_rows(
        chain_id=USYC_CHAIN_ID,
        last_scanned_block=end_block,
        leads={USYC_TOKEN_ADDRESS: lead},
        rows={VaultSpec(USYC_CHAIN_ID, USYC_TOKEN_ADDRESS): create_vault_scan_record(web3, detection, block_identifier=end_block, token_cache=token_cache)},
    )
    # This is a one-vault migration, not a complete Ethereum discovery scan.
    # Retain the chain watermark so unrelated discovery work is never skipped.
    if prior_chain_watermark is None:
        del vault_db.last_scanned_block[USYC_CHAIN_ID]
    else:
        vault_db.last_scanned_block[USYC_CHAIN_ID] = prior_chain_watermark
    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    vault_db.write(vault_db_path)

    prior_states = read_reader_states(reader_state_path)
    selected_id = VaultSpec(USYC_CHAIN_ID, USYC_TOKEN_ADDRESS)
    retained_states = {spec: state for spec, state in prior_states.items() if spec != selected_id}
    hypersync_config = configure_hypersync_from_env(web3)
    if hypersync_config.hypersync_client is None:
        message = "USYC price backfill requires a HyperSync client for cache-aware block timestamps"
        raise RuntimeError(message)
    result = scan_historical_prices_to_parquet(
        output_fname=uncleaned_price_path,
        web3=web3,
        web3factory=MultiProviderWeb3Factory(json_rpc_url, retries=5),
        vaults=[vault],
        start_block=start_block,
        end_block=end_block,
        max_workers=int(os.environ.get("MAX_WORKERS", "8")),
        chunk_size=32,
        token_cache=token_cache,
        frequency=frequency,
        reader_states=None,
        hypersync_client=hypersync_config.hypersync_client,
        vault_addresses={USYC_TOKEN_ADDRESS},
    )
    # The USYC reader is deliberately non-stateful because its daily oracle
    # updates do not need adaptive polling. Remove stale USYC state while
    # retaining every unrelated vault state unchanged.
    write_reader_states(reader_state_path, retained_states)
    if clean_prices:
        replace_cleaned_vault_histories({selected_id.as_string_id()}, vault_db_path=vault_db_path, raw_price_df_path=uncleaned_price_path, cleaned_price_df_path=cleaned_price_path, logger=print)
    token_cache.commit()
    logger.info("%s", pformat_scan_result(result))


if __name__ == "__main__":
    main()
