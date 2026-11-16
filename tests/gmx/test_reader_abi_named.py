"""Offline regression tests for the pinned GMX Reader tuple layout."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_abi.exceptions import DecodingError
from web3 import Web3

from eth_defi.gmx.core.abi_named import named_abi_value, reader_named_output, reader_output_abi
from eth_defi.gmx.core.funding_fee import GetFundingFee
from eth_defi.gmx.core.get_data import GetData
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
def captured_calls() -> dict[str, dict]:
    """Read captured Arbitrum v2.2.1 return payloads."""
    fixture = json.loads((FIXTURES / "gmx_v221_reader_capture.json").read_text())
    return {call["function"]: call for call in fixture["calls"] if call["function"] in {"getMarketInfo", "getAccountPositions"}}


def test_market_info_formatter_uses_named_v221_output(captured_calls):
    """Market formatting and funding inputs match direct named ABI decoding."""
    call = captured_calls["getMarketInfo"]
    decoded = _decode_return(call["return_data"], reader_output_abi("getMarketInfo"))
    named = reader_named_output(decoded, "getMarketInfo")
    formatted = GetData._format_market_info_output(decoded)

    assert formatted["market_address"] == named["market"]["marketToken"]
    assert formatted["baseFunding_long_fundingFeeAmountPerSize_longToken"] == named["baseFunding"]["fundingFeeAmountPerSize"]["long"]["longToken"]
    assert formatted["nextFunding_baseFundingshort_claimableFundingAmountPerSize_shortToken"] == named["nextFunding"]["claimableFundingAmountPerSizeDelta"]["short"]["shortToken"]
    assert formatted["virtualInventoryForPositions"] == named["virtualInventory"]["virtualInventoryForPositions"]
    funding_inputs = GetFundingFee._format_market_info_output(decoded)
    assert funding_inputs["is_long_pays_short"] is named["nextFunding"]["longsPayShorts"]
    assert funding_inputs["funding_factor_per_second"] == named["nextFunding"]["fundingFactorPerSecond"]
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
    with pytest.raises(KeyError, match="notAnAbiField"):
        numbers["notAnAbiField"]
    assert public["market"] == market
    assert public["position_size_usd_raw"] == numbers["sizeInUsd"]
    assert public["pending_impact_amount"] == numbers["pendingImpactAmount"]
    assert public["borrowing_factor"] == numbers["borrowingFactor"]
    assert public["increased_at_time"] == numbers["increasedAtTime"]
    assert public["decreased_at_time"] == numbers["decreasedAtTime"]
    assert public["is_long"] is direct["flags"]["isLong"]


def test_v22c_reader_abi_misdecodes_v221_position_payload(captured_calls):
    """The previous Reader ABI decodes the new payload with a shifted flag."""
    old_abi = json.loads((FIXTURES / "gmx_v22c_reader_fragments.json").read_text())
    old_output = next(entry["outputs"][0] for entry in old_abi if entry["name"] == "getAccountPositions")
    new_output = reader_output_abi("getAccountPositions")
    payload = captured_calls["getAccountPositions"]["return_data"]

    try:
        old_decoded = _decode_return(payload, old_output)
    except DecodingError:
        return
    old_named = named_abi_value(old_decoded, old_output)[0]
    new_decoded = _decode_return(payload, new_output)
    new_named = reader_named_output(new_decoded, "getAccountPositions")[0]

    assert old_named["flags"]["isLong"] is False
    assert new_named["flags"]["isLong"] is True
    assert old_named["numbers"]["borrowingFactor"] != new_named["numbers"]["borrowingFactor"]


def test_reader_tuple_numeric_position_indexing_is_absent():
    """Catch regressions to numeric Position.Numbers tuple access."""
    package_root = Path(__file__).parents[2] / "eth_defi" / "gmx"
    offenders = []
    for source in package_root.rglob("*.py"):
        for line_number, line in enumerate(source.read_text().splitlines(), start=1):
            if "[1][" in line and any(f"[1][{index}]" in line for index in range(10)):
                offenders.append(f"{source}:{line_number}: {line.strip()}")
    assert offenders == []
