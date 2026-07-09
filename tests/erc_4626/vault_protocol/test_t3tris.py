"""Test T3tris vault metadata and historical reader."""

import datetime
import os
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault import ERC4626HistoricalReader
from eth_defi.erc_4626.vault_protocol.t3tris.vault import STALE_NAV_CORRECTED_ERROR, STALE_NAV_FIRST_SAMPLE_ERROR, T3trisHistoricalReader, T3trisVault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader, VaultSpec
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.risk import VaultTechnicalRisk

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

FORK_BLOCK = 480_900_000

#: Gami USDC vault on Arbitrum, listed in the T3tris app.
GAMI_USDC_VAULT = "0x9984ad74c5fb6bec3888e14b4e453707d3be7f8f"
GAMI_USDC_VAULT_CHECKSUM = Web3.to_checksum_address(GAMI_USDC_VAULT)

#: T3tris vault with a known async stale-NAV settlement window.
STALE_NAV_USDC_VAULT = "0x98e43a491a464F0886bC5E57207c340BBed0D01F"
STALE_NAV_USDC_VAULT_CHECKSUM = Web3.to_checksum_address(STALE_NAV_USDC_VAULT)

#: Before deposit settlement minted new shares.
STALE_NAV_BEFORE_BLOCK = 477_900_000

#: After settlement minted shares but before the oracle NAV absorbed the deposit.
STALE_NAV_GAP_BLOCK = 478_000_000

#: First block where the oracle NAV update closed the stale-NAV gap.
STALE_NAV_AFTER_BLOCK = 478_353_946

requires_arbitrum_rpc = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests")
SYNTHETIC_SAMPLE_START = datetime.datetime(2026, 6, 27, 12, 0, tzinfo=datetime.UTC).replace(tzinfo=None)


class _FakeToken:
    """Minimal token object for T3tris reader unit tests."""

    def __init__(self, decimals: int = 6):
        self.decimals = decimals

    def convert_to_decimals(self, raw_amount: int) -> Decimal:
        """Convert raw token units to decimals."""
        return Decimal(raw_amount) / Decimal(10**self.decimals)


class _FakeVault:
    """Minimal vault object for T3tris reader unit tests."""

    address = Web3.to_checksum_address("0x0000000000000000000000000000000000000001")
    share_token = _FakeToken()
    denomination_token = _FakeToken()

    def __repr__(self) -> str:
        return "<_FakeVault>"


class _FakeReaderState:
    """Capture reader state updates."""

    def __init__(self):
        self.calls = []

    def on_called(
        self,
        result: EncodedCallResult,
        total_assets: Decimal | None = None,
        share_price: Decimal | None = None,
    ) -> None:
        """Record one state update."""
        self.calls.append(
            {
                "block": result.block_identifier,
                "total_assets": total_assets,
                "share_price": share_price,
            }
        )


def _make_t3tris_reader() -> T3trisHistoricalReader:
    """Create a T3tris reader without Web3 dependencies."""
    reader = T3trisHistoricalReader.__new__(T3trisHistoricalReader)
    reader.vault = _FakeVault()
    reader.reader_state = None
    reader.previous_total_supply = None
    reader.previous_last_valuation_timestamp = None
    reader.previous_block_number = None
    reader.last_good_share_price = None
    reader.in_stale_nav_gap = False
    reader.stale_nav_gap_started_at_valuation_timestamp = None
    return reader


