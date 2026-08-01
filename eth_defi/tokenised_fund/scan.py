"""Recurring price scans for reviewed tokenised-fund products.

The protocol backfill modules remain the explicit bootstrap and repair path.
This module is deliberately narrower: it refreshes only products that are
already present in the shared vault metadata database and writes only their
address-scoped raw-price rows.
"""

import datetime
import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from eth_typing import HexAddress

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.midas.constants import MIDAS_PRODUCTS
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.provider.rpcdb import RPCRequestStats
from eth_defi.token import TokenDiskCache
from eth_defi.tokenised_fund.libeara.constants import LIBEARA_PRODUCTS
from eth_defi.tokenised_fund.securitize.backfill import has_historical_price
from eth_defi.tokenised_fund.securitize.description import SECURITIZE_PRODUCTS
from eth_defi.tokenised_fund.wisdomtree.nav import WISDOMTREE_DATASPAN_API_KEY_ENV
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

TOKENISED_FUND_PRICE_DEFAULT_CYCLE = datetime.timedelta(hours=24)


@dataclass(slots=True, frozen=True)
class TokenisedFundPriceScanSpec:
    """One independently scheduled tokenised-fund price feed.

    The feature marker is the authoritative link between the scheduler and
    the persisted vault metadata. Product-specific predicates exclude known
    supply-only products such as Libeara ULTRA and non-fund Midas products.
    """

    #: Stable configuration selector.
    selector: str

    #: Human-readable independent scheduler row.
    dashboard_name: str

    #: Persisted vault feature identifying candidate products.
    feature: ERC4626Feature

    #: Optional reviewed-product filter within the feature family.
    accepts: Callable[[VaultSpec], bool] = lambda _: True

    #: Optional credential required by the source adapter.
    prerequisite_env: str | None = None

    #: Replay a bounded tail because issuer NAV data may be revised.
    refetch_tail: bool = False

    def prerequisite_error(self) -> str | None:
        """Return a human-readable unmet prerequisite, when any.

        :return:
            Missing environment-variable error, or ``None`` when ready.
        """

        if self.prerequisite_env and not os.environ.get(self.prerequisite_env):
            return f"{self.prerequisite_env} is not set"
        return None


@dataclass(slots=True)
class TokenisedFundPriceScanContext:
    """Explicit shared paths and resource limits for one scheduled scan."""

    #: Shared reviewed vault metadata database.
    vault_db_path: Path

    #: Shared raw vault-price Parquet file.
    raw_price_path: Path

    #: Maximum threaded historical-read workers.
    max_workers: int

    #: Chains allowed by the operator's chain configuration.
    enabled_chain_ids: frozenset[int]

    #: Optional lowercase product-address filter for a targeted backfill.
    vault_addresses: frozenset[str] | None = None

    #: Shared Hypersync stream concurrency limit.
    hypersync_concurrency: int | None = None

    #: Optional shared physical JSON-RPC request counter.
    rpc_request_stats: RPCRequestStats | None = None


@dataclass(slots=True)
class TokenisedFundPriceScanResult:
    """Summary of one tokenised-fund scheduled price refresh."""

    #: Number of exact registered products considered.
    vault_count: int

    #: Number of raw daily sample rows written.
    price_rows: int

    #: Latest non-null sampled NAV timestamp.
    latest_data_timestamp: str | None

    #: Earliest block scanned across target products.
    start_block: int | None

    #: Latest block scanned across target products.
    end_block: int | None

    #: Non-fatal skipped-target diagnostics.
    diagnostics: str | None = None


def _is_midas_tokenised_fund(spec: VaultSpec) -> bool:
    """Select only Midas products explicitly classified as tokenised funds."""

    product = MIDAS_PRODUCTS.get((spec.chain_id, HexAddress(spec.vault_address)))
    return product is not None and product.is_tokenised_fund


def _is_price_capable_securitize_product(spec: VaultSpec) -> bool:
    """Select Securitize products with a reviewed NAV source."""

    product = SECURITIZE_PRODUCTS.get((spec.chain_id, HexAddress(spec.vault_address)))
    return product is not None and has_historical_price(product)


def _is_price_capable_libeara_product(spec: VaultSpec) -> bool:
    """Select CMTAT products with reviewed NAV, excluding supply-only ULTRA."""

    product = LIBEARA_PRODUCTS.get((spec.chain_id, HexAddress(spec.vault_address)))
    return product is not None and product.symbol in {"CUMIU", "BELIF"}


