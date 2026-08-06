#!/usr/bin/env python3
"""Backfill reviewed NestVault routes into the shared vault pipeline.

Nest's public contract catalogue is the reviewed registry for this migration.
The ordinary scanner discovers only new events after its saved cursor, so this
tool seeds the already-deployed NestVault routes without restarting discovery
for unrelated protocols. It updates only the selected Nest leads, metadata,
reader states and price histories.

Usage:

.. code-block:: shell

    source .local-test.env && NETWORKS=avalanche poetry run python scripts/nest/backfill-history.py

Set ``DRY_RUN=true`` to inspect the selected route catalogue without writing.
Set ``NEST_SCAN_PRICES=false`` for a metadata-only repair. Historical price
rows are addressed by selected vault address, so other protocol histories are
preserved.
"""

import logging
import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from atomicwrites import atomic_write
from tabulate import tabulate

from eth_defi.chain import CHAIN_NAMES, get_chain_name
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.nest.offchain_metadata import NestVaultMetadata, fetch_nest_vaults
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import get_json_rpc_env, read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.research.wrangle_vault_prices import replace_cleaned_vault_histories
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    :param name:
        Environment variable name.

    :param default:
        Value used when the variable is unset.

    :return:
        Parsed boolean value.
    """
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_path_env(name: str, default: Path) -> Path:
    """Read an optional filesystem path environment variable.

    :param name:
        Environment variable name.

    :param default:
        Default path when the variable is unset.

    :return:
        Expanded path value.
    """
    return Path(os.environ.get(name, str(default))).expanduser()


def parse_frequency() -> Literal["1d", "1h"]:
    """Read the supported historical sampling frequency.

    :return:
        Daily or hourly historical sampling frequency.
    """
    frequency = os.environ.get("FREQUENCY", "1d")
    if frequency not in {"1d", "1h"}:
        message = f"Unsupported FREQUENCY: {frequency}"
        raise ValueError(message)
    return frequency


def get_chain_selector_names(chain_id: int) -> set[str]:
    """Return accepted ``NETWORKS`` selector values for an EVM chain.

    :param chain_id:
        EVM chain identifier.

    :return:
        Decimal and canonical-name selector forms.
    """
    chain_name = CHAIN_NAMES.get(chain_id)
    return {str(chain_id), chain_name.lower()} if chain_name else {str(chain_id)}


def iter_selected_vaults() -> Iterable[NestVaultMetadata]:
    """Yield Nest catalogue routes selected by ``NETWORKS``.

    A route must have a published deployment block because it is used as the
    earliest possible history point. Nest's own API is the address authority;
    this script never broadens selection through an event scan.

    :return:
        Selected, deployment-block-qualified NestVault entries.
    """
    networks = {value.strip().lower() for value in os.environ.get("NETWORKS", "").split(",") if value.strip()}
    if not networks:
        message = "NETWORKS is required, for example NETWORKS=avalanche or NETWORKS=43114"
        raise ValueError(message)

    for metadata in fetch_nest_vaults().values():
        if not get_chain_selector_names(metadata["chain_id"]) & networks:
            continue
        if metadata["start_block"] is None:
            logger.warning("Skipping Nest route without a published deployment block: %s", metadata["vault_address"])
            continue
        yield metadata


def create_detection(metadata: NestVaultMetadata) -> ERC4262VaultDetection:
    """Create persisted Nest classification data for one reviewed route.

    :param metadata:
        First-party route metadata.

    :return:
        Vault-classification row compatible with the common scanner.
    """
    return ERC4262VaultDetection(
        chain=metadata["chain_id"],
        address=metadata["vault_address"],
        first_seen_at_block=metadata["start_block"],
        first_seen_at=native_datetime_utc_now(),
        features={ERC4626Feature.nest_like, ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def create_lead(metadata: NestVaultMetadata) -> PotentialVaultMatch:
    """Create a synthetic first-party lead for one NestVault route.

    :param metadata:
        First-party route metadata.

    :return:
        Lead data accepted by :class:`VaultDatabase`.
    """
    return PotentialVaultMatch(
        chain=metadata["chain_id"],
        address=metadata["vault_address"],
        first_seen_at_block=metadata["start_block"],
        first_seen_at=native_datetime_utc_now(),
        deposit_count=0,
        withdrawal_count=0,
    )


def read_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Read persisted reader states when they exist.

    :param path:
        Pickle file path.

    :return:
        Existing reader states, or an empty mapping.
    """
    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Atomically persist updated reader states.

    :param path:
        Target pickle path.

    :param states:
        Reader state mapping returned by the historical scanner.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as out:
        pickle.dump(states, out)


