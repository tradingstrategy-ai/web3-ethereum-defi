"""Axis StakedUSDx Ethereum V2 vault tests.

The adapter intentionally uses the shared ERC-4626 ABI. Axis's verified V2
implementation exposes the same reader selectors and single-word return types:
https://etherscan.io/address/0x1D8191c20c06c5628f1a977bc6D6aFe7dD541cf2#code
"""

import datetime
import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault import ERC4626HistoricalReader
from eth_defi.erc_4626.vault_protocol.axis.constants import AXIS_ETHEREUM_STAKED_USDX_IMPLEMENTATION, AXIS_ETHEREUM_STAKED_USDX_VAULT
from eth_defi.erc_4626.vault_protocol.axis.vault import AxisVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.vault.fee import FeeData, VaultFeeMode

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: The canonical Ethereum block is only 878 blocks after deployment and still
#: has zero Axis supply. This later fixed block has meaningful V2 vault state.
AXIS_ETHEREUM_V2_READER_BLOCK = 25_800_000

#: EIP-1967 ``keccak256("eip1967.proxy.implementation") - 1`` storage slot.
EIP1967_IMPLEMENTATION_SLOT = int.from_bytes(Web3.keccak(text="eip1967.proxy.implementation"), "big") - 1

#: Reader functions and selectors confirmed against the verified V2 ABI.
AXIS_READER_SELECTORS = {
    "asset()": "38d52e0f",
    "totalAssets()": "01e1d114",
    "totalSupply()": "18160ddd",
    "convertToAssets(uint256)": "07a2d13a",
    "maxDeposit(address)": "402d267d",
}

#: EVM ABI scalar return size.
EVM_WORD_BYTES = 32

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    # The canonical midnight block has no meaningful Axis state, so co-locate
    # this exceptional fixed-block fork on its own xdist worker.
    pytest.mark.xdist_group("fork:ethereum:axis-v2-reader"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Return the shared, fixed-block Ethereum fork used for Axis V2 reads.

    The test is read-only, therefore it can safely share the session-level
    Anvil instance and needs no per-test snapshot restoration.

    :param anvil_fork_pool:
        Session-scoped fixed-block Anvil fork registry.
    :return:
        Web3 client connected to the fixed Axis V2 reader block.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, AXIS_ETHEREUM_V2_READER_BLOCK)


@pytest.fixture(scope="module")
def live_web3() -> Web3:
    """Return a direct Ethereum client for the current implementation guard.

    :return:
        Multi-provider Web3 client connected to the current Ethereum head.
    """
    return create_multi_provider_web3(JSON_RPC_ETHEREUM)


def fetch_proxy_implementation(web3: Web3, block_identifier: int) -> str:
    """Read the Axis proxy implementation from its EIP-1967 storage slot.

    :param web3:
        Ethereum client.
    :param block_identifier:
        Exact state block to inspect.
    :return:
        Checksummed implementation address.
    """
    proxy_address = Web3.to_checksum_address(AXIS_ETHEREUM_STAKED_USDX_VAULT)
    value = web3.eth.get_storage_at(proxy_address, EIP1967_IMPLEMENTATION_SLOT, block_identifier=block_identifier)
    return Web3.to_checksum_address(value[-20:])


def test_axis_staked_usdx_v2_vault(web3: Web3) -> None:
    """Characterise the reviewed Axis StakedUSDx Ethereum V2 deployment.

    The fixed block has non-zero production state and proves the chain-aware
    hardcoded classifier selects the Axis adapter for the V2 vault.

    :param web3:
        Shared Web3 client for the fixed Axis V2 reader block.
    :return:
        ``None`` after asserting the immutable deployment metadata.
    """
    vault = create_vault_instance_autodetect(web3, AXIS_ETHEREUM_STAKED_USDX_VAULT)

    assert vault.features == {ERC4626Feature.axis_like, ERC4626Feature.erc_7540_like}
    assert isinstance(vault, AxisVault)
    assert vault.get_protocol_name() == "Axis"
    assert vault.name == "Staked USDx"
    assert vault.share_token.symbol == "sUSDx"
    assert vault.denomination_token.address == "0xa1fA7777974312f7d801A8880714a218F76233f8"
    assert vault.get_fee_data() == FeeData(VaultFeeMode.internalised_skimming, 0.0, 0.0, 0.0, 0.0)
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=7)
    assert vault.get_deposit_manager_capability() is None


