"""Test Mellow Core Vault metadata."""

import datetime
import os
from decimal import Decimal
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.mellow.vault import MellowVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.vault.fee import VaultFeeMode

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

FORK_BLOCK = 25_431_636
SHARE_PRICE_START_BLOCK = 25_000_000
SHARE_PRICE_END_BLOCK = 25_100_000
LIDO_EARN_USD_VAULT = "0x014e6DA8F283C4aF65B2AA0f201438680A004452"
LIDO_EARN_USD_SHARE_MANAGER = "0x4Ce1ac8F43E0E5BD7A346A98aF777bF8fbeA1981"
LIDO_EARN_USD_FEE_MANAGER = "0x72fa23f40e08eB9E45953233b2Dd9665E347e8Dc"
LIDO_EARN_USD_FEE_RECIPIENT = "0xcCf2daba8Bb04a232a2fDA0D01010D4EF6C69B85"
SHARE_TOKEN_DECIMALS = 18
EXPECTED_START_SHARE_PRICE = Decimal("1.008280253418576")
EXPECTED_END_SHARE_PRICE = Decimal("1.009843353086236")
EXPECTED_FEE_MANAGER_TIMESTAMP = 1_782_802_559
EXPECTED_FEE_MANAGER_MIN_PRICE_D18 = 983_334_787_364_413_344_512_750_452_124
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
    assert vault.fee_manager_address == LIDO_EARN_USD_FEE_MANAGER
    assert vault.fetch_share_token_address(FORK_BLOCK) == LIDO_EARN_USD_SHARE_MANAGER
    assert vault.fetch_total_supply(FORK_BLOCK) == Decimal("21518125.250450660540252819")
    assert vault.fetch_share_price(FORK_BLOCK) == pytest.approx(Decimal("1.016947648806622261909260417"))

    fee_configuration = vault.fetch_fee_configuration(FORK_BLOCK)
    assert fee_configuration is not None
    assert fee_configuration.fee_recipient == LIDO_EARN_USD_FEE_RECIPIENT
    assert fee_configuration.deposit_fee_d6 == 0
    assert fee_configuration.redeem_fee_d6 == 0
    assert fee_configuration.performance_fee_d6 == 0
    assert fee_configuration.protocol_fee_d6 == 0
    assert fee_configuration.base_asset == USDC
    assert fee_configuration.timestamp == EXPECTED_FEE_MANAGER_TIMESTAMP
    assert fee_configuration.min_price_d18 == EXPECTED_FEE_MANAGER_MIN_PRICE_D18

    assert vault.get_management_fee(FORK_BLOCK) == 0.0
    assert vault.get_performance_fee(FORK_BLOCK) == 0.0
    assert vault.get_deposit_fee(FORK_BLOCK) == 0.0
    assert vault.get_withdraw_fee(FORK_BLOCK) == 0.0
    assert vault.has_custom_fees() is True

    fee_data = vault.get_fee_data()
    assert fee_data.fee_mode == VaultFeeMode.internalised_minting
    assert fee_data.management == 0.0
    assert fee_data.performance == 0.0
    assert fee_data.deposit == 0.0
    assert fee_data.withdraw == 0.0

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
    assert info["fee_manager"] == LIDO_EARN_USD_FEE_MANAGER
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


@flaky.flaky
def test_mellow_lido_earn_usd_share_price_increases_between_fixed_blocks() -> None:
    """Read two historical Lido Earn USD oracle prices and check appreciation."""

    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM, retries=2)
    vault = create_vault_instance_autodetect(
        web3,
        vault_address=LIDO_EARN_USD_VAULT,
    )

    assert isinstance(vault, MellowVault)

    start_share_price = vault.fetch_share_price(SHARE_PRICE_START_BLOCK)
    end_share_price = vault.fetch_share_price(SHARE_PRICE_END_BLOCK)

    assert start_share_price is not None
    assert end_share_price is not None
    assert start_share_price == pytest.approx(EXPECTED_START_SHARE_PRICE)
    assert end_share_price == pytest.approx(EXPECTED_END_SHARE_PRICE)
    assert end_share_price > start_share_price


@flaky.flaky
def test_mellow_lido_earn_usd_scan_record(web3: Web3, tmp_path: Path) -> None:
    """Create a shared vault scan row for a Mellow vault.

    Mellow is routed through ``ERC4626Feature.mellow_like`` but the adapter is
    a :py:class:`eth_defi.vault.base.VaultBase` subclass, not an ERC-4626
    subclass. This test checks ``create_vault_scan_record()`` does not need a
    separate Mellow branch to populate the common scan row fields.
    """

    detection = ERC4262VaultDetection(
        chain=1,
        address=LIDO_EARN_USD_VAULT,
        first_seen_at_block=FORK_BLOCK,
        first_seen_at=datetime.datetime(2026, 6, 20),  # noqa: DTZ001
        features={ERC4626Feature.mellow_like},
        updated_at=datetime.datetime(2026, 6, 20),  # noqa: DTZ001
        deposit_count=0,
        redeem_count=0,
    )

    record = create_vault_scan_record(
        web3,
        detection,
        FORK_BLOCK,
        token_cache=TokenDiskCache(tmp_path / "tokens.sqlite"),
    )

    assert record["Protocol"] == "Mellow"
    assert record["Symbol"] == "earnUSD"
    assert record["Name"] == "Lido Earn USD"
    assert record["Address"] == LIDO_EARN_USD_VAULT
    assert record["Denomination"] == "USDC"
    assert record["Share token"] == "earnUSD"
    assert record["NAV"] is None
    assert record["Mgmt fee"] == 0.0
    assert record["Perf fee"] == 0.0
    assert record["Deposit fee"] == 0.0
    assert record["Withdraw fee"] == 0.0
    assert record["Shares"] == Decimal("21518125.250450660540252819")
    assert record["Features"] == "mellow_like"
    assert record["_detection_data"] == detection
    assert record["_denomination_token"]["symbol"] == "USDC"
    assert record["_share_token"]["address"] == LIDO_EARN_USD_SHARE_MANAGER
    assert record["_mellow_info"]["share_manager"] == LIDO_EARN_USD_SHARE_MANAGER
    assert record["_mellow_info"]["assets"] == [USDC, USDT, USDE]
    assert record["_morpho_offchain_data"] is None
