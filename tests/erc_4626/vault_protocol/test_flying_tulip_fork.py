"""Fixed-block fork coverage for Flying Tulip's registry and Curve price reads."""

import os

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_FT_ETHEREUM, FLYING_TULIP_SFTUSD_BY_CHAIN
from eth_defi.erc_4626.vault_protocol.flying_tulip.reward_price import fetch_curve_pool_configuration, fetch_curve_reward_price
from eth_defi.erc_4626.vault_protocol.flying_tulip.vault import FlyingTulipVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test"),
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Return the shared fixed-block Ethereum fork for read-only ABI coverage.

    :param anvil_fork_pool:
        Session-scoped fixed-block Anvil fork registry.
    :return:
        Web3 client connected to the canonical Ethereum midnight fork.
    """

    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


def test_flying_tulip_registry_and_curve_price_reads_at_fixed_block(web3: Web3) -> None:
    """Verify the address registry and canonical Curve valuation at one fork block.

    :param web3:
        Shared fixed-block Ethereum fork.
    :return:
        ``None`` after checking the adapter and Curve oracle provenance reads.
    """

    vault = create_vault_instance_autodetect(web3, FLYING_TULIP_SFTUSD_BY_CHAIN[1])

    assert isinstance(vault, FlyingTulipVault)
    assert vault.fetch_reward_token_address(ETHEREUM_MIDNIGHT_BLOCK) == FLYING_TULIP_FT_ETHEREUM
    assert vault.reward_token.address == FLYING_TULIP_FT_ETHEREUM
    assert vault.fetch_share_price(ETHEREUM_MIDNIGHT_BLOCK) == 1
    assert vault.fetch_total_assets(ETHEREUM_MIDNIGHT_BLOCK) == vault.fetch_total_supply(ETHEREUM_MIDNIGHT_BLOCK)

    fetch_curve_pool_configuration(web3, ETHEREUM_MIDNIGHT_BLOCK)
    timestamp = web3.eth.get_block(ETHEREUM_MIDNIGHT_BLOCK)["timestamp"]
    price = fetch_curve_reward_price(web3, ETHEREUM_MIDNIGHT_BLOCK, timestamp)

    assert price.raw_oracle > 0
    assert price.oracle_updated_at <= timestamp
    assert price.raw_ft_price_in_ftusd > 0
