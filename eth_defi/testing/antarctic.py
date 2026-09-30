"""Recorded transport fixtures for Antarctic integration tests.

Recorded manager events and RPC results exercise the real adapter, decoder,
context writer and publication pipeline without contacting external services.
Live provider tests complement this deterministic transport.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from web3 import Web3
from web3.providers import BaseProvider

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.antarctic.constants import ANTARCTIC_DEPLOYMENTS
from eth_defi.erc_4626.vault_protocol.antarctic.historical_context import ANTARCTIC_ADD_TOPIC, ANTARCTIC_REMOVE_TOPIC, AntarcticSettlement
from eth_defi.erc_4626.vault_protocol.antarctic.vault import AntarcticVault
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import VaultDatabase


class RecordedAntarcticProvider(BaseProvider):
    """Serve fixed-state RPC responses recorded from Arbitrum at block 510301932."""

    def __init__(self, path: Path) -> None:
        """Load exact contract call replies.

        :param path: Recorded address/calldata-to-result mapping.
        :return: None.
        """
        super().__init__()
        self.responses = json.loads(path.read_text())

    def make_request(self, method: str, params: Any) -> dict:
        """Return recorded JSON-RPC transport data, rejecting unknown calls.

        :param method: RPC method name.
        :param params: Web3 request parameters.
        :return: JSON-RPC envelope for chain identity or fixed contract state.
        """
        if method == "eth_chainId":
            value = hex(42161)
        elif method == "eth_blockNumber":
            value = hex(510301932)
        elif method == "eth_call":
            call = params[0]
            value = self.responses[call["to"].lower() + ":" + call["data"].lower()]
        elif method == "eth_getBlockByNumber":
            value = {"number": hex(510301932), "timestamp": hex(1790759411), "hash": "0x" + "42" * 32, "gasLimit": hex(30_000_000), "transactions": []}
        else:
            raise AssertionError(f"Unexpected Antarctic test RPC method {method}")
        return {"jsonrpc": "2.0", "id": 1, "result": value}


class RecordedAntarcticStream:
    """One complete Hypersync response with real recorded source bytes."""

    def __init__(self, query: object, records: list[AntarcticSettlement]) -> None:
        """Filter a recorded response using the real collector's query.

        :param query: Hypersync selection built by the integration.
        :param records: Verified settlement fixtures.
        :return: None.
        """
        self.query = query
        self.records = records
        self.sent = False

    async def recv(self) -> object | None:
        """Yield a complete source response once, then signal stream completion.

        :return: Hypersync-compatible response or end of stream.
        """
        if self.sent:
            return None
        self.sent = True
        managers = {d.address: d.manager for d in ANTARCTIC_DEPLOYMENTS}
        addresses = self.query.logs[0].address
        selected = [r for r in self.records if self.query.from_block <= r.block_number < self.query.to_block and managers[r.pool_address] in addresses]
        logs = [SimpleNamespace(address=managers[r.pool_address], block_number=r.block_number, block_hash=r.block_hash, transaction_hash=r.transaction_hash, transaction_index=0, log_index=r.log_index, data=r.data, topics=[ANTARCTIC_ADD_TOPIC if r.kind == "AddLiquidity" else ANTARCTIC_REMOVE_TOPIC, None, None, None]) for r in selected]
        blocks = {r.block_number: SimpleNamespace(number=r.block_number, hash=r.block_hash, timestamp=r.block_timestamp) for r in selected}
        return SimpleNamespace(next_block=self.query.to_block, data=SimpleNamespace(logs=logs, blocks=list(blocks.values())))

    def close(self) -> None:
        """Release the recorded transport stream.

        :return: None.
        """


def load_antarctic_settlements(path: Path) -> list[AntarcticSettlement]:
    """Load immutable real-event fixture records.

    :param path: Recorded decoded/raw Hypersync source observations.
    :return: Small reusable test fixture.
    """
    return [AntarcticSettlement(**row) for row in json.loads(path.read_text())]


def create_antarctic_test_metadata(web3: Web3, token_cache: TokenDiskCache) -> VaultDatabase:
    """Build both metadata records through the production adapter factory.

    :param web3: Recorded or live Arbitrum transport.
    :param token_cache: Isolated token metadata cache.
    :return: Database containing both reviewed product rows.
    """
    database = VaultDatabase()
    for deployment in ANTARCTIC_DEPLOYMENTS:
        detection = ERC4262VaultDetection(chain=42161, address=deployment.address, first_seen_at_block=deployment.deployment_block, first_seen_at=deployment.deployed_at, features={ERC4626Feature.antarctic_like, ERC4626Feature.share_price_equivalence}, updated_at=deployment.deployed_at, deposit_count=0, redeem_count=0)
        row = create_vault_scan_record(web3, detection, 510301932, token_cache)
        assert row["Protocol"] == "Antarctic" and not row["Name"].startswith("<broken:")
        database.rows[VaultSpec(42161, deployment.address)] = row
    return database


def write_antarctic_test_prices(web3: Web3, token_cache: TokenDiskCache, directory: Path, start_block: int, end_block: int) -> dict:
    """Run the actual common contextual writer with isolated files.

    :param web3: Recorded or live Arbitrum connection.
    :param token_cache: Isolated token cache.
    :param directory: Temporary pipeline root, containing prefetched context.
    :param start_block: Inclusive price boundary.
    :param end_block: Exclusive price boundary.
    :return: Common writer result.
    """
    vaults = []
    for deployment in ANTARCTIC_DEPLOYMENTS:
        vault = AntarcticVault(web3, VaultSpec(42161, deployment.address), token_cache=token_cache)
        vault.first_seen_at_block = deployment.deployment_block
        vault.historical_context_path = directory / "vault-historical-context.duckdb"
        vaults.append(vault)
    return scan_historical_prices_to_parquet(output_fname=directory / "vault-prices-1h.parquet", web3=web3, web3factory=lambda: web3, vaults=vaults, token_cache=token_cache, start_block=start_block, end_block=end_block, frequency="1h", max_workers=1, timestamp_cache_file=directory / "block-timestamp", vault_addresses={d.address for d in ANTARCTIC_DEPLOYMENTS})
