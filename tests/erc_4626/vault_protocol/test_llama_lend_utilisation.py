"""Controller-based liquidity accounting for Llama Lend V1 and V2."""

import datetime
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from web3 import Web3
from web3.exceptions import ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.scan import _fetch_lending_stats  # noqa: PLC2701
from eth_defi.erc_4626.vault_protocol.llama_lend.vault import LlamaLendVault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult, read_multicall_chunked
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails
from eth_defi.vault.base import VaultHistoricalRead, VaultSpec

VAULT_ADDRESS = "0xaB504518C7B83ED6C67718877C4FFc2B52b29436"
CONTROLLER_ADDRESS = "0xb5EC7A3D591877A66BE4f3eafdC4205E98A1BCAA"
SAMPLE_TIME = datetime.datetime(2026, 10, 17, 23, 56, 21, tzinfo=datetime.UTC).replace(tzinfo=None)
JSON_RPC_OPTIMISM = os.environ.get("JSON_RPC_OPTIMISM")
FIRST_SEEN_BLOCK = 100


@pytest.fixture
def vault() -> LlamaLendVault:
    """Create an offline adapter with real token conversion helpers.

    Cached metadata prevents unit tests from reading an RPC endpoint.

    :return: Llama adapter with a six-decimal denomination token.
    """
    web3 = Web3()
    vault = LlamaLendVault(web3, VaultSpec(10, VAULT_ADDRESS), features={ERC4626Feature.llamma_like})
    token = TokenDetails(contract=get_deployed_contract(web3, "ERC20MockDecimals.json", VAULT_ADDRESS), decimals=6)
    vault.__dict__["denomination_token"] = token
    vault.__dict__["share_token"] = token
    vault.__dict__["controller_address"] = CONTROLLER_ADDRESS
    vault.first_seen_at_block = FIRST_SEEN_BLOCK
    return vault


@pytest.fixture
def spot_reads(vault: LlamaLendVault, monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock]:
    """Stub the two spot reads without replacing the accounting logic.

    Defaults represent a vault with 100 assets and 75 borrowed.

    :param vault: Offline adapter under test.
    :param monkeypatch: Fixture restoring the encoded-call factory.
    :return: Assets contract and encoded debt call mocks.
    """
    contract, debt_call = Mock(), Mock()
    contract.functions.totalAssets.return_value.call.return_value = 100_000_000
    debt_call.call.return_value = (75_000_000).to_bytes(32, "big")
    vault.__dict__["vault_contract"] = contract
    monkeypatch.setattr(vault, "create_total_debt_call", Mock(return_value=debt_call))
    return contract, debt_call


def make_result(vault: LlamaLendVault, function: str, value: int, block: int = 123) -> EncodedCallResult:
    """Encode a synthetic uint256 observation for the historical reader.

    The vault attribution matches the production multicall grouping.

    :param vault: Adapter owning this observation.
    :param function: Reader function key.
    :param value: Raw integer return value.
    :param block: Source block number.
    :return: Successful multicall result.
    """
    call = EncodedCall(func_name=function, address=vault.address, data=b"", extra_data={"function": function, "vault": vault.address})
    return EncodedCallResult(call=call, success=True, result=value.to_bytes(32, "big"), block_identifier=block, timestamp=SAMPLE_TIME)


