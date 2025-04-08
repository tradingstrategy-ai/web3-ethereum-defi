"""Test ERC-4626: scan historical vault prices."""

import os
from pathlib import Path

import pytest

from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.ipor.vault import IPORVault
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.morpho.vault import MorphoVault
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import scan_historical_prices_to_parquet


JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")
JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None or JSON_RPC_ETHEREUM is None, reason="JSON_RPC_BASE and JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope='module')
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    return web3


@pytest.fixture(scope='module')
def web3_ethereum() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    return web3


def test_4626_historical_prices(
    web3: Web3,
    web3_ethereum: Web3,
    tmp_path: Path,
):
    """Read historical prices of Base and Ethereum chain vaults to the same Parquet file"""

    import pyarrow.parquet as pq

    max_workers = 8

    token_cache = TokenDiskCache(tmp_path / "token-cache.sqlite")

    # https://app.ipor.io/fusion/base/0x45aa96f0b3188d47a1dafdbefce1db6b37f58216
    ipor_usdc = IPORVault(web3, VaultSpec(web3.eth.chain_id, "0x45aa96f0b3188d47a1dafdbefce1db6b37f58216"), token_cache=token_cache)

    # https://app.morpho.org/base/vault/0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca/moonwell-flagship-usdc
    moonwell_flagship_usdc = MorphoVault(web3, VaultSpec(web3.eth.chain_id, "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca"), token_cache=token_cache)

    # https://www.superform.xyz/vault/Dgrw4wBA1YgfvI2BxA8YN/
    steakhouse_susds = ERC4626Vault(web3, VaultSpec(web3.eth.chain_id, "0xB17B070A56043e1a5a1AB7443AfAFDEbcc1168D7"), token_cache=token_cache)

    vaults = [
        ipor_usdc,
        moonwell_flagship_usdc,
        steakhouse_susds,
    ]

    # When IPOR vault was deployed https://basescan.org/tx/0x65e66f1b8648a880ade22e316d8394ed4feddab6fc0fc5bbc3e7128e994e84bf
    start = 22_140_976
    end = 23_000_000

    for v in vaults:
        v.first_seen_at_block = start

    web3_factory = MultiProviderWeb3Factory(JSON_RPC_BASE)

    output_fname = tmp_path / 'price-history.parquet'

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
    )

    assert output_fname.exists(), f"Did not create: {output_fname}"
    assert scan_result["existing"] is False
    assert scan_result["rows_written"] == 60
    assert scan_result["rows_deleted"] == 0

    table = pq.read_table(output_fname)
    chain_column = table["chain"].to_pylist()
    assert all(c == 8453 for c in chain_column)

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
    )
    assert output_fname.exists(), f"Did not create: {output_fname}"
    assert scan_result["existing"] is True
    assert scan_result["rows_written"] == 60
    assert scan_result["rows_deleted"] == 60

    # https://app.lagoon.finance/vault/1/0x03D1eC0D01b659b89a87eAbb56e4AF5Cb6e14BFc
    lagoon_vault = LagoonVault(web3_ethereum, VaultSpec(web3_ethereum.eth.chain_id, "0x03D1eC0D01b659b89a87eAbb56e4AF5Cb6e14BFc"))
    vaults = [
        lagoon_vault,
    ]
    start = 21_137_231
    end = 22_000_000
    web3_factory = MultiProviderWeb3Factory(JSON_RPC_ETHEREUM)
    lagoon_vault.first_seen_at_block = start

    #
    # Scan another chan
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
    )

    assert scan_result["existing"] is True
    assert scan_result["rows_written"] == 120
    assert scan_result["rows_deleted"] == 0

    table = pq.read_table(output_fname)
    assert table.num_rows == 60 + 120
