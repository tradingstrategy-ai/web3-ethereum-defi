"""Scan Midas product token deployment blocks.

Manual script for refreshing :data:`eth_defi.midas.registry.MIDAS_PRODUCT_DEPLOYMENTS`.
The scanner uses archive-node ``eth_getCode`` binary search to find the first
block where each product token address has bytecode, then prints a tabulated
audit trail.

Configuration is through environment variables:

``NETWORKS``
    Optional comma-separated Midas network keys, e.g. ``main,base``.

``PRODUCTS``
    Optional comma-separated product symbols, e.g. ``mTBILL,mBASIS``.

``MAX_PRODUCTS``
    Optional cap for debugging.

``UPDATE_REGISTRY``
    Set to ``true`` to rewrite the deployment metadata block in
    ``eth_defi/midas/registry.py``.

``LOG_LEVEL``
    Python logging level. Defaults to ``info``.

Example:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/midas/scan-deployments.py
    source .local-test.env && UPDATE_REGISTRY=true poetry run python scripts/midas/scan-deployments.py

The script only scans products whose Midas registry entry exposes a token and
datafeed and whose chain has a configured local JSON-RPC environment variable.
"""

import datetime
import logging
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.midas.registry import MidasRegistryProduct, iter_midas_registry_products
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.multi_provider import create_multi_provider_web3

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ProductDeployment:
    """Midas product token deployment scan result."""

    #: Midas registry product entry.
    product: MidasRegistryProduct

    #: Product token address that was scanned.
    address: str

    #: First block where token bytecode exists.
    first_seen_at_block: int | None

    #: Timestamp of :py:attr:`first_seen_at_block` as naive UTC datetime.
    first_seen_at: datetime.datetime | None

    #: Error message if the scan failed.
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


def parse_bool_env(name: str) -> bool:
    """Parse a boolean environment variable.

    :param name:
        Environment variable name.
    :return:
        ``True`` for common truthy string values.
    """

    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y"}


