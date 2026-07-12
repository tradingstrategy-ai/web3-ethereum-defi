"""Test Frankencoin vault metadata."""

import datetime
import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.frankencoin.vault import (
    FRANKENCOIN_BASE_SAVINGS_VAULT,
    FRANKENCOIN_ETHEREUM_SAVINGS_VAULT,
    FRANKENCOIN_GNOSIS_SAVINGS_VAULT,
    FRANKENCOIN_SAVINGS_VAULTS,
    FrankencoinHistoricalReader,
    FrankencoinVault,
)
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec, VaultTechnicalRisk
from eth_defi.vault.fee import VaultFeeMode

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")


class _SyntheticToken:
    """Minimal token wrapper for Frankencoin historical reader unit tests.

    :return:
        Token-like object that converts raw 18-decimal balances.
    """

    decimals = 18
    name = "Synthetic ZCHF"
    symbol = "ZCHF"

    @staticmethod
    def convert_to_decimals(raw_amount: int) -> Decimal:
        """Convert a raw 18-decimal token amount.

        :param raw_amount:
            Raw token amount.

        :return:
            Decimal token amount.
        """
        return Decimal(raw_amount) / Decimal(10**18)


class _SyntheticReaderState:
    """Capture state updates from the Frankencoin historical reader.

    :return:
        Synthetic reader state with captured ``on_called`` inputs.
    """

    def __init__(self) -> None:
        self.calls = []

    def on_called(
        self,
        result: EncodedCallResult,
        total_assets: Decimal | None = None,
        share_price: Decimal | None = None,
    ) -> None:
        """Record a state update.

        :param result:
            Multicall result passed to the state update.
        :param total_assets:
            TVL value passed to the state update.
        :param share_price:
            Share price value passed to the state update.
        """
        self.calls.append((result.call.extra_data["function"], total_assets, share_price))


def _make_frankencoin_call_result(
    function_name: str,
    value: int,
    *,
    block_number: int,
    timestamp: datetime.datetime,
    state: _SyntheticReaderState | None = None,
) -> EncodedCallResult:
    """Create a synthetic Frankencoin multicall result.

    :param function_name:
        Function key used by the historical reader.
    :param value:
        Raw uint256 value to encode.
    :param block_number:
        Block number for the synthetic read.
    :param timestamp:
        Timestamp for the synthetic read.
    :param state:
        Optional reader state attached to the call result.

    :return:
        Encoded call result.
    """
    call = EncodedCall(
        func_name=function_name,
        address=FRANKENCOIN_ETHEREUM_SAVINGS_VAULT,
        data=b"",
        extra_data={
            "function": function_name,
            "vault": FRANKENCOIN_ETHEREUM_SAVINGS_VAULT,
        },
    )
    return EncodedCallResult(
        call=call,
        success=True,
        result=value.to_bytes(32, "big"),
        block_identifier=block_number,
        timestamp=timestamp,
        state=state,
    )


def test_frankencoin_hardcoded_protocols() -> None:
    """Official Frankencoin Savings Vaults are classified by hardcoded address."""
    for vault_address in FRANKENCOIN_SAVINGS_VAULTS:
        features = HARDCODED_PROTOCOLS[vault_address]

        assert features == {ERC4626Feature.frankencoin_like}
        assert get_vault_protocol_name(features) == "Frankencoin"


def test_frankencoin_create_vault_instance() -> None:
    """Frankencoin features create a FrankencoinVault adapter."""
    web3 = Web3()
    web3.eth._chain_id = lambda: 100

    vault = create_vault_instance(
        web3,
        FRANKENCOIN_GNOSIS_SAVINGS_VAULT,
        features={ERC4626Feature.frankencoin_like},
    )

    assert isinstance(vault, FrankencoinVault)
    assert vault.get_protocol_name() == "Frankencoin"


def test_frankencoin_static_fee_metadata() -> None:
    """Frankencoin exposes static fee, lock-up, risk, and link metadata."""
    vault = FrankencoinVault(
        Web3(),
        VaultSpec(100, FRANKENCOIN_GNOSIS_SAVINGS_VAULT),
        features={ERC4626Feature.frankencoin_like},
    )

    assert vault.has_custom_fees() is True
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=3)
    assert vault.get_link() == "https://frankencoin.com/token/#svzchf"
    assert vault.get_risk() == VaultTechnicalRisk.low
    assert vault.get_fee_mode() == VaultFeeMode.internalised_skimming


