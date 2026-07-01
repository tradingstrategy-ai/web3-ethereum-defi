"""Initial Mellow vault mapping scan using Hypersync.

This script discovers Mellow Core Vault instances from their factory
``Created(address,uint256,address,bytes)`` events and enriches the results
with light on-chain metadata and public Mellow API USD TVL diagnostics.

Usage:

.. code-block:: shell

    source .local-test.env
    LOG_LEVEL=info poetry run python scripts/mellow/scan-initial-mellow.py

Optional environment variables:

``CHAINS``
    Comma-separated chain aliases to scan. Defaults to
    ``ethereum,arbitrum,plasma,monad,base``.

``MELLOW_ETHEREUM_VAULT_FACTORY`` / ``MELLOW_ARBITRUM_VAULT_FACTORY`` /
``MELLOW_PLASMA_VAULT_FACTORY`` / ``MELLOW_MONAD_VAULT_FACTORY`` /
``MELLOW_BASE_VAULT_FACTORY``
    Override the vault factory address for a chain. Mellow's public Core
    deployments page currently documents Mainnet, Plasma, Arbitrum and Monad
    Core vault factories, but not a Base Core vault factory.

``FETCH_MELLOW_API``
    Set to ``false`` to skip the public API USD TVL enrichment.

``START_BLOCK`` / ``END_BLOCK`` / ``BLOCK_RANGE``
    Restrict the factory scan block range. If ``BLOCK_RANGE`` is set without
    ``START_BLOCK``, the scanner uses ``END_BLOCK - BLOCK_RANGE``. The manual
    integration test path uses ``BLOCK_RANGE=1000000``.

``MELLOW_TEST_LEADS`` / ``MELLOW_TEST_METADATA`` / ``MELLOW_TEST_PRICES``
    Enable explicit manual-test sections in the tabulate output.

``SKIP_WRITE``
    Accepted for parity with pipeline manual tests. This script is read-only.

``HYPERSYNC_API_KEY``
    Optional Envio Hypersync API key.
"""

import asyncio
import datetime
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import hypersync
from eth_abi.exceptions import DecodingError
from eth_typing import HexAddress
from hypersync import BlockField, LogField
from tabulate import tabulate
from web3 import Web3
from web3.contract.contract import Contract, ContractFunction
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.hypersync.session import (
    create_throttled_hypersync_client,
    get_hypersync_concurrency_from_env,
    get_hypersync_rpm_from_env,
    open_hypersync_stream,
)
from eth_defi.mellow.discovery import decode_mellow_created_event, fetch_mellow_created_event_topic, fetch_mellow_factories_for_chain
from eth_defi.mellow.offchain_metadata import MellowApiVaultMetadata, fetch_mellow_api_vaults
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

