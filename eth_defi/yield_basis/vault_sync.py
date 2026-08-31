"""Reconcile reviewed YieldBasis products into ``VaultDatabase``."""

import datetime
import logging
from dataclasses import dataclass

from web3 import Web3

from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import YIELD_BASIS_NOTE
from eth_defi.vault.strategy_tag import lookup_strategy_tags
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow
from eth_defi.yield_basis.tags import STRATEGY_TAGS
from eth_defi.yield_basis.vault import YIELD_BASIS_VAULT_FEATURES, export_yield_basis_usd_denomination
from eth_defi.yield_basis.vault_catalog import YieldBasisMarket, YieldBasisScanPreparation, fetch_yield_basis_scan_preparation

logger = logging.getLogger(__name__)

#: Explanation exported when the reviewed AMM kill switch is active.
YIELD_BASIS_DISABLED_DEPOSIT_REASON = "YieldBasis market is killed or not currently reviewed for publication"

#: Catalogue fields that remain safe to refresh after a transient metadata
#: read failure. Protocol component fields are selected by their prefix.
YIELD_BASIS_CATALOGUE_FIELDS: frozenset[str] = frozenset(
    {
        "Name",
        "Protocol",
        "Denomination",
        "Features",
        "Link",
        "_notes",
        "_description",
        "_short_description",
        "_denomination_token",
        "_synthetic_usd_denomination",
        "_deposits_open",
        "_deposit_closed_reason",
        "_strategy_tags",
        "features",
        "_detection_data",
    }
)


@dataclass(frozen=True, slots=True)
class YieldBasisCatalogueSyncResult:
    """Summarise one YieldBasis catalogue reconciliation."""

    #: Valid products merged into the in-memory database.
    products: int
    #: New common vault rows.
    inserted: int
    #: Existing common vault rows refreshed.
    updated: int
    #: Valid products whose AMM kill switch is active.
    closed: int
    #: Operator-visible products withheld by the pre-scan.
    review_required: tuple[str, ...]


def _product_name(product: YieldBasisMarket) -> str:
    """Build a stable display name for one reviewed market."""

    return f"yb-LP {product.review.asset_symbol}"


def _product_description(product: YieldBasisMarket) -> str:
    """Build a concise, user-facing description for one market."""

    return f"YieldBasis market {product.market_id} supplies {product.review.asset_symbol} and borrowed crvUSD to a Curve Cryptoswap pool through LEVAMM. The yb-LP share remains exposed to {product.review.asset_symbol} price volatility, and an immediate redemption can be below fundamental value because of the Temporary Redemption Discount."


def _normalise_row(row: VaultRow, product: YieldBasisMarket) -> VaultRow:
    """Apply catalogue-owned identity and availability fields."""

    row["Name"] = _product_name(product)
    row["Protocol"] = "YieldBasis"
    row["Denomination"] = "USD"
    row["Features"] = ", ".join(sorted(feature.name for feature in YIELD_BASIS_VAULT_FEATURES))
    row["Link"] = "https://yieldbasis.com/earn"
    row["_notes"] = YIELD_BASIS_NOTE
    row["_strategy_tags"] = lookup_strategy_tags(STRATEGY_TAGS, product.lt_address)
    row["_description"] = _product_description(product)
    row["_short_description"] = f"YieldBasis {product.review.asset_symbol} leveraged liquidity-provider share"
    row["_denomination_token"] = export_yield_basis_usd_denomination(1)
    row["_synthetic_usd_denomination"] = True
    row["_yield_basis_market_id"] = product.market_id
    row["_yield_basis_underlying_token"] = product.asset_address.lower()
    row["_yield_basis_underlying_symbol"] = product.review.asset_symbol
    row["_yield_basis_component_addresses"] = {
        "cryptopool": product.cryptopool.lower(),
        "amm": product.amm.lower(),
    }
    row["_yield_basis_market_killed"] = product.killed
    # A live kill-switch read can establish closure, but an enabled market
    # still needs a protocol-specific quote and limits check before a deposit
    # is executable. Keep the shared field tri-state rather than promising
    # that generic deposits are open.
    row["_deposits_open"] = False if product.killed else None
    row["_deposit_closed_reason"] = YIELD_BASIS_DISABLED_DEPOSIT_REASON if product.killed else None
    return row