def test_frankencoin_addresses() -> None:
    """Frankencoin savings vault address constants stay lower-case."""
    assert FRANKENCOIN_ETHEREUM_SAVINGS_VAULT == "0xe5f130253ff137f9917c0107659a4c5262abf6b0"
    assert FRANKENCOIN_BASE_SAVINGS_VAULT == "0xa09ebdf8a01b9ef04149319d64f83b9c01a5b585"
    assert FRANKENCOIN_GNOSIS_SAVINGS_VAULT == "0x6165946250dd04740ab1409217e95a4f38374fe9"


def test_frankencoin_reader_updates_state_once_with_combined_tvl() -> None:
    """Frankencoin reader state sees the combined savings product TVL.

    The generic ERC-4626 core decoder updates reader state using
    ``totalAssets()``. Frankencoin overrides TVL after that decode, so the
    stateful reader must avoid the generic update and update state exactly once
    with ``savings_module_balance + wrapper_balance``.
    """
    vault = FrankencoinVault(
        Web3(),
        VaultSpec(1, FRANKENCOIN_ETHEREUM_SAVINGS_VAULT),
        features={ERC4626Feature.frankencoin_like},
    )
    synthetic_token = _SyntheticToken()
    vault.__dict__["denomination_token"] = synthetic_token
    vault.__dict__["share_token"] = synthetic_token

    reader = FrankencoinHistoricalReader(vault, stateful=False)
    state = _SyntheticReaderState()
    block_number = 123
    timestamp = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None)
    raw_unit = 10**18

    historical_read = reader.process_result(
        block_number,
        timestamp,
        [
            _make_frankencoin_call_result("total_assets", 3 * raw_unit, block_number=block_number, timestamp=timestamp, state=state),
            _make_frankencoin_call_result("total_supply", 10 * raw_unit, block_number=block_number, timestamp=timestamp, state=state),
            _make_frankencoin_call_result("convertToAssets", raw_unit + raw_unit // 100, block_number=block_number, timestamp=timestamp, state=state),
            _make_frankencoin_call_result("savings_module_balance", 11 * raw_unit, block_number=block_number, timestamp=timestamp, state=state),
            _make_frankencoin_call_result("wrapper_balance", 4 * raw_unit, block_number=block_number, timestamp=timestamp, state=state),
        ],
    )

    assert historical_read.total_assets == Decimal(15)
    assert historical_read.share_price == Decimal("1.01")
    assert state.calls == [("convertToAssets", Decimal(15), Decimal("1.01"))]


@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test")
@pytest.mark.timeout(180)
def test_frankencoin_ethereum_combined_savings_tvl_latest() -> None:
    """Ethereum svZCHF TVL includes the underlying savings module.

    The ERC-4626 wrapper's own ``totalAssets()`` currently only reports assets
    attributed to the wrapper's savings-module account. The Frankencoin savings
    product TVL is much larger because most ZCHF is deposited directly into the
    underlying savings module.
    """
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    vault = create_vault_instance(
        web3,
        FRANKENCOIN_ETHEREUM_SAVINGS_VAULT,
        features={ERC4626Feature.frankencoin_like},
    )

    assert isinstance(vault, FrankencoinVault)
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, FrankencoinHistoricalReader)

    block_number = web3.eth.block_number
    block = web3.eth.get_block(block_number)
    timestamp = datetime.datetime.fromtimestamp(block["timestamp"], datetime.UTC).replace(tzinfo=None)
    erc4626_assets = ERC4626Vault.fetch_total_assets(vault, block_number)
    savings_product_assets = vault.fetch_total_assets(block_number)
    share_price = vault.fetch_share_price(block_number)
    call_results = [call.call_as_result(web3, block_number) for call in reader.construct_multicalls()]
    historical_read = reader.process_result(block_number, timestamp, call_results)

    assert erc4626_assets is not None
    assert savings_product_assets is not None
    assert erc4626_assets < Decimal("1000")
    assert savings_product_assets > Decimal("1000000")
    assert historical_read.total_assets == pytest.approx(savings_product_assets)
    assert historical_read.share_price == pytest.approx(share_price)
    assert share_price == pytest.approx(Decimal("1.017"), rel=Decimal("0.01"))
