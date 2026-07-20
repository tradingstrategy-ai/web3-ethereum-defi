"""Backfill historical Asseto vault data into the shared vault pipeline.

This is a targeted production tool for the EVM products currently published by
Asseto's public product registry. It does not rediscover whole chains and
updates only the selected Asseto vault identifiers. Historical rows combine
on-chain ERC-20 supply with Asseto's verified ``Pricer`` NAV/share where
available, or its public daily display-NAV history for other products.

Usage:

.. code-block:: shell

    source .local-test.env
    export JSON_RPC_ETHEREUM="https://your-archive-ethereum-rpc"
    PROTOCOLS=asseto poetry run python scripts/backfill-tokenised-funds.py

Useful environment variables:

.. list-table::
   :header-rows: 1

   * - Variable
     - Description
   * - ``DRY_RUN``
     - If ``true``, print and validate the planned work without database writes.
   * - ``NETWORKS``
     - Optional comma-separated chain ids or known names, e.g. ``177,hashkey``.
   * - ``JSON_RPC_<CHAIN>``
     - Archive-capable RPC URL for each selected supported chain, e.g.
       ``JSON_RPC_ETHEREUM``. Products are skipped when this is not set.
   * - ``HYPERSYNC_API_KEY``
     - Required for cached HyperSync block-timestamp reads during price
       backfill.
   * - ``PRODUCTS``
     - Optional comma-separated Asseto symbols, e.g. ``AoABT``.
   * - ``ASSETO_SCAN_PRICES``
     - If ``false``, update only metadata. Default: ``true``.
   * - ``ASSETO_CLEAN_PRICES``
     - If ``true``, replace only selected histories in cleaned prices. Default:
       ``true``.
   * - ``MAX_WORKERS``
     - Historical multicall worker count. Default: ``8``.
   * - ``FREQUENCY``
     - Historical price frequency. Asseto supports daily ``1d`` samples only.
   * - ``START_BLOCK`` / ``END_BLOCK``
     - Optional global scan range overrides.
   * - ``VAULT_DB_PATH`` / ``UNCLEANED_PRICE_DATABASE``
     - Optional production database path overrides.
   * - ``CLEANED_PRICE_DATABASE`` / ``READER_STATE_DATABASE``
     - Optional cleaned price and reader-state path overrides.
   * - ``CURRENCY_API_DB_PATH``
     - Exchange-rate DuckDB used to convert HKD products to USD. Run
       ``scan-currencies`` first to populate it.

The backfill removes stale reader state only for selected Asseto vaults. This
is required because the targeted scanner replaces those rows from its explicit
start block onwards; retained later state would otherwise skip the rewrite.
Only chains registered by the shared project registry and supported by
HyperSync are included. Asseto products on unsupported chains, such as HashKey
or Pharos until they are added to the project, are reported and skipped.
"""

import datetime
import logging
import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
import sys
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from atomicwrites import atomic_write
from eth_typing import HexAddress
from tabulate import tabulate
from web3 import Web3

