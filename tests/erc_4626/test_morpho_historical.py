"""Scan Morpho vault price data"""

import os
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.token import TokenDiskCache
from eth_defi.vault.historical import scan_historical_prices_to_parquet

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope='module')
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    return web3


def test_steakhouse_usdt(
    web3: Web3,
    tmp_path: Path,
):
    """Read historical data of Morpho vault.

    - Caused some data corruption
    """

    token_cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    parquet_file = tmp_path / "prices.parquet"

    # https://etherscan.io/address/0xbEef047a543E45807105E51A8BBEFCc5950fcfBa#code
    # https://app.morpho.org/ethereum/vault/0xbEef047a543E45807105E51A8BBEFCc5950fcfBa/steakhouse-usdt
    steakhouse_usdt = create_vault_instance(
        web3,
        address="0xbEef047a543E45807105E51A8BBEFCc5950fcfBa",
        features={ERC4626Feature.morpho_like},
        token_cache=token_cache
    )

    vaults = [
        steakhouse_usdt,
    ]

    start = 19_043_398
    end = 22_196_299

    steakhouse_usdt.first_seen_at_block = start

    scan_report = scan_historical_prices_to_parquet(
        output_fname=parquet_file,
        web3=web3,
        web3factory=MultiProviderWeb3Factory(JSON_RPC_ETHEREUM),
        vaults=vaults,
        start_block=start,
        end_block=end,
        step=24*3600 // 12,
        token_cache=token_cache,
    )
    assert scan_report["rows_written"] == 438

    df = pd.read_parquet(parquet_file)

    # Records are not guaranteed to be in specific order, so fix it here
    df = df.set_index("block_number").sort_index()

    r = df.iloc[-1]
    assert r.share_price == pytest.approx(1.077792700142924944038560077)
    assert r.management_fee == 0
    assert r.performance_fee == 0
    assert r.chain == 1
    assert 1_000_000 < r.total_assets < 100_000_000



@pytest.mark.skip(reason="No need to implement, the vault seems to read inception APY correctly")
def test_morpho_compounder(
    web3: Web3,
    tmp_path: Path,
):
    """Read historical data of Morpho vault.

    - Caused some data corruption
    """

    token_cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    parquet_file = tmp_path / "prices.parquet"

    # https://yearn.fi/vaults/1/0x0a4ea2bDe8496a878a7ca2772056a8e6fe3245c5
    compounder = create_vault_instance(
        web3,
        address="0xbEef047a543E45807105E51A8BBEFCc5950fcfBa",
        features={ERC4626Feature.morpho_like},
        token_cache=token_cache
    )

    vaults = [
        compounder,
    ]

    # When IPOR vault was deployed https://basescan.org/tx/0x65e66f1b8648a880ade22e316d8394ed4feddab6fc0fc5bbc3e7128e994e84bf
    start = 19_043_398
    end = 22_196_299

    steakhouse_usdt.first_seen_at_block = start

    scan_report = scan_historical_prices_to_parquet(
        output_fname=parquet_file,
        web3=web3,
        web3factory=MultiProviderWeb3Factory(JSON_RPC_ETHEREUM),
        vaults=vaults,
        start_block=start,
        end_block=end,
        step=24*3600 // 12,
        token_cache=token_cache,
    )
    assert scan_report["rows_written"] == 438

    df = pd.read_parquet(parquet_file)

    # Records are not guaranteed to be in specific order, so fix it here
    df = df.set_index("block_number").sort_index()

    r = df.iloc[-1]
    assert r.share_price == pytest.approx(1.077792700142924944038560077)
    assert r.management_fee == 0
    assert r.performance_fee == 0
    assert r.chain == 1