def test_axis_staked_usdx_v2_current_state_and_abi(live_web3: Web3) -> None:
    """Read current V2 state and guard against unreviewed proxy ABI drift.

    The implementation-address assertion deliberately fails after a proxy
    upgrade so the new implementation ABI must be reviewed. Raw calls then
    prove every function used by the current and historical readers retains
    its canonical selector and 32-byte return shape.

    :param live_web3:
        Direct multi-provider Ethereum client.
    """
    block_number = live_web3.eth.block_number
    assert fetch_proxy_implementation(live_web3, block_number).lower() == AXIS_ETHEREUM_STAKED_USDX_IMPLEMENTATION

    vault = create_vault_instance_autodetect(live_web3, AXIS_ETHEREUM_STAKED_USDX_VAULT)
    one_raw_share = vault.share_token.convert_to_raw(Decimal(1))
    calls = {
        "asset()": vault.vault_contract.functions.asset(),
        "totalAssets()": vault.vault_contract.functions.totalAssets(),
        "totalSupply()": vault.vault_contract.functions.totalSupply(),
        "convertToAssets(uint256)": vault.vault_contract.functions.convertToAssets(one_raw_share),
        "maxDeposit(address)": vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR),
    }
    for signature, call in calls.items():
        call_data = call._encode_transaction_data()
        assert call_data[2:10] == AXIS_READER_SELECTORS[signature]
        result = live_web3.eth.call({"to": vault.vault_contract.address, "data": call_data}, block_identifier=block_number)
        assert len(result) == EVM_WORD_BYTES

    total_assets = vault.fetch_total_assets(block_number)
    total_supply = vault.fetch_total_supply(block_number)
    assert total_assets is not None
    assert total_assets > 0
    assert total_supply > 0
    assert vault.fetch_nav(block_number) == total_assets
    assert vault.fetch_share_price(block_number) == total_assets / total_supply


def test_axis_staked_usdx_v2_historical_state_reader(web3: Web3) -> None:
    """Decode exact V2 state through the production historical reader.

    :param web3:
        Shared Web3 client for the fixed Axis V2 reader block.
    """
    block_number = web3.eth.block_number
    assert block_number == AXIS_ETHEREUM_V2_READER_BLOCK
    assert fetch_proxy_implementation(web3, block_number).lower() == AXIS_ETHEREUM_STAKED_USDX_IMPLEMENTATION

    vault = create_vault_instance_autodetect(web3, AXIS_ETHEREUM_STAKED_USDX_VAULT)
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, ERC4626HistoricalReader)

    calls = list(reader.construct_multicalls())
    assert [call.extra_data["function"] for call in calls] == ["total_assets", "total_supply", "convertToAssets", "maxDeposit"]
    call_results = [call.call_as_result(web3=web3, block_identifier=block_number) for call in calls]
    assert all(result.success and len(result.result) == EVM_WORD_BYTES for result in call_results)

    block = web3.eth.get_block(block_number)
    timestamp = datetime.datetime.fromtimestamp(block["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    vault_read = reader.process_result(block_number, timestamp, call_results)

    expected_timestamp = datetime.datetime(2026, 8, 21, 0, 44, 11, tzinfo=datetime.UTC).replace(tzinfo=None)
    assert vault_read.timestamp == expected_timestamp
    assert vault_read.total_assets == Decimal("33894352.49999999999998724")
    assert vault_read.total_supply == Decimal("33624357.38864116626185574")
    assert vault_read.share_price == Decimal("1.008029747847316252")
    assert vault_read.max_deposit == Decimal("1.157920892373161954235709850E+59")
    assert vault_read.errors is None
