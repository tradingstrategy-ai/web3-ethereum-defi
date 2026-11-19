"""Onboard reviewed ERC-4626 vaults that lead discovery has not reached yet.

Lead discovery caches its result for ``LEAD_DISCOVERY_STATE_TIMEOUT``,
seven days by default, so a vault whose first deposit falls after the stored
lead cursor stays unknown until the cache expires. During that time the price
scanner never reads it and the vault is missing from the export.

A numbered migration onboards such a vault ahead of the next discovery pass.
:py:func:`seed_vault_rows` inserts a direct lead and a scanner metadata row
built with the ordinary scan record, then :py:func:`backfill_onboarded_vault_prices`
fills the prices from the first deposit to the block where ordinary price
scanning takes over.

The lead's deposit and withdrawal counts start at zero on purpose. Discovery
resumes from the unchanged lead cursor, sees this vault's events again and
adds them to the existing lead
(see :py:meth:`eth_defi.erc_4626.hypersync_discovery.HypersyncVaultDiscover.scan_vaults`),
so seeding the observed counts would double them. Until then, the price
scanner admits the vault through its live TVL probe; see
:py:func:`eth_defi.vault.scan_all_chains.scan_prices_for_chain`.

The RPC-dependent steps are small module-level functions, so the numbered
migration batch test can replace them without provider access.
"""

import datetime
import logging
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import detect_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection, get_vault_protocol_name
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs import price_backfill
from eth_defi.vault_protocols.repairs.price_backfill import PriceBackfillTarget, read_price_cursor

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class VaultOnboardingTarget:
    """One reviewed vault and its first observed flow event.

    Freeze the values from the vault's first ``Deposit`` event, read through
    Hypersync, so the lead looks exactly like one created by discovery.
    """

    #: Chain of the vault.
    chain_id: int

    #: Vault share-token address, lower case.
    vault_address: HexAddress

    #: Block of the first ``Deposit`` event, used as the lead's first-seen block.
    first_seen_at_block: int

    #: Naive UTC timestamp of :py:attr:`first_seen_at_block`.
    first_seen_at: datetime.datetime

    #: Protocol name the ordinary classifier must assign, such as ``Llama Lend``.
    expected_protocol: str

    def as_spec(self) -> VaultSpec:
        """Return the metadata key of this target.

        :return: Chain and address of the vault.
        """
        return VaultSpec(self.chain_id, self.vault_address)


def create_chain_web3(chain_id: int) -> Web3:
    """Connect to the configured archive RPC of a chain.

    :param chain_id:
        EVM chain id, resolved to its ``JSON_RPC_*`` environment variable.
    :return:
        Web3 connection.
    """
    return create_multi_provider_web3(read_json_rpc_url(chain_id))


def build_vault_row(web3: Web3, target: VaultOnboardingTarget, block: int) -> dict:
    """Classify a vault and build its scanner metadata row.

    Uses the same feature probes and scan record as lead discovery. Token
    metadata goes to a temporary cache, so a preview writes no provider cache.

    :param web3:
        Archive connection of the target's chain.
    :param target:
        Reviewed vault.
    :param block:
        Metadata source block.
    :return:
        Scanner metadata row, including ``_detection_data``.
    """
    features = detect_vault_features(web3, target.vault_address, verbose=False)
    detection = ERC4262VaultDetection(
        chain=target.chain_id,
        address=target.vault_address,
        features=features,
        first_seen_at_block=target.first_seen_at_block,
        first_seen_at=target.first_seen_at,
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )
    with TemporaryDirectory(prefix="migration-vault-onboarding-") as directory:
        token_cache = TokenDiskCache(Path(directory) / "tokens.sqlite")
        try:
            return create_vault_scan_record(web3, detection, block, token_cache)
        finally:
            token_cache.close()