from eth_defi.chain import CHAIN_NAMES, get_chain_name
from eth_defi.compat import native_datetime_utc_now
from eth_defi.currency_api.constants import SOURCE_NAME
from eth_defi.currency_api.database import CurrencyRateDatabase
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.hypersync.server import is_hypersync_supported_chain
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import get_json_rpc_env, read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.provider.named import get_provider_name
from eth_defi.research.wrangle_vault_prices import replace_cleaned_vault_histories
from eth_defi.token import TokenDiskCache, is_stablecoin_like
from eth_defi.tokenised_fund.asseto.constants import ASSETO_PRODUCTS, ASSETO_USD_DENOMINATIONS, AssetoProduct
from eth_defi.tokenised_fund.asseto.offchain_api import AssetoOffchainProduct, fetch_asseto_products
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.data_file_export import resolve_exchange_rate_database_path
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value returned when the variable is unset.
    :return:
        Parsed boolean value.
    """

    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_csv_env(name: str) -> set[str] | None:
    """Parse a comma-separated environment variable.

    :param name:
        Environment variable name.
    :return:
        Lowercase values, or ``None`` when unset.
    """

    value = os.environ.get(name, "").strip()
    return {part.strip().lower() for part in value.split(",") if part.strip()} if value else None


def parse_optional_int_env(name: str) -> int | None:
    """Parse an optional integer environment variable.

    :param name:
        Environment variable name.
    :return:
        Integer value, or ``None`` when unset.
    """

    value = os.environ.get(name)
    return int(value) if value else None


def parse_path_env(name: str, default: Path) -> Path:
    """Parse an optional filesystem path environment variable.

    :param name:
        Environment variable name.
    :param default:
        Default production path.
    :return:
        Expanded selected path.
    """

    return Path(os.environ[name]).expanduser() if os.environ.get(name) else default.expanduser()


def resolve_frequency() -> Literal["1h", "1d"]:
    """Resolve the daily historical sampling frequency for Asseto.

    Asseto's public NAV history contains daily observations. The scanner must
    therefore not generate artificial hourly rows by carrying a daily value
    forward between price publications.

    :return:
        The required daily sampling interval.
    :raise ValueError:
        If an hourly or other unsupported frequency is requested.
    """

    frequency = os.environ.get("FREQUENCY", "1d")
    if frequency != "1d":
        raise ValueError(f"Asseto backfill supports only daily FREQUENCY=1d, got: {frequency}")
    return cast(Literal["1h", "1d"], "1d")


def get_chain_selector_names(chain_id: int) -> set[str]:
    """Return accepted ``NETWORKS`` selector names for a chain.

    :param chain_id:
        EVM chain id.
    :return:
        Numeric and configured textual selector values.
    """

    chain_name = CHAIN_NAMES.get(chain_id)
    return {str(chain_id), chain_name.lower()} if chain_name else {str(chain_id)}


def is_supported_asseto_chain(chain_id: int) -> bool:
    """Check whether a chain is eligible for the shared Asseto backfill.

    :param chain_id:
        Asseto product chain id.
    :return:
        ``True`` when the project registers the chain and HyperSync supports it.
    """

    return chain_id in CHAIN_NAMES and is_hypersync_supported_chain(chain_id)


def get_asseto_rpc_env(chain_id: int) -> str:
    """Return the normal RPC environment variable for a supported chain.

    :param chain_id:
        Asseto product chain id.
    :return:
        The project-standard ``JSON_RPC_<CHAIN>`` environment variable.
    """

    assert is_supported_asseto_chain(chain_id), f"Unsupported Asseto chain {chain_id}"
    return get_json_rpc_env(chain_id)


def read_asseto_json_rpc_url(chain_id: int) -> str:
    """Read the script-local Asseto JSON-RPC URL from its environment variable.

    :param chain_id:
        Asseto product chain id.
    :return:
        Configured archive-capable RPC URL for a supported chain.
    :raise ValueError:
        If the standard RPC variable is unset.
    """

    return read_json_rpc_url(chain_id)


def iter_selected_products() -> Iterable[AssetoOffchainProduct]:
    """Iterate current eligible Asseto registry products filtered by environment.

    :return:
        Unique registry products on supported chains with configured RPC URLs.
    """

    networks = parse_csv_env("NETWORKS")
    products = parse_csv_env("PRODUCTS")
    seen: set[tuple[int, HexAddress]] = set()
    for product in fetch_asseto_products():
        key = (product.chain_id, product.contract_address)
        if key in seen:
            continue
        seen.add(key)
        if networks and not (get_chain_selector_names(product.chain_id) & networks):
            continue
        if products and (product.symbol or product.product_name).lower() not in products:
            continue
        if product.chain_id not in CHAIN_NAMES:
            logger.warning(
                "Skipping Asseto product %s on unsupported chain %d: chain is not configured in eth_defi.chain",
                product.symbol or product.product_name,
                product.chain_id,
            )
            continue
        if not is_hypersync_supported_chain(product.chain_id):
            logger.warning(
                "Skipping Asseto product %s on chain %d: HyperSync is not supported",
                product.symbol or product.product_name,
                product.chain_id,
            )
            continue
        rpc_env_var = get_asseto_rpc_env(product.chain_id)
        if not os.environ.get(rpc_env_var):
            logger.warning(
                "Skipping Asseto product %s on chain %d: %s is not set",
                product.symbol or product.product_name,
                product.chain_id,
                rpc_env_var,
            )
            continue
        yield product


def resolve_price_scan_start_block(
    products: list[AssetoProduct],
) -> int:
    """Resolve the earliest history start for selected Asseto products.

    The normal scanner's incremental reader state must not decide the start of
    a targeted rewrite. Begin at the earliest Asseto deployment unless the
    operator supplied ``START_BLOCK``. HyperSync populates any missing block
    timestamps, so an existing local timestamp cache must never truncate the
    requested history.

    :param products:
        Selected products on one EVM chain.
    :return:
        Explicit override or the earliest deployment block.
    """

    explicit_start_block = parse_optional_int_env("START_BLOCK")
    if explicit_start_block is not None:
        return explicit_start_block

    assert products, "Cannot resolve a scan start block without Asseto products"
    chain_ids = {product.chain_id for product in products}
    assert len(chain_ids) == 1, f"Expected products from one chain, got {chain_ids}"
    deployment_start_block = min(product.first_seen_at_block for product in products)
    return deployment_start_block


def create_asseto_detection(product: AssetoProduct) -> ERC4262VaultDetection:
    """Create a synthetic shared scanner detection for an Asseto product.

    :param product:
        Asseto product metadata.
    :return:
        Detection compatible with metadata scan record generation.
    """

    return ERC4262VaultDetection(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        features={ERC4626Feature.asseto_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def create_asseto_lead(product: AssetoProduct) -> PotentialVaultMatch:
    """Create a synthetic discovery lead for an Asseto product.

    :param product:
        Asseto product metadata.
    :return:
        Lead compatible with the vault metadata database.
    """

    return PotentialVaultMatch(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        deposit_count=0,
        withdrawal_count=0,
    )


def read_vault_database(path: Path) -> VaultDatabase:
    """Read or initialise the vault metadata database.

    :param path:
        Vault metadata database path.
    :return:
        Existing or empty database.
    """

    return VaultDatabase.read(path) if path.exists() else VaultDatabase()


def read_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Read trusted local historical reader states.

    :param path:
        Reader-state pickle file path.
    :return:
        Existing states, or an empty mapping when the file does not exist.
    """

    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Atomically write historical reader state.

    :param path:
        Reader-state pickle file path.
    :param states:
        Reader state mapping returned by the historical scanner.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as out:
        pickle.dump(states, out)


