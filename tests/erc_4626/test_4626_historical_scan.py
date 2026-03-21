"""Test ERC-4626: scan historical vault prices."""

import os
from pathlib import Path

import pytest

from web3 import Web3

from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoVault
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import scan_historical_prices_to_parquet


JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")
JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None or JSON_RPC_ETHEREUM is None, reason="JSON_RPC_BASE and JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE, hint="Base RPC")
    return web3


@pytest.fixture(scope="module")
def web3_ethereum() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM, hint="Ethereum RPC")
    return web3


def test_4626_historical_prices(
    web3: Web3,
    web3_ethereum: Web3,
    tmp_path: Path,
):
    """Read historical prices of Base and Ethereum chain vaults to the same Parquet file"""

    import pyarrow.parquet as pq

    max_workers = 4

    token_cache = TokenDiskCache(tmp_path / "token-cache.sqlite")
    timestamp_cache_path = tmp_path / "timestamp-cache"

    # https://app.morpho.org/base/vault/0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca/moonwell-flagship-usdc
    moonwell_flagship_usdc = MorphoVault(web3, VaultSpec(web3.eth.chain_id, "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca"), token_cache=token_cache)

    vaults = [
        moonwell_flagship_usdc,
    ]

    # Keep the scan window tight: this test only needs to exercise parquet write,
    # rescan replacement, and multi-chain append behaviour.
    start = 22_140_976
    end = 22_300_000

    for v in vaults:
        v.first_seen_at_block = start

    web3_factory = MultiProviderWeb3Factory(JSON_RPC_BASE)

    output_fname = tmp_path / "price-history.parquet"

    #
    # First scan
    #
    scan_result = scan_historical_prices_to_parquet(
        output_fname=output_fname,
        web3=web3,
        web3factory=web3_factory,
        vaults=vaults,
        start_block=start,
        end_block=end,
        token_cache=token_cache,
        max_workers=max_workers,
        timestamp_cache_file=timestamp_cache_path,
    )

    assert output_fname.exists(), f"Did not create: {output_fname}"
    assert scan_result["existing"] is False
    assert scan_result["rows_written"] > 0
    assert scan_result["rows_deleted"] == 0
    base_rows_written = scan_result["rows_written"]

    table = pq.read_table(output_fname)
    chain_column = table["chain"].to_pylist()
    assert all(c == 8453 for c in chain_column)

    # Verify new vault state columns exist in the Parquet schema
    column_names = table.column_names
    assert "max_deposit" in column_names
    assert "max_redeem" in column_names
    assert "deposits_open" in column_names
    assert "redemption_open" in column_names
    assert "trading" in column_names

    #
    # Rescan
    #
    scan_result = scan_historical_prices_to_parquet(
        output_fname=output_fname,
        web3=web3,
        web3factory=web3_factory,
        vaults=vaults,
        start_block=start,
        end_block=end,
        max_workers=max_workers,
        token_cache=token_cache,
        timestamp_cache_file=timestamp_cache_path,
    )
    assert output_fname.exists(), f"Did not create: {output_fname}"
    assert scan_result["existing"] is True
    assert scan_result["rows_written"] == base_rows_written
    assert scan_result["rows_deleted"] == base_rows_written

    # https://app.lagoon.finance/vault/1/0x03D1eC0D01b659b89a87eAbb56e4AF5Cb6e14BFc
    lagoon_vault = LagoonVault(web3_ethereum, VaultSpec(web3_ethereum.eth.chain_id, "0x03D1eC0D01b659b89a87eAbb56e4AF5Cb6e14BFc"))
    vaults = [
        lagoon_vault,
    ]
    start = 21_137_231
    end = 21_200_000
    web3_factory = MultiProviderWeb3Factory(JSON_RPC_ETHEREUM)
    lagoon_vault.first_seen_at_block = start

    #
    # Scan another chain
    #
    scan_result = scan_historical_prices_to_parquet(
        output_fname=output_fname,
        web3=web3_ethereum,
        web3factory=web3_factory,
        vaults=vaults,
        start_block=start,
        end_block=end,
        max_workers=max_workers,
        token_cache=token_cache,
        timestamp_cache_file=timestamp_cache_path,
    )

    assert scan_result["existing"] is True
    assert scan_result["rows_written"] > 0
    assert scan_result["rows_deleted"] == 0
    ethereum_rows_written = scan_result["rows_written"]

    table = pq.read_table(output_fname)
    assert table.num_rows == base_rows_written + ethereum_rows_written
