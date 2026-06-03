#!/usr/bin/env python3
"""Scan Royco Vault Market wrappers and compare them to our vault database.

This helper reads Royco Vault Markets from their REST API, checks each wrapper
contract onchain where an RPC URL is configured, and compares the wrapper
address to the local vault metadata database.

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/scan-royco-vaults.py

Environment variables:

- ``ROYCO_API_KEY``: Royco API key, defaults to ``ROYCO_DEMO``.
- ``VAULT_DB``: Vault metadata pickle path, defaults to the production local path.
- ``MAX_WORKERS``: Thread count for onchain checks, defaults to ``8``.
- ``MAX_ROWS``: Maximum rows in the detailed table, defaults to ``100``.
- ``ACTIVE_VERIFIED_ONLY``: Set to ``true`` to show only active verified rows.
- ``LOG_LEVEL``: Console log level, defaults to ``warning``.

The script reads RPC URLs from ``JSON_RPC_{CHAIN}`` environment variables.
For Royco chains not yet present in :py:data:`eth_defi.chain.CHAIN_NAMES`,
the script has local env-var fallbacks such as ``JSON_RPC_CORN`` and
``JSON_RPC_PLUME``.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from eth_typing import HexAddress
from joblib import Parallel, delayed
from tabulate import tabulate
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3Exception

from eth_defi.chain import CHAIN_NAMES
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.env import get_json_rpc_env
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


ROYCO_MARKET_EXPLORE_URL = "https://api.royco.org/api/v1/market/explore"

#: Royco chains not yet covered by ``CHAIN_NAMES`` in this repository.
ROYCO_CHAIN_RPC_ENV_FALLBACKS = {
    21000000: "JSON_RPC_CORN",
    98866: "JSON_RPC_PLUME",
}

#: Human labels for known Royco chains, including chains not in ``CHAIN_NAMES``.
ROYCO_CHAIN_NAMES = {
    1: "Ethereum",
    146: "Sonic",
    999: "HyperEVM",
    8453: "Base",
    42161: "Arbitrum",
    80094: "Berachain",
    98866: "Plume",
    11155111: "Sepolia",
    21000000: "Corn",
}

ONCHAIN_ERRORS = (
    AssertionError,
    BadFunctionCallOutput,
    ContractLogicError,
    OSError,
    RuntimeError,
    ValueError,
    Web3Exception,
)


@dataclass(slots=True)
class RoycoMarket:
    """Royco Vault Market row.

    :param chain_id:
        EVM chain id.

    :param market_id:
        Royco market contract address. For ``market_type == 1`` this is the
        ERC-4626 wrapper address to scan.

    :param underlying_vault_address:
        Underlying vault wrapped by the Royco market.

    :param name:
        Royco display name.

    :param market_type:
        Royco market type. This script fetches ``1`` only.

    :param is_active:
        Whether the market is active.

    :param is_verified:
        Whether the market has been verified by Royco.

    :param tvl_usd:
        Royco API TVL in USD.

    :param raw:
        Original API row for ad hoc debugging.
    """

    chain_id: int
    market_id: HexAddress
    underlying_vault_address: HexAddress | None
    name: str
    market_type: int
    is_active: bool
    is_verified: bool
    tvl_usd: float
    raw: dict[str, Any]

    @property
    def spec(self) -> VaultSpec:
        """Vault database key for this Royco wrapper."""
        return VaultSpec(self.chain_id, self.market_id)

    @property
    def chain_name(self) -> str:
        """Human readable chain name."""
        return ROYCO_CHAIN_NAMES.get(self.chain_id, CHAIN_NAMES.get(self.chain_id, str(self.chain_id)))


@dataclass(slots=True)
class DatabaseStatus:
    """Vault database comparison result for a Royco market.

    :param state:
        ``row`` if the wrapper is in the metadata DB, ``lead`` if it is only a
        pending lead, otherwise ``missing``.

    :param protocol:
        Stored protocol name.

    :param name:
        Stored vault name.

    :param nav:
        Stored metadata NAV, if available.
    """

    state: str
    protocol: str
    name: str
    nav: Decimal | None


@dataclass(slots=True)
class OnchainStatus:
    """Onchain status for a Royco wrapper.

    :param status:
        ``ok`` when metadata was read, otherwise a concise reason.

    :param protocol:
        Detected protocol name.

    :param vault_class:
        Python vault class selected by classifier.

    :param features:
        Detected feature names.

    :param nav:
        Onchain NAV from ``fetch_nav()``.

    :param share_price:
        Current share price from ``fetch_share_price()``.

    :param error:
        Error text when status is not ``ok``.
    """

    status: str
    protocol: str = ""
    vault_class: str = ""
    features: str = ""
    nav: Decimal | None = None
    share_price: Decimal | None = None
    error: str = ""


def fetch_royco_page(api_key: str, page_index: int, page_size: int = 100) -> dict[str, Any]:
    """Fetch a single Royco Vault Market page.

    :param api_key:
        Royco API key.

    :param page_index:
        One-based page index.

    :param page_size:
        Number of results per page.

    :return:
        Royco API JSON response.
    """
    payload = {
        "filters": [
            {
                "id": "marketType",
                "value": 1,
                "condition": "eq",
            },
        ],
        "sorting": [
            {
                "id": "tvlUsd",
                "desc": True,
            },
        ],
        "page": {
            "index": page_index,
            "size": page_size,
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        # Older docs/examples used a bearer-style demo key. Supplying both is
        # harmless for Royco and makes the helper resilient across deployments.
        "Authorization": f"Bearer {api_key}",
    }
    request = urllib.request.Request(ROYCO_MARKET_EXPLORE_URL, data=body, headers=headers, method="POST")  # noqa: S310
    with urllib.request.urlopen(request, timeout=45) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def fetch_royco_vault_markets(api_key: str) -> list[RoycoMarket]:
    """Fetch all Royco Vault Market rows.

    Royco API pagination is one-based, so page ``1`` is the first page.

    :param api_key:
        Royco API key.

    :return:
        Royco Vault Market rows.
    """
    first_page = fetch_royco_page(api_key, page_index=1)
    rows = list(first_page["data"])
    total_pages = int(first_page["page"]["total"])

    for page_index in range(2, total_pages + 1):
        rows.extend(fetch_royco_page(api_key, page_index=page_index)["data"])

    markets = []
    for row in rows:
        market_id = row["marketId"].lower()
        underlying_vault_address = row.get("underlyingVaultAddress")
        markets.append(
            RoycoMarket(
                chain_id=int(row["chainId"]),
                market_id=market_id,
                underlying_vault_address=underlying_vault_address.lower() if underlying_vault_address else None,
                name=row.get("name") or "",
                market_type=int(row["marketType"]),
                is_active=bool(row.get("isActive")),
                is_verified=bool(row.get("isVerified")),
                tvl_usd=float(row.get("tvlUsd") or 0),
                raw=row,
            )
        )

    return markets


def get_rpc_env_for_chain(chain_id: int) -> str:
    """Resolve the JSON-RPC environment variable for a chain.

    :param chain_id:
        EVM chain id.

    :return:
        Environment variable name.
    """
    if chain_id in CHAIN_NAMES:
        return get_json_rpc_env(chain_id)
    return ROYCO_CHAIN_RPC_ENV_FALLBACKS.get(chain_id, f"JSON_RPC_CHAIN_{chain_id}")


def create_web3_connections(markets: list[RoycoMarket]) -> dict[int, Web3]:
    """Create Web3 connections for Royco market chains that have configured RPCs.

    :param markets:
        Royco markets to scan.

    :return:
        Chain id to Web3 connection mapping.
    """
    connections = {}
    for chain_id in sorted({market.chain_id for market in markets}):
        env_var = get_rpc_env_for_chain(chain_id)
        json_rpc_url = os.environ.get(env_var)
        if not json_rpc_url:
            logger.info("No %s configured, skipping onchain checks for chain %s", env_var, chain_id)
            continue
        connections[chain_id] = create_multi_provider_web3(json_rpc_url)
    return connections


def load_vault_database(path: Path) -> VaultDatabase | None:
    """Load the vault metadata database if it exists.

    :param path:
        Vault metadata pickle path.

    :return:
        Vault database or ``None`` if the file is absent.
    """
    if not path.exists():
        logger.warning("Vault database %s does not exist; database comparison will be empty", path)
        return None
    return VaultDatabase.read(path)


def get_database_status(vault_db: VaultDatabase | None, market: RoycoMarket) -> DatabaseStatus:
    """Compare a Royco wrapper to our vault metadata database.

    :param vault_db:
        Vault metadata database.

    :param market:
        Royco market.

    :return:
        Database status.
    """
    if vault_db is None:
        return DatabaseStatus("no-db", "", "", None)

    row = vault_db.rows.get(market.spec)
    if row:
        return DatabaseStatus(
            state="row",
            protocol=str(row.get("Protocol") or ""),
            name=str(row.get("Name") or ""),
            nav=row.get("NAV"),
        )

    lead = vault_db.leads.get(market.spec)
    if lead:
        return DatabaseStatus("lead", "", "", None)

    return DatabaseStatus("missing", "", "", None)


def fetch_onchain_metadata(market: RoycoMarket, web3_by_chain: dict[int, Web3]) -> OnchainStatus:
    """Fetch onchain status for a Royco wrapper.

    :param market:
        Royco market to inspect.

    :param web3_by_chain:
        Chain id to Web3 mapping.

    :return:
        Onchain status.
    """
    web3 = web3_by_chain.get(market.chain_id)
    if web3 is None:
        return OnchainStatus(status="no-rpc", error=get_rpc_env_for_chain(market.chain_id))

    try:
        checksum_address = Web3.to_checksum_address(market.market_id)
        features = detect_vault_features(web3, checksum_address, verbose=False)
        vault = create_vault_instance(web3, checksum_address, features)
        feature_names = ",".join(sorted(feature.value for feature in features))

        if vault is None:
            if ERC4626Feature.broken in features:
                return OnchainStatus(status="broken", features=feature_names)
            return OnchainStatus(status="unsupported", features=feature_names)

        return OnchainStatus(
            status="ok",
            protocol=vault.get_protocol_name(),
            vault_class=vault.__class__.__name__,
            features=feature_names,
            nav=vault.fetch_nav(),
            share_price=vault.fetch_share_price("latest"),
        )
    except ONCHAIN_ERRORS as e:
        return OnchainStatus(status=type(e).__name__, error=str(e)[:160])


def format_money(value: float | Decimal | None) -> str:
    """Format a USD-like value for tabular output."""
    if value is None:
        return "-"
    return f"${float(value):,.2f}"


def format_decimal(value: Decimal | None) -> str:
    """Format a decimal value for tabular output."""
    if value is None:
        return "-"
    return f"{float(value):,.6g}"


def format_protocol(value: str) -> str:
    """Format protocol names compactly for terminal tables."""
    if not value or value == "<protocol not yet identified>":
        return "unknown"
    return value


def format_market_state(market: RoycoMarket) -> str:
    """Format active/verified flags into one compact column."""
    active = "A" if market.is_active else "-"
    verified = "V" if market.is_verified else "-"
    return f"{active}/{verified}"


def build_chain_summary(
    markets: list[RoycoMarket],
    db_status_by_id: dict[str, DatabaseStatus],
    onchain_status_by_id: dict[str, OnchainStatus],
) -> list[list[Any]]:
    """Build the per-chain summary table.

    :param markets:
        Royco markets.

    :param db_status_by_id:
        Database status keyed by vault id.

    :param onchain_status_by_id:
        Onchain status keyed by vault id.

    :return:
        Table rows.
    """
    rows = []
    for chain_id in sorted({market.chain_id for market in markets}):
        chain_markets = [market for market in markets if market.chain_id == chain_id]
        rows.append(
            [
                chain_id,
                chain_markets[0].chain_name,
                len(chain_markets),
                sum(1 for market in chain_markets if market.is_active),
                sum(1 for market in chain_markets if market.is_verified),
                sum(1 for market in chain_markets if market.tvl_usd > 0),
                sum(1 for market in chain_markets if db_status_by_id[market.spec.as_string_id()].state == "row"),
                sum(1 for market in chain_markets if db_status_by_id[market.spec.as_string_id()].state == "lead"),
                sum(1 for market in chain_markets if db_status_by_id[market.spec.as_string_id()].state == "missing"),
                sum(1 for market in chain_markets if onchain_status_by_id[market.spec.as_string_id()].status == "ok"),
                sum(1 for market in chain_markets if ERC4626Feature.royco_like.value in onchain_status_by_id[market.spec.as_string_id()].features),
                format_money(sum(market.tvl_usd for market in chain_markets)),
            ]
        )
    return rows


def build_detail_rows(
    markets: list[RoycoMarket],
    db_status_by_id: dict[str, DatabaseStatus],
    onchain_status_by_id: dict[str, OnchainStatus],
    *,
    active_verified_only: bool,
    max_rows: int,
) -> list[list[Any]]:
    """Build detailed per-vault rows.

    :param markets:
        Royco markets.

    :param db_status_by_id:
        Database status keyed by vault id.

    :param onchain_status_by_id:
        Onchain status keyed by vault id.

    :param active_verified_only:
        Show only active and verified markets.

    :param max_rows:
        Maximum number of rows.

    :return:
        Table rows.
    """
    filtered = markets
    if active_verified_only:
        filtered = [market for market in markets if market.is_active and market.is_verified]

    filtered = sorted(
        filtered,
        key=lambda market: (
            db_status_by_id[market.spec.as_string_id()].state != "missing",
            not market.is_active,
            not market.is_verified,
            -market.tvl_usd,
            market.chain_id,
            market.market_id,
        ),
    )

    rows = []
    for market in filtered[:max_rows]:
        vault_id = market.spec.as_string_id()
        db_status = db_status_by_id[vault_id]
        onchain_status = onchain_status_by_id[vault_id]
        rows.append(
            [
                market.chain_name,
                market.market_id,
                format_market_state(market),
                format_money(market.tvl_usd),
                db_status.state,
                format_protocol(db_status.protocol),
                onchain_status.status,
                format_protocol(onchain_status.protocol),
                format_decimal(onchain_status.nav),
                market.name[:48],
            ]
        )
    return rows


def main() -> None:
    """Run the Royco vault scanner helper."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning").upper())

    api_key = os.environ.get("ROYCO_API_KEY", "ROYCO_DEMO")
    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    max_rows = int(os.environ.get("MAX_ROWS", "100"))
    active_verified_only = os.environ.get("ACTIVE_VERIFIED_ONLY", "false").lower() == "true"
    vault_db_path = Path(os.environ.get("VAULT_DB", str(DEFAULT_VAULT_DATABASE))).expanduser()

    try:
        markets = fetch_royco_vault_markets(api_key)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Royco API request failed with HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Royco API request failed: {e}") from e

    logger.info("Fetched %d Royco Vault Market rows", len(markets))

    vault_db = load_vault_database(vault_db_path)
    web3_by_chain = create_web3_connections(markets)

    db_status_by_id = {market.spec.as_string_id(): get_database_status(vault_db, market) for market in markets}

    onchain_results = Parallel(n_jobs=max_workers, backend="threading")(delayed(fetch_onchain_metadata)(market, web3_by_chain) for market in markets)
    onchain_status_by_id = {market.spec.as_string_id(): status for market, status in zip(markets, onchain_results)}

    active_verified_missing = [market for market in markets if market.is_active and market.is_verified and db_status_by_id[market.spec.as_string_id()].state == "missing"]

    print()
    print(f"Royco Vault Market rows fetched: {len(markets):,}")
    print(f"Vault DB: {vault_db_path}")
    if vault_db is not None:
        print(f"Vault DB rows: {len(vault_db.rows):,}; leads: {len(vault_db.leads):,}")
    print(f"Onchain RPC chains checked: {', '.join(str(chain_id) for chain_id in sorted(web3_by_chain)) or '-'}")
    print(f"Missing active verified Royco wrappers: {len(active_verified_missing):,} ({format_money(sum(market.tvl_usd for market in active_verified_missing))})")

    print()
    print("Per-chain summary")
    print(
        tabulate(
            build_chain_summary(markets, db_status_by_id, onchain_status_by_id),
            headers=[
                "chain_id",
                "chain",
                "api rows",
                "active",
                "verified",
                "+tvl",
                "db rows",
                "db leads",
                "missing",
                "onchain ok",
                "royco-like",
                "api tvl",
            ],
            tablefmt="github",
        )
    )

    print()
    print("Detailed Royco wrapper comparison")
    print(
        tabulate(
            build_detail_rows(
                markets,
                db_status_by_id,
                onchain_status_by_id,
                active_verified_only=active_verified_only,
                max_rows=max_rows,
            ),
            headers=[
                "chain",
                "wrapper",
                "state",
                "api tvl",
                "db",
                "db protocol",
                "onchain",
                "onchain protocol",
                "onchain nav",
                "name",
            ],
            tablefmt="github",
        )
    )


if __name__ == "__main__":
    main()