VAULT_ABI = [
    {
        "inputs": [],
        "name": "shareManager",
        "outputs": [{"internalType": "contract IShareManager", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getAssetCount",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "index", "type": "uint256"}],
        "name": "assetAt",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getQueueCount",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
        "name": "getQueueCount",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "asset", "type": "address"},
            {"internalType": "uint256", "name": "index", "type": "uint256"},
        ],
        "name": "queueAt",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

ERC20_ABI = [
    {
        "inputs": [],
        "name": "name",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass(slots=True, frozen=True)
class ChainConfig:
    """Configuration for a chain scanned by this script.

    Each chain is configured through repository-standard JSON-RPC environment
    variables. The factory address is the Mellow Core Vault factory, if known.

    :param alias:
        Environment-facing chain name used in ``CHAINS`` and factory overrides.

    :param chain_id:
        EVM chain id expected from the JSON-RPC connection.

    :param rpc_env_var:
        JSON-RPC environment variable name.

    :param vault_factory:
        Mellow Core Vault factory address to scan with Hypersync.
    """

    alias: str
    chain_id: int
    rpc_env_var: str
    vault_factory: HexAddress | None


@dataclass(slots=True)
class CoreVaultLead:
    """A Mellow Core Vault instance discovered from a factory event.

    :param chain_id:
        EVM chain id.

    :param chain:
        Human-readable chain alias.

    :param factory:
        Factory contract that emitted the creation event.

    :param vault:
        Created vault proxy address.

    :param version:
        Factory implementation version used for deployment.

    :param owner:
        Owner address passed to the factory.

    :param block_number:
        Block number of the creation event.

    :param timestamp:
        Block timestamp of the creation event.

    :param transaction_hash:
        Transaction hash of the creation event.

    :param init_params_size:
        ABI-encoded init parameter length in bytes.
    """

    chain_id: int
    chain: str
    factory: HexAddress
    vault: HexAddress
    version: int
    owner: HexAddress
    block_number: int
    timestamp: datetime.datetime | None
    transaction_hash: str
    init_params_size: int


@dataclass(slots=True)
class CoreVaultRow:
    """A discovered Core Vault row for tabular reporting.

    :param lead:
        The Hypersync-discovered vault lead.

    :param share_manager:
        Share manager address returned by the vault.

    :param share_symbol:
        ERC-20 symbol of the share manager, if tokenised.

    :param share_name:
        ERC-20 name of the share manager, if tokenised.

    :param total_supply:
        ERC-20 total supply converted with token decimals.

    :param asset_count:
        Number of assets registered in the vault's ShareModule.

    :param queue_count:
        Total number of queues registered in the vault's ShareModule.

    :param asset_symbols:
        Registered asset token symbols.

    :param api_vault:
        Matching public Mellow API entry, when present.
    """

    lead: CoreVaultLead
    share_manager: HexAddress | None
    share_symbol: str | None
    share_name: str | None
    total_supply: Decimal | None
    asset_count: int | None
    queue_count: int | None
    asset_symbols: tuple[str, ...]
    api_vault: MellowApiVaultMetadata | None


def env_bool(name: str, default: bool) -> bool:  # noqa: FBT001
    """Read a boolean environment variable.

    Accepts common true/false spellings. Empty values fall back to the
    provided default.

    :param name:
        Environment variable name.

    :param default:
        Default value when the variable is unset.

    :return:
        Parsed boolean value.
    """

    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def env_int(name: str) -> int | None:
    """Read an integer environment variable.

    :param name:
        Environment variable name.

    :return:
        Parsed integer or ``None``.
    """

    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return int(value.replace("_", ""))


def resolve_scan_range(chain_alias: str, default_end_block: int) -> tuple[int, int]:
    """Resolve block range from environment.

    :param chain_alias:
        Chain alias used in diagnostics.

    :param default_end_block:
        Latest block available from RPC/Hypersync.

    :return:
        Start and end block.
    """

    end_block = env_int("END_BLOCK") or default_end_block
    block_range = env_int("BLOCK_RANGE")
    start_block = env_int("START_BLOCK")

    if start_block is None:
        if block_range is not None:
            start_block = max(0, end_block - block_range)
        else:
            start_block = 0

    if start_block >= end_block:
        raise ValueError(f"Invalid {chain_alias} scan range: START_BLOCK={start_block:,}, END_BLOCK={end_block:,}")

    return start_block, end_block


def get_created_event_topic() -> str:
    """Return the Mellow factory ``Created`` event topic.

    The event is documented by Mellow Core Vault docs:
    https://docs.mellow.finance/core-vaults/architecture/factory

    :return:
        Hex topic0 for ``Created(address,uint256,address,bytes)``.
    """

    return fetch_mellow_created_event_topic()


def get_chain_configs() -> dict[str, ChainConfig]:
    """Build supported chain configuration from environment variables.

    Mainnet, Plasma, Arbitrum and Monad defaults come from Mellow's Core
    deployments page:
    https://docs.mellow.finance/core-vaults/core-deployments

    :return:
        Mapping of lower-case chain alias to configuration.
    """

    mainnet_factories = fetch_mellow_factories_for_chain(1)
    plasma_factories = fetch_mellow_factories_for_chain(9745)
    arbitrum_factories = fetch_mellow_factories_for_chain(42161)
    monad_factories = fetch_mellow_factories_for_chain(143)
    base_factories = fetch_mellow_factories_for_chain(8453)

    return {
        "ethereum": ChainConfig(
            alias="ethereum",
            chain_id=1,
            rpc_env_var="JSON_RPC_ETHEREUM",
            vault_factory=mainnet_factories[0] if mainnet_factories else None,
        ),
        "plasma": ChainConfig(
            alias="plasma",
            chain_id=9745,
            rpc_env_var="JSON_RPC_PLASMA",
            vault_factory=plasma_factories[0] if plasma_factories else None,
        ),
        "arbitrum": ChainConfig(
            alias="arbitrum",
            chain_id=42161,
            rpc_env_var="JSON_RPC_ARBITRUM",
            vault_factory=arbitrum_factories[0] if arbitrum_factories else None,
        ),
        "monad": ChainConfig(
            alias="monad",
            chain_id=143,
            rpc_env_var="JSON_RPC_MONAD",
            vault_factory=monad_factories[0] if monad_factories else None,
        ),
        "base": ChainConfig(
            alias="base",
            chain_id=8453,
            rpc_env_var="JSON_RPC_BASE",
            vault_factory=base_factories[0] if base_factories else None,
        ),
    }


def iter_selected_chains(configs: dict[str, ChainConfig]) -> Iterator[ChainConfig]:
    """Yield chain configurations selected by ``CHAINS``.

    :param configs:
        All supported chain configurations.

    :return:
        Iterator of selected chain configurations.

    :raise ValueError:
        Raised when ``CHAINS`` contains an unknown alias.
    """

    chain_aliases = [part.strip().lower() for part in os.environ.get("CHAINS", "ethereum,arbitrum,plasma,monad,base").split(",") if part.strip()]
    for alias in chain_aliases:
        if alias not in configs:
            raise ValueError(f"Unknown chain alias in CHAINS: {alias}. Supported: {', '.join(configs)}")
        yield configs[alias]


def create_hypersync_client(chain_id: int) -> hypersync.HypersyncClient:
    """Create a throttled Hypersync client for a chain.

    Handles both ``bearer_token`` and ``api_token`` constructor spellings used
    by different Hypersync Python releases.

    :param chain_id:
        EVM chain id.

    :return:
        Hypersync client.
    """

    hypersync_url = get_hypersync_server(chain_id)
    api_key = os.environ.get("HYPERSYNC_API_KEY")
    config_candidates: list[dict[str, Any]]
    if api_key:
        config_candidates = [
            {"url": hypersync_url, "bearer_token": api_key},
            {"url": hypersync_url, "api_token": api_key},
        ]
    else:
        config_candidates = [{"url": hypersync_url}]

    last_error: TypeError | None = None
    for config_kwargs in config_candidates:
        try:
            config = hypersync.ClientConfig(**config_kwargs)
            break
        except TypeError as e:
            last_error = e
    else:
        raise TypeError(f"Could not initialise Hypersync ClientConfig for chain {chain_id}: {last_error}") from last_error

    return create_throttled_hypersync_client(
        config,
        requests_per_minute=get_hypersync_rpm_from_env(),
        concurrency=get_hypersync_concurrency_from_env(),
    )


def decode_hypersync_int(value: int | str | None) -> int:
    """Decode an integer returned by Hypersync.

    Hypersync fields may be returned as Python integers or hex strings,
    depending on the wheel version and selected field.

    :param value:
        Hypersync integer-like value.

    :return:
        Decoded integer.
    """

    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if value.startswith("0x"):
        return int(value, 16)
    return int(value)


def decode_created_event(web3: Web3, log: Any) -> tuple[HexAddress, int, HexAddress, bytes]:
    """Decode a Mellow factory ``Created`` event.

    Mellow documents the event arguments as non-indexed, but this decoder also
    supports an indexed variant to make the scanner tolerant of older factory
    implementations.

    :param web3:
        Web3 instance whose ABI codec is used for decoding.

    :param log:
        Hypersync log object.

    :return:
        Vault instance, factory version, owner, and raw ``initParams``.

    :raise DecodingError:
        Raised when log data cannot be decoded.
    """

    return decode_mellow_created_event(web3, log)


async def fetch_hypersync_height(client: hypersync.HypersyncClient) -> int | None:
    """Fetch the latest block height available from Hypersync.

    :param client:
        Hypersync client.

    :return:
        Latest Hypersync block height, or ``None`` if the server does not
        expose it cleanly.
    """

    try:
        return decode_hypersync_int(await client.get_height())
    except RuntimeError as e:
        logger.warning("Could not fetch Hypersync height: %s", e)
        return None


async def fetch_created_events(
    web3: Web3,
    client: hypersync.HypersyncClient,
    chain: ChainConfig,
    start_block: int,
    end_block: int,
) -> list[CoreVaultLead]:
    """Fetch Mellow Core Vault factory creation events with Hypersync.

    :param web3:
        Web3 connection for ABI decoding.

    :param client:
        Hypersync client.

    :param chain:
        Chain configuration.

    :param start_block:
        Inclusive start block.

    :param end_block:
        Exclusive end block for Hypersync query.

    :return:
        List of Core Vault leads discovered from the factory.
    """

    assert chain.vault_factory is not None

    query = hypersync.Query(
        from_block=start_block,
        to_block=end_block,
        logs=[
            hypersync.LogSelection(
                address=[chain.vault_factory],
                topics=[[get_created_event_topic()]],
            )
        ],
        field_selection=hypersync.FieldSelection(
            block=[BlockField.NUMBER, BlockField.TIMESTAMP],
            log=[
                LogField.BLOCK_NUMBER,
                LogField.LOG_INDEX,
                LogField.ADDRESS,
                LogField.TRANSACTION_HASH,
                LogField.TOPIC0,
                LogField.TOPIC1,
                LogField.TOPIC2,
                LogField.TOPIC3,
                LogField.DATA,
            ],
        ),
    )

    receiver = await open_hypersync_stream(client, query)
    leads: list[CoreVaultLead] = []
    seen_keys: set[tuple[int, str]] = set()

    while True:
        result = await asyncio.wait_for(receiver.recv(), timeout=90.0)
        if result is None:
            break

        block_timestamps = {decode_hypersync_int(block.number): native_datetime_utc_fromtimestamp(decode_hypersync_int(block.timestamp)) for block in result.data.blocks or [] if block.number is not None and block.timestamp is not None}

        for log in result.data.logs or []:
            try:
                vault, version, owner, init_params = decode_created_event(web3, log)
            except (DecodingError, ValueError) as e:
                logger.warning(
                    "Could not decode Mellow Created event on %s tx %s: %s",
                    chain.alias,
                    log.transaction_hash,
                    e,
                )
                continue

            key = (chain.chain_id, vault.lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)

            block_number = decode_hypersync_int(log.block_number)
            leads.append(
                CoreVaultLead(
                    chain_id=chain.chain_id,
                    chain=chain.alias,
                    factory=chain.vault_factory,
                    vault=vault,
                    version=version,
                    owner=owner,
                    block_number=block_number,
                    timestamp=block_timestamps.get(block_number),
                    transaction_hash=log.transaction_hash,
                    init_params_size=len(init_params),
                )
            )

    return sorted(leads, key=lambda lead: (lead.block_number, lead.vault.lower()))


def call_contract_function(function: ContractFunction) -> Any | None:
    """Call a contract function and return ``None`` if the method is absent.

    :param function:
        Bound Web3 contract function.

    :return:
        Function return value, or ``None`` for missing/reverting view calls.
    """

    try:
        return function.call()
    except (BadFunctionCallOutput, ContractLogicError, ValueError) as e:
        logger.debug("Contract call failed: %s", e)
        return None


def fetch_erc20_metadata(web3: Web3, token_address: HexAddress) -> tuple[str | None, str | None, int | None, int | None]:
    """Fetch basic ERC-20 metadata.

    :param web3:
        Web3 connection.

    :param token_address:
        ERC-20 token address.

    :return:
        ``name``, ``symbol``, ``decimals`` and ``totalSupply``.
    """

    if token_address == ZERO_ADDRESS:
        return None, None, None, None

    token = web3.eth.contract(address=token_address, abi=ERC20_ABI)
    name = call_contract_function(token.functions.name())
    symbol = call_contract_function(token.functions.symbol())
    decimals = call_contract_function(token.functions.decimals())
    total_supply = call_contract_function(token.functions.totalSupply())

    return (
        str(name) if name is not None else None,
        str(symbol) if symbol is not None else None,
        int(decimals) if decimals is not None else None,
        int(total_supply) if total_supply is not None else None,
    )


def humanise_token_amount(raw_amount: int | None, decimals: int | None) -> Decimal | None:
    """Convert a raw token amount to decimals.

    :param raw_amount:
        Raw ERC-20 amount.

    :param decimals:
        ERC-20 decimals.

    :return:
        Decimal human amount.
    """

    if raw_amount is None or decimals is None:
        return None
    return Decimal(raw_amount) / Decimal(10**decimals)


def fetch_core_vault_row(web3: Web3, lead: CoreVaultLead, api_vaults: dict[tuple[int, str], MellowApiVaultMetadata]) -> CoreVaultRow:  # noqa: PLR0914
    """Fetch light on-chain metadata for a discovered Mellow Core Vault.

    :param web3:
        Web3 connection.

    :param lead:
        Hypersync-discovered vault lead.

    :param api_vaults:
        API enrichment mapping.

    :return:
        Tabular row model.
    """

    vault: Contract = web3.eth.contract(address=lead.vault, abi=VAULT_ABI)

    share_manager_raw = call_contract_function(vault.functions.shareManager())
    share_manager = HexAddress(Web3.to_checksum_address(share_manager_raw)) if share_manager_raw else None

    share_name: str | None = None
    share_symbol: str | None = None
    total_supply: Decimal | None = None
    if share_manager:
        share_name, share_symbol, share_decimals, raw_total_supply = fetch_erc20_metadata(web3, share_manager)
        total_supply = humanise_token_amount(raw_total_supply, share_decimals)

    asset_count_raw = call_contract_function(vault.functions.getAssetCount())
    asset_count = int(asset_count_raw) if asset_count_raw is not None else None

    queue_count_raw = call_contract_function(vault.functions.getQueueCount())
    queue_count = int(queue_count_raw) if queue_count_raw is not None else None

    asset_symbols: list[str] = []
    if asset_count is not None:
        for index in range(asset_count):
            asset_raw = call_contract_function(vault.functions.assetAt(index))
            if not asset_raw:
                continue
            asset = HexAddress(Web3.to_checksum_address(asset_raw))
            _, symbol, _, _ = fetch_erc20_metadata(web3, asset)
            asset_symbols.append(symbol or asset)

    api_vault = api_vaults.get((lead.chain_id, lead.vault.lower()))

    return CoreVaultRow(
        lead=lead,
        share_manager=share_manager,
        share_symbol=share_symbol,
        share_name=share_name,
        total_supply=total_supply,
        asset_count=asset_count,
        queue_count=queue_count,
        asset_symbols=tuple(asset_symbols),
        api_vault=api_vault,
    )


def format_money(value: Decimal | None) -> str:
    """Format USD money for table output.

    :param value:
        Decimal USD value.

    :return:
        Human-readable string.
    """

    if value is None:
        return "n/a"
    return f"${value:,.2f}"


def format_decimal(value: Decimal | None) -> str:
    """Format a token amount for table output.

    :param value:
        Decimal token amount.

    :return:
        Human-readable string.
    """

    if value is None:
        return "n/a"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def build_api_summary(api_vaults: dict[tuple[int, str], MellowApiVaultMetadata], selected_chain_ids: set[int]) -> list[dict[str, Any]]:
    """Build summary rows from the public Mellow API.

    :param api_vaults:
        API vault mapping.

    :param selected_chain_ids:
        Chain ids included in this scan.

    :return:
        Summary rows for ``tabulate``.
    """

    selected_vaults = [vault for vault in api_vaults.values() if vault.chain_id in selected_chain_ids]
    rows: list[dict[str, Any]] = []

    for label, predicate in (
        ("Mellow API catalogue", lambda _: True),
        ("Mellow API layer=mellow", lambda vault: vault.layer == "mellow"),
    ):
        matching = [vault for vault in selected_vaults if predicate(vault)]
        rows.append(
            {
                "Scope": label,
                "Vaults": len(matching),
                "API USD TVL": format_money(sum((vault.tvl_usd or Decimal(0) for vault in matching), Decimal(0))),
            }
        )

    return rows


def build_core_summary(rows: list[CoreVaultRow]) -> list[dict[str, Any]]:
    """Build summary rows from Hypersync Core Vault discovery.

    :param rows:
        Enriched Core Vault rows.

    :return:
        Summary rows for ``tabulate``.
    """

    api_matched = [row for row in rows if row.api_vault is not None]
    api_tvl = sum((row.api_vault.tvl_usd or Decimal(0) for row in api_matched if row.api_vault), Decimal(0))
    return [
        {
            "Scope": "Hypersync Core factory events",
            "Vaults": len(rows),
            "API USD TVL": format_money(api_tvl) if api_matched else "n/a",
        },
        {
            "Scope": "Hypersync Core events matched to API",
            "Vaults": len(api_matched),
            "API USD TVL": format_money(api_tvl),
        },
    ]


def render_core_rows(rows: list[CoreVaultRow]) -> str:
    """Render discovered Core Vaults as a table.

    :param rows:
        Enriched Core Vault rows.

    :return:
        ``tabulate`` output.
    """

    table = []
    for row in rows:
        api_vault = row.api_vault
        table.append(
            {
                "Chain": row.lead.chain,
                "Vault": row.lead.vault,
                "Share": row.share_symbol or (api_vault.symbol if api_vault else "n/a"),
                "Name": row.share_name or (api_vault.name if api_vault else "n/a"),
                "Version": row.lead.version,
                "Assets": ", ".join(row.asset_symbols) or "n/a",
                "Queues": row.queue_count if row.queue_count is not None else "n/a",
                "Supply": format_decimal(row.total_supply),
                "API layer": api_vault.layer if api_vault else "n/a",
                "API USD TVL": format_money(api_vault.tvl_usd if api_vault else None),
                "Created": row.lead.timestamp.strftime("%Y-%m-%d") if row.lead.timestamp else row.lead.block_number,
            }
        )

    return tabulate(table, headers="keys", tablefmt="simple")


def render_manual_price_rows(rows: list[CoreVaultRow], end_block: int) -> str:
    """Render manual price-scan sample rows.

    :param rows:
        Enriched Core Vault rows.

    :param end_block:
        Block used for metadata calls.

    :return:
        ``tabulate`` output.
    """

    table = []
    for row in rows:
        table.append(
            {
                "Chain": row.lead.chain,
                "Vault": row.lead.vault,
                "Block": end_block,
                "Supply": format_decimal(row.total_supply),
                "Share price": "see historical reader",
                "Total assets": "see historical reader",
                "API USD TVL": format_money(row.api_vault.tvl_usd if row.api_vault else None),
                "Error": "Manual mapping script does not run the historical multicall reader",
            }
        )

    return tabulate(table, headers="keys", tablefmt="simple")


def main() -> None:  # noqa: PLR0914
    """Run the initial Mellow scan.

    The scanner prints two tables: a summary table and the per-vault mapping
    table. It uses Hypersync for discovery and repository-standard JSON-RPC
    variables for metadata calls.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))

    configs = get_chain_configs()
    chains = list(iter_selected_chains(configs))
    selected_chain_ids = {chain.chain_id for chain in chains}

    fetch_api = env_bool("FETCH_MELLOW_API", True)
    api_vaults: dict[tuple[int, str], MellowApiVaultMetadata] = {}
    if fetch_api:
        logger.info("Fetching public Mellow API vault metadata")
        api_vaults = fetch_mellow_api_vaults()

    rows: list[CoreVaultRow] = []
    skipped_chains: list[str] = []
    last_end_block = 0

    for chain in chains:
        json_rpc_url = os.environ.get(chain.rpc_env_var)
        if not json_rpc_url:
            logger.warning("Skipping %s: %s is not set", chain.alias, chain.rpc_env_var)
            skipped_chains.append(chain.alias)
            continue

        if chain.vault_factory is None:
            logger.warning("Skipping %s Hypersync scan: Mellow Core Vault factory is not configured", chain.alias)
            skipped_chains.append(chain.alias)
            continue

        web3 = create_multi_provider_web3(json_rpc_url)
        rpc_chain_id = web3.eth.chain_id
        if rpc_chain_id != chain.chain_id:
            raise ValueError(f"{chain.rpc_env_var} points to chain id {rpc_chain_id}, expected {chain.chain_id}")

        client = create_hypersync_client(chain.chain_id)
        rpc_end_block = web3.eth.block_number
        hypersync_height = asyncio.run(fetch_hypersync_height(client))
        default_end_block = min(rpc_end_block, hypersync_height) if hypersync_height else rpc_end_block
        start_block, end_block = resolve_scan_range(chain.alias, default_end_block)
        last_end_block = max(last_end_block, end_block)

        logger.info(
            "Scanning %s factory %s from block %s to %s",
            chain.alias,
            chain.vault_factory,
            f"{start_block:,}",
            f"{end_block:,}",
        )
        leads = asyncio.run(
            fetch_created_events(
                web3=web3,
                client=client,
                chain=chain,
                start_block=start_block,
                end_block=end_block,
            )
        )

        logger.info("Discovered %d Mellow Core Vault leads on %s", len(leads), chain.alias)
        for lead in leads:
            rows.append(fetch_core_vault_row(web3, lead, api_vaults))

    summary_rows: list[dict[str, Any]] = []
    if api_vaults:
        summary_rows.extend(build_api_summary(api_vaults, selected_chain_ids))
    summary_rows.extend(build_core_summary(rows))

    print("Mellow vault summary")
    print(tabulate(summary_rows, headers="keys", tablefmt="simple"))

    if skipped_chains:
        print()
        print(f"Skipped Hypersync Core factory scan for: {', '.join(skipped_chains)}")

    print()
    print("Hypersync-discovered Mellow Core Vaults")
    print(render_core_rows(rows) if rows else "No Core Vault factory events found.")

    if env_bool("MELLOW_TEST_LEADS", False):
        print()
        print("Manual test: leads")
        print(render_core_rows(rows) if rows else "No leads in the selected block range.")

    if env_bool("MELLOW_TEST_METADATA", False):
        print()
        print("Manual test: metadata")
        print(render_core_rows(rows) if rows else "No metadata rows in the selected block range.")

    if env_bool("MELLOW_TEST_PRICES", False):
        print()
        print("Manual test: prices")
        print(render_manual_price_rows(rows, last_end_block) if rows else "No price rows in the selected block range.")


if __name__ == "__main__":
    main()
