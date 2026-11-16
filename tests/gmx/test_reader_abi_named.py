"""Offline regression tests for the pinned GMX Reader tuple layout."""

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from eth_abi.exceptions import DecodingError
from web3 import Web3

from eth_defi.gmx.core.abi_named import named_abi_value, reader_named_output, reader_output_abi
from eth_defi.gmx.core.funding_fee import GetFundingFee
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.core.liquidation import _transform_position_data
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.core.oracle import OraclePrices

FIXTURES = Path(__file__).parent / "fixtures"


def _abi_type(parameter: dict) -> str:
    """Build the canonical eth-abi type string for an ABI parameter."""
    abi_type = parameter["type"]
    if abi_type.startswith("tuple"):
        suffix = abi_type[len("tuple") :]
        components = ",".join(_abi_type(component) for component in parameter["components"])
        return f"({components}){suffix}"
    return abi_type


def _decode_return(raw_hex: str, abi_parameter: dict):
    """Decode raw captured return data with the Web3 codec."""
    return Web3().codec.decode([_abi_type(abi_parameter)], bytes.fromhex(raw_hex[2:]))[0]


@pytest.fixture(scope="module")
def captured_calls() -> dict[str, Any]:
    """Read captured Arbitrum v2.2.1 return payloads."""
    fixture = json.loads((FIXTURES / "gmx_v221_reader_capture.json").read_text())
    return {
        "getMarketInfo": [call for call in fixture["calls"] if call["function"] == "getMarketInfo"],
        "getAccountPositions": next(call for call in fixture["calls"] if call["function"] == "getAccountPositions"),
    }


@pytest.mark.parametrize(
    ("label", "market_token", "funding_factor", "longs_pay_shorts"),
    [
        ("ETH/USD", "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336", 4393981092189802944630, False),
        ("BTC/USD", "0x47c031236e19d024b42f8AE6780E44A573170703", 4675868391294009690720, True),
        ("SOL/USD", "0x09400D9DB990D5ed3f35D7be61DfAEB900Af03C9", 4778895056858270138368, False),
    ],
)
def test_market_info_formatter_uses_named_v221_output(captured_calls, label, market_token, funding_factor, longs_pay_shorts):
    """Market formatting and funding inputs match direct named ABI decoding."""
    call = next(call for call in captured_calls["getMarketInfo"] if call["label"] == label)
    decoded = _decode_return(call["return_data"], reader_output_abi("getMarketInfo"))
    named = reader_named_output(decoded, "getMarketInfo")
    formatted = GetData._format_market_info_output(decoded)

    assert formatted["market_address"] == named["market"]["marketToken"]
    assert formatted["baseFunding_long_fundingFeeAmountPerSize_longToken"] == named["baseFunding"]["fundingFeeAmountPerSize"]["long"]["longToken"]
    assert formatted["nextFunding_baseFundingshort_claimableFundingAmountPerSize_shortToken"] == named["nextFunding"]["claimableFundingAmountPerSizeDelta"]["short"]["shortToken"]
    assert formatted["virtualInventoryForPositions"] == named["virtualInventory"]["virtualInventoryForPositions"]
    funding_inputs = GetFundingFee._format_funding_inputs(decoded)
    assert formatted["market_address"].lower() == market_token.lower()
    assert funding_inputs["is_long_pays_short"] is longs_pay_shorts
    assert funding_inputs["funding_factor_per_second"] == funding_factor
    rate = GetFundingFee._get_funding_factor_per_period(funding_inputs, True, 3600, 10**30, 2 * 10**30)
    assert isinstance(rate, (int, float))


def test_open_position_processing_reads_v221_named_fields(captured_calls, monkeypatch):
    """The public position dictionary preserves its keys and maps fields by name."""
    call = captured_calls["getAccountPositions"]
    decoded = _decode_return(call["return_data"], reader_output_abi("getAccountPositions"))
    named_positions = reader_named_output(decoded, "getAccountPositions")
    direct = named_positions[0]
    market = direct["addresses"]["market"]
    index_token = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    collateral_token = direct["addresses"]["collateralToken"]

    class FakeMarkets:
        def get_available_markets(self):
            return {market: {"index_token_address": index_token, "market_symbol": "ETH"}}

    processor = object.__new__(GetOpenPositions)
    processor.config = SimpleNamespace(chain="arbitrum")
    processor.markets = FakeMarkets()
    processor._get_tokens_address_dict = lambda: {
        index_token: {"decimals": 18, "symbol": "WETH"},
        collateral_token: {"decimals": 6, "symbol": "USDC"},
    }
    monkeypatch.setattr(OraclePrices, "get_recent_prices", lambda self: {})

    public = processor._get_data_processing(decoded[0])
    numbers = direct["numbers"]
    assert public["market"] == market
    assert public["position_size_usd_raw"] == numbers["sizeInUsd"]
    assert public["pending_impact_amount"] == numbers["pendingImpactAmount"]
    assert public["borrowing_factor"] == numbers["borrowingFactor"]
    assert public["increased_at_time"] == numbers["increasedAtTime"]
    assert public["decreased_at_time"] == numbers["decreasedAtTime"]
    assert public["is_long"] is direct["flags"]["isLong"]