TOKENISED_FUND_PRICE_SCANNERS: tuple[TokenisedFundPriceScanSpec, ...] = (
    TokenisedFundPriceScanSpec("asseto", "Asseto", ERC4626Feature.asseto_like, refetch_tail=True),
    TokenisedFundPriceScanSpec("franklin", "Franklin", ERC4626Feature.franklin_like),
    TokenisedFundPriceScanSpec("libeara", "Libeara", ERC4626Feature.libeara_like, _is_price_capable_libeara_product),
    TokenisedFundPriceScanSpec("midas", "Midas", ERC4626Feature.midas_like, _is_midas_tokenised_fund),
    TokenisedFundPriceScanSpec("ondo", "Ondo", ERC4626Feature.ondo_like),
    TokenisedFundPriceScanSpec("openeden", "OpenEden", ERC4626Feature.openeden_like),
    TokenisedFundPriceScanSpec("securitize", "Securitize", ERC4626Feature.securitize_like, _is_price_capable_securitize_product),
    TokenisedFundPriceScanSpec("spiko", "Spiko", ERC4626Feature.spiko_like),
    TokenisedFundPriceScanSpec("superstate", "Superstate", ERC4626Feature.superstate_like),
    TokenisedFundPriceScanSpec("sygnum", "Sygnum", ERC4626Feature.sygnum_like),
    TokenisedFundPriceScanSpec("usyc", "USYC", ERC4626Feature.usyc_like),
    TokenisedFundPriceScanSpec("wisdomtree", "WisdomTree", ERC4626Feature.wisdomtree_like, prerequisite_env=WISDOMTREE_DATASPAN_API_KEY_ENV, refetch_tail=True),
)

#: Every aggregate backfill selector has either a scheduled source or an
#: explicit reason why it is not a recurring price feed.
TOKENISED_FUND_PRICE_CAPABILITIES: dict[str, str] = {
    "asseto": "scheduled",
    "centrifuge": "supply only; no reviewed NAV/share source",
    "fdit": "supply only; no reviewed NAV/share source",
    "franklin": "scheduled",
    "kinexys": "static adapter estimate, not a refreshable feed",
    "kaio": "supply only; no reviewed NAV/share source",
    "libeara": "scheduled for CUMIU and BELIF only",
    "midas": "scheduled for tokenised-fund products only",
    "ondo": "scheduled",
    "openeden": "scheduled",
    "securitize": "scheduled for products with reviewed NAV",
    "spiko": "scheduled",
    "superstate": "scheduled",
    "sygnum": "scheduled",
    "theo": "supply only; no reviewed scalar NAV source",
    "usyc": "scheduled",
    "wisdomtree": "scheduled when DataSpan credentials are configured",
}


def select_tokenised_fund_price_scanners(selection: str | None) -> tuple[TokenisedFundPriceScanSpec, ...]:
    """Resolve an optional comma-separated protocol selection.

    :param selection:
        ``TOKENISED_FUND_PROTOCOLS`` value, or ``None`` for every scanner.
    :return:
        Selected scanner specs in stable dashboard order.
    :raise ValueError:
        If a selector is not price-capable.
    """

    if not selection or not selection.strip():
        return TOKENISED_FUND_PRICE_SCANNERS
    requested = {item.strip().lower() for item in selection.split(",") if item.strip()}
    known = {spec.selector for spec in TOKENISED_FUND_PRICE_SCANNERS}
    unknown = requested - known
    if unknown:
        raise ValueError(f"Unknown TOKENISED_FUND_PROTOCOLS selectors: {', '.join(sorted(unknown))}; supported: {', '.join(sorted(known))}")
    return tuple(spec for spec in TOKENISED_FUND_PRICE_SCANNERS if spec.selector in requested)


def _iter_target_rows(vault_db: VaultDatabase, spec: TokenisedFundPriceScanSpec) -> Iterable[tuple[VaultSpec, VaultRow]]:
    """Yield metadata rows owned by one price scanner.

    :param vault_db:
        Shared metadata database.
    :param spec:
        Scanner whose feature and product predicate select the rows.
    :return:
        Iterator of exact vault specs and their metadata rows.
    """

    for vault_spec, row in vault_db.rows.items():
        detection = row["_detection_data"]
        if spec.feature in detection.features and spec.accepts(vault_spec):
            yield vault_spec, row


def _find_last_priced_block(table: pa.Table | None, spec: VaultSpec) -> int | None:
    """Return the last block with a non-null price for one exact product."""

    if table is None:
        return None
    mask = pc.and_(
        pc.and_(pc.equal(table["chain"], spec.chain_id), pc.equal(table["address"], spec.vault_address)),
        pc.invert(pc.is_null(table["share_price"], nan_is_null=True)),
    )
    rows = table.filter(mask)
    return pc.max(rows["block_number"]).as_py() if rows.num_rows else None