def build_vaults(web3: Web3, products: list[AssetoProduct], token_cache: TokenDiskCache) -> list[VaultBase]:
    """Build Asseto vault adapters and attach deployment block hints.

    :param web3:
        Connected chain Web3 instance.
    :param products:
        Selected products on this chain.
    :param token_cache:
        Shared token metadata cache.
    :return:
        Configured Asseto vault adapters.
    """

    vaults: list[VaultBase] = []
    for product in products:
        if product.collateral is None and product.denomination_symbol is None:
            logger.warning(
                "Skipping price history for Asseto product %s: Asseto registry does not publish an accounting denomination",
                product.symbol,
            )
            continue
        vault = create_vault_instance(
            web3,
            product.token,
            features={ERC4626Feature.asseto_like},
            token_cache=token_cache,
        )
        if vault is None:
            raise RuntimeError(f"Could not create Asseto vault adapter for {product.symbol} {product.token}")
        if not vault.uses_onchain_pricer() and not vault.fetch_offchain_price_history():
            logger.warning("Skipping price history for Asseto product %s: Asseto API returned no prices", product.symbol)
            continue
        vault.first_seen_at_block = product.first_seen_at_block
        vaults.append(vault)
    return vaults


def select_cleanable_vault_ids(products: Iterable[AssetoProduct], rows: dict[VaultSpec, dict]) -> set[str]:
    """Select active Asseto histories supported by the USD-only cleaner.

    HKD histories are converted to USD by the adapter before reaching this
    point. Inactive zero-supply products remain mapped in metadata and may keep
    raw observations, but are not passed to the strict replacement helper
    because normal cleaning intentionally removes every zero-NAV row.

    :param products:
        Asseto products whose raw histories were scanned.
    :param rows:
        Fresh vault metadata rows keyed by vault specification.
    :return:
        Canonical vault identifiers eligible for cleaned-history replacement.
    """

    cleanable_ids: set[str] = set()
    for product in products:
        spec = VaultSpec(product.chain_id, product.token)
        row = rows[spec]
        denomination = row.get("Denomination")
        nav = row.get("NAV")
        if is_stablecoin_like(denomination) and nav is not None and nav > 0:
            cleanable_ids.add(spec.as_string_id())
    return cleanable_ids