def test_ccxt_reader_position_matching_uses_named_market_address(captured_calls):
    """Sync and async CCXT resolvers read the market address through ABI names."""
    from eth_defi.gmx.ccxt.async_support.exchange import GMX as AsyncGMX
    from eth_defi.gmx.ccxt.exchange import GMX

    call = captured_calls["getAccountPositions"]
    raw_positions = _decode_return(call["return_data"], reader_output_abi("getAccountPositions"))
    market_address = reader_named_output(raw_positions, "getAccountPositions")[0]["addresses"]["market"]

    class FakeExchange:
        def _map_market_to_symbol(self, address: str) -> str | None:
            return "ETH/USD" if address == market_address else None

    expected = reader_named_output(raw_positions, "getAccountPositions")[0]
    assert GMX._find_matching_reader_position(FakeExchange(), raw_positions, "ETH/USD") == expected
    assert AsyncGMX._find_matching_reader_position(FakeExchange(), raw_positions, "ETH/USD") == expected


def test_liquidation_position_transformer_handles_captured_position(captured_calls):
    """The liquidation transformer preserves captured Position, fee and PnL values."""
    captured = captured_calls["getAccountPositions"]
    raw_position = _decode_return(captured["return_data"], reader_output_abi("getAccountPositions"))
    named_position = reader_named_output(raw_position, "getAccountPositions")[0]
    account_info_abi = reader_output_abi("getAccountPositionInfoList")

    def build_value(parameter: dict[str, Any], values: Any = None) -> Any:
        """Build a raw account-info tuple around the captured Position values.

        :param parameter: Account-position ABI parameter to construct.
        :param values: Named values to insert at this tuple level.
        :return: ABI-shaped value for the account-position transformer.
        """
        abi_type = parameter["type"]
        values = values or {}
        if abi_type.endswith("[]"):
            element = {**parameter, "type": abi_type[:-2]}
            return [build_value(element, values)]
        if abi_type == "tuple":
            return tuple(build_value(component, values.get(component["name"])) for component in parameter["components"])
        if abi_type == "address":
            return values or "0x0000000000000000000000000000000000000000"
        if abi_type == "bytes32":
            return values or bytes(32)
        if abi_type == "bool":
            return bool(values)
        if abi_type == "string":
            return values or ""
        return values or 0

    info_values = {
        "position": named_position,
        "fees": {"funding": {"fundingFeeAmount": 101}, "borrowing": {"borrowingFeeUsd": 202}},
        "basePnlUsd": -303,
        "uncappedBasePnlUsd": -404,
        "pnlAfterPriceImpactUsd": -505,
        "positionValueInUsd": 606,
    }
    transformed = _transform_position_data(build_value(account_info_abi, info_values))

    assert len(transformed) == 1
    assert transformed[0]["position"]["addresses"]["market"] == named_position["addresses"]["market"]
    assert transformed[0]["position"]["numbers"]["sizeInUsd"] == named_position["numbers"]["sizeInUsd"]
    assert transformed[0]["fees"]["fundingFeeAmount"] == 101
    assert transformed[0]["borrowing"]["borrowingFeeUsd"] == 202
    assert transformed[0]["basePnlUsd"] == -303
    assert transformed[0]["uncappedBasePnlUsd"] == -404
    assert transformed[0]["pnlAfterPriceImpactUsd"] == -505
    assert transformed[0]["positionValueInUsd"] == 606


def test_v22c_reader_abi_rejects_v221_position_payload(captured_calls):
    """The previous Reader ABI cannot decode the v2.2.1 position payload."""
    old_abi = json.loads((FIXTURES / "gmx_v22c_reader_fragments.json").read_text())
    old_output = next(entry["outputs"][0] for entry in old_abi if entry["name"] == "getAccountPositions")
    payload = captured_calls["getAccountPositions"]["return_data"]

    with pytest.raises(DecodingError):
        _decode_return(payload, old_output)