def _find_refetch_tail_start_block(table: pa.Table | None, spec: VaultSpec, sample_count: int = 7) -> int | None:
    """Return the earliest block in a bounded seven-sample issuer-NAV tail."""

    if table is None:
        return None
    mask = pc.and_(
        pc.and_(pc.equal(table["chain"], spec.chain_id), pc.equal(table["address"], spec.vault_address)),
        pc.invert(pc.is_null(table["share_price"], nan_is_null=True)),
    )
    target_rows = table.filter(mask)
    if target_rows.num_rows == 0:
        return None
    tail_rows = sorted(target_rows.to_pylist(), key=lambda row: row["timestamp"], reverse=True)[:sample_count]
    return min(row["block_number"] for row in tail_rows)


def resolve_target_start_blocks(
    spec: TokenisedFundPriceScanSpec,
    raw_price_path: Path,
    targets: list[tuple[VaultSpec, int]],
) -> dict[VaultSpec, int]:
    """Choose one independent continuation point per exact product.

    The latest non-null raw price is the continuation source for these
    stateless daily reads. Null rows after that point do not hide a price gap.
    A missing product starts at its reviewed deployment or discovery block.
    Each product is scanned and rewritten separately, so bootstrapping one
    product cannot delete another product's newer history.

    :param spec:
        Protocol source semantics, including whether recent samples need a
        bounded refetch.
    :param raw_price_path:
        Existing shared raw-price Parquet file, if any.
    :param targets:
        Exact target specs paired with their deployment blocks.
    :return:
        Exact target-to-start-block mapping.
    """

    raw_table = pq.read_table(raw_price_path, columns=["chain", "address", "block_number", "timestamp", "share_price"]) if raw_price_path.exists() else None
    start_blocks: dict[VaultSpec, int] = {}
    for target, first_seen_at_block in targets:
        if spec.refetch_tail:
            prior_block = _find_refetch_tail_start_block(raw_table, target)
        else:
            prior_block = _find_last_priced_block(raw_table, target)
        start_blocks[target] = prior_block if prior_block is not None else first_seen_at_block
    return start_blocks


def load_tokenised_fund_target_specs(
    vault_db_path: Path,
    scanners: Iterable[TokenisedFundPriceScanSpec],
) -> set[VaultSpec]:
    """Load exact products owned by ready dedicated scanners.

    Disabled, unselected and product-filtered vaults remain under generic
    scanner ownership. A missing metadata database safely owns no products.

    :param vault_db_path:
        Shared reviewed vault metadata database.
    :param scanners:
        Ready scanner specifications for this process configuration.
    :return:
        Exact chain and address pairs reserved for dedicated daily scans.
    """

    if not vault_db_path.exists():
        return set()
    vault_db = VaultDatabase.read(vault_db_path)
    return {target for scanner in scanners for target, _ in _iter_target_rows(vault_db, scanner)}


def _find_latest_data_timestamp(table: pa.Table | None, target_specs: set[VaultSpec]) -> str | None:
    """Return the latest non-null NAV timestamp across one protocol's targets."""

    if table is None or not target_specs:
        return None
    latest: datetime.datetime | None = None
    for target in target_specs:
        mask = pc.and_(
            pc.and_(pc.equal(table["chain"], target.chain_id), pc.equal(table["address"], target.vault_address)),
            pc.invert(pc.is_null(table["share_price"], nan_is_null=True)),
        )
        rows = table.filter(mask)
        timestamp = pc.max(rows["timestamp"]).as_py() if rows.num_rows else None
        if timestamp is not None and (latest is None or timestamp > latest):
            latest = timestamp
    return latest.isoformat() if latest else None


def load_tokenised_fund_last_timestamps(
    raw_price_path: Path,
    vault_db_path: Path,
) -> dict[str, str]:
    """Load dashboard freshness per protocol from its exact target addresses.

    :param raw_price_path:
        Shared raw price Parquet path.
    :param vault_db_path:
        Shared vault metadata path containing persisted feature markers.
    :return:
        Mapping of dashboard names to latest valid NAV timestamp.
    """

    if not raw_price_path.exists() or not vault_db_path.exists():
        return {}
    vault_db = VaultDatabase.read(vault_db_path)
    table = pq.read_table(raw_price_path, columns=["chain", "address", "timestamp", "share_price"])
    return {spec.dashboard_name: timestamp for spec in TOKENISED_FUND_PRICE_SCANNERS if (timestamp := _find_latest_data_timestamp(table, {target for target, _ in _iter_target_rows(vault_db, spec)})) is not None}


