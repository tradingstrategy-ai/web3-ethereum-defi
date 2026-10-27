"""Register or refresh only the hardcoded Asseto IP Vault metadata.

The historical deployment bound comes from the verified proxy creation.
Reader state, event counts and price history are never reset by this migration.
"""

import dataclasses
import logging

from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.tokenised_fund.asseto.royalty_constants import ROYALTY_CHAIN_ID, ROYALTY_DEPLOYED_AT, ROYALTY_DEPLOYMENT_BLOCK, ROYALTY_VAULT_ADDRESS
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


def migrate_royalty_metadata(vault_db: VaultDatabase, web3: Web3, *, dry_run: bool = True) -> VaultRow:
    """Build the IP Vault record without modifying unrelated products.

    Existing discovery counts, first-seen data and custom row fields survive
    refresh. A new row uses the historical deployment bound with zero observed
    flow counts. No price or reader-state files are opened.

    :param vault_db: Metadata database to update when applying.
    :param web3: Configured Soneium connection.
    :param dry_run: Return the proposed record without mutating the database.
    :return: Validated proposed IP Vault metadata row.
    :raises ValueError: If existing identity or refreshed metadata is invalid.
    """
    if web3.eth.chain_id != ROYALTY_CHAIN_ID:
        message = "Asseto IP Vault migration requires Soneium"
        raise ValueError(message)
    spec = VaultSpec(ROYALTY_CHAIN_ID, ROYALTY_VAULT_ADDRESS)
    existing = vault_db.rows.get(spec, {})
    features = {ERC4626Feature.asseto_royalty_like}
    if existing:
        detection = existing.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection) or detection.chain != spec.chain_id or detection.address.lower() != spec.vault_address:
            message = "Existing IP Vault row has incompatible discovery metadata"
            raise ValueError(message)
        detection = dataclasses.replace(detection, features=features, updated_at=native_datetime_utc_now())
    else:
        detection = ERC4262VaultDetection(chain=spec.chain_id, address=spec.vault_address, features=features, first_seen_at_block=ROYALTY_DEPLOYMENT_BLOCK, first_seen_at=ROYALTY_DEPLOYED_AT, updated_at=native_datetime_utc_now(), deposit_count=0, redeem_count=0)
    logger.info("Reading Asseto IP Vault metadata for %s (dry_run=%s)", spec, dry_run)
    rebuilt = create_vault_scan_record(web3, detection, web3.eth.block_number, token_cache=None)
    if rebuilt.get("Protocol") != "Asseto" or rebuilt.get("Symbol") != "sIPUSD" or VaultFlag.review_needed not in rebuilt.get("_flags", set()):
        message = "IP Vault metadata did not pass identity and review-status checks"
        raise ValueError(message)
    result = existing | rebuilt
    if not dry_run:
        vault_db.rows[spec] = result
    return result
