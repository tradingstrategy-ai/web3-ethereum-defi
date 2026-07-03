"""Fail-loud collateral validation in ``OrderArgumentParser`` (issue #1178, B3).

The 2026-07-01/02 live incident: a poisoned symbol→market mapping handed the
parser a ``market_key`` whose pool rejects USDC. ``_check_if_valid_collateral_for_market``
**correctly detected** the mismatch and raised — but ``_handle_missing_collateral_address``
swallowed the exception behind a bare ``except Exception`` ("relying on GMX
router swap_path"), and ``_handle_missing_swap_path`` short-circuits to
``swap_path = []`` whenever ``start_token == collateral`` (always true for the
USDC-only bot). The doomed order shipped, burned gas, and died on-chain as an
``InvalidCollateralTokenForMarket`` keeper cancel — three strikes locking the
pair for 60 minutes.

B3 restores a loud local failure for exactly that case, with exception
classification so an *indeterminate* lookup (market_key absent from the RPC
markets snapshot — ``KeyError``) is tolerated, while a *definitive* rejection
(market resolved, collateral matches neither token) fails pre-flight:

- definitive rejection + ``start == collateral`` (no swap leg will ever be
  built) → raise :class:`InvalidCollateralForMarketError` before submission;
- definitive rejection + ``start != collateral`` → a real swap route is built
  (issue #67 flow) — unchanged;
- indeterminate (``KeyError``) → warn and proceed — never block on "couldn't
  verify";
- decrease orders never run ``_handle_missing_swap_path`` (``swap_path`` is
  not in their required keys), so closes can never be blocked by this guard.

All tests run offline — ``Markets`` / ``GMXConfig`` / token metadata are
patched, following ``test_order_argument_parser_refresh_on_miss.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# Checksummed fixture addresses (same convention as the refresh-on-miss tests).
_BTC_INDEX = "0x47904963fc8b2340414262125aF798B9655E58Cd"
_USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
_WBTC = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
_TBTC = "0x6c84a8f1c29108F47a79964b5Fe888D4f4D0dE40"
_REAL_BTC_MARKET = "0x47c031236e19d024b42f8AE6780E44A573170703"
_SYNTH_BTC_MARKET = "0xd62068697bCc92AF253225676D618B0C9f17C663"


def _usdc_market() -> dict:
    """A market that accepts USDC (long WBTC / short USDC)."""
    return {
        "gmx_market_address": _REAL_BTC_MARKET,
        "market_symbol": "BTC",
        "index_token_address": _BTC_INDEX,
        "long_token_address": _WBTC,
        "short_token_address": _USDC,
        "market_metadata": {"symbol": "BTC", "decimals": 8},
        "long_token_metadata": {"symbol": "WBTC", "decimals": 8},
        "short_token_metadata": {"symbol": "USDC", "decimals": 6},
    }


def _synthetic_market() -> dict:
    """A single-sided market that does NOT accept USDC (tBTC-tBTC)."""
    return {
        "gmx_market_address": _SYNTH_BTC_MARKET,
        "market_symbol": "BTC2",
        "index_token_address": _BTC_INDEX,
        "long_token_address": _TBTC,
        "short_token_address": _TBTC,
        "market_metadata": {"symbol": "BTC2", "decimals": 8},
        "long_token_metadata": {"symbol": "tBTC", "decimals": 8},
        "short_token_metadata": {"symbol": "tBTC", "decimals": 8},
    }


def _build_config() -> MagicMock:
    config = MagicMock()
    config.chain = "arbitrum"
    config.web3 = MagicMock()
    config.web3.eth.chain_id = 42161
    config.user_wallet_address = None
    return config


@pytest.fixture(autouse=True)
def _clean_cache():
    """Reset the class-level markets cache around every test."""
    from eth_defi.gmx.core.markets import Markets

    Markets.invalidate_cache()
    yield
    Markets.invalidate_cache()


def _build_parser(monkeypatch, markets: dict):
    """Offline ``OrderArgumentParser`` whose markets snapshot is ``markets``."""
    from eth_defi.gmx.order import order_argument_parser as parser_mod
    from eth_defi.gmx.order.order_argument_parser import OrderArgumentParser

    monkeypatch.setattr(
        parser_mod.Markets,
        "get_available_markets",
        lambda self: markets,
    )
    monkeypatch.setattr(parser_mod, "GMXConfig", MagicMock())
    # Token metadata for find_key_by_symbol (offline).
    monkeypatch.setattr(
        parser_mod,
        "_get_token_metadata_dict",
        lambda web3, chain, use_cache=True: {
            _USDC: {"symbol": "USDC", "decimals": 6},
            _WBTC: {"symbol": "WBTC", "decimals": 8},
            _TBTC: {"symbol": "tBTC", "decimals": 8},
        },
    )
    return OrderArgumentParser(_build_config(), is_increase=True)


# ---------------------------------------------------------------------------
# _check_if_valid_collateral_for_market — dedicated exception class
# ---------------------------------------------------------------------------


def test_rejection_raises_dedicated_exception_class(monkeypatch):
    """Definitive rejection raises InvalidCollateralForMarketError with context.

    Must stay a subclass of Exception with market_key / collateral / Hint in
    the message — the live-RPC contract test in test_market_disambiguation.py
    pins that message shape.
    """
    from eth_defi.gmx.order.order_argument_parser import (
        InvalidCollateralForMarketError,
    )

    parser = _build_parser(monkeypatch, {_SYNTH_BTC_MARKET: _synthetic_market()})
    parser.parameters_dict = {"chain": "arbitrum", "market_key": _SYNTH_BTC_MARKET}

    with pytest.raises(InvalidCollateralForMarketError) as exc_info:
        parser._check_if_valid_collateral_for_market(_USDC)

    message = str(exc_info.value)
    assert _SYNTH_BTC_MARKET in message
    assert _USDC in message
    assert "Hint" in message
    assert isinstance(exc_info.value, Exception)


# ---------------------------------------------------------------------------
# _handle_missing_collateral_address — exception classification
# ---------------------------------------------------------------------------


def test_definitive_rejection_sets_flag_false(monkeypatch):
    """Market resolved, collateral matches neither token → flag False."""
    parser = _build_parser(monkeypatch, {_SYNTH_BTC_MARKET: _synthetic_market()})
    parser.parameters_dict = {
        "chain": "arbitrum",
        "market_key": _SYNTH_BTC_MARKET,
        "collateral_token_symbol": "USDC",
    }
    parser._handle_missing_collateral_address()

    assert parser._collateral_directly_supported is False
    # collateral_address is still set — downstream handlers decide the outcome.
    assert parser.parameters_dict["collateral_address"] == _USDC


def test_accepted_collateral_sets_flag_true(monkeypatch):
    """Market accepts USDC → flag True, order proceeds untouched."""
    parser = _build_parser(monkeypatch, {_REAL_BTC_MARKET: _usdc_market()})
    parser.parameters_dict = {
        "chain": "arbitrum",
        "market_key": _REAL_BTC_MARKET,
        "collateral_token_symbol": "USDC",
    }
    parser._handle_missing_collateral_address()

    assert parser._collateral_directly_supported is True


def test_arbitrum_btc_collateral_still_runs_validation(monkeypatch):
    """Arbitrum BTC collateral (mapped to WBTC) must run the SAME validation.

    Adversarial-review finding: the ``collateral_token_symbol == "BTC" and
    chain == "arbitrum"`` special case used to set the WBTC address and
    ``return`` early — bypassing ``_check_if_valid_collateral_for_market`` and
    leaving ``_collateral_directly_supported`` as ``None``. A caller passing
    ``collateral_symbol="BTC"`` (e.g. GMXTrading) against a pool that rejects
    WBTC then walked straight around the new guard: the swap-path gate saw
    ``None`` (indeterminate), shipped ``swap_path=[]``, and reproduced the
    keeper-cancel failure class this whole change exists to prevent.

    BTC must resolve to WBTC WITHOUT returning early, then run validation like
    every other collateral.
    """
    # BTC index pool that only accepts tBTC (rejects WBTC).
    parser = _build_parser(monkeypatch, {_SYNTH_BTC_MARKET: _synthetic_market()})
    parser.parameters_dict = {
        "chain": "arbitrum",
        "market_key": _SYNTH_BTC_MARKET,
        "collateral_token_symbol": "BTC",
    }
    parser._handle_missing_collateral_address()

    # WBTC not in (tBTC, tBTC) → DEFINITIVE rejection, not indeterminate.
    assert parser._collateral_directly_supported is False
    assert parser.parameters_dict["collateral_address"] == _WBTC


def test_arbitrum_btc_collateral_accepted_by_wbtc_pool(monkeypatch):
    """Arbitrum BTC collateral against the real WBTC-USDC pool → accepted.

    Guards the positive side of the same fix: routing BTC through normal
    validation must NOT falsely reject the real pool whose long token is WBTC.
    """
    parser = _build_parser(monkeypatch, {_REAL_BTC_MARKET: _usdc_market()})
    parser.parameters_dict = {
        "chain": "arbitrum",
        "market_key": _REAL_BTC_MARKET,
        "collateral_token_symbol": "BTC",
    }
    parser._handle_missing_collateral_address()

    assert parser._collateral_directly_supported is True
    assert parser.parameters_dict["collateral_address"] == _WBTC


def test_arbitrum_btc_rejection_blocks_swap_path_gate(monkeypatch):
    """End-to-end: BTC-on-tBTC-pool + start==collateral raises pre-flight.

    The full path the finding describes: BTC collateral, a pool that rejects
    WBTC, and start_token == collateral (no swap leg) must now raise
    InvalidCollateralForMarketError instead of shipping an empty swap path.
    """
    from eth_defi.gmx.order.order_argument_parser import (
        InvalidCollateralForMarketError,
    )

    parser = _build_parser(monkeypatch, {_SYNTH_BTC_MARKET: _synthetic_market()})
    parser.parameters_dict = {
        "chain": "arbitrum",
        "market_key": _SYNTH_BTC_MARKET,
        "collateral_token_symbol": "BTC",
    }
    parser._handle_missing_collateral_address()
    assert parser._collateral_directly_supported is False

    parser.parameters_dict["start_token_address"] = _WBTC  # start == collateral
    with pytest.raises(InvalidCollateralForMarketError):
        parser._handle_missing_swap_path()


def test_unknown_market_key_is_indeterminate_not_rejection(monkeypatch):
    """market_key absent from the RPC snapshot (KeyError) → flag None.

    A stale/partial Markets snapshot must NOT be classified as a rejection —
    we could not verify either way, so the order must not be blocked.
    """
    parser = _build_parser(monkeypatch, {_REAL_BTC_MARKET: _usdc_market()})
    parser.parameters_dict = {
        "chain": "arbitrum",
        "market_key": "0x0000000000000000000000000000000000000bad",
        "collateral_token_symbol": "USDC",
    }
    parser._handle_missing_collateral_address()

    assert parser._collateral_directly_supported is None


# ---------------------------------------------------------------------------
# _handle_missing_swap_path — the fail-loud gate
# ---------------------------------------------------------------------------


def _swap_path_parser(monkeypatch, flag: bool | None):
    parser = _build_parser(monkeypatch, {_REAL_BTC_MARKET: _usdc_market()})
    parser._collateral_directly_supported = flag
    parser.parameters_dict = {
        "chain": "arbitrum",
        "market_key": _SYNTH_BTC_MARKET,
        "start_token_address": _USDC,
        "collateral_address": _USDC,  # start == collateral → no swap leg
    }
    return parser


def test_swap_path_raises_on_definitive_rejection(monkeypatch):
    """Rejected collateral + start == collateral → loud pre-flight failure.

    Pre-B3 this shipped swap_path=[] and died on-chain as a keeper cancel.
    """
    from eth_defi.gmx.order.order_argument_parser import (
        InvalidCollateralForMarketError,
    )

    parser = _swap_path_parser(monkeypatch, flag=False)

    with pytest.raises(InvalidCollateralForMarketError) as exc_info:
        parser._handle_missing_swap_path()

    message = str(exc_info.value)
    assert _SYNTH_BTC_MARKET in message
    assert "swap" in message.lower()
    assert "swap_path" not in parser.parameters_dict  # order never completed


def test_swap_path_proceeds_when_indeterminate(monkeypatch):
    """Flag None (couldn't verify) → tolerate, swap_path=[] as before."""
    parser = _swap_path_parser(monkeypatch, flag=None)
    parser._handle_missing_swap_path()
    assert parser.parameters_dict["swap_path"] == []


def test_swap_path_proceeds_when_accepted(monkeypatch):
    """Flag True (verified accepted) → swap_path=[] as before."""
    parser = _swap_path_parser(monkeypatch, flag=True)
    parser._handle_missing_swap_path()
    assert parser.parameters_dict["swap_path"] == []


def test_swap_path_default_flag_is_tolerant(monkeypatch):
    """A parser whose collateral handler never ran must behave as indeterminate.

    Callers may pre-supply collateral_address (so _handle_missing_collateral_address
    is skipped); the guard must not fire from an unset attribute.
    """
    parser = _build_parser(monkeypatch, {_REAL_BTC_MARKET: _usdc_market()})
    parser.parameters_dict = {
        "chain": "arbitrum",
        "start_token_address": _USDC,
        "collateral_address": _USDC,
    }
    parser._handle_missing_swap_path()
    assert parser.parameters_dict["swap_path"] == []


def test_rejection_message_survives_missing_metadata_keys(monkeypatch):
    """A definitive rejection must raise even if metadata dicts are entirely absent.

    Adversarial-review finding: the message builder used bracket access
    (market['long_token_metadata']) on optional metadata AFTER the address
    check had already proven rejection. A market dict missing that key (not
    just missing 'symbol' inside it) raised KeyError, which the caller
    classifies as indeterminate — silently downgrading a definitive rejection
    to "proceed unverified" and defeating the whole point of B3.
    """
    from eth_defi.gmx.order.order_argument_parser import (
        InvalidCollateralForMarketError,
    )

    bare_market = {
        _SYNTH_BTC_MARKET: {
            "gmx_market_address": _SYNTH_BTC_MARKET,
            "market_symbol": "BTC2",
            "index_token_address": _BTC_INDEX,
            "long_token_address": _TBTC,
            "short_token_address": _TBTC,
            # long_token_metadata / short_token_metadata deliberately ABSENT.
        }
    }
    parser = _build_parser(monkeypatch, bare_market)
    parser.parameters_dict = {"chain": "arbitrum", "market_key": _SYNTH_BTC_MARKET}

    with pytest.raises(InvalidCollateralForMarketError) as exc_info:
        parser._check_if_valid_collateral_for_market(_USDC)

    assert _SYNTH_BTC_MARKET in str(exc_info.value)


def test_missing_metadata_does_not_relax_swap_path_gate(monkeypatch):
    """End-to-end: missing metadata must still result in a raised swap-path gate.

    Regression for the same finding via the full _handle_missing_collateral_address
    -> _handle_missing_swap_path path — proves the classification stays
    "definitive rejection" (False), not silently "indeterminate" (None).
    """
    from eth_defi.gmx.order.order_argument_parser import (
        InvalidCollateralForMarketError,
    )

    bare_market = {
        _SYNTH_BTC_MARKET: {
            "gmx_market_address": _SYNTH_BTC_MARKET,
            "market_symbol": "BTC2",
            "index_token_address": _BTC_INDEX,
            "long_token_address": _TBTC,
            "short_token_address": _TBTC,
        }
    }
    parser = _build_parser(monkeypatch, bare_market)
    parser.parameters_dict = {
        "chain": "arbitrum",
        "market_key": _SYNTH_BTC_MARKET,
        "collateral_token_symbol": "USDC",
    }
    parser._handle_missing_collateral_address()
    assert parser._collateral_directly_supported is False  # NOT None

    parser.parameters_dict["start_token_address"] = _USDC
    with pytest.raises(InvalidCollateralForMarketError):
        parser._handle_missing_swap_path()


def test_real_swap_leg_still_built_when_start_differs(monkeypatch):
    """Rejected collateral + start != collateral → issue-#67 swap flow intact."""
    from eth_defi.gmx.order import order_argument_parser as parser_mod

    parser = _build_parser(monkeypatch, {_REAL_BTC_MARKET: _usdc_market()})
    parser._collateral_directly_supported = False
    parser.parameters_dict = {
        "chain": "arbitrum",
        "market_key": _SYNTH_BTC_MARKET,
        "start_token_address": _USDC,
        "collateral_address": _TBTC,  # start != collateral → real route
    }
    monkeypatch.setattr(
        parser_mod,
        "determine_swap_route",
        lambda markets, start, out, chain: ([_REAL_BTC_MARKET], False),
    )
    parser._handle_missing_swap_path()
    assert parser.parameters_dict["swap_path"] == [_REAL_BTC_MARKET]
