"""Unit tests for the GMX V2 custom-error selector decoder.

Covers :func:`eth_defi.gmx.errors.decode_gmx_revert_selector` and the
integration point in :mod:`eth_defi.gmx.ccxt.exchange` where the decoder
augments keeper-cancel error messages.

The anchor case is the live 2026-05-22 observation: a BTC long open was
keeper-cancelled with selector ``0x839c693e`` (= the keccak prefix of
``InvalidCollateralTokenForMarket(address,address)`` in the
gmx-synthetics ``Errors.sol``).
"""

from __future__ import annotations

from eth_utils import keccak

from eth_defi.gmx.errors import (
    GmxError,
    _GMX_ERROR_REGISTRY,
    decode_gmx_revert_selector,
)


# ---------------------------------------------------------------------------
# decode_gmx_revert_selector — happy paths
# ---------------------------------------------------------------------------


def test_decode_known_selector_invalid_collateral_token():
    """0x839c693e is the live-observed selector for InvalidCollateralTokenForMarket."""
    err = decode_gmx_revert_selector("0x839c693e")
    assert err is not None
    assert err.name == "InvalidCollateralTokenForMarket"
    assert err.selector == "0x839c693e"
    assert err.params == ("address", "address")
    # Curated description must mention "collateral token" so operators can grep.
    assert "collateral token" in err.description.lower()


def test_decode_handles_long_revert_data():
    """A real on-chain revert blob has selector + ABI-encoded params.

    The decoder must only inspect the first 4 bytes (8 hex chars after 0x).
    """
    # Selector + 2 address params (32 bytes each, ABI-encoded).
    payload = "0x839c693e" + "00" * 64
    err = decode_gmx_revert_selector(payload)
    assert err is not None
    assert err.name == "InvalidCollateralTokenForMarket"


def test_decode_handles_uppercase_input():
    """Selectors are sometimes uppercased; lookup must be case-insensitive."""
    err = decode_gmx_revert_selector("0x839C693E")
    assert err is not None
    assert err.name == "InvalidCollateralTokenForMarket"


def test_decode_handles_missing_prefix():
    """Selector without leading ``0x`` should still resolve."""
    err = decode_gmx_revert_selector("839c693e")
    assert err is not None
    assert err.name == "InvalidCollateralTokenForMarket"


def test_decode_handles_mixed_case_with_long_payload():
    """Combination of mixed case + ABI payload trailing bytes."""
    err = decode_gmx_revert_selector("0x839C693E" + "DEADBEEF" * 16)
    assert err is not None
    assert err.name == "InvalidCollateralTokenForMarket"


# ---------------------------------------------------------------------------
# decode_gmx_revert_selector — unknown / malformed inputs return None
# ---------------------------------------------------------------------------


def test_decode_unknown_selector_returns_none():
    """An invented selector must produce ``None`` rather than raising."""
    assert decode_gmx_revert_selector("0xdeadbeef") is None


def test_decode_none_returns_none():
    """``None`` input must short-circuit to ``None``."""
    assert decode_gmx_revert_selector(None) is None


def test_decode_empty_string_returns_none():
    """An empty string must produce ``None``."""
    assert decode_gmx_revert_selector("") is None


def test_decode_too_short_returns_none():
    """A hex string shorter than 4 bytes cannot be a selector."""
    assert decode_gmx_revert_selector("0x12") is None
    assert decode_gmx_revert_selector("0x1234") is None  # only 2 bytes
    assert decode_gmx_revert_selector("12") is None


# ---------------------------------------------------------------------------
# Registry invariants
# ---------------------------------------------------------------------------


def test_registry_has_at_least_20_entries():
    """Plan acceptance: registry must cover the top ~20 GMX V2 errors.

    The auto-generated registry is sourced from the full Errors.sol so
    actual size is in the hundreds, but pin the >= 20 floor explicitly.
    """
    assert len(_GMX_ERROR_REGISTRY) >= 20


def test_registry_keys_are_canonical_4byte_selectors():
    """Every key must be a lowercase 0x-prefixed 4-byte hex string."""
    for key in _GMX_ERROR_REGISTRY:
        assert key.startswith("0x")
        assert len(key) == 10  # "0x" + 8 hex chars
        assert key == key.lower()
        # Body must be valid hex.
        int(key, 16)


def test_registry_values_are_gmx_errors_with_matching_selector():
    """Every stored ``GmxError`` must agree with its dict key."""
    for key, value in _GMX_ERROR_REGISTRY.items():
        assert isinstance(value, GmxError)
        assert value.selector == key
        assert value.name  # non-empty


def test_anchor_selector_matches_keccak_locally():
    """Anchor selector is reproducible from keccak — no copy-paste hazard.

    This guards against silently regenerating the registry with a buggy
    parser: if anyone changes how the selector is computed and forgets to
    update the registry, this test fails immediately on the known anchor.
    """
    sig = "InvalidCollateralTokenForMarket(address,address)"
    computed = "0x" + keccak(text=sig)[:4].hex()
    assert computed == "0x839c693e"
    assert _GMX_ERROR_REGISTRY[computed].name == "InvalidCollateralTokenForMarket"


def test_curated_descriptions_cover_operational_errors():
    """The most operationally-important errors must have descriptions.

    These are the errors operators see daily and need to grep for.
    """
    operational_errors = {
        "InvalidCollateralTokenForMarket",
        "InsufficientCollateralAmount",
        "OrderNotFulfillableAtAcceptablePrice",
        "InsufficientPoolAmount",
        "MaxOpenInterestExceeded",
        "DisabledMarket",
        "InsufficientExecutionFee",
    }
    described = {
        v.name for v in _GMX_ERROR_REGISTRY.values() if v.description
    }
    missing = operational_errors - described
    assert not missing, f"Operational errors missing descriptions: {missing}"


# ---------------------------------------------------------------------------
# Integration: adapter exception-message construction
# ---------------------------------------------------------------------------


def test_adapter_keeper_cancel_message_includes_decoded_name():
    """Mimic the exchange.py keeper-cancel path.

    When the legacy ``decode_error_reason`` returns
    ``"Unknown error (selector: 0x839c693e)"``, the adapter must upgrade
    that to a human-readable name+description before raising
    ``InvalidOrder``.
    """
    # Inline the small fragment from exchange.py so the test is hermetic
    # (no need to spin up the full Exchange class).
    decoded_error = "Unknown error (selector: 0x839c693e)"
    if decoded_error and decoded_error.startswith("Unknown error (selector: 0x"):
        _selector_hex = decoded_error.split("0x", 1)[1].rstrip(")")
        _named = decode_gmx_revert_selector(_selector_hex)
        if _named is not None:
            _desc = f" — {_named.description}" if _named.description else ""
            decoded_error = f"{_named.name}{_desc} (selector: {_named.selector})"

    assert decoded_error == (
        "InvalidCollateralTokenForMarket — "
        "The collateral token is not accepted by this market "
        "(selector: 0x839c693e)"
    )


def test_adapter_keeper_cancel_message_falls_through_for_unknown_selector():
    """For an unknown selector the message must be left untouched."""
    decoded_error = "Unknown error (selector: 0xdeadbeef)"
    original = decoded_error
    if decoded_error and decoded_error.startswith("Unknown error (selector: 0x"):
        _selector_hex = decoded_error.split("0x", 1)[1].rstrip(")")
        _named = decode_gmx_revert_selector(_selector_hex)
        if _named is not None:
            _desc = f" — {_named.description}" if _named.description else ""
            decoded_error = f"{_named.name}{_desc} (selector: {_named.selector})"
    assert decoded_error == original
