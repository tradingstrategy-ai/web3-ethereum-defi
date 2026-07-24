"""Refresh mutable ERC-20/ERC-4626 names and symbols in the vault database.

ERC-20 and ERC-4626 token contracts are permitted to change the values returned
by ``name()`` and ``symbol()``.  The vault metadata database persists both
values, so this migration compares them with the current onchain values and
repairs only changed rows.  It does not rescan vault detection metadata, prices,
reader state, or any other vault fields.

Each EVM chain is queried independently.  Calls to ``name()`` and ``symbol()``
are packed through Multicall3, rather than making one RPC request per accessor.
Native vault sources and synthetic non-EVM chain ids are excluded.

Usage:

.. code-block:: shell

    # Inspect changes without modifying the metadata pickle (the default)
    source .local-test.env && poetry run python scripts/erc-4626/migrate-vault-token-metadata.py

    # Persist any changed names and symbols
    source .local-test.env && DRY_RUN=false \\
        poetry run python scripts/erc-4626/migrate-vault-token-metadata.py

Environment variables:

- ``VAULT_DB_PATH``: Optional path to ``vault-metadata-db.pickle``.
- ``DRY_RUN``: Set to ``false`` to write changes. Defaults to ``true``.
- ``MAX_WORKERS``: Multicall worker threads per chain. Defaults to ``8``.
- ``JSON_RPC_<CHAIN>``: RPC URL for every EVM chain present in the database.
- ``LOG_LEVEL``: Optional console log level. Defaults to ``info``.
"""

import logging
import os
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate
from web3 import Web3

from eth_defi.chain import EVM_BLOCK_TIMES, get_chain_name
from eth_defi.event_reader.conversion import convert_solidity_bytes_to_string
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult, read_multicall_chunked
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.risk import BROKEN_VAULT_CONTRACTS
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)

#: Maximum accepted length for an ERC-20 metadata accessor value.
MAX_METADATA_VALUE_LENGTH = 256

#: ERC-20 accessor selectors encoded once for every vault multicall.
NAME_SELECTOR = Web3.keccak(text="name()")[0:4]
SYMBOL_SELECTOR = Web3.keccak(text="symbol()")[0:4]


@dataclass(slots=True, frozen=True)
class VaultTokenMetadataUpdate:
    """Describe a changed persisted ERC-20/ERC-4626 token metadata record."""

    #: Chain and vault address identifying the metadata row.
    spec: VaultSpec

    #: Persisted vault protocol name.
    protocol: str

    #: Name currently stored in the metadata pickle.
    old_name: str | None

    #: Symbol currently stored in the metadata pickle.
    old_symbol: str | None

    #: Current value returned by the vault's ``name()`` accessor.
    new_name: str

    #: Current value returned by the vault's ``symbol()`` accessor.
    new_symbol: str


@dataclass(slots=True, frozen=True)
class VaultTokenMetadataMigrationResult:
    """Summarise a vault token metadata migration run."""

    #: Number of EVM vault rows sent to Multicall3.
    inspected_rows: int

    #: Number of rows that were or would be updated.
    updated_rows: int

    #: Number of rows for which one or both accessors could not be read.
    skipped_rows: int

    #: Number of native-source or synthetic-chain rows excluded from the run.
    skipped_non_evm_rows: int

    #: Detailed changed rows in database iteration order.
    updates: list[VaultTokenMetadataUpdate]


def create_metadata_calls(specs: Iterable[VaultSpec]) -> Iterable[EncodedCall]:
    """Create Multicall3 payloads for vault ``name()`` and ``symbol()`` accessors.

    :param specs:
        Vaults on one EVM chain.
    :return:
        Two encoded calls for each vault, first ``name()`` and then ``symbol()``.
    """

    for spec in specs:
        for function, signature in (("name", NAME_SELECTOR), ("symbol", SYMBOL_SELECTOR)):
            yield EncodedCall.from_keccak_signature(
                address=spec.vault_address,
                signature=signature,
                function=function,
                data=b"",
                extra_data={"spec": spec},
            )


def decode_metadata_value(result: EncodedCallResult | None) -> str | None:
    """Decode a successful ERC-20 string accessor result.

    An unsuccessful, malformed, or empty accessor result is represented as
    ``None`` so the migration never overwrites known metadata with an empty
    value.

    :param result:
        A result emitted by the Multicall3 reader.
    :return:
        Sanitised token metadata, or ``None`` when it could not be read.
    """

    if result is None or not result.success or not result.result:
        return None

    value = convert_solidity_bytes_to_string(result.result, MAX_METADATA_VALUE_LENGTH)
    return value or None