def run_tokenised_fund_price_scan(  # noqa: PLR0914 - explicit production resource inputs.
    spec: TokenisedFundPriceScanSpec,
    context: TokenisedFundPriceScanContext,
) -> TokenisedFundPriceScanResult:
    """Refresh one reviewed protocol's raw price rows.

    When no prior raw row exists, start from the reviewed vault deployment
    block. This intentionally favours complete daily history over a precise
    initial source boundary; the relevant adapter still rejects calls before
    its oracle or issuer data is available.

    The scanner uses the same archive-state adapters as the normal vault price
    pipeline and samples at approximately one-day intervals. It does not claim
    to preserve every source update or its original publication timestamp.

    :param spec:
        Protocol selection and product filter.
    :param context:
        Shared production paths and worker limits.
    :return:
        Aggregate scan result across every configured target chain.
    :raise RuntimeError:
        If the metadata database or a selected product adapter is invalid.
    """

    if not context.vault_db_path.exists():
        raise RuntimeError(f"Tokenised-fund metadata database does not exist: {context.vault_db_path}")
    vault_db = VaultDatabase.read(context.vault_db_path)
    target_rows = list(_iter_target_rows(vault_db, spec))
    if context.vault_addresses is not None:
        target_rows = [(target, row) for target, row in target_rows if target.vault_address in context.vault_addresses]
    if not target_rows:
        return TokenisedFundPriceScanResult(0, 0, None, None, None, "no registered price-capable products")

    enabled_rows = [(target, row) for target, row in target_rows if target.chain_id in context.enabled_chain_ids]
    target_specs = {target for target, _ in enabled_rows}
    diagnostics = [f"chain {target.chain_id} disabled by operator" for target, _ in target_rows if target.chain_id not in context.enabled_chain_ids]
    if not enabled_rows:
        return TokenisedFundPriceScanResult(0, 0, None, None, None, "; ".join(sorted(set(diagnostics))))

    rows_written = 0
    first_start: int | None = None
    last_end: int | None = None
    token_cache = TokenDiskCache()
    try:
        for chain_id in sorted({target.chain_id for target in target_specs}):
            try:
                json_rpc_url = read_json_rpc_url(chain_id)
            except ValueError as exc:
                diagnostics.append(str(exc))
                continue
            web3 = create_multi_provider_web3(json_rpc_url, rpc_request_stats=context.rpc_request_stats)
            chain_targets = [(target, row) for target, row in enabled_rows if target.chain_id == chain_id]
            vaults = []
            for target, row in chain_targets:
                detection = row["_detection_data"]
                vault = create_vault_instance(web3, target.vault_address, detection.features, token_cache=token_cache)
                if vault is None:
                    raise RuntimeError(f"Could not create {spec.dashboard_name} adapter for {target.as_string_id()}")
                vault.first_seen_at_block = detection.first_seen_at_block
                vaults.append(vault)
            start_blocks = resolve_target_start_blocks(
                spec,
                context.raw_price_path,
                [(target, vault.first_seen_at_block) for (target, _), vault in zip(chain_targets, vaults, strict=True)],
            )
            hypersync_client = configure_hypersync_from_env(web3, concurrency=context.hypersync_concurrency).hypersync_client
            end_block = web3.eth.block_number
            web3factory = MultiProviderWeb3Factory(
                json_rpc_url,
                retries=5,
                skip_verification=True,
                expected_chain_id=chain_id,
                rpc_request_stats=context.rpc_request_stats,
            )
            # Keep every delete window identical to its rewrite window. A
            # newly registered product may start years before an incremental
            # neighbour, so batching addresses here would risk data loss.
            for (target, _), vault in zip(chain_targets, vaults, strict=True):
                start_block = start_blocks[target]
                if start_block > end_block:
                    diagnostics.append(f"{target.as_string_id()} stored block {start_block} is ahead of RPC head {end_block}")
                    logger.warning("Skipping %s because stored block %d is ahead of RPC head %d", target.as_string_id(), start_block, end_block)
                    continue
                result = scan_historical_prices_to_parquet(
                    output_fname=context.raw_price_path,
                    web3=web3,
                    web3factory=web3factory,
                    vaults=[vault],
                    start_block=start_block,
                    end_block=end_block,
                    max_workers=context.max_workers,
                    chunk_size=32,
                    token_cache=token_cache,
                    write_all_samples=True,
                    frequency="1d",
                    reader_states=None,
                    hypersync_client=hypersync_client,
                    rpc_request_stats=context.rpc_request_stats,
                    vault_addresses={target.vault_address},
                )
                rows_written += result["rows_written"]
                first_start = result["start_block"] if first_start is None else min(first_start, result["start_block"])
                last_end = result["end_block"] if last_end is None else max(last_end, result["end_block"])
    finally:
        token_cache.commit()

    raw_table = pq.read_table(context.raw_price_path, columns=["chain", "address", "timestamp", "share_price"]) if context.raw_price_path.exists() else None
    return TokenisedFundPriceScanResult(
        vault_count=len(target_specs),
        price_rows=rows_written,
        latest_data_timestamp=_find_latest_data_timestamp(raw_table, target_specs),
        start_block=first_start,
        end_block=last_end,
        diagnostics="; ".join(sorted(set(diagnostics))) or None,
    )