def backfill_chain(
    chain_id: int,
    routes: list[NestVaultMetadata],
    *,
    vault_db: VaultDatabase,
    vault_db_path: Path,
    raw_price_path: Path,
    cleaned_price_path: Path,
    reader_state_path: Path,
    token_cache: TokenDiskCache,
    dry_run: bool,
    scan_prices: bool,
    frequency: Literal["1d", "1h"],
) -> dict[str, object]:
    """Upsert and optionally rescan exactly one chain's NestVault routes.

    :param chain_id:
        Nest route chain identifier.

    :param routes:
        First-party routes to process on this chain.

    :param vault_db:
        Open shared vault metadata database.

    :param vault_db_path:
        Metadata database destination.

    :param raw_price_path:
        Historical raw price parquet location.

    :param cleaned_price_path:
        Cleaned price parquet location.

    :param reader_state_path:
        Persistent historical reader-state pickle.

    :param token_cache:
        Shared token cache used by scanner adapters.

    :param dry_run:
        Whether database and price writes are disabled.

    :param scan_prices:
        Whether selected historical price rows are rebuilt.

    :return:
        Summary suitable for tabular output.
    """
    json_rpc_url = read_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(json_rpc_url)
    end_block = web3.eth.block_number
    leads = {route["vault_address"]: create_lead(route) for route in routes}
    rows = {
        VaultSpec(chain_id, route["vault_address"]): create_vault_scan_record(
            web3,
            detection=create_detection(route),
            block_identifier=end_block,
            token_cache=token_cache,
        )
        for route in routes
    }

    if not dry_run:
        vault_db_path.parent.mkdir(parents=True, exist_ok=True)
        vault_db.update_leads_and_rows(chain_id=chain_id, last_scanned_block=end_block, leads=leads, rows=rows)
        vault_db.write(vault_db_path)

    scan_summary = "disabled"
    if scan_prices:
        vault_addresses = {route["vault_address"].lower() for route in routes}
        if dry_run:
            scan_summary = "dry-run"
        else:
            reader_states = read_reader_states(reader_state_path)
            reader_states = {spec: state for spec, state in reader_states.items() if spec.vault_address.lower() not in vault_addresses}
            vaults = []
            for route in routes:
                vault = create_vault_instance(
                    web3,
                    route["vault_address"],
                    features={ERC4626Feature.nest_like, ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like},
                    token_cache=token_cache,
                )
                assert vault is not None, f"Could not initialise NestVault {route['vault_address']}"
                vault.first_seen_at_block = route["start_block"]
                vaults.append(vault)

            scan_result = scan_historical_prices_to_parquet(
                output_fname=raw_price_path,
                web3=web3,
                web3factory=MultiProviderWeb3Factory(json_rpc_url, retries=5),
                vaults=vaults,
                start_block=min(route["start_block"] for route in routes),
                end_block=end_block,
                max_workers=int(os.environ.get("MAX_WORKERS", "8")),
                chunk_size=32,
                token_cache=token_cache,
                frequency=frequency,
                reader_states=reader_states,
                hypersync_client=configure_hypersync_from_env(web3).hypersync_client,
                vault_addresses=vault_addresses,
            )
            write_reader_states(reader_state_path, scan_result["reader_states"])
            cleaned_rows = replace_cleaned_vault_histories(
                {VaultSpec(chain_id, route["vault_address"]).as_string_id() for route in routes},
                vault_db_path=vault_db_path,
                raw_price_df_path=raw_price_path,
                cleaned_price_df_path=cleaned_price_path,
                logger=logger.info,
            )
            scan_summary = f"{pformat_scan_result(scan_result)}; cleaned_rows={cleaned_rows:,}"

    return {
        "chain": get_chain_name(chain_id),
        "chain_id": chain_id,
        "rpc": get_json_rpc_env(chain_id),
        "routes": len(routes),
        "scan": scan_summary,
    }


def main() -> None:
    """Run the targeted Nest historical lead migration."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"), log_file=Path("logs/nest-backfill-history.log"))
    routes_by_chain: dict[int, list[NestVaultMetadata]] = {}
    for route in iter_selected_vaults():
        routes_by_chain.setdefault(route["chain_id"], []).append(route)
    if not routes_by_chain:
        message = "Nest public catalogue returned no routes for NETWORKS"
        raise RuntimeError(message)

    dry_run = parse_bool_env("DRY_RUN")
    scan_prices = parse_bool_env("NEST_SCAN_PRICES", default=True)
    frequency = parse_frequency()
    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    raw_price_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    cleaned_price_path = parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    reader_state_path = parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)
    print(tabulate([{"chain": get_chain_name(chain_id), "chain_id": chain_id, "routes": len(routes), "start_block": min(route["start_block"] for route in routes)} for chain_id, routes in sorted(routes_by_chain.items())], headers="keys", tablefmt="github"))

    if dry_run:
        print("Dry run: no vault database, reader state or price data will be read or changed")
        return

    vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
    token_cache = TokenDiskCache()
    summaries = [
        backfill_chain(
            chain_id,
            routes,
            vault_db=vault_db,
            vault_db_path=vault_db_path,
            raw_price_path=raw_price_path,
            cleaned_price_path=cleaned_price_path,
            reader_state_path=reader_state_path,
            token_cache=token_cache,
            dry_run=dry_run,
            scan_prices=scan_prices,
            frequency=frequency,
        )
        for chain_id, routes in sorted(routes_by_chain.items())
    ]
    if not dry_run:
        token_cache.commit()
    print(tabulate(summaries, headers="keys", tablefmt="github"))


if __name__ == "__main__":
    main()
