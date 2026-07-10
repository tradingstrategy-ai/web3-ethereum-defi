"""Scan Midas product share price and TVL history.

Manual script for validating the Pythonised Midas registry against live
on-chain data. The script samples all registry products that expose both an
mToken and a Midas ``dataFeed`` contract, then prints tabulated daily history.

Configuration is through environment variables:

``DAYS``
    Number of trailing days to scan. Defaults to ``7``.

``NETWORKS``
    Optional comma-separated Midas network keys, e.g. ``main,base``.

``PRODUCTS``
    Optional comma-separated product symbols, e.g. ``mTBILL,mBASIS``.

``MAX_PRODUCTS``
    Optional cap for debugging.

``LOG_LEVEL``
    Python logging level. Defaults to ``info``.

Example:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/midas/scan-history.py
    source .local-test.env && NETWORKS=main PRODUCTS=mTBILL,mBASIS poetry run python scripts/midas/scan-history.py

The script intentionally does not write files. It is meant for manual
inspection and quick integration checks.
"""

import datetime
import logging
import os
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal

from tabulate import tabulate
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.chain import EVM_BLOCK_TIMES
from eth_defi.midas.registry import MidasRegistryProduct, iter_midas_registry_products
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.multi_provider import create_multi_provider_web3

logger = logging.getLogger(__name__)

