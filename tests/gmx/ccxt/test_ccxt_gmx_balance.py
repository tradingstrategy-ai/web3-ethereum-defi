"""Integration tests for the GMX CCXT fetch_balance calculation.

Regression tests for the balance accounting fix:

Before the fix ``fetch_balance()`` computed::

    total_amount = balance_float  # wallet only
    free_amount = max(0, balance_float - used_amount)  # double-subtracted

This caused freqtrade to re-base ``starting_capital`` downward on every loop
iteration as GMX positions were opened, eventually showing a negative
available balance even though the vault held ample USDC.

After the fix::

    free_amount = balance_float  # wallet IS the free amount
    total_amount = balance_float + used_amount  # true vault value

These tests make real RPC calls to Arbitrum mainnet via the ``gmx_arbitrum``
fixture — no mocking.
"""

import os

import pytest
from flaky import flaky

# A well-known Arbitrum address used as a read-only test wallet.
# This is the Lagoon vault Safe used for GMX trading; it will hold
# some USDC balance even if no positions are currently open.
_KNOWN_ARBITRUM_WALLET = "0x64A449D8311ECAf863CB840b04a58caEEEbFaB4C"

# Fallback: use a zero-like address that will have zero balance — the
# structure assertions still hold regardless of whether the balance is empty.
_ZERO_WALLET = "0x0000000000000000000000000000000000000001"


# ---------------------------------------------------------------------------
# Structure and invariant tests
# ---------------------------------------------------------------------------


@flaky(max_runs=3, min_passes=1)
def test_fetch_balance_result_has_required_keys(gmx_arbitrum):
    """Verify fetch_balance result contains CCXT-standard top-level keys.

    :param gmx_arbitrum:
        Real GMX CCXT instance connected to Arbitrum mainnet (view-only).
    """
    result = gmx_arbitrum.fetch_balance(params={"wallet_address": _ZERO_WALLET})

    assert "free" in result
    assert "used" in result
    assert "total" in result


@flaky(max_runs=3, min_passes=1)
def test_fetch_balance_usdc_keys(gmx_arbitrum):
    """Verify USDC entry in fetch_balance has free, used, and total keys.

    :param gmx_arbitrum:
        Real GMX CCXT instance connected to Arbitrum mainnet (view-only).
    """
    result = gmx_arbitrum.fetch_balance(params={"wallet_address": _ZERO_WALLET})

    assert "USDC" in result
    usdc = result["USDC"]
    assert set(usdc.keys()) == {"free", "used", "total"}


@flaky(max_runs=3, min_passes=1)
def test_fetch_balance_total_equals_free_plus_used(gmx_arbitrum):
    """Verify the balance invariant: total == free + used.

    This invariant must hold regardless of whether any GMX positions are open.
    Uses a known Arbitrum wallet address so the call exercises the full
    position-lookup code path.

    :param gmx_arbitrum:
        Real GMX CCXT instance connected to Arbitrum mainnet (view-only).
    """
    result = gmx_arbitrum.fetch_balance(params={"wallet_address": _KNOWN_ARBITRUM_WALLET})

    usdc = result["USDC"]
    assert usdc["total"] == pytest.approx(usdc["free"] + usdc["used"], abs=1e-4)


@flaky(max_runs=3, min_passes=1)
def test_fetch_balance_all_values_non_negative(gmx_arbitrum):
    """Verify that free, used, and total are all non-negative.

    Before the fix, ``free`` could go negative when used > wallet balance.

    :param gmx_arbitrum:
        Real GMX CCXT instance connected to Arbitrum mainnet (view-only).
    """
    result = gmx_arbitrum.fetch_balance(params={"wallet_address": _KNOWN_ARBITRUM_WALLET})

    usdc = result["USDC"]
    assert usdc["free"] >= 0.0, f"free balance is negative: {usdc['free']}"
    assert usdc["used"] >= 0.0, f"used balance is negative: {usdc['used']}"
    assert usdc["total"] >= 0.0, f"total balance is negative: {usdc['total']}"


@flaky(max_runs=3, min_passes=1)
def test_fetch_balance_total_gte_free(gmx_arbitrum):
    """Verify that total >= free (total includes locked collateral).

    :param gmx_arbitrum:
        Real GMX CCXT instance connected to Arbitrum mainnet (view-only).
    """
    result = gmx_arbitrum.fetch_balance(params={"wallet_address": _KNOWN_ARBITRUM_WALLET})

    usdc = result["USDC"]
    assert usdc["total"] >= usdc["free"] - 1e-6, f"total ({usdc['total']}) is less than free ({usdc['free']})"


@flaky(max_runs=3, min_passes=1)
def test_fetch_balance_unknown_wallet_returns_valid_structure(gmx_arbitrum):
    """Verify fetch_balance returns a well-formed result for any wallet address.

    Uses a contract-like address that is extremely unlikely to hold user funds.
    The values may be non-zero but the structure must always be valid.

    :param gmx_arbitrum:
        Real GMX CCXT instance connected to Arbitrum mainnet (view-only).
    """
    # 0xdead…dead is a common burn address with no meaningful user activity
    burn_address = "0x000000000000000000000000000000000000dEaD"
    result = gmx_arbitrum.fetch_balance(params={"wallet_address": burn_address})

    assert "USDC" in result
    usdc = result["USDC"]
    assert set(usdc.keys()) == {"free", "used", "total"}
    assert isinstance(usdc["free"], float)
    assert isinstance(usdc["used"], float)
    assert isinstance(usdc["total"], float)
    assert usdc["total"] == pytest.approx(usdc["free"] + usdc["used"], abs=1e-4)


@flaky(max_runs=3, min_passes=1)
def test_fetch_balance_free_does_not_exceed_total(gmx_arbitrum):
    """Verify free never exceeds total across all token entries.

    :param gmx_arbitrum:
        Real GMX CCXT instance connected to Arbitrum mainnet (view-only).
    """
    result = gmx_arbitrum.fetch_balance(params={"wallet_address": _KNOWN_ARBITRUM_WALLET})

    # Check every per-token dict in the result
    for key, value in result.items():
        if isinstance(value, dict) and "free" in value and "total" in value:
            assert value["free"] <= value["total"] + 1e-6, f"Token {key}: free ({value['free']}) > total ({value['total']})"
