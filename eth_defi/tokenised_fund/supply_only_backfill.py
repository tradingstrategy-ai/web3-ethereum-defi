"""Address-scoped metadata backfill for supply-only tokenised fund shares."""

import logging
import os
from pathlib import Path

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def backfill_supply_only_product(product, feature: ERC4626Feature, protocol: str) -> None:
    """Upsert one reviewed product without altering price histories.

    :param product: Static product record with chain, token and deployment data.
    :param feature: Hardcoded routing feature for the adapter.
    :param protocol: Human-readable protocol label for operator logs.
    :return: ``None``.
    """

    dry_run = os.environ.get("DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "on"}
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    logger.info("%s supply-only migration: chain=%d address=%s first_block=%d dry_run=%s", protocol, product.chain_id, product.token, product.first_seen_at_block, dry_run)
    if dry_run:
        return
    web3 = create_multi_provider_web3(read_json_rpc_url(product.chain_id))
    end_block = int(os.environ.get("END_BLOCK", web3.eth.block_number))
    if end_block < product.first_seen_at_block:
        raise ValueError(f"END_BLOCK must be at least {product.first_seen_at_block}")
    detection = ERC4262VaultDetection(chain=product.chain_id, address=product.token, first_seen_at_block=product.first_seen_at_block, first_seen_at=product.first_seen_at, features={feature}, updated_at=native_datetime_utc_now(), deposit_count=0, redeem_count=0)
    lead = PotentialVaultMatch(chain=product.chain_id, address=product.token, first_seen_at_block=product.first_seen_at_block, first_seen_at=product.first_seen_at, deposit_count=0, withdrawal_count=0)
    row = create_vault_scan_record(web3, detection=detection, block_identifier=end_block, token_cache=TokenDiskCache())
    vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
    vault_db.leads[VaultSpec(product.chain_id, product.token)] = lead
    vault_db._merge_rows({VaultSpec(product.chain_id, product.token): row})
    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    vault_db.write(vault_db_path)