def resolve_active_asseto_product_ids(
    registry_products: Iterable[AssetoOffchainProduct],
    runtime_products: Iterable[AssetoProduct],
    rows: dict[VaultSpec, dict],
) -> set[str]:
    """Resolve active products from both registry TVL and on-chain supply.

    Using both sources prevents a missing or stale registry TVL field from
    hiding a positive-supply product from live-feed coverage validation.

    :param registry_products:
        Current public Asseto registry entries.
    :param runtime_products:
        Corresponding products with on-chain deployment metadata.
    :param rows:
        Fresh current vault metadata rows.
    :return:
        Lower-case token addresses considered active.
    """

    active_ids = {product.contract_address.lower() for product in registry_products if product.tvl is not None and product.tvl > 0}
    active_ids.update(product.token.lower() for product in runtime_products if (rows[VaultSpec(product.chain_id, product.token)].get("Shares") or 0) > 0)
    return active_ids


def validate_active_asseto_coverage(
    chain_id: int,
    active_product_ids: set[str],
    runtime_products: Iterable[AssetoProduct],
    rows: dict[VaultSpec, dict],
    price_history_ids: set[str],
) -> None:
    """Reject a backfill that would leave an active Asseto fund out of live data.

    :param chain_id:
        EVM chain being validated.
    :param active_product_ids:
        Lower-case active token addresses.
    :param runtime_products:
        Products represented by the current metadata scan.
    :param rows:
        Fresh metadata rows.
    :param price_history_ids:
        Lower-case addresses whose adapters found NAV history.
    :raise RuntimeError:
        If an active product lacks price history or positive USD-compatible
        live metadata.
    """

    missing_active_price_ids = active_product_ids - price_history_ids
    if missing_active_price_ids:
        raise RuntimeError(f"Active Asseto registry products are missing price history on chain {chain_id}: {', '.join(sorted(missing_active_price_ids))}")

    invalid_active_rows = [VaultSpec(product.chain_id, product.token).as_string_id() for product in runtime_products if product.token.lower() in active_product_ids and (rows[VaultSpec(product.chain_id, product.token)].get("NAV") is None or rows[VaultSpec(product.chain_id, product.token)].get("NAV") <= 0 or not is_stablecoin_like(rows[VaultSpec(product.chain_id, product.token)].get("Denomination")))]
    if invalid_active_rows:
        raise RuntimeError(f"Active Asseto products do not have positive USD-compatible live metadata: {', '.join(sorted(invalid_active_rows))}")


def resolve_asseto_denomination_symbol(product: AssetoOffchainProduct) -> str | None:
    """Resolve the currency in which Asseto publishes a product's NAV.

    Asseto omits ``tokenSymbol`` for its ``stoken`` products even though their
    product pages and NAV series use United States dollars. Treat this registry
    category as synthetic USD accounting, while retaining explicit symbols for
    UDA products such as USDC, USDT and HKD.

    :param product:
        Public Asseto registry entry.
    :return:
        Upper-case accounting denomination, or ``None`` if Asseto publishes
        neither a symbol nor a recognised product category.
    """

    if product.denomination_symbol:
        return product.denomination_symbol.upper()
    if product.product_type and product.product_type.casefold() == "stoken":
        return "USD"
    return None


def load_usd_exchange_rates(
    database_path: Path,
    denomination_symbols: Iterable[str | None],
) -> dict[str, tuple[tuple[int, Decimal], ...]]:
    """Load historical fiat conversion rates needed by selected products.

    Stored rates are units of quote currency per one USD. Asseto NAV values in
    a non-USD currency are consequently divided by the matching rate before
    they enter the shared USD-denominated cleaned history.

    :param database_path:
        Currency API DuckDB produced by ``scan-currencies``.
    :param denomination_symbols:
        Asseto accounting currencies selected for this run.
    :return:
        Rates keyed by upper-case quote currency, each ordered by UTC day.
    :raise RuntimeError:
        If a required database or currency history is missing.
    """

    required = {symbol.upper() for symbol in denomination_symbols if symbol and symbol.upper() not in ASSETO_USD_DENOMINATIONS}
    if not required:
        return {}
    if not database_path.exists():
        currencies = ", ".join(sorted(required))
        raise RuntimeError(f"Asseto products require {currencies}/USD history, but the currency database does not exist at {database_path}; run scan-currencies first")

    database = CurrencyRateDatabase(database_path)
    try:
        rates_df = database.get_rates_dataframe(base_currency="usd", source=SOURCE_NAME)
    finally:
        database.close()

    result: dict[str, tuple[tuple[int, Decimal], ...]] = {}
    for symbol in sorted(required):
        selected = rates_df.loc[rates_df["quote_currency"].str.casefold() == symbol.casefold()].sort_values("date")
        if selected.empty:
            raise RuntimeError(f"Asseto product history requires {symbol}/USD rates in {database_path}; run scan-currencies with QUOTE_CURRENCIES including {symbol.lower()}")
        result[symbol] = tuple(
            (
                int(datetime.datetime.combine(row.date, datetime.time.min, tzinfo=datetime.UTC).timestamp()),
                Decimal(str(row.rate)),
            )
            for row in selected.itertuples(index=False)
        )
    return result


