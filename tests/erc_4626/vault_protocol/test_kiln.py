"""Test Kiln OmniVault protocol metadata and classification."""

import datetime
import os
from decimal import Decimal
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault_protocol.kiln.vault import KilnVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_WHALE, TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.fee import get_vault_fee_mode
from eth_defi.vault.protocol_metadata import build_metadata_json
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

KILN_VAULT_ADDRESS = "0x19A0F016Ac3989e754ab8216810beD8503bDA37e"
KILN_FORK_BLOCK = 484_188_059
KILN_ETHEREUM_VAULT_ADDRESS = "0xF4918Ef824a242602E0d3e5DB07fFd4DaC4ad3Ea"
KILN_ETHEREUM_FORK_BLOCK = 23_000_000
DAI_DECIMALS = 18


@pytest.fixture(scope="module")
def anvil_arbitrum_fork() -> AnvilLaunch:
    """Fork Arbitrum at the latest block observed during Kiln integration."""
    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM,
        fork_block_number=KILN_FORK_BLOCK,
        unlocked_addresses=[USDC_WHALE[42161]],
    )
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork: AnvilLaunch) -> Web3:
    """Create a Web3 client for the deterministic Arbitrum fork."""
    return create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)


@pytest.fixture(scope="module")
def anvil_ethereum_fee_fork() -> AnvilLaunch:
    """Fork Ethereum at a block with a 20% Kiln DAI reward fee."""
    launch = fork_network_anvil(
        JSON_RPC_ETHEREUM,
        fork_block_number=KILN_ETHEREUM_FORK_BLOCK,
    )
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def ethereum_web3(anvil_ethereum_fee_fork: AnvilLaunch) -> Web3:
    """Create a Web3 client for the deterministic Ethereum fee fork."""
    return create_multi_provider_web3(anvil_ethereum_fee_fork.json_rpc_url)


@pytest.fixture()
def usdc(web3: Web3) -> TokenDetails:
    """Fetch the denomination token of the official Kiln test vault."""
    return fetch_erc20_details(web3, "0xaf88d065e77c8cC2239327C5EDb3A432268e5831")


@pytest.fixture()
def test_user(web3: Web3, usdc: TokenDetails) -> str:
    """Fund an Anvil account with USDC for deposit-manager testing."""
    account = web3.eth.accounts[0]
    tx_hash = usdc.transfer(account, Decimal(1_000)).transact({"from": USDC_WHALE[42161]})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return account


@pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run this test")
@flaky.flaky
def test_kiln_omnivault_classification(web3: Web3) -> None:
    """Classify an official Kiln Bitnovo Compound v3 USDC OmniVault."""
    vault = create_vault_instance_autodetect(web3, vault_address=KILN_VAULT_ADDRESS)

    assert isinstance(vault, KilnVault)
    assert vault.features == {ERC4626Feature.kiln_metavault_like}
    assert vault.get_protocol_name() == "Kiln"
    assert vault.name == "Bitnovo Compound v3 USDC"
    assert vault.get_management_fee(KILN_FORK_BLOCK) == 0.0
    assert vault.get_performance_fee(KILN_FORK_BLOCK) == pytest.approx(0.20)
    assert vault.get_deposit_fee(KILN_FORK_BLOCK) is None
    assert vault.get_deposit_fee_amount(KILN_FORK_BLOCK) == Decimal(0)
    assert vault.has_custom_fees() is True
    assert vault.get_estimated_lock_up() == datetime.timedelta(0)

    fee_data = vault.get_fee_data()
    assert fee_data.management == 0.0
    assert fee_data.performance == pytest.approx(0.20)
    assert fee_data.deposit is None


@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test")
@flaky.flaky
def test_kiln_omnivault_reward_fee_uses_asset_decimals(ethereum_web3: Web3) -> None:
    """Scale the 20e18 DAI vault reward fee to the same 20% ratio as USDC."""
    vault = create_vault_instance_autodetect(ethereum_web3, vault_address=KILN_ETHEREUM_VAULT_ADDRESS)

    assert isinstance(vault, KilnVault)
    assert vault.denomination_token.decimals == DAI_DECIMALS
    assert vault.get_performance_fee(KILN_ETHEREUM_FORK_BLOCK) == pytest.approx(0.20)


@pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run this test")
@flaky.flaky
def test_kiln_omnivault_deposit_manager(web3: Web3, usdc: TokenDetails, test_user: str) -> None:
    """Deposit and redeem through the certified generic ERC-4626 manager."""
    vault = create_vault_instance_autodetect(web3, vault_address=KILN_VAULT_ADDRESS)
    deposit_manager = vault.get_deposit_manager()

    assert isinstance(deposit_manager, ERC4626DepositManager)
    assert deposit_manager.has_synchronous_deposit() is True
    assert deposit_manager.has_synchronous_redemption() is True
    assert vault.get_deposit_manager_capability().as_initial_public_schema() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "synchronous",
        "redemption_flow": "synchronous",
    }

    amount = Decimal(100)
    tx_hash = usdc.approve(vault.address, amount).transact({"from": test_user})
    assert_transaction_success_with_explanation(web3, tx_hash)

    deposit_ticket = deposit_manager.create_deposit_request(test_user, amount=amount).broadcast()
    assert deposit_manager.can_finish_deposit(deposit_ticket) is True

    raw_shares = vault.share_token.contract.functions.balanceOf(test_user).call()
    assert raw_shares > 0

    redemption_ticket = deposit_manager.create_redemption_request(test_user, raw_shares=raw_shares).broadcast()
    assert deposit_manager.can_finish_redeem(redemption_ticket) is True
    assert vault.share_token.contract.functions.balanceOf(test_user).call() == 0


def test_kiln_protocol_metadata() -> None:
    """Export Kiln metadata using the public Kiln protocol slug."""
    metadata = build_metadata_json(Path("eth_defi/data/vaults/metadata/kiln.yaml"), "https://example.invalid")

    assert metadata["name"] == "Kiln"
    assert metadata["slug"] == "kiln"
    assert metadata["logos"]["generic"] == "https://example.invalid/vault-protocol-metadata/kiln/generic.png"
    assert metadata["logos"]["light"] == "https://example.invalid/vault-protocol-metadata/kiln/light.png"


def test_kiln_risk_and_fee_data() -> None:
    """Classify Kiln risk while keeping its mixed protocol-wide fee mode unknown."""
    assert get_vault_protocol_name({ERC4626Feature.kiln_metavault_like}) == "Kiln"
    assert get_vault_risk("Kiln") == VaultTechnicalRisk.low
    assert get_vault_fee_mode("Kiln", KILN_VAULT_ADDRESS) is None