@pytest.mark.parametrize(
    "decimals,assets,debt,liquidity,utilisation",
    [
        (6, 100_000_000, 75_000_000, "25", 0.75),
        (18, 100 * 10**18, 75 * 10**18, "25", 0.75),
        (6, 100_000_000, 0, "100", 0.0),
        (6, 100_000_000, 100_000_000, "0", 1.0),
        (6, 0, 0, "0", 0.0),
        (6, 100_000_000, 100_000_001, "0", 1.0),
        # V2 assets already exclude protocol fees: preserve micro-USDC liquidity.
        (6, 98_724_801_097, 98_724_757_723, "0.043374", 0.9999995606575094),
    ],
)
def test_llama_lending_metrics(vault: LlamaLendVault, spot_reads: tuple[Mock, Mock], *, decimals: int, assets: int, debt: int, liquidity: str, utilisation: float) -> None:
    """Spot and historical reads agree across token precisions and liquidity states."""
    vault.denomination_token.decimals = decimals
    contract, debt_call = spot_reads
    debt_call.call.return_value = debt.to_bytes(32, "big")
    contract.functions.totalAssets.return_value.call.return_value = assets

    assert vault.fetch_available_liquidity(123) == Decimal(liquidity)
    assert vault.fetch_utilisation_percent(123) == pytest.approx(utilisation)
    contract.functions.totalAssets.return_value.call.assert_called_with(block_identifier=123)
    debt_call.call.assert_called_with(vault.web3, block_identifier=123, attempts=0)

    reader = vault.get_historical_reader(stateful=False)
    results = {name: make_result(vault, name, value) for name, value in [("total_assets", assets), ("total_debt", debt)]}
    available, used = reader.process_utilisation_result(results)
    assert available == Decimal(liquidity)
    assert used == pytest.approx(utilisation)


def test_llama_debt_call_targets_controller(vault: LlamaLendVault) -> None:
    """Historical debt calls preserve vault attribution and deployment boundaries."""
    calls = list(vault.get_historical_reader(stateful=False).construct_utilisation_calls())
    assert len(calls) == 1
    call = calls[0]
    assert call.address == CONTROLLER_ADDRESS
    assert call.data == Web3.keccak(text="total_debt()")[:4]
    assert call.extra_data == {"function": "total_debt", "vault": vault.address}
    assert call.first_block_number == FIRST_SEEN_BLOCK


@pytest.mark.parametrize("function", ["total_assets", "total_debt"])
@pytest.mark.parametrize("failure", ["missing", "reverted", "empty", "short", "long"])
def test_llama_invalid_metrics(vault: LlamaLendVault, caplog: pytest.LogCaptureFixture, function: str, failure: str) -> None:
    """Failed or malformed reads stay missing instead of manufacturing utilisation."""
    results = {name: make_result(vault, name, value) for name, value in [("total_assets", 100_000_000), ("total_debt", 75_000_000)]}
    if failure == "missing":
        del results[function]
    elif failure == "reverted":
        results[function].success = False
    else:
        results[function].result = {"empty": b"", "short": b"\x01", "long": bytes(64)}[failure]
    reader = vault.get_historical_reader(stateful=False)
    assert reader.process_utilisation_result(results) == (None, None)
    assert "Cannot read Llama lending metrics" in caplog.text


@pytest.mark.parametrize("failure", [ContractLogicError("reverted"), b"", bytes(64)])
def test_llama_spot_failed_debt(vault: LlamaLendVault, spot_reads: tuple[Mock, Mock], caplog: pytest.LogCaptureFixture, failure: Exception | bytes) -> None:
    """Spot provider failures and invalid payloads return missing values with context."""
    _, debt_call = spot_reads
    if isinstance(failure, Exception):
        debt_call.call.side_effect = failure
    else:
        debt_call.call.return_value = failure
    assert vault.fetch_available_liquidity(123) is None
    assert vault.fetch_utilisation_percent(123) is None
    assert vault.address in caplog.text


def test_llama_spot_pins_latest(vault: LlamaLendVault, spot_reads: tuple[Mock, Mock], monkeypatch: pytest.MonkeyPatch) -> None:
    """A changing latest block cannot mix assets and debt from different blocks."""
    web3 = Mock()
    web3.eth.get_block.return_value = {"number": 123}
    monkeypatch.setattr(vault, "web3", web3)
    contract, debt_call = spot_reads
    assert vault.fetch_available_liquidity() == Decimal(25)
    web3.eth.get_block.assert_called_once_with("latest")
    contract.functions.totalAssets.return_value.call.assert_called_once_with(block_identifier=123)
    debt_call.call.assert_called_once_with(web3, block_identifier=123, attempts=0)


