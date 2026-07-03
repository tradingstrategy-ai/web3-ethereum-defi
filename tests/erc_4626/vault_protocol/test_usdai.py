"USDai vault tests"

import datetime
import os

import pytest
from web3 import Web3
from web3.exceptions import ContractLogicError

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.usdai.vault import USDAI_REDEMPTION_WINDOW, StakedUSDaiVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.fee import VaultFeeMode

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests")


class _MockCall:
    def __init__(self, result: int | Exception):
        self.result = result

    def call(self) -> int:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _MockUSDaiFunctions:
    def __init__(self, redemption_timestamp: int | Exception):
        self.redemption_timestamp = redemption_timestamp

    def redemptionTimestamp(self) -> _MockCall:  # noqa: N802
        return _MockCall(self.redemption_timestamp)


class _MockUSDaiContract:
    def __init__(self, redemption_timestamp: int | Exception):
        self.functions = _MockUSDaiFunctions(redemption_timestamp)


def _create_mock_usdai_vault(redemption_timestamp: int | Exception) -> StakedUSDaiVault:
    vault = StakedUSDaiVault.__new__(StakedUSDaiVault)
    vault.__dict__["vault_contract"] = _MockUSDaiContract(redemption_timestamp)
    return vault


@pytest.fixture(scope="module")
def anvil_arbitrum_fork() -> AnvilLaunch:
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=478_000_000)
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


def test_usdai(
    web3: Web3,
):
    """Read USDai vault metadata from the current redemption-window implementation."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x0B2b2B2076d95dda7817e785989fE353fe955ef9",
    )

    assert vault.features == {ERC4626Feature.usdai_like, ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}
    assert isinstance(vault, StakedUSDaiVault)
    assert vault.get_protocol_name() == "USDai"
    assert vault.get_management_fee("latest") == 0.00
    assert vault.get_performance_fee("latest") == 0.00
    assert vault.get_fee_mode() == VaultFeeMode.internalised_skimming
    assert vault.get_estimated_lock_up() == USDAI_REDEMPTION_WINDOW

    # Check maxDeposit/maxRedeem with address(0)
    # USDai returns large values (no per-address cap)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit > 0
    assert max_redeem == 0

    # USDai doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False

    function_names = {entry["name"] for entry in vault.vault_contract.abi if entry.get("type") == "function"}
    assert "timelock" not in function_names
    assert "redemptionTimestamp" in function_names

    raw_redemption_timestamp = vault.vault_contract.functions.redemptionTimestamp().call()
    next_redemption_open = vault.fetch_redemption_next_open()
    assert next_redemption_open == datetime.datetime.fromtimestamp(raw_redemption_timestamp, tz=datetime.timezone.utc).replace(tzinfo=None)


def test_usdai_redemption_next_open_zero_timestamp():
    """Zero timestamp means there is no future redemption window to report."""
    vault = _create_mock_usdai_vault(0)

    assert vault.fetch_redemption_next_open() is None


def test_usdai_redemption_next_open_call_failure():
    """Failed optional redemption timestamp read is ignored."""
    vault = _create_mock_usdai_vault(ContractLogicError("execution reverted"))

    assert vault.fetch_redemption_next_open() is None