def fetch_contract_deployment_block(web3: Web3, address: HexAddress, end_block: int) -> int:
    """Find the first block containing runtime code for an Asseto token.

    :param web3:
        Archive-capable connection for the product chain.
    :param address:
        Asseto ERC-20 token address.
    :param end_block:
        Highest block that may be checked.
    :return:
        First block containing contract code.
    :raise ValueError:
        If the public registry address has no contract code on this chain.
    """

    address = Web3.to_checksum_address(address)
    if not web3.eth.get_code(address, block_identifier=end_block):
        raise ValueError(f"No contract code for Asseto product {address} at block {end_block}")

    low = 0
    high = end_block
    while low < high:
        middle = (low + high) // 2
        if web3.eth.get_code(address, block_identifier=middle):
            high = middle
        else:
            low = middle + 1
    return low


def create_runtime_product(
    product: AssetoOffchainProduct,
    deployment_block: int,
    first_seen_at: datetime.datetime,
    usd_exchange_rates: tuple[tuple[int, Decimal], ...] = (),
) -> AssetoProduct:
    """Convert one public registry entry to a temporary scanner product.

    Public registry products do not all expose Asseto's request/claim manager
    and ``Pricer`` contracts. The generic adapter therefore uses their
    published daily NAV history, while preserving the exact token identity and
    deployment information read from the configured archive RPC.

    :param product:
        Asseto public EVM product entry.
    :param deployment_block:
        First token-code block found on-chain.
    :param first_seen_at:
        Naive UTC deployment timestamp.
    :param usd_exchange_rates:
        Historical units of the product denomination per USD. Empty for
        products already denominated in USD or a USD stablecoin.
    :return:
        Runtime Asseto adapter product metadata.
    """

    return AssetoProduct(
        chain_id=product.chain_id,
        token=product.contract_address,
        symbol=product.symbol or product.product_name,
        product_name=product.full_name or product.product_name,
        manager=None,
        pricer=None,
        collateral=product.denomination_address,
        first_seen_at_block=deployment_block,
        first_seen_at=first_seen_at,
        denomination_symbol=resolve_asseto_denomination_symbol(product),
        usd_exchange_rates=usd_exchange_rates,
        offchain_product_id=product.product_id,
        offchain_product_name=product.product_name,
        description=product.introduction,
    )


