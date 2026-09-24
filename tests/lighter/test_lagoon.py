"""Tests for guarded Lagoon Safe Lighter custody helpers."""

from decimal import Decimal
from types import SimpleNamespace

from pytest_mock import MockerFixture
from web3 import Web3

from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT
from eth_defi.lighter.lagoon import claim_usdc_to_lagoon_safe_from_lighter


SAFE = Web3.to_checksum_address("0x0000000000000000000000000000000000000011")
MODULE = Web3.to_checksum_address("0x0000000000000000000000000000000000000022")


def test_claim_usdc_encodes_exact_safe_asset_and_amount(
    mocker: MockerFixture,
) -> None:
    """Encode the claim for the Lagoon Safe and broadcast it through its module.

    1. Create contract, token and vault doubles for one claimable USDC amount.
    2. Call the production Lighter claim helper.
    3. Verify the exact Safe receiver, asset index and raw amount are guarded and broadcast.
    """
    # 1. Create contract, token and vault doubles for one claimable USDC amount.
    web3 = mocker.Mock(name="web3")
    hot_wallet = mocker.Mock(name="hot_wallet")
    usdc = mocker.Mock(name="usdc")
    usdc.convert_to_raw.return_value = 1_250_000
    vault = SimpleNamespace(
        safe_address=SAFE,
        trading_strategy_module_address=MODULE,
    )
    zk_lighter = mocker.Mock(name="zk_lighter")
    zk_lighter.functions.USDC_ASSET_INDEX.return_value.call.return_value = 1
    withdraw_call = zk_lighter.functions.withdrawPendingBalance.return_value
    withdraw_call._encode_transaction_data.return_value = "0xclaim"
    module = mocker.Mock(name="module")
    guarded_call = mocker.Mock(name="guarded_call")
    module.functions.performCall.return_value = guarded_call

    def get_contract(_web3: object, abi_name: str, _address: str) -> object:
        return zk_lighter if abi_name == "lighter/ZkLighter.json" else module

    mocker.patch(
        "eth_defi.lighter.lagoon.get_deployed_contract",
        side_effect=get_contract,
    )
    broadcast = mocker.patch(
        "eth_defi.lighter.lagoon.broadcast_tx",
        return_value="0xreceipt",
    )

    # 2. Call the production Lighter claim helper.
    result = claim_usdc_to_lagoon_safe_from_lighter(
        web3,
        hot_wallet,
        vault=vault,
        usdc=usdc,
        claimable_usdc=Decimal("1.25"),
    )

    # 3. The exact Safe receiver, asset index and raw amount are guarded and broadcast.
    assert result == "0xreceipt"
    zk_lighter.functions.withdrawPendingBalance.assert_called_once_with(
        SAFE,
        1,
        1_250_000,
    )
    module.functions.performCall.assert_called_once_with(
        Web3.to_checksum_address(LIGHTER_L1_CONTRACT),
        "0xclaim",
        0,
    )
    broadcast.assert_called_once_with(
        web3,
        hot_wallet,
        guarded_call,
        "Claim Lighter USDC withdrawal to Safe",
    )
