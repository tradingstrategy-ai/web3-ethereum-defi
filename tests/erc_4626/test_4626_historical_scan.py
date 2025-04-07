"""Test ERC-4626: scan historical vault prices."""

import os
from pathlib import Path

import pytest

from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.ipor.vault import IPORVault
from eth_defi.morpho.vault import MorphoVault
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import scan_historical_prices_to_parquet


JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests")


@pytest.fixture(scope='module')
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    return web3


def test_4626_historical_prices(
    web3: Web3,
    tmp_path: Path,
):
    """Read historical data of IPOR USDC and some other vaults."""

    # https://app.ipor.io/fusion/base/0x45aa96f0b3188d47a1dafdbefce1db6b37f58216
    ipor_usdc = IPORVault(web3, VaultSpec(web3.eth.chain_id, "0x45aa96f0b3188d47a1dafdbefce1db6b37f58216"))

    # https://app.morpho.org/base/vault/0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca/moonwell-flagship-usdc
    moonwell_flagship_usdc = MorphoVault(web3, VaultSpec(web3.eth.chain_id, "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca"))

    # https://www.superform.xyz/vault/Dgrw4wBA1YgfvI2BxA8YN/
    steakhouse_susds = ERC4626Vault(web3, VaultSpec(web3.eth.chain_id, "0xB17B070A56043e1a5a1AB7443AfAFDEbcc1168D7"))

    vaults = [
        ipor_usdc,
        moonwell_flagship_usdc,
        steakhouse_susds,
    ]

    # When IPOR vault was deployed https://basescan.org/tx/0x65e66f1b8648a880ade22e316d8394ed4feddab6fc0fc5bbc3e7128e994e84bf
    start = 22_140_976
    end = 27_000_000

    for v in vaults:
        v.first_seen_at_block = start

    web3_factory = MultiProviderWeb3Factory(JSON_RPC_BASE)

    output_fname = tmp_path / 'price-history.parquet'

    scan_result = scan_historical_prices_to_parquet(
        output_fname=output_fname,
        web3=web3,
        web3factory=web3_factory,
        vaults=vaults,
        start_block=start,
        end_block=end,
    )

    assert output_fname.exists(), f"Did not create: {output_fname}"
    assert scan_result["rows_written"] == 339