def setup_logging() -> None:
    """Set up console logging."""

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def iter_products() -> Iterator[MidasRegistryProduct]:
    """Iterate requested Midas products.

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


def has_code_at_block(web3: Web3, address: str, block_number: int) -> bool:
    """Check whether an address has bytecode at a historical block.

    :param web3:
        Web3 connection.
    :param address:
        Contract address.
    :param block_number:
        Historical block number.
    :return:
        ``True`` if bytecode exists.
    """

    code = web3.eth.get_code(Web3.to_checksum_address(address), block_identifier=block_number)
    return len(code) > 0


def find_first_code_block(web3: Web3, address: str, latest_block: int) -> int:
    """Find the first block where an address has bytecode.

    :param web3:
        Archive Web3 connection.
    :param address:
        Contract address.
    :param latest_block:
        Upper bound for the binary search.
    :return:
        First bytecode block.
    :raises ValueError:
        If the address has no bytecode at ``latest_block``.
    """

    if not has_code_at_block(web3, address, latest_block):
        message = f"No bytecode at latest block {latest_block:,}"
        raise ValueError(message)

    low = 1
    high = latest_block
    while low < high:
        mid = (low + high) // 2
        if has_code_at_block(web3, address, mid):
            high = mid
        else:
            low = mid + 1

    return low


def fetch_deployment(web3: Web3, product: MidasRegistryProduct, latest_block: int) -> ProductDeployment:
    """Fetch deployment metadata for one Midas product.

    :param web3:
        Archive Web3 connection.
    :param product:
        Midas registry product entry.
    :param latest_block:
        Upper bound for the binary search.
    :return:
        Deployment scan result.
    """

    assert product.token is not None
    try:
        first_seen_at_block = find_first_code_block(web3, product.token, latest_block=latest_block)
        timestamp = web3.eth.get_block(first_seen_at_block)["timestamp"]
        first_seen_at = datetime.datetime.fromtimestamp(timestamp, tz=datetime.UTC).replace(tzinfo=None)
        return ProductDeployment(
            product=product,
            address=product.token,
            first_seen_at_block=first_seen_at_block,
            first_seen_at=first_seen_at,
            error=None,
        )
    except ValueError as e:
        return ProductDeployment(
            product=product,
            address=product.token,
            first_seen_at_block=None,
            first_seen_at=None,
            error=str(e).splitlines()[0],
        )


def create_rows(deployments: list[ProductDeployment]) -> list[dict[str, object]]:
    """Convert deployment results to tabulate rows.

    :param deployments:
        Deployment scan results.
    :return:
        Table rows.
    """

    rows: list[dict[str, object]] = []
    for deployment in deployments:
        rows.append(
            {
                "network": deployment.product.network,
                "chain": deployment.product.chain_id,
                "product": deployment.product.symbol,
                "address": deployment.address,
                "first_seen_at_block": deployment.first_seen_at_block if deployment.first_seen_at_block is not None else "-",
                "first_seen_at": deployment.first_seen_at.isoformat(sep=" ") if deployment.first_seen_at else "-",
                "status": "ok" if deployment.error is None else "error",
                "error": deployment.error or "",
            }
        )

    return rows


def format_registry_datetime(value: datetime.datetime) -> str:
    """Format a datetime for ``registry.py``.

    :param value:
        Naive UTC datetime.
    :return:
        Python source expression.
    """

    return f"datetime.datetime({value.year}, {value.month}, {value.day}, {value.hour}, {value.minute}, {value.second}, tzinfo=datetime.UTC).replace(tzinfo=None)"


def generate_registry_block(deployments: list[ProductDeployment]) -> str:
    """Generate the ``MIDAS_PRODUCT_DEPLOYMENTS`` source block.

    :param deployments:
        Successful deployment scan results.
    :return:
        Python source block.
    """

    lines = [
        "#: Scanned deployment blocks for Midas products.",
        "#:",
        "#: This table is maintained by live archive-node scans over the product token",
        "#: addresses, not by the upstream TypeScript registry. To refresh it, run:",
        "#:",
        "#: .. code-block:: shell",
        "#:",
        "#:    source .local-test.env && UPDATE_REGISTRY=true poetry run python scripts/midas/scan-deployments.py",
        "#:",
        "#: Keys are ``(Midas network key, product symbol)`` and values are",
        "#: ``(first bytecode block, first bytecode block timestamp)``. Timestamps are",
        "#: naive UTC datetimes.",
        "MIDAS_PRODUCT_DEPLOYMENTS: Final[dict[tuple[str, str], tuple[int, datetime.datetime]]] = {",
    ]

    for deployment in sorted(deployments, key=lambda item: (item.product.network, item.product.symbol)):
        if deployment.first_seen_at_block is None or deployment.first_seen_at is None:
            continue
        lines.append(f'    ("{deployment.product.network}", "{deployment.product.symbol}"): ({deployment.first_seen_at_block}, {format_registry_datetime(deployment.first_seen_at)}),')

    lines.append("}")
    return "\n".join(lines)


def update_registry(deployments: list[ProductDeployment]) -> None:
    """Rewrite deployment metadata in ``eth_defi/midas/registry.py``.

    :param deployments:
        Deployment scan results.
    """

    repo_root = Path(__file__).resolve().parents[2]
    registry_path = repo_root / "eth_defi" / "midas" / "registry.py"
    registry_source = registry_path.read_text()
    new_block = generate_registry_block(deployments)
    pattern = re.compile(
        r"#: Scanned deployment blocks for Midas products\.\n.*?(?=\n#: Non-product registry sections\.)",
        flags=re.DOTALL,
    )
    updated_source, replacement_count = pattern.subn(new_block + "\n", registry_source, count=1)
    if replacement_count != 1:
        message = f"Could not find MIDAS_PRODUCT_DEPLOYMENTS block in {registry_path}"
        raise RuntimeError(message)

    registry_path.write_text(updated_source)
    logger.info("Updated %s with %d deployment entries", registry_path, len([item for item in deployments if item.error is None]))


def main() -> None:
    """Run the Midas deployment scan."""

    setup_logging()

    products = list(iter_products())
    logger.info("Scanning deployment blocks for %d Midas products", len(products))

    products_by_network: dict[str, list[MidasRegistryProduct]] = {}
    for product in products:
        products_by_network.setdefault(product.network, []).append(product)

    deployments: list[ProductDeployment] = []
    deployment_cache: dict[tuple[int, str], ProductDeployment] = {}
    for network, network_products in sorted(products_by_network.items()):
        rpc_env_var = network_products[0].rpc_env_var
        assert rpc_env_var is not None
        rpc_url = os.environ[rpc_env_var]
        web3 = create_multi_provider_web3(rpc_url, retries=2, hint=f"Midas {network}")
        chain_id = web3.eth.chain_id
        latest_block = get_almost_latest_block_number(web3)
        logger.info("Scanning %s chain %d with %d products", network, chain_id, len(network_products))

        for product in tqdm(network_products, desc=f"{network} deployments"):
            if product.chain_id != chain_id:
                deployments.append(
                    ProductDeployment(
                        product=product,
                        address=product.token or "-",
                        first_seen_at_block=None,
                        first_seen_at=None,
                        error=f"registry chain id {product.chain_id} does not match RPC chain id {chain_id}",
                    )
                )
                continue

            assert product.token is not None
            cache_key = (product.chain_id, product.token.lower())
            cached = deployment_cache.get(cache_key)
            if cached:
                deployments.append(
                    ProductDeployment(
                        product=product,
                        address=product.token,
                        first_seen_at_block=cached.first_seen_at_block,
                        first_seen_at=cached.first_seen_at,
                        error=cached.error,
                    )
                )
                continue

            deployment = fetch_deployment(web3, product, latest_block=latest_block)
            deployment_cache[cache_key] = deployment
            deployments.append(deployment)

    print(tabulate(create_rows(deployments), headers="keys", tablefmt="simple", disable_numparse=True))

    if parse_bool_env("UPDATE_REGISTRY"):
        update_registry(deployments)


if __name__ == "__main__":
    main()