def test_llama_controller_is_cached(vault: LlamaLendVault) -> None:
    """Resolve the immutable controller only once across subsequent reads."""
    del vault.__dict__["controller_address"]
    contract = Mock()
    contract.functions.controller.return_value.call.return_value = CONTROLLER_ADDRESS
    vault.__dict__["vault_contract"] = contract
    assert vault.controller_address == CONTROLLER_ADDRESS
    assert vault.controller_address == CONTROLLER_ADDRESS
    contract.functions.controller.return_value.call.assert_called_once_with()


def test_llama_failed_assets(vault: LlamaLendVault, spot_reads: tuple[Mock, Mock], caplog: pytest.LogCaptureFixture) -> None:
    """An assets read failure cannot be exported as full utilisation."""
    contract, _ = spot_reads
    contract.functions.totalAssets.return_value.call.side_effect = ContractLogicError("reverted")
    assert vault.fetch_available_liquidity(123) is None
    assert vault.fetch_utilisation_percent(123) is None
    assert vault.address in caplog.text


def test_llama_failed_controller_lookup(vault: LlamaLendVault, caplog: pytest.LogCaptureFixture) -> None:
    """An unavailable controller does not abort historical reader preparation.

    Core price observations remain readable when the optional lending calls
    cannot be constructed.

    :param vault: Offline adapter with cached token metadata.
    :param caplog: Captured diagnostics for the failed controller lookup.
    """
    del vault.__dict__["controller_address"]
    contract = Mock()
    contract.functions.controller.return_value.call.side_effect = ContractLogicError("reverted")
    vault.__dict__["vault_contract"] = contract
    reader = vault.get_historical_reader(stateful=False)
    assert list(reader.construct_utilisation_calls()) == []
    results = [make_result(vault, name, value) for name, value in [("total_assets", 100_000_000), ("total_supply", 100_000_000), ("convertToAssets", 1_000_000)]]
    row = reader.process_result(123, SAMPLE_TIME, results)
    assert row.share_price == Decimal(1)
    assert row.available_liquidity is None
    assert row.utilisation is None
    assert "Cannot resolve Llama controller" in caplog.text
    assert len(caplog.records) == 1


def test_llama_metadata_reads_metrics_once(vault: LlamaLendVault, spot_reads: tuple[Mock, Mock]) -> None:
    """The metadata scanner reads one coherent pair without duplicate RPC calls.

    :param vault: Offline adapter with combined lending metrics.
    :param spot_reads: Assets and debt stubs with call counters.
    """
    contract, debt_call = spot_reads
    stats = _fetch_lending_stats(vault, SimpleNamespace(features=vault.features), Decimal(100_000), 123)
    assert stats == {"_available_liquidity": Decimal(25), "_utilisation": 0.75, "_lending_fields_unavailable": []}
    contract.functions.totalAssets.return_value.call.assert_called_once_with(block_identifier=123)
    debt_call.call.assert_called_once_with(vault.web3, block_identifier=123, attempts=0)


def test_lending_metadata_legacy_spot_methods() -> None:
    """Adapters without a combined getter retain their independent spot reads.

    The scanner's optional fast path must not change existing lending adapters.
    """
    vault = SimpleNamespace(fetch_available_liquidity=Mock(return_value=Decimal(25)), fetch_utilisation_percent=Mock(return_value=0.75))
    stats = _fetch_lending_stats(vault, SimpleNamespace(features={ERC4626Feature.llamma_like}), Decimal(100_000), 123)
    assert stats == {"_available_liquidity": Decimal(25), "_utilisation": 0.75, "_lending_fields_unavailable": []}
    vault.fetch_available_liquidity.assert_called_once_with(123)
    vault.fetch_utilisation_percent.assert_called_once_with(123)