def fetch_and_sync_yield_basis_vault_catalogue(  # noqa: PLR0914
    *,
    web3: Web3,
    vault_db: VaultDatabase,
    token_cache: TokenDiskCache | dict | None = None,
    block_number: int | None = None,
    preparation: YieldBasisScanPreparation | None = None,
) -> YieldBasisCatalogueSyncResult:
    """Validate and upsert the reviewed YieldBasis products.

    The function mutates only the supplied in-memory database. Callers choose
    when to commit it, allowing a Factory-wide failure to leave the prior
    catalogue intact.

    :param web3:
        Ethereum connection used for validation and metadata reads.
    :param vault_db:
        Common metadata database updated in memory.
    :param token_cache:
        Optional shared ERC-20 metadata cache.
    :param block_number:
        Fixed catalogue block when ``preparation`` is not supplied.
    :param preparation:
        Reusable validated pre-scan result from the same chain cycle.
    :return:
        Reconciliation counts and any review messages.
    """

    preparation = preparation or fetch_yield_basis_scan_preparation(web3, block_number)
    if not preparation.factory_valid:
        raise RuntimeError("YieldBasis Factory validation failed: " + "; ".join(preparation.review_required))
    now_block = preparation.block_number
    updated_at = web3.eth.get_block(now_block)["timestamp"]
    updated_at_datetime = datetime.datetime.fromtimestamp(updated_at, tz=datetime.UTC).replace(tzinfo=None)
    results: list[tuple[VaultSpec, VaultRow, bool, bool]] = []
    runtime_review_required = list(preparation.review_required)
    for product in preparation.products:
        spec = VaultSpec(preparation.chain_id, product.lt_address.lower())
        existing = vault_db.rows.get(spec)
        if existing is None:
            first_seen_at_block = product.review.first_seen_at_block
            first_seen_at = product.review.first_seen_at
        else:
            old_detection = existing["_detection_data"]
            first_seen_at_block = old_detection.first_seen_at_block
            first_seen_at = old_detection.first_seen_at
        detection = ERC4262VaultDetection(
            chain=preparation.chain_id,
            address=product.lt_address.lower(),
            first_seen_at_block=first_seen_at_block,
            first_seen_at=first_seen_at,
            features=set(YIELD_BASIS_VAULT_FEATURES),
            updated_at=updated_at_datetime,
            deposit_count=0,
            redeem_count=0,
        )
        row = create_vault_scan_record(web3, detection, now_block, token_cache if token_cache is not None else {})
        scan_failed = str(row.get("Name", "")).startswith("<broken:")
        if scan_failed and existing is None:
            message = f"market {product.market_id}: metadata read returned {row.get('Name')}"
            logger.warning("YieldBasis product withheld until a later metadata cycle: %s", message)
            runtime_review_required.append(message)
            continue
        row = _normalise_row(row, product)
        if existing is not None:
            merged = existing.copy()
            if scan_failed:
                # Keep previously healthy scanner fields, while replacing
                # catalogue identity and current kill state.
                merged.update({key: row[key] for key in row if key.startswith("_yield_basis") or key in YIELD_BASIS_CATALOGUE_FIELDS})
            else:
                merged.update(row)
            row = merged
        results.append((spec, row, existing is None, product.killed))
    vault_db._merge_rows({spec: row for spec, row, _new, _killed in results})
    return YieldBasisCatalogueSyncResult(
        products=len(results),
        inserted=sum(new for _spec, _row, new, _killed in results),
        updated=sum(not new for _spec, _row, new, _killed in results),
        closed=sum(killed for _spec, _row, _new, killed in results),
        review_required=tuple(runtime_review_required),
    )
