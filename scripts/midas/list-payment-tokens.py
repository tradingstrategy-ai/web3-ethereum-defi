"""List Midas issuance-vault payment tokens.

This manual script reads ``getPaymentTokens()`` from every Midas product that
the shared Midas vault adapter supports and displays the full on-chain list.
The first payment token is the token exported as the scanner denomination;
when the list is empty, the adapter falls back to off-chain USD.

Configuration is through environment variables:

``NETWORKS``
    Optional comma-separated Midas network keys, e.g. ``main,base``.

``PRODUCTS``
    Optional comma-separated product symbols, e.g. ``mTBILL,mBASIS``.

``MAX_PRODUCTS``
    Optional cap for debugging.

``REQUIRE_ALL_SUCCESS``
    If ``true``, exit with an error if any selected payment-token read fails.

``LOG_LEVEL``
    Python logging level. Defaults to ``info``.

Example:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/midas/list-payment-tokens.py
    source .local-test.env && NETWORKS=main PRODUCTS=carryTradeUSDTRYLeverage poetry run python scripts/midas/list-payment-tokens.py

The script does not write files.
"""

import logging
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass

from tabulate import tabulate
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3Exception

from eth_defi.midas.registry import MidasRegistryProduct, iter_midas_registry_products
from eth_defi.midas.vault import MidasVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails
from eth_defi.vault.base import VaultSpec

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class PaymentTokenRead:
    """Payment-token read result for one Midas product."""

    #: Midas product registry entry.
    product: MidasRegistryProduct

    #: Token details in the vault's contract-returned order.
    payment_tokens: list[TokenDetails]

    #: Error message when the contract read failed.
    error: str | None


def parse_csv_env(name: str) -> set[str] | None:
    """Parse a comma-separated environment variable.

    :param name:
        Environment variable name.
    :return:
        Lower-case values, or ``None`` if unset.
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
        ``True`` for common truthy values.
    """

    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y"}


def setup_logging() -> None:
    """Configure console logging."""

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def iter_products() -> Iterator[MidasRegistryProduct]:
    """Iterate requested Midas products with issuance vaults and local RPCs.

    :return:
        Products filtered by ``NETWORKS``, ``PRODUCTS`` and ``MAX_PRODUCTS``.
    """

    networks = parse_csv_env("NETWORKS")
    products = parse_csv_env("PRODUCTS")
    max_products = int(os.environ.get("MAX_PRODUCTS", "0") or "0")
    yielded = 0

    for product in iter_midas_registry_products(require_adapter_data=True):
        if networks and product.network.lower() not in networks:
            continue
        if products and product.symbol.lower() not in products:
            continue
        if product.deposit_vault is None:
            logger.info("Skipping %s %s: no issuance vault", product.network, product.symbol)
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


def fetch_payment_tokens(web3: Web3, product: MidasRegistryProduct) -> list[TokenDetails]:
    """Fetch payment tokens through the Midas vault adapter.

    :param web3:
        Connected Web3 provider for the product network.
    :param product:
        Registry product with an issuance vault address.
    :return:
        Payment-token details in contract-returned order.
    """

    assert product.token is not None
    vault = MidasVault(web3, VaultSpec(chain_id=product.chain_id, vault_address=product.token))
    return vault.fetch_payment_tokens()


def fetch_product_read(web3: Web3, product: MidasRegistryProduct) -> PaymentTokenRead:
    """Fetch payment tokens for one Midas product without aborting the report.

    :param web3:
        Connected Web3 provider for the product network.
    :param product:
        Registry product to inspect.
    :return:
        Successful token read or an error row.
    """

    try:
        return PaymentTokenRead(product=product, payment_tokens=fetch_payment_tokens(web3, product), error=None)
    except (BadFunctionCallOutput, ContractLogicError, ValueError, Web3Exception) as e:
        return PaymentTokenRead(product=product, payment_tokens=[], error=str(e).splitlines()[0])


def create_rows(reads: list[PaymentTokenRead]) -> list[dict[str, object]]:
    """Create printable rows for payment-token reads.

    :param reads:
        Per-product payment-token reads.
    :return:
        Tabulate-friendly row dictionaries.
    """

    rows: list[dict[str, object]] = []
    for read in reads:
        tokens = ", ".join(f"{token.symbol or '<unknown>'} ({token.address})" for token in read.payment_tokens)
        first_token = read.payment_tokens[0].symbol if read.payment_tokens else "USD (off-chain)"
        rows.append(
            {
                "network": read.product.network,
                "chain": read.product.chain_id,
                "product": read.product.symbol,
                "issuance_vault": read.product.deposit_vault,
                "denomination": first_token,
                "payment_tokens": tokens or "-",
                "status": "ok" if read.error is None else "error",
                "error": read.error or "",
            }
        )
    return rows


def main() -> None:
    """Read and display payment tokens for all selectable Midas vaults."""

    setup_logging()
    web3s: dict[str, Web3] = {}
    reads: list[PaymentTokenRead] = []

    for product in iter_products():
        assert product.rpc_env_var is not None
        web3 = web3s.get(product.rpc_env_var)
        if web3 is None:
            web3 = create_multi_provider_web3(os.environ[product.rpc_env_var], retries=2, hint="Midas payment-token report")
            web3s[product.rpc_env_var] = web3

        reads.append(fetch_product_read(web3, product))

    rows = create_rows(reads)
    print(tabulate(rows, headers="keys", tablefmt="rounded_outline"))

    errors = [read for read in reads if read.error is not None]
    if errors and parse_bool_env("REQUIRE_ALL_SUCCESS"):
        raise RuntimeError(f"{len(errors)} Midas payment-token reads failed")


if __name__ == "__main__":
    main()