def backfill_chain(  # noqa: PLR0914 - explicit production pipeline state keeps the write path auditable.
    chain_id: int,
    products: list[AssetoOffchainProduct],
    *,
    dry_run: bool,
    scan_prices: bool,
    clean_prices: bool,
    frequency: Literal["1h", "1d"],
    vault_db: VaultDatabase,
    vault_db_path: Path,
    price_database_path: Path,
    cleaned_price_database_path: Path,
    reader_state_database_path: Path,
    token_cache: TokenDiskCache,
    usd_exchange_rates_by_symbol: dict[str, tuple[tuple[int, Decimal], ...]],
) -> dict[str, object]:
    """Backfill one Asseto EVM chain.

    Metadata and historical writes use the same shared database and scanner
    paths as the normal vault pipeline, but both are limited to selected Asseto
    identifiers.

    :param chain_id:
        EVM chain id.
    :param products:
        Selected Asseto products on this chain.
    :param dry_run:
        Whether filesystem/database writes are disabled.
    :param scan_prices:
        Whether to scan supply, NAV, and TVL history.
    :param clean_prices:
        Whether to replace selected cleaned price histories.
    :param frequency:
        Historical sampling interval.
    :param vault_db:
        In-memory metadata database.
    :param vault_db_path:
        Metadata database output path.
    :param price_database_path:
        Uncleaned historical price parquet path.
    :param cleaned_price_database_path:
        Cleaned historical price parquet path.
    :param reader_state_database_path:
        Reader-state pickle path.
    :param token_cache:
        Shared token metadata cache.
    :param usd_exchange_rates_by_symbol:
        Historical fiat conversion rates for non-USD product denominations.
    :return:
        Summary row for operator output.
    """

    rpc_env_var = get_asseto_rpc_env(chain_id)
    json_rpc_url = read_asseto_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(json_rpc_url)
    chain_name = get_chain_name(chain_id)
    logger.info("Backfilling %d Asseto products on %s using %s", len(products), chain_name, get_provider_name(web3.provider))

    end_block = parse_optional_int_env("END_BLOCK") or web3.eth.block_number
    runtime_products: list[AssetoProduct] = []
    for product in products:
        deployment_block = fetch_contract_deployment_block(web3, product.contract_address, end_block)
        timestamp = web3.eth.get_block(deployment_block)["timestamp"]
        first_seen_at = datetime.datetime.fromtimestamp(timestamp, tz=datetime.UTC).replace(tzinfo=None)
        denomination_symbol = resolve_asseto_denomination_symbol(product)
        exchange_rates = usd_exchange_rates_by_symbol.get(denomination_symbol or "", ())
        runtime_product = create_runtime_product(product, deployment_block, first_seen_at, exchange_rates)
        ASSETO_PRODUCTS[runtime_product.chain_id, runtime_product.token] = runtime_product
        runtime_products.append(runtime_product)

    leads = {product.token: create_asseto_lead(product) for product in runtime_products}
    rows = {
        VaultSpec(product.chain_id, product.token): create_vault_scan_record(
            web3,
            detection=create_asseto_detection(product),
            block_identifier=end_block,
            token_cache=token_cache,
        )
        for product in runtime_products
    }

    if not dry_run:
        vault_db_path.parent.mkdir(parents=True, exist_ok=True)
        vault_db.leads.update({VaultSpec(chain_id, address): lead for address, lead in leads.items()})
        vault_db._merge_rows(rows)
        vault_db.write(vault_db_path)

    scan_summary = "-"
    active_product_ids = resolve_active_asseto_product_ids(products, runtime_products, rows)
    cleanable_count = 0
    if scan_prices:
        vaults = build_vaults(web3, runtime_products, token_cache)
        vault_ids = {vault.address.lower() for vault in vaults}
        scanned_products = [product for product in runtime_products if product.token.lower() in vault_ids]
        validate_active_asseto_coverage(chain_id, active_product_ids, runtime_products, rows, vault_ids)
        cleanable_count = len(select_cleanable_vault_ids(scanned_products, rows))
        if dry_run:
            scan_summary = f"dry-run ({len(scanned_products)} products with price history)"
        elif vaults:
            reader_states = read_reader_states(reader_state_database_path)
            target_specs = {VaultSpec(chain_id, address) for address in vault_ids}
            reader_states = {spec: state for spec, state in reader_states.items() if spec not in target_specs}
            web3factory = MultiProviderWeb3Factory(json_rpc_url, retries=5)
            hypersync_config = configure_hypersync_from_env(web3)
            if hypersync_config.hypersync_client is None:
                message = "Asseto price backfill requires a HyperSync client for block timestamp reads"
                raise RuntimeError(message)
            scan_result = scan_historical_prices_to_parquet(
                output_fname=price_database_path,
                web3=web3,
                web3factory=web3factory,
                vaults=vaults,
                start_block=resolve_price_scan_start_block(scanned_products),
                end_block=end_block,
                max_workers=int(os.environ.get("MAX_WORKERS", "8")),
                chunk_size=32,
                token_cache=token_cache,
                frequency=frequency,
                # Asseto's public NAV is sampled daily. Its reader has no
                # on-chain state result with which to adapt polling, so use
                # the non-stateful reader. Sampled timestamps are fetched
                # through the cache-aware HyperSync API, never via RPC.
                reader_states=None,
                hypersync_client=hypersync_config.hypersync_client,
                vault_addresses=vault_ids,
            )
            # The non-stateful reader intentionally has no new Asseto state.
            # Preserve states for all other vaults while removing stale Asseto
            # entries from earlier runs.
            scan_result["reader_states"] = reader_states
            write_reader_states(reader_state_database_path, reader_states)
            scan_summary = pformat_scan_result(scan_result)
            if clean_prices:
                cleanable_ids = select_cleanable_vault_ids(scanned_products, rows)
                scanned_ids = {VaultSpec(product.chain_id, product.token).as_string_id() for product in scanned_products}
                skipped_ids = scanned_ids - cleanable_ids
                if skipped_ids:
                    logger.warning(
                        "Keeping raw history but skipping cleaned-history replacement for %d inactive or unsupported-denomination Asseto vaults: %s",
                        len(skipped_ids),
                        ", ".join(sorted(skipped_ids)),
                    )
                cleaned_rows = (
                    replace_cleaned_vault_histories(
                        cleanable_ids,
                        vault_db_path=vault_db_path,
                        raw_price_df_path=price_database_path,
                        cleaned_price_df_path=cleaned_price_database_path,
                        logger=logger.info,
                    )
                    if cleanable_ids
                    else 0
                )
                scan_summary = f"{scan_summary}; cleaned_rows={cleaned_rows:,}"

    return {
        "chain": chain_name,
        "chain_id": chain_id,
        "rpc": rpc_env_var,
        "products": ", ".join(product.symbol or product.product_name for product in products),
        "metadata_rows": len(rows),
        "active_products": len(active_product_ids),
        "cleanable_histories": cleanable_count,
        "scan": scan_summary,
    }