def seed_vault_rows(context: MigrationContext, targets: tuple[VaultOnboardingTarget, ...]) -> None:
    """Insert reviewed leads and metadata rows that discovery has not created yet.

    A vault already known to the metadata database keeps its lead and row, so
    the migration is a no-op if discovery got there first. A new row must be
    classified as the expected protocol without an RPC failure before
    anything is committed; a transient read failure fails the migration
    instead of persisting a broken placeholder, and a retry rebuilds it.

    :param context:
        Guarded migration attempt and shared metadata database.
    :param targets:
        Reviewed vaults.
    :raise ValueError:
        If a chain has no lead cursor, a vault's first event is not after it,
        or a row could not be read as the expected protocol.
    """
    database = context.metadata()
    missing = [target for target in targets if target.as_spec() not in database.rows]
    rows = {}
    for target in missing:
        cursor_block = database.last_scanned_block.get(target.chain_id)
        if cursor_block is None:
            message = f"Chain {target.chain_id} lead scan has not run; nothing to onboard"
            raise ValueError(message)
        if target.first_seen_at_block <= cursor_block:
            # Discovery has already scanned this block, so the vault is missing for another reason
            message = f"{target.as_spec().as_string_id()} first event at block {target.first_seen_at_block:,} is not after the lead cursor {cursor_block:,}; investigate discovery instead"
            raise ValueError(message)
        web3 = create_chain_web3(target.chain_id)
        row = build_vault_row(web3, target, web3.eth.block_number)
        protocol = get_vault_protocol_name(row["_detection_data"].features) if "_detection_data" in row else None
        if row.get("_rpc_failure_category") or row.get("Protocol") != target.expected_protocol or protocol != target.expected_protocol:
            message = f"Could not read {target.as_spec().as_string_id()} as {target.expected_protocol}: protocol {row.get('Protocol')!r}, failure {row.get('_rpc_failure_category')!r}; retry the migration"
            raise ValueError(message)
        rows[target.as_spec()] = row

    inserted_leads = 0
    for target in missing:
        spec = target.as_spec()
        if spec not in database.leads:
            database.leads[spec] = PotentialVaultMatch(
                chain=target.chain_id,
                address=target.vault_address,
                first_seen_at_block=target.first_seen_at_block,
                first_seen_at=target.first_seen_at,
            )
            inserted_leads += 1

    database.rows.update(rows)
    context.report(
        inserted_rows=len(rows),
        inserted_leads=inserted_leads,
        existing_rows=sorted(target.as_spec().as_string_id() for target in targets if target not in missing),
        onboarded_vaults={spec.as_string_id(): {"name": row.get("Name"), "protocol": row.get("Protocol"), "denomination": row.get("Denomination"), "nav": str(row.get("NAV"))} for spec, row in rows.items()},
    )
    context.commit_metadata()


def backfill_onboarded_vault_prices(context: MigrationContext, targets: tuple[VaultOnboardingTarget, ...]) -> None:
    """Backfill reviewed vaults' prices up to the chain's price scan cursor.

    The scanner continues a chain from its highest stored reader block plus
    one and replaces only rows at or after it. Each range therefore runs from
    the vault's first deposit to that block, exclusive, so the backfill and
    ordinary scanning meet without overlap. A vault whose first deposit is
    after the cursor is left to ordinary scanning.

    :param context:
        Guarded migration attempt and shared metadata database.
    :param targets:
        Reviewed vaults, already seeded by :py:func:`seed_vault_rows`.
    :raise ValueError:
        If a reviewed vault has no metadata row of the expected protocol.
    """
    database = context.metadata()
    for target in targets:
        row = database.rows.get(target.as_spec())
        if row is None or row.get("Protocol") != target.expected_protocol:
            message = f"Backfill target {target.as_spec().as_string_id()} is missing or not {target.expected_protocol}; seed it first"
            raise ValueError(message)
    end_blocks = {chain_id: read_price_cursor(context.data_dir, chain_id) + 1 for chain_id in {target.chain_id for target in targets}}
    backfill_targets = [PriceBackfillTarget(target.chain_id, target.vault_address, target.first_seen_at_block, end_blocks[target.chain_id]) for target in targets if target.first_seen_at_block < end_blocks[target.chain_id]]
    context.report(price_cursor_end_blocks={str(chain_id): block for chain_id, block in end_blocks.items()}, backfill_targets=len(backfill_targets))
    if not backfill_targets:
        logger.info("Every reviewed vault starts after its chain's price cursor; nothing to backfill")
        return
    # Module attribute access lets the numbered batch test replace the helper.
    price_backfill.run_price_backfill(context, backfill_targets)