DATA_FEED_ABI = [
    {
        "inputs": [],
        "name": "getDataInBase18",
        "outputs": [{"internalType": "uint256", "name": "answer", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

ERC20_ABI = [
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
class ProductRead:
    """Single Midas product sample row."""

    #: Midas registry product entry.
    product: MidasRegistryProduct

    #: Sample block.
    block_number: int

    #: Naive UTC block timestamp, if it could be read.
    timestamp: datetime.datetime | None

    #: NAV/share from ``getDataInBase18()``.
    share_price: Decimal | None

    #: mToken supply in human units.
    total_supply: Decimal | None

    #: Derived TVL as ``share_price * total_supply``.
    tvl: Decimal | None

    #: Error message if the sample failed.
    error: str | None


def parse_csv_env(name: str) -> set[str] | None:
    """Parse a comma-separated environment variable.

    :param name:
        Environment variable name.
    :return:
        Lowercase values or ``None`` when unset.
    """

    value = os.environ.get(name, "").strip()
    if not value:
        return None

    return {part.strip().lower() for part in value.split(",") if part.strip()}


def setup_logging() -> None:
    """Set up console logging."""

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def iter_products() -> Iterator[MidasRegistryProduct]:
    """Iterate requested scannable products.

    :return:
        Product entries filtered by ``NETWORKS``, ``PRODUCTS`` and
        ``MAX_PRODUCTS``.
    """

    networks = parse_csv_env("NETWORKS")
    products = parse_csv_env("PRODUCTS")
    max_products = int(os.environ.get("MAX_PRODUCTS", "0") or "0")
    yielded = 0

    for product in iter_midas_registry_products(require_historical_contracts=True):
        if networks and product.network.lower() not in networks:
            continue
        if products and product.symbol.lower() not in products:
            continue
        if product.rpc_env_var is None:
            logger.info("Skipping %s %s: no local RPC env mapping", product.network, product.symbol)
            continue
        if not os.environ.get(product.rpc_env_var):
            logger.info("Skipping %s %s: %s not set", product.network, product.symbol, product.rpc_env_var)
            continue

        yield product
        yielded += 1
        if max_products and yielded >= max_products:
            return


def get_sample_blocks(web3: Web3, chain_id: int, days: int) -> list[int]:
    """Build daily sample blocks for a chain.

    :param web3:
        Web3 connection.
    :param chain_id:
        EVM chain id.
    :param days:
        Number of trailing days.
    :return:
        Oldest-to-newest sample blocks, inclusive.
    """

    latest_block = get_almost_latest_block_number(web3)
    block_time = EVM_BLOCK_TIMES.get(chain_id, 12)
    blocks_per_day = int(datetime.timedelta(days=1).total_seconds() // block_time)

    return [max(1, latest_block - blocks_per_day * day) for day in range(days, -1, -1)]


def fetch_product_read(web3: Web3, product: MidasRegistryProduct, block_number: int, decimals: int) -> ProductRead:
    """Fetch one Midas product historical sample.

    :param web3:
        Web3 connection.
    :param product:
        Product to sample.
    :param block_number:
        Historical block.
    :param decimals:
        mToken decimals.
    :return:
        Historical read row.
    """

    assert product.token is not None
    assert product.data_feed is not None

    timestamp: datetime.datetime | None = None
    try:
        block = web3.eth.get_block(block_number)
        timestamp = datetime.datetime.fromtimestamp(block["timestamp"], tz=datetime.UTC).replace(tzinfo=None)

        token = web3.eth.contract(address=Web3.to_checksum_address(product.token), abi=ERC20_ABI)
        data_feed = web3.eth.contract(address=Web3.to_checksum_address(product.data_feed), abi=DATA_FEED_ABI)

        raw_supply = token.functions.totalSupply().call(block_identifier=block_number)
        raw_share_price = data_feed.functions.getDataInBase18().call(block_identifier=block_number)

        total_supply = Decimal(raw_supply) / Decimal(10**decimals)
        share_price = Decimal(raw_share_price) / Decimal(10**18)
        tvl = total_supply * share_price

        return ProductRead(
            product=product,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_supply=total_supply,
            tvl=tvl,
            error=None,
        )
    except (BadFunctionCallOutput, ContractLogicError, ValueError) as e:
        return ProductRead(
            product=product,
            block_number=block_number,
            timestamp=timestamp,
            share_price=None,
            total_supply=None,
            tvl=None,
            error=str(e).splitlines()[0],
        )


def fetch_token_decimals(web3: Web3, product: MidasRegistryProduct) -> int:
    """Fetch mToken decimals.

    :param web3:
        Web3 connection.
    :param product:
        Midas product.
    :return:
        ERC-20 decimals.
    """

    assert product.token is not None
    token = web3.eth.contract(address=Web3.to_checksum_address(product.token), abi=ERC20_ABI)
    return token.functions.decimals().call()


def format_decimal(value: Decimal | None, precision: int = 6) -> str:
    """Format decimal value for tabular output.

    :param value:
        Decimal value or ``None``.
    :param precision:
        Number of decimal places.
    :return:
        Human-readable string.
    """

    if value is None:
        return "-"

    return f"{value:,.{precision}f}"


def create_history_rows(reads: Iterable[ProductRead]) -> list[dict[str, object]]:
    """Convert reads to tabulate rows.

    :param reads:
        Product reads.
    :return:
        Table rows.
    """

    rows: list[dict[str, object]] = []
    for read in reads:
        rows.append(
            {
                "network": read.product.network,
                "chain": read.product.chain_id,
                "product": read.product.symbol,
                "block": read.block_number,
                "timestamp": read.timestamp.isoformat(sep=" ") if read.timestamp else "-",
                "share_price": format_decimal(read.share_price, precision=8),
                "total_supply": format_decimal(read.total_supply, precision=4),
                "tvl": format_decimal(read.tvl, precision=2),
                "status": "ok" if read.error is None else "error",
                "error": read.error or "",
            }
        )

    return rows


def create_summary_rows(reads: Iterable[ProductRead]) -> list[dict[str, object]]:
    """Create compact product summary rows.

    :param reads:
        Product reads.
    :return:
        Summary table rows.
    """

    latest_success: dict[tuple[str, str], ProductRead] = {}
    error_counts: dict[tuple[str, str], int] = {}
    sample_counts: dict[tuple[str, str], int] = {}

    for read in reads:
        key = (read.product.network, read.product.symbol)
        sample_counts[key] = sample_counts.get(key, 0) + 1
        if read.error is None:
            latest_success[key] = read
        else:
            error_counts[key] = error_counts.get(key, 0) + 1

    rows = []
    for key in sorted(sample_counts):
        read = latest_success.get(key)
        network, product = key
        rows.append(
            {
                "network": network,
                "product": product,
                "samples": sample_counts[key],
                "errors": error_counts.get(key, 0),
                "latest_share_price": format_decimal(read.share_price, precision=8) if read else "-",
                "latest_tvl": format_decimal(read.tvl, precision=2) if read else "-",
            }
        )

    return rows


def main() -> None:
    """Run the Midas history scan."""

    setup_logging()

    days = int(os.environ.get("DAYS", "7"))
    products = list(iter_products())
    logger.info("Scanning %d Midas products for %d days", len(products), days)

    products_by_network: dict[str, list[MidasRegistryProduct]] = {}
    for product in products:
        products_by_network.setdefault(product.network, []).append(product)

    reads: list[ProductRead] = []
    for network, network_products in sorted(products_by_network.items()):
        rpc_env_var = network_products[0].rpc_env_var
        assert rpc_env_var is not None
        rpc_url = os.environ[rpc_env_var]
        web3 = create_multi_provider_web3(rpc_url, retries=2, hint=f"Midas {network}")
        chain_id = web3.eth.chain_id
        sample_blocks = get_sample_blocks(web3, chain_id=chain_id, days=days)

        logger.info(
            "Scanning %s chain %d with %d products and %d samples",
            network,
            chain_id,
            len(network_products),
            len(sample_blocks),
        )

        for product in network_products:
            if product.chain_id != chain_id:
                logger.warning(
                    "Skipping %s %s: registry chain id %d does not match RPC chain id %d",
                    product.network,
                    product.symbol,
                    product.chain_id,
                    chain_id,
                )
                continue

            try:
                decimals = fetch_token_decimals(web3, product)
            except (BadFunctionCallOutput, ContractLogicError, ValueError) as e:
                logger.warning("Could not read decimals for %s %s: %s", product.network, product.symbol, e)
                for block_number in sample_blocks:
                    reads.append(
                        ProductRead(
                            product=product,
                            block_number=block_number,
                            timestamp=None,
                            share_price=None,
                            total_supply=None,
                            tvl=None,
                            error=f"decimals failed: {str(e).splitlines()[0]}",
                        )
                    )
                continue

            for block_number in sample_blocks:
                reads.append(fetch_product_read(web3, product, block_number=block_number, decimals=decimals))

    print(tabulate(create_history_rows(reads), headers="keys", tablefmt="simple", disable_numparse=True))
    print()
    print(tabulate(create_summary_rows(reads), headers="keys", tablefmt="simple", disable_numparse=True))


if __name__ == "__main__":
    main()
