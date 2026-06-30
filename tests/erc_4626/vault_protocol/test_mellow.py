"""Test Mellow Core Vault metadata."""

import os
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.mellow.vault import MellowVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

FORK_BLOCK = 25_431_636
LIDO_EARN_USD_VAULT = "0x014e6DA8F283C4aF65B2AA0f201438680A004452"
LIDO_EARN_USD_SHARE_MANAGER = "0x4Ce1ac8F43E0E5BD7A346A98aF777bF8fbeA1981"
SHARE_TOKEN_DECIMALS = 18
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
USDE = "0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork at a specific block for reproducibility.

    Lido Earn USD was created through the Mellow Core Vault factory before this
    block. The fixed block pins the component graph and share supply values.
    """

    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=FORK_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork) -> Web3:
    """Create Web3 connection to the Ethereum fork."""

    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


@flaky.flaky
def test_mellow_lido_earn_usd(web3: Web3) -> None:
    """Read Lido Earn USD Mellow vault metadata on a fixed Ethereum fork."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=LIDO_EARN_USD_VAULT,
    )

    assert isinstance(vault, MellowVault)
    assert vault.get_protocol_name() == "Mellow"
    assert vault.features == {ERC4626Feature.mellow_like}
    assert vault.address == LIDO_EARN_USD_VAULT
    assert vault.vault_address == LIDO_EARN_USD_VAULT

    assert vault.name == "Lido Earn USD"
    assert vault.symbol == "earnUSD"
    assert vault.share_manager_address == LIDO_EARN_USD_SHARE_MANAGER
    assert vault.fetch_share_token_address(FORK_BLOCK) == LIDO_EARN_USD_SHARE_MANAGER
    assert vault.fetch_total_supply(FORK_BLOCK) == Decimal("21518125.250450660540252819")

    share_token = vault.share_token
    assert share_token.address == LIDO_EARN_USD_SHARE_MANAGER
    assert share_token.name == "Lido Earn USD"
    assert share_token.symbol == "earnUSD"
    assert share_token.decimals == SHARE_TOKEN_DECIMALS

    assert vault.fetch_denomination_token_address() == USDC
    assert vault.fetch_assets() == [USDC, USDT, USDE]

    info = vault.fetch_info()
    assert info["vault"] == LIDO_EARN_USD_VAULT
    assert info["share_manager"] == LIDO_EARN_USD_SHARE_MANAGER
    assert info["fee_manager"] == "0x72fa23f40e08eB9E45953233b2Dd9665E347e8Dc"
    assert info["risk_manager"] == "0x7b1e06C46d4510277FC37a37bBeF65F3794fdDE4"
    assert info["oracle"] == "0x827044735c9708a2cf850e7Ea37EBa43bc786028"
    assert info["assets"] == [USDC, USDT, USDE]
    assert info["deposit_queues"] == {
        USDC: [
            "0xC75E7E73B25fEa8bB23EB55CC48BA55067b5be76",
            "0xf6AFAf6afcAe116dD37A779D50fE6c5fa6f8C8f5",
        ],
        USDT: [
            "0xEeC5041c47Cba1e31321AC6941Bf09Ad60645B73",
            "0x534d0bEb82C47cf703BFb9E959297658b65Ec8E9",
        ],
        USDE: [
            "0xeEc37568b01e0C4d5028501A49E024B475E2D7cA",
        ],
    }
    assert info["redeem_queues"] == {
        USDC: [
            "0x9e36A74FE278906a76e7615263e46a83fC40c47F",
        ],
        USDT: [],
        USDE: [],
    }