def test_named_abi_value_rejects_missing_and_duplicate_component_names():
    """ABI tuples must have unique, non-empty names before being converted."""
    missing_name_abi = {"type": "tuple", "components": [{"name": "", "type": "uint256"}]}
    duplicate_name_abi = {
        "type": "tuple",
        "components": [{"name": "value", "type": "uint256"}, {"name": "value", "type": "uint256"}],
    }
    with pytest.raises(ValueError, match="missing ABI component name"):
        named_abi_value((1,), missing_name_abi)
    with pytest.raises(ValueError, match="duplicate sibling"):
        named_abi_value((1, 2), duplicate_name_abi)


def test_named_abi_value_rejects_fixed_size_arrays():
    """Fixed-size tuple arrays are unsupported rather than partially traversed."""
    with pytest.raises(ValueError, match="Fixed-size ABI arrays are not supported"):
        named_abi_value((1, 2), {"type": "uint256[2]"})


def test_reader_tuple_numeric_position_indexing_is_absent():
    """Catch positional Reader tuple reads in package and GMX scripts."""
    repository_root = Path(__file__).parents[2]
    package_root = Path(__file__).parents[2] / "eth_defi" / "gmx"
    scripts_root = repository_root / "scripts"
    offenders = []
    safe_layout_allowlist = [
        # EventEmitter EventData is a seven-family tuple; these accesses select each family's item list.
        ("eth_defi/gmx/historical_oracle.py", "event_data[", "EventEmitter data-family tuple access, independently covered by event decoding tests"),
        # GlvReader getGlvInfoList retains the stable (addresses, market addresses) layout.
        ("eth_defi/gmx/core/glv_stats.py", "raw_glv[", "GlvReader getGlvInfoList output layout is unchanged and tuple lengths are asserted"),
        # OHLCV rows are CCXT candle tuples, not contract outputs.
        ("eth_defi/gmx/ccxt/validation.py", "ohlcv[0][0]", "CCXT OHLCV rows use the normalised timestamp-first tuple"),
        ("eth_defi/gmx/ccxt/exchange.py", "ohlcv[0][1]", "CCXT OHLCV rows use the normalised open-price tuple"),
        # These ABI outputs have multiple unnamed top-level return values; existing length checks guard this layout.
        ("eth_defi/gmx/core/gm_prices.py", "result[0]", "Reader getMarketTokenPrice has unnamed multiple outputs; two-field length assertion guards the ABI layout"),
        ("eth_defi/gmx/order/swap_order.py", "result[0]", "Reader getSwapAmountOut has unnamed multiple outputs; three-field length assertion guards the ABI layout"),
        ("eth_defi/gmx/order/swap_order.py", "result[1]", "Reader getSwapAmountOut has unnamed multiple outputs; three-field length assertion guards the ABI layout"),
        # These are local tuples unrelated to contract or Reader outputs.
        ("scripts/gmx/gmx_ccxt_order_creation.py", "result[1]", "Local order-creation check tuple, not a contract or Reader output"),
        ("scripts/erc-4626/benchmark-sparklines.py", "result[5]", "Local render benchmark tuple, not a contract or Reader output"),
        ("scripts/erc-4626/benchmark-sparklines.py", "result[0]", "Local render benchmark tuple, not a contract or Reader output"),
        ("scripts/gmx/gmx_ccxt_positions.py", "positions[0]", "CCXT positions are normalised dictionaries, not raw Reader tuples"),
        ("scripts/gmx/gmx_ccxt_trading.py", "orders[0]", "CCXT orders are normalised dictionaries, not raw Reader tuples"),
        ("eth_defi/gmx/graphql/client.py", "positions[0]", "Subsquid GraphQL records are named dictionaries, not Reader tuples"),
    ]
    scan_roots = (package_root, scripts_root)
    for scan_root in scan_roots:
        for source in scan_root.rglob("*.py"):
            for line_number, line in enumerate(source.read_text().splitlines(), start=1):
                relative_name = source.relative_to(repository_root).as_posix()
                double_index = re.search(r"\[[0-9]+\]\[[0-9]+\]", line)
                reader_collection_index = re.search(r"\b(?:raw_(?:positions|orders|position|order)|positions|orders)\[\d+\]", line)
                numeric_result = re.search(r"\bresult\[\d+\]", line)
                if double_index or reader_collection_index or numeric_result:
                    if not any(relative_name.endswith(name) and token in line and reason for (name, token, reason) in safe_layout_allowlist):
                        offenders.append(f"{source}:{line_number}: {line.strip()}")
    assert offenders == []
