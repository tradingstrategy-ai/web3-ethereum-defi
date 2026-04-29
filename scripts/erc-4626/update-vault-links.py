"""Refresh native vault links for one protocol in the vault metadata pickle.

This helper rewrites ``VaultRow["Link"]`` values using each protocol-specific
vault Python class as the source of truth. It is intended for fixing persisted
metadata rows after a ``get_link()`` implementation has changed without doing a
full chain rescan.

Usage:

.. code-block:: shell

    source .local-test.env && PROTOCOL_ID=accountable poetry run python scripts/erc-4626/update-vault-links.py

Optional environment variables:

- ``VAULT_DB``: metadata pickle path, defaults to ``~/.tradingstrategy/vaults/vault-metadata-db.pickle``
- ``DRY_RUN``: set to ``true`` to report changes without writing
- ``LOG_LEVEL``: console log level, defaults to ``info``

The script reads chain RPC URLs from ``JSON_RPC_{CHAIN}`` environment variables,
for example ``JSON_RPC_MONAD`` for Accountable vaults on Monad.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.provider.env import get_json_rpc_env, read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.research.vault_metrics import slugify_protocol
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


VaultFactory = Callable[[Web3, ERC4262VaultDetection, TokenDiskCache], VaultBase | None]


@dataclass(slots=True)
class VaultLinkUpdate:
    """Describe a single vault link refresh.

    :param spec:
        Vault chain/address key in the metadata database.

    :param protocol_id:
        Normalised protocol id matched from the vault row.

    :param vault_class:
        Python vault class used to regenerate the link.

    :param old_link:
        Link value currently stored in the pickle.

    :param new_link:
        Link returned by the vault class.
    """

    spec: VaultSpec
    protocol_id: str
    vault_class: str
    old_link: str | None
    new_link: str

    @property
    def changed(self) -> bool:
        """Whether this refresh changes the stored link value.

        :return:
            ``True`` when ``old_link`` and ``new_link`` differ.
        """
        return self.old_link != self.new_link


def _get_row_protocol_id(row: VaultRow) -> str:
    """Resolve a protocol id from a vault metadata row.

    Older pickles may not have ``protocol_slug`` stored, so this falls back to
    slugifying the human-readable ``Protocol`` field.

    :param row:
        Vault metadata row.

    :return:
        Normalised protocol id.
    """
    existing_slug = row.get("protocol_slug")
    if existing_slug:
        return existing_slug

    protocol_name = row.get("Protocol")
    if not protocol_name:
        raise ValueError(f"Vault row is missing Protocol field: {row}")

    return slugify_protocol(protocol_name)


def _create_vault_from_detection(
    web3: Web3,
    detection: ERC4262VaultDetection,
    token_cache: TokenDiskCache,
) -> VaultBase | None:
    """Create a vault instance for a detection row.

    :param web3:
        Web3 connection for the detection chain.

    :param detection:
        Persisted vault detection metadata.

    :param token_cache:
        Token metadata cache shared across refreshes.

    :return:
        Protocol-specific vault instance, or ``None`` if the detection is not supported.
    """
    return create_vault_instance(
        web3,
        detection.address,
        detection.features,
        token_cache=token_cache,
    )


def refresh_vault_links_for_protocol(
    vault_db: VaultDatabase,
    protocol_id: str,
    web3_by_chain: dict[int, Web3],
    token_cache: TokenDiskCache,
    vault_factory: VaultFactory = _create_vault_from_detection,
) -> list[VaultLinkUpdate]:
    """Refresh native app links for all vault rows matching a protocol.

    The function first reconstructs every matching vault instance and computes
    every new link. Only after all rows have succeeded are the rows mutated. This
    keeps the caller from writing a partially refreshed pickle if one vault
    fails.

    :param vault_db:
        Vault metadata database loaded from pickle.

    :param protocol_id:
        Protocol id to refresh, e.g. ``"accountable"``.

    :param web3_by_chain:
        Web3 connections keyed by chain id. Must contain every chain used by
        matching rows.

    :param token_cache:
        Token metadata cache passed to reconstructed vault instances.

    :param vault_factory:
        Factory for constructing vault instances. Defaults to
        :py:func:`eth_defi.erc_4626.classification.create_vault_instance` via
        ``_create_vault_from_detection``.

    :return:
        List of applied updates.
    """
    protocol_id = slugify_protocol(protocol_id)
    pending_updates: list[tuple[VaultRow, VaultLinkUpdate]] = []

    for spec, row in vault_db.rows.items():
        row_protocol_id = _get_row_protocol_id(row)
        if row_protocol_id != protocol_id:
            continue

        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection):
            raise ValueError(f"Vault row {spec} is missing ERC4262VaultDetection: {detection}")

        web3 = web3_by_chain.get(spec.chain_id)
        if web3 is None:
            env_var = get_json_rpc_env(spec.chain_id)
            raise ValueError(f"Missing Web3 connection for chain {spec.chain_id}. Set {env_var} and retry.")

        vault = vault_factory(web3, detection, token_cache)
        if vault is None:
            raise ValueError(f"Could not resolve vault class for {spec}, features: {detection.features}")

        new_link = vault.get_link()
        if not new_link:
            raise ValueError(f"Vault class {vault.__class__.__name__} returned an empty link for {spec}")

        pending_updates.append(
            (
                row,
                VaultLinkUpdate(
                    spec=spec,
                    protocol_id=row_protocol_id,
                    vault_class=vault.__class__.__name__,
                    old_link=row.get("Link"),
                    new_link=new_link,
                ),
            )
        )

    if not pending_updates:
        raise ValueError(f"No vault rows found for protocol id {protocol_id}")

    for row, update in pending_updates:
        row["Link"] = update.new_link

    return [update for _, update in pending_updates]


def _create_web3_connections_for_protocol(vault_db: VaultDatabase, protocol_id: str) -> dict[int, Web3]:
    """Create Web3 connections for all chains used by a protocol.

    :param vault_db:
        Vault metadata database loaded from pickle.

    :param protocol_id:
        Protocol id to refresh, e.g. ``"accountable"``.

    :return:
        Web3 connections keyed by chain id.
    """
    protocol_id = slugify_protocol(protocol_id)
    chain_ids = sorted({spec.chain_id for spec, row in vault_db.rows.items() if _get_row_protocol_id(row) == protocol_id})
    if not chain_ids:
        raise ValueError(f"No vault rows found for protocol id {protocol_id}")

    web3_by_chain: dict[int, Web3] = {}
    for chain_id in chain_ids:
        rpc_url = read_json_rpc_url(chain_id)
        web3 = create_multi_provider_web3(rpc_url)
        connected_chain_id = web3.eth.chain_id
        if connected_chain_id != chain_id:
            env_var = get_json_rpc_env(chain_id)
            raise ValueError(f"{env_var} points to chain {connected_chain_id}, expected {chain_id}")
        web3_by_chain[chain_id] = web3

    return web3_by_chain


def main() -> None:
    """Run the vault link refresh from environment variables.

    Reads ``PROTOCOL_ID`` and optional ``VAULT_DB`` / ``DRY_RUN`` environment
    variables, refreshes matching rows, and writes the pickle atomically using
    :py:meth:`eth_defi.vault.vaultdb.VaultDatabase.write`.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    protocol_id = os.environ.get("PROTOCOL_ID")
    if not protocol_id:
        msg = "Set PROTOCOL_ID environment variable, e.g. PROTOCOL_ID=accountable"
        raise RuntimeError(msg)

    vault_db_path = Path(os.environ.get("VAULT_DB", str(DEFAULT_VAULT_DATABASE))).expanduser()
    dry_run = os.environ.get("DRY_RUN", "false").lower() in {"1", "true", "yes"}

    logger.info("Reading vault metadata from %s", vault_db_path)
    vault_db = VaultDatabase.read(vault_db_path)
    web3_by_chain = _create_web3_connections_for_protocol(vault_db, protocol_id)
    token_cache = TokenDiskCache()

    updates = refresh_vault_links_for_protocol(
        vault_db=vault_db,
        protocol_id=protocol_id,
        web3_by_chain=web3_by_chain,
        token_cache=token_cache,
    )

    changed = [update for update in updates if update.changed]
    logger.info("Refreshed %d %s vault links, %d changed", len(updates), slugify_protocol(protocol_id), len(changed))
    for update in changed:
        logger.info("%s %s: %s -> %s", update.vault_class, update.spec.as_string_id(), update.old_link, update.new_link)

    if dry_run:
        logger.info("DRY_RUN=true, not writing %s", vault_db_path)
        return

    vault_db.write(vault_db_path)
    logger.info("Wrote refreshed vault metadata to %s", vault_db_path)


if __name__ == "__main__":
    main()