def read_chain_metadata(
    chain_id: int,
    specs: list[VaultSpec],
    *,
    max_workers: int,
) -> tuple[dict[VaultSpec, dict[str, str | None]], int]:
    """Read vault names and symbols from one EVM chain using Multicall3.

    The parent connection verifies the configured RPC chain id before a factory
    creates worker-local connections.  This retains provider-chain safety while
    avoiding a chain-id request from every worker.

    :param chain_id:
        Expected EVM chain id.
    :param specs:
        Vault rows on ``chain_id``.
    :param max_workers:
        Number of worker threads used to submit batched multicalls.
    :return:
        Mapping of vault specs to name/symbol values and number of incomplete rows.
    """

    rpc_url = read_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(rpc_url, expected_chain_id=chain_id)
    connected_chain_id = web3.eth.chain_id
    if connected_chain_id != chain_id:
        raise ValueError(f"Configured RPC reports chain {connected_chain_id}, expected {chain_id}")

    factory = MultiProviderWeb3Factory(
        rpc_url,
        hint="migrate_vault_token_metadata",
        skip_verification=True,
        expected_chain_id=chain_id,
    )
    multicall_safe_specs = [spec for spec in specs if spec.vault_address.lower() not in BROKEN_VAULT_CONTRACTS]
    blacklisted_count = len(specs) - len(multicall_safe_specs)
    if blacklisted_count:
        logger.warning(
            "Skipping %d known Multicall-unsafe vaults on %s",
            blacklisted_count,
            get_chain_name(chain_id),
        )

    calls = list(create_metadata_calls(multicall_safe_specs))
    results_by_spec: dict[VaultSpec, dict[str, EncodedCallResult]] = defaultdict(dict)

    if calls:
        for result in read_multicall_chunked(
            chain_id=chain_id,
            web3factory=factory,
            calls=calls,
            block_identifier="latest",
            max_workers=max_workers,
            progress_bar_desc=f"Reading {len(multicall_safe_specs):,} vault names and symbols on {get_chain_name(chain_id)}",
            timestamped_results=False,
            backend="threading",
        ):
            spec = result.call.extra_data["spec"]
            results_by_spec[spec][result.call.func_name] = result

    values: dict[VaultSpec, dict[str, str | None]] = {}
    incomplete_rows = 0
    for spec in specs:
        accessor_results = results_by_spec.get(spec, {})
        name = decode_metadata_value(accessor_results.get("name"))
        symbol = decode_metadata_value(accessor_results.get("symbol"))
        values[spec] = {"name": name, "symbol": symbol}
        if name is None or symbol is None:
            incomplete_rows += 1

    return values, incomplete_rows


def migrate_vault_token_metadata(
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    *,
    dry_run: bool,
    max_workers: int = 8,
) -> VaultTokenMetadataMigrationResult:
    """Refresh stale ERC-20/ERC-4626 names and symbols across all EVM vault rows.

    All rows are compared before any write occurs.  This makes repeated runs
    idempotent and ensures failed name or symbol calls leave their existing
    metadata untouched.

    :param vault_db_path:
        Path to the persisted vault metadata pickle.
    :param dry_run:
        Report changes without modifying the pickle when ``True``.
    :param max_workers:
        Number of Multicall3 worker threads per EVM chain.
    :return:
        Counts and details of stale token metadata rows.
    """

    if max_workers < 1:
        raise ValueError(f"MAX_WORKERS must be at least one, got {max_workers}")

    vault_db = VaultDatabase.read(vault_db_path)
    specs_by_chain: dict[int, list[VaultSpec]] = defaultdict(list)
    skipped_non_evm_rows = 0
    for spec in vault_db.rows:
        if spec.chain_id in EVM_BLOCK_TIMES:
            specs_by_chain[spec.chain_id].append(spec)
        else:
            skipped_non_evm_rows += 1

    updates: list[VaultTokenMetadataUpdate] = []
    skipped_rows = 0
    for chain_id, specs in sorted(specs_by_chain.items()):
        logger.info("Reading name() and symbol() for %d vaults on %s", len(specs), get_chain_name(chain_id))
        values_by_spec, incomplete_rows = read_chain_metadata(chain_id, specs, max_workers=max_workers)
        skipped_rows += incomplete_rows

        for spec in specs:
            values = values_by_spec[spec]
            new_name = values["name"]
            new_symbol = values["symbol"]
            if new_name is None or new_symbol is None:
                continue

            row = vault_db.rows[spec]
            old_name = row.get("Name")
            old_symbol = row.get("Symbol")
            if old_name == new_name and old_symbol == new_symbol:
                continue

            updates.append(
                VaultTokenMetadataUpdate(
                    spec=spec,
                    protocol=row.get("Protocol", ""),
                    old_name=old_name,
                    old_symbol=old_symbol,
                    new_name=new_name,
                    new_symbol=new_symbol,
                )
            )

    if not dry_run:
        for update in updates:
            row = vault_db.rows[update.spec]
            row["Name"] = update.new_name
            row["Symbol"] = update.new_symbol
        if updates:
            vault_db.write(vault_db_path)
            logger.info("Wrote %d vault token metadata updates to %s", len(updates), vault_db_path)

    return VaultTokenMetadataMigrationResult(
        inspected_rows=sum(len(specs) for specs in specs_by_chain.values()),
        updated_rows=len(updates),
        skipped_rows=skipped_rows,
        skipped_non_evm_rows=skipped_non_evm_rows,
        updates=updates,
    )


def main() -> None:
    """Run the vault token metadata migration from environment configuration.

    :return:
        ``None``. Raises when an EVM chain has unusable RPC configuration.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    dry_run = os.environ.get("DRY_RUN", "true").lower() in {"1", "true", "yes"}
    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    if not vault_db_path.exists():
        raise FileNotFoundError(f"Vault database not found: {vault_db_path}")

    result = migrate_vault_token_metadata(
        vault_db_path=vault_db_path,
        dry_run=dry_run,
        max_workers=max_workers,
    )

    if result.updates:
        print(
            tabulate(
                [
                    [
                        update.protocol,
                        update.old_name,
                        update.old_symbol,
                        update.new_name,
                        update.new_symbol,
                        update.spec.vault_address,
                    ]
                    for update in result.updates
                ],
                headers=["protocol", "old name", "old symbol", "new name", "new symbol", "address"],
                tablefmt="simple",
            )
        )

    print(f"Inspected {result.inspected_rows:,} EVM vault rows, updated {result.updated_rows:,}, skipped {result.skipped_rows:,} incomplete rows, and excluded {result.skipped_non_evm_rows:,} non-EVM rows.")
    if dry_run:
        print("Dry run - no changes written.")


if __name__ == "__main__":
    main()