def _make_call_result(
    function_name: str,
    value: int,
    *,
    block_number: int,
    timestamp: datetime.datetime,
    state: _FakeReaderState | None = None,
) -> EncodedCallResult:
    """Create one encoded call result for a synthetic T3tris sample."""
    call = EncodedCall(
        func_name=function_name,
        address=_FakeVault.address,
        data=b"",
        extra_data={
            "function": function_name,
            "vault": _FakeVault.address,
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


def _make_t3tris_sample(
    *,
    block_number: int,
    total_assets: Decimal,
    total_supply: Decimal,
    share_price: Decimal,
    is_vault_open: bool = False,
    last_valuation_timestamp: int = 1,
    state: _FakeReaderState | None = None,
) -> list[EncodedCallResult]:
    """Create synthetic T3tris multicall results."""
    timestamp = SYNTHETIC_SAMPLE_START + datetime.timedelta(seconds=block_number)
    scale = Decimal(10**6)
    return [
        _make_call_result("total_assets", int(total_assets * scale), block_number=block_number, timestamp=timestamp, state=state),
        _make_call_result("total_supply", int(total_supply * scale), block_number=block_number, timestamp=timestamp, state=state),
        _make_call_result("convertToAssets", int(share_price * scale), block_number=block_number, timestamp=timestamp, state=state),
        _make_call_result("maxDeposit", 0, block_number=block_number, timestamp=timestamp, state=state),
        _make_call_result("isVaultOpen", int(is_vault_open), block_number=block_number, timestamp=timestamp, state=state),
        _make_call_result("lastValuationTimestamp", last_valuation_timestamp, block_number=block_number, timestamp=timestamp, state=state),
    ]


def _process_t3tris_sample(
    reader: T3trisHistoricalReader,
    *,
    block_number: int,
    total_assets: Decimal,
    total_supply: Decimal,
    share_price: Decimal,
    is_vault_open: bool = False,
    last_valuation_timestamp: int = 1,
    state: _FakeReaderState | None = None,
    failed_calls: set[str] | None = None,
) -> VaultHistoricalRead:
    """Process one synthetic T3tris sample."""
    timestamp = SYNTHETIC_SAMPLE_START + datetime.timedelta(seconds=block_number)
    call_results = _make_t3tris_sample(
        block_number=block_number,
        total_assets=total_assets,
        total_supply=total_supply,
        share_price=share_price,
        is_vault_open=is_vault_open,
        last_valuation_timestamp=last_valuation_timestamp,
        state=state,
    )
    if failed_calls:
        for result in call_results:
            if result.call.extra_data["function"] in failed_calls:
                result.success = False
                result.result = b""
    return reader.process_result(block_number, timestamp, call_results)


def test_t3tris_reader_updates_state_once_with_corrected_values() -> None:
    """T3tris reader must not update adaptive state with raw stale-NAV values."""
    reader = _make_t3tris_reader()
    state = _FakeReaderState()

    first_read = _process_t3tris_sample(
        reader,
        block_number=1,
        total_assets=Decimal("100"),
        total_supply=Decimal("100"),
        share_price=Decimal("1"),
        last_valuation_timestamp=10,
        state=state,
    )
    gap_read = _process_t3tris_sample(
        reader,
        block_number=2,
        total_assets=Decimal("100"),
        total_supply=Decimal("200"),
        share_price=Decimal("0.5"),
        last_valuation_timestamp=10,
        state=state,
    )

    assert first_read.share_price == Decimal("1")
    assert gap_read.share_price == Decimal("1")
    assert gap_read.total_assets == Decimal("200")
    assert state.calls == [
        {
            "block": 1,
            "total_assets": Decimal("100"),
            "share_price": Decimal("1"),
        },
        {
            "block": 2,
            "total_assets": Decimal("200"),
            "share_price": Decimal("1"),
        },
    ]


def test_t3tris_reader_latches_and_recovers_stale_nav_gap() -> None:
    """T3tris reader keeps PPS flat during stale-NAV gap and exits on recovery."""
    reader = _make_t3tris_reader()

    _process_t3tris_sample(
        reader,
        block_number=1,
        total_assets=Decimal("100"),
        total_supply=Decimal("100"),
        share_price=Decimal("1"),
        last_valuation_timestamp=10,
    )
    first_gap_read = _process_t3tris_sample(
        reader,
        block_number=2,
        total_assets=Decimal("100"),
        total_supply=Decimal("200"),
        share_price=Decimal("0.5"),
        last_valuation_timestamp=10,
    )
    second_gap_read = _process_t3tris_sample(
        reader,
        block_number=3,
        total_assets=Decimal("100"),
        total_supply=Decimal("200"),
        share_price=Decimal("0.5"),
        last_valuation_timestamp=10,
    )
    recovered_read = _process_t3tris_sample(
        reader,
        block_number=4,
        total_assets=Decimal("202"),
        total_supply=Decimal("200"),
        share_price=Decimal("1.01"),
        last_valuation_timestamp=11,
    )

    assert first_gap_read.share_price == Decimal("1")
    assert first_gap_read.total_assets == Decimal("200")
    assert STALE_NAV_CORRECTED_ERROR in first_gap_read.errors
    assert second_gap_read.share_price == Decimal("1")
    assert second_gap_read.total_assets == Decimal("200")
    assert STALE_NAV_CORRECTED_ERROR in second_gap_read.errors
    assert recovered_read.share_price == Decimal("1.01")
    assert recovered_read.total_assets == Decimal("202")
    assert recovered_read.errors is None
    assert reader.in_stale_nav_gap is False


def test_t3tris_reader_supply_jump_starts_gap_even_if_timestamp_advances() -> None:
    """Settlement can advance oracle timestamp before NAV absorbs new assets."""
    reader = _make_t3tris_reader()

    _process_t3tris_sample(
        reader,
        block_number=1,
        total_assets=Decimal("100"),
        total_supply=Decimal("100"),
        share_price=Decimal("1"),
        last_valuation_timestamp=10,
    )
    gap_read = _process_t3tris_sample(
        reader,
        block_number=2,
        total_assets=Decimal("100"),
        total_supply=Decimal("200"),
        share_price=Decimal("0.5"),
        last_valuation_timestamp=11,
    )
    second_gap_read = _process_t3tris_sample(
        reader,
        block_number=3,
        total_assets=Decimal("100"),
        total_supply=Decimal("200"),
        share_price=Decimal("0.5"),
        last_valuation_timestamp=11,
    )

    assert gap_read.share_price == Decimal("1")
    assert gap_read.total_assets == Decimal("200")
    assert STALE_NAV_CORRECTED_ERROR in gap_read.errors
    assert second_gap_read.share_price == Decimal("1")
    assert STALE_NAV_CORRECTED_ERROR in second_gap_read.errors


def test_t3tris_reader_failed_protocol_call_does_not_poison_gap_baseline() -> None:
    """Failed T3tris-specific reads must not promote collapsed PPS to baseline."""
    reader = _make_t3tris_reader()

    _process_t3tris_sample(
        reader,
        block_number=1,
        total_assets=Decimal("100"),
        total_supply=Decimal("100"),
        share_price=Decimal("1"),
        last_valuation_timestamp=10,
    )
    failed_gap_read = _process_t3tris_sample(
        reader,
        block_number=2,
        total_assets=Decimal("100"),
        total_supply=Decimal("200"),
        share_price=Decimal("0.5"),
        last_valuation_timestamp=11,
        failed_calls={"isVaultOpen"},
    )

    assert failed_gap_read.share_price == Decimal("0.5")
    assert "isVaultOpen call failed" in failed_gap_read.errors
    assert reader.last_good_share_price == Decimal("1")
    assert reader.previous_total_supply == Decimal("100")

    confirmed_gap_read = _process_t3tris_sample(
        reader,
        block_number=3,
        total_assets=Decimal("100"),
        total_supply=Decimal("200"),
        share_price=Decimal("0.5"),
        last_valuation_timestamp=11,
    )

    assert confirmed_gap_read.share_price == Decimal("1")
    assert confirmed_gap_read.total_assets == Decimal("200")
    assert STALE_NAV_CORRECTED_ERROR in confirmed_gap_read.errors
    assert reader.last_good_share_price == Decimal("1")
    assert reader.previous_total_supply == Decimal("200")


def test_t3tris_reader_valuation_timestamp_advance_stops_correction() -> None:
    """A fresh oracle valuation must be treated as measured NAV, not stale NAV."""
    reader = _make_t3tris_reader()

    _process_t3tris_sample(
        reader,
        block_number=1,
        total_assets=Decimal("100"),
        total_supply=Decimal("100"),
        share_price=Decimal("1"),
        last_valuation_timestamp=10,
    )
    gap_read = _process_t3tris_sample(
        reader,
        block_number=2,
        total_assets=Decimal("100"),
        total_supply=Decimal("200"),
        share_price=Decimal("0.5"),
        last_valuation_timestamp=10,
    )
    loss_read = _process_t3tris_sample(
        reader,
        block_number=3,
        total_assets=Decimal("100"),
        total_supply=Decimal("200"),
        share_price=Decimal("0.5"),
        last_valuation_timestamp=11,
    )

    assert gap_read.share_price == Decimal("1")
    assert STALE_NAV_CORRECTED_ERROR in gap_read.errors
    assert loss_read.share_price == Decimal("0.5")
    assert loss_read.total_assets == Decimal("100")
    assert loss_read.errors is None
    assert reader.in_stale_nav_gap is False


def test_t3tris_reader_flags_first_sample_inside_possible_gap() -> None:
    """First low-PPS sample cannot be safely corrected and must not seed baseline."""
    reader = _make_t3tris_reader()

    first_read = _process_t3tris_sample(
        reader,
        block_number=1,
        total_assets=Decimal("100"),
        total_supply=Decimal("200"),
        share_price=Decimal("0.5"),
        last_valuation_timestamp=10,
    )
    recovered_read = _process_t3tris_sample(
        reader,
        block_number=2,
        total_assets=Decimal("200"),
        total_supply=Decimal("200"),
        share_price=Decimal("1"),
        last_valuation_timestamp=11,
    )

    assert first_read.share_price == Decimal("0.5")
    assert STALE_NAV_FIRST_SAMPLE_ERROR in first_read.errors
    assert recovered_read.share_price == Decimal("1")
    assert reader.last_good_share_price == Decimal("1")


def test_t3tris_reader_rejects_out_of_order_samples() -> None:
    """Stateful PPS correction requires monotonically increasing blocks."""
    reader = _make_t3tris_reader()

    _process_t3tris_sample(
        reader,
        block_number=2,
        total_assets=Decimal("100"),
        total_supply=Decimal("100"),
        share_price=Decimal("1"),
    )

    with pytest.raises(ValueError, match="out-of-order block"):
        _process_t3tris_sample(
            reader,
            block_number=1,
            total_assets=Decimal("100"),
            total_supply=Decimal("100"),
            share_price=Decimal("1"),
        )


@pytest.fixture(scope="module")
def anvil_arbitrum_fork() -> AnvilLaunch:
    """Fork Arbitrum at a specific block for reproducibility.

    Gami USDC is a live T3tris vault at the pinned block. The fixed block pins
    the classification probe response and fee configuration values.
    """

    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=FORK_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork) -> Web3:
    """Create Web3 connection to the Arbitrum fork."""

    return create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url, retries=2)