def main() -> None:  # noqa: PLR0914 - keep production path configuration explicit.
    """Run the targeted Asseto historical backfill."""

    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
        log_file=Path("logs/asseto-backfill-history.log"),
    )
    dry_run = parse_bool_env("DRY_RUN")
    scan_prices = parse_bool_env("ASSETO_SCAN_PRICES", default=True)
    clean_prices = parse_bool_env("ASSETO_CLEAN_PRICES", default=True)
    frequency = resolve_frequency()
    products = list(iter_selected_products())
    if not products:
        logger.warning("No eligible Asseto products selected; nothing to backfill")
        return

    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    price_database_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    cleaned_price_database_path = parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    reader_state_database_path = parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)
    exchange_rate_database_path = resolve_exchange_rate_database_path(vault_db_path.parent)
    denomination_symbols = [resolve_asseto_denomination_symbol(product) for product in products]
    usd_exchange_rates_by_symbol = load_usd_exchange_rates(exchange_rate_database_path, denomination_symbols)
    products_by_chain: dict[int, list[AssetoOffchainProduct]] = {}
    for product in products:
        products_by_chain.setdefault(product.chain_id, []).append(product)

    plan = [
        {
            "chain": get_chain_name(chain_id),
            "chain_id": chain_id,
            "rpc": get_asseto_rpc_env(chain_id),
            "products": ", ".join(product.symbol or product.product_name for product in chain_products),
        }
        for chain_id, chain_products in sorted(products_by_chain.items())
    ]
    logger.info("Asseto backfill plan\n%s", tabulate(plan, headers="keys", tablefmt="github"))
    logger.info("Vault DB: %s", vault_db_path)
    logger.info("Price DB: %s", price_database_path)
    logger.info("Cleaned price DB: %s", cleaned_price_database_path)
    logger.info("Reader states: %s", reader_state_database_path)
    logger.info("Exchange rates: %s", exchange_rate_database_path)
    logger.info("Frequency: %s", frequency)
    logger.info("Dry run: %s", dry_run)
    logger.info("Update cleaned prices: %s", clean_prices)

    vault_db = read_vault_database(vault_db_path)
    token_cache = TokenDiskCache()
    summaries = [
        backfill_chain(
            chain_id,
            chain_products,
            dry_run=dry_run,
            scan_prices=scan_prices,
            clean_prices=clean_prices,
            frequency=frequency,
            vault_db=vault_db,
            vault_db_path=vault_db_path,
            price_database_path=price_database_path,
            cleaned_price_database_path=cleaned_price_database_path,
            reader_state_database_path=reader_state_database_path,
            token_cache=token_cache,
            usd_exchange_rates_by_symbol=usd_exchange_rates_by_symbol,
        )
        for chain_id, chain_products in sorted(products_by_chain.items())
    ]
    if not dry_run:
        token_cache.commit()

    logger.info("Asseto backfill summary\n%s", tabulate(summaries, headers="keys", tablefmt="github"))
    logger.info("All ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logger.exception("Fatal error: %s", error, exc_info=error)
        sys.exit(1)
