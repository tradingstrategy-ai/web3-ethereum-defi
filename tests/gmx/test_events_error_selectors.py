"""Tests for the v2.2.1 custom-error selectors in :data:`eth_defi.gmx.events.GMX_ERROR_SELECTORS`."""

import json
from pathlib import Path

import pytest
from eth_abi import encode
from eth_utils import keccak

import eth_defi.gmx.events as events_module
from eth_defi.gmx.events import GMX_ERROR_SELECTORS, decode_error_reason

ABI_DIR = Path(events_module.__file__).parent.parent / "abi" / "gmx"


def _selector(name: str, types: list[str]) -> str:
    return keccak(text=f"{name}({','.join(types)})")[:4].hex()


@pytest.mark.parametrize(
    ("name", "types"),
    [
        ("OrderNotUpdatable", ["uint256"]),
        ("RequestNotYetCancellable", ["uint256", "uint256", "string"]),
    ],
)
def test_v221_error_selector_is_registered(name, types):
    """The new v2.2.1 order errors are in the table under their keccak selector."""
    assert GMX_ERROR_SELECTORS[_selector(name, types)] == (name, types)


def test_v221_error_selectors_match_signatures():
    """Every error in the vendored ABIs maps to a table entry with its own keccak selector."""
    checked = 0
    for abi_file in ("ExchangeRouter.json", "Reader.json", "GlvReader.json", "Oracle.json"):
        for entry in json.loads((ABI_DIR / abi_file).read_text()):
            if entry.get("type") != "error":
                continue
            types = [i["type"] for i in entry["inputs"]]
            if any(t.startswith("tuple") for t in types):
                continue
            selector = _selector(entry["name"], types)
            assert selector in GMX_ERROR_SELECTORS, f"{entry['name']} ({selector}) missing from {abi_file}"
            assert GMX_ERROR_SELECTORS[selector] == (entry["name"], types)
            checked += 1
    assert checked > 20


def test_every_registered_error_key_matches_its_signature():
    """No selector key is derived from a shifted slice of the signature hash."""
    for selector, (name, types) in GMX_ERROR_SELECTORS.items():
        assert selector == _selector(name, types), f"{name}({','.join(types)}) has wrong selector {selector}"


def test_decode_order_not_updatable():
    """decode_error_reason() names the v2.2.1 OrderNotUpdatable error with its label."""
    reason = bytes.fromhex(_selector("OrderNotUpdatable", ["uint256"])) + encode(["uint256"], [2])
    assert decode_error_reason(reason) == "OrderNotUpdatable(orderType: 2)"


def test_decode_request_not_yet_cancellable():
    """The dynamic ``string`` parameter of RequestNotYetCancellable decodes."""
    reason = bytes.fromhex(_selector("RequestNotYetCancellable", ["uint256", "uint256", "string"])) + encode(["uint256", "uint256", "string"], [5, 300, "order"])
    assert decode_error_reason(reason) == 'RequestNotYetCancellable(requestAge: 5, requestExpirationAge: 300, requestType: "order")'