@pytest.fixture(scope="module")
def archive_web3() -> Web3:
    """Create Web3 connection to the Arbitrum archive RPC."""

    return create_multi_provider_web3(JSON_RPC_ARBITRUM, retries=2)


def _read_historical_sample(
    web3: Web3,
    reader: VaultHistoricalReader,
    block_number: int,
) -> VaultHistoricalRead:
    """Read one historical sample through a vault reader.

    The direct integration test uses ``eth_call`` per encoded call instead of
    the chunked multicall scanner so the assertions can target a few exact
    blocks around the known T3tris stale-NAV window.

    :param web3:
        Archive RPC connection.

    :param reader:
        Historical reader to exercise.

    :param block_number:
        Historical block number.

    :return:
        Decoded vault sample.
    """
    block = web3.eth.get_block(block_number)
    timestamp = datetime.datetime.fromtimestamp(block["timestamp"], datetime.UTC).replace(tzinfo=None)
    call_results = [call.call_as_result(web3=web3, block_identifier=block_number) for call in reader.construct_multicalls()]
    for result in call_results:
        result.timestamp = timestamp
    return reader.process_result(block_number, timestamp, call_results)


@flaky.flaky
@requires_arbitrum_rpc
def test_t3tris_gami_usdc(web3: Web3) -> None:
    """Read T3tris vault metadata on a fixed Arbitrum fork."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=GAMI_USDC_VAULT,
    )

    assert isinstance(vault, T3trisVault)
    assert vault.get_protocol_name() == "T3tris"
    assert vault.features == {ERC4626Feature.t3tris_like}
    assert vault.address == GAMI_USDC_VAULT_CHECKSUM
    assert vault.vault_address == GAMI_USDC_VAULT_CHECKSUM

    assert vault.name == "Gami USDC"
    assert vault.symbol == "gamiusdc"
    assert vault.fetch_denomination_token_address() == "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    assert vault.fetch_total_assets(FORK_BLOCK) > 0
    assert vault.fetch_total_supply(FORK_BLOCK) > 0

    assert vault.get_risk() == VaultTechnicalRisk.low
    assert vault.get_fee_mode() == VaultFeeMode.internalised_minting
    assert vault.get_management_fee(FORK_BLOCK) == 0.0
    assert vault.get_performance_fee(FORK_BLOCK) == pytest.approx(0.2)
    assert vault.get_deposit_fee(FORK_BLOCK) == 0.0
    assert vault.get_withdraw_fee(FORK_BLOCK) == 0.0

    fee_data = vault.get_fee_data()
    assert fee_data.fee_mode == VaultFeeMode.internalised_minting
    assert fee_data.management == 0.0
    assert fee_data.performance == pytest.approx(0.2)
    assert fee_data.deposit == 0.0
    assert fee_data.withdraw == 0.0

    gross_tvl, gross_managed_assets, gross_pending_deposits, gross_claimable_redeems = vault.vault_contract.functions.getGrossTVL().call(block_identifier=FORK_BLOCK)
    assert gross_tvl > 0
    assert gross_managed_assets >= 0
    assert gross_pending_deposits >= 0
    assert gross_claimable_redeems >= 0

    assert vault.get_link() == f"https://app.t3tris.finance/vaults?chainId=42161&address={GAMI_USDC_VAULT_CHECKSUM}"


@pytest.mark.timeout(180)
@flaky.flaky
@requires_arbitrum_rpc
def test_t3tris_historical_reader_holds_pps_during_stale_nav_window(archive_web3: Web3) -> None:
    """T3tris historical reader hides the async settlement phantom drawdown.

    Real Arbitrum blocks for vault
    ``0x98e43a491a464F0886bC5E57207c340BBed0D01F``:

    - block 477,900,000: PPS is around 1.004245 before settlement
    - block 478,000,000: raw ERC-4626 PPS is 0.5136 after supply doubled
    - block 478,353,946: oracle NAV update closes the gap and PPS is 1.005157
    """

    vault = T3trisVault(
        archive_web3,
        VaultSpec(42161, STALE_NAV_USDC_VAULT_CHECKSUM),
        features={ERC4626Feature.t3tris_like},
    )

    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, T3trisHistoricalReader)

    raw_reader = ERC4626HistoricalReader(vault, stateful=False)
    raw_gap_read = _read_historical_sample(archive_web3, raw_reader, STALE_NAV_GAP_BLOCK)
    assert raw_gap_read.share_price == Decimal("0.5136")
    assert raw_gap_read.total_assets == Decimal("10439.4129")
    assert raw_gap_read.total_supply == Decimal("20325.958838")

    before_read = _read_historical_sample(archive_web3, reader, STALE_NAV_BEFORE_BLOCK)
    gap_read = _read_historical_sample(archive_web3, reader, STALE_NAV_GAP_BLOCK)
    after_read = _read_historical_sample(archive_web3, reader, STALE_NAV_AFTER_BLOCK)

    assert before_read.share_price == Decimal("1.004245")
    assert before_read.total_assets == Decimal("10435.0379")
    assert before_read.total_supply == Decimal("10390.921927")

    assert gap_read.share_price == before_read.share_price
    assert gap_read.total_supply == Decimal("20325.958838")
    assert gap_read.total_assets == before_read.share_price * gap_read.total_supply

    assert after_read.share_price == Decimal("1.005157")
    assert after_read.total_assets == Decimal("20430.794014")
    assert after_read.total_supply == Decimal("20325.958838")