def test_llama_historical_export(vault: LlamaLendVault, tmp_path: Path) -> None:
    """Corrected metrics survive the existing historical row and Parquet schema."""
    results = [make_result(vault, name, value) for name, value in [("total_assets", 100_000_000), ("total_debt", 75_000_000), ("total_supply", 100_000_000), ("convertToAssets", 1_000_000)]]
    row = vault.get_historical_reader(stateful=False).process_result(123, SAMPLE_TIME, results)
    assert row.available_liquidity == Decimal(25)
    assert row.utilisation == pytest.approx(0.75)
    assert row.management_fee == row.performance_fee == 0.0
    path = tmp_path / "prices.parquet"
    pq.write_table(pa.Table.from_pylist([row.export()], schema=VaultHistoricalRead.to_pyarrow_schema()), path)
    exported = pq.read_table(path).to_pylist()[0]
    assert exported["available_liquidity"] == pytest.approx(25.0)
    assert exported["utilisation"] == pytest.approx(0.75)

    results = [r for r in results if r.call.extra_data["function"] != "total_debt"]
    row = vault.get_historical_reader(stateful=False).process_result(123, SAMPLE_TIME, results)
    assert row.available_liquidity is None
    assert row.utilisation is None
    assert any("Llama lending metrics unavailable" in error for error in row.errors)


def test_llama_historical_rejects_mixed_blocks(vault: LlamaLendVault) -> None:
    """Reuse the base reader's block-consistency check for controller debt.

    Assets and debt from different states cannot form one utilisation observation.

    :param vault: Offline adapter with cached token metadata.
    """
    results = [make_result(vault, "total_assets", 100_000_000), make_result(vault, "total_debt", 75_000_000, block=124)]
    with pytest.raises(AssertionError, match="Mismatch of block numbers"):
        vault.get_historical_reader(stateful=False).process_result(123, SAMPLE_TIME, results)


@pytest.mark.skipif(not JSON_RPC_OPTIMISM, reason="JSON_RPC_OPTIMISM needed for the real V2 archive integration")
@pytest.mark.parametrize(
    "block,liquidity,utilisation",
    [(158_341_301, "8910.043432", 0.9172196781718864), (158_341_302, "0.043374", 0.9999995606575094)],
)
def test_llama_v2_real_withdrawal(block: int, liquidity: str, utilisation: float) -> None:
    """Read real V2 state immediately before and after the liquidity-draining withdrawal.

    This uses archive RPC directly, without launching a separate Anvil fork.
    The transaction is documented on the
    `Optimism explorer <https://explorer.optimism.io/tx/0x22f0e9940116072f8d3c3251ccb76b26f8d611b9353e12f09ef50e42be6028fd>`__.
    """
    web3 = create_multi_provider_web3(JSON_RPC_OPTIMISM)
    vault = create_vault_instance_autodetect(web3, vault_address=VAULT_ADDRESS)
    assert isinstance(vault, LlamaLendVault)
    assert vault.features == {ERC4626Feature.llamma_like}
    assert vault.controller_address == CONTROLLER_ADDRESS
    reader = vault.get_historical_reader(stateful=False)
    results = list(read_multicall_chunked(10, lambda: web3, list(reader.construct_multicalls()), block, max_workers=1, backend="threading", timestamped_results=False))
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(block)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    row = reader.process_result(block, timestamp, results)
    assert row.available_liquidity == Decimal(liquidity)
    assert row.utilisation == pytest.approx(utilisation, rel=0, abs=1e-12)
    assert row.export()["available_liquidity"] == float(liquidity)
    stats = _fetch_lending_stats(vault, Mock(features=vault.features), row.total_assets, block)
    assert stats["_available_liquidity"] == Decimal(liquidity)
    assert stats["_utilisation"] == pytest.approx(utilisation, rel=0, abs=1e-12)
