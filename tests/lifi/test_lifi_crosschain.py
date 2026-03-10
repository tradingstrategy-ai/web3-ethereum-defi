"""Integration tests for LI.FI cross-chain gas feeding.

These tests make real API calls to the LI.FI API and real RPC calls
to check balances, but do not execute any transactions (dry run only).

To run:

.. code-block:: shell

    source .local-test.env && poetry run pytest tests/lifi/test_lifi_crosschain.py -s --log-cli-level=info --timeout=120

"""

import os
from decimal import Decimal

import pytest

from eth_account import Account

from eth_defi.hotwallet import HotWallet
from eth_defi.lifi.api import fetch_lifi_native_token_prices, fetch_lifi_token_price_usd
from eth_defi.lifi.constants import LIFI_NATIVE_TOKEN_ADDRESS
from eth_defi.lifi.crosschain import (
    CrossChainSwap,
    fetch_crosschain_gas_balances,
    prepare_crosschain_swaps,
)
from eth_defi.lifi.quote import fetch_lifi_quote
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN


pytestmark = pytest.mark.skipif(
    not os.environ.get("JSON_RPC_ETHEREUM") or not os.environ.get("JSON_RPC_BASE"),
    reason="JSON_RPC_ETHEREUM and JSON_RPC_BASE environment variables needed",
)


@pytest.fixture
def ethereum_web3():
    return create_multi_provider_web3(os.environ["JSON_RPC_ETHEREUM"])


@pytest.fixture
def base_web3():
    return create_multi_provider_web3(os.environ["JSON_RPC_BASE"])


@pytest.fixture
def wallet():
    """Create a random wallet for testing (no funds needed for dry run)."""
    account = Account.create()
    return HotWallet(account)


def test_fetch_native_token_price():
    """Fetch ETH price from LI.FI token endpoint."""
    price = fetch_lifi_token_price_usd(chain_id=1)
    assert isinstance(price, Decimal)
    # ETH should be in a reasonable range
    assert price > Decimal("100")
    assert price < Decimal("100000")


def test_fetch_multiple_native_token_prices():
    """Fetch native token prices for multiple chains."""
    prices = fetch_lifi_native_token_prices([1, 8453, 42161])
    assert len(prices) == 3
    # All chains should have a price
    for chain_id in [1, 8453, 42161]:
        assert chain_id in prices
        assert prices[chain_id] > Decimal("0")
    # ETH price on Ethereum and Base should be similar (both use ETH)
    assert prices[1] == pytest.approx(prices[8453], rel=Decimal("0.1"))


def test_fetch_lifi_quote_native_bridge():
    """Fetch a real LI.FI quote for bridging ETH from Ethereum to Base."""
    # Use a random address - we only need a valid address format
    dummy_address = "0x1234567890abcdef1234567890abcdef12345678"

    quote = fetch_lifi_quote(
        from_chain_id=1,
        to_chain_id=8453,
        from_token=LIFI_NATIVE_TOKEN_ADDRESS,
        to_token=LIFI_NATIVE_TOKEN_ADDRESS,
        from_amount=10**16,  # 0.01 ETH
        from_address=dummy_address,
        slippage=0.03,
    )

    assert quote.source_chain_id == 1
    assert quote.target_chain_id == 8453
    assert quote.estimate_to_amount > 0
    assert quote.estimate_to_amount_min > 0
    assert quote.estimate_to_amount_min <= quote.estimate_to_amount

    tx_request = quote.get_transaction_request()
    assert "to" in tx_request
    assert "data" in tx_request
    assert "value" in tx_request


def test_fetch_crosschain_gas_balances(ethereum_web3, base_web3, wallet):
    """Fetch gas balances across multiple chains."""
    target_web3s = {
        1: ethereum_web3,
        8453: base_web3,
    }

    balances_native, balances_usd = fetch_crosschain_gas_balances(
        target_web3s=target_web3s,
        wallet_address=wallet.address,
    )

    # Random test wallet should have zero balance
    assert len(balances_native) == 2
    assert len(balances_usd) == 2
    for chain_id in [1, 8453]:
        assert chain_id in balances_native
        assert chain_id in balances_usd
        assert balances_native[chain_id] == Decimal("0")
        assert balances_usd[chain_id] == Decimal("0")


def test_prepare_crosschain_swaps_native(ethereum_web3, base_web3, wallet):
    """Dry run: prepare cross-chain swaps using native ETH as source token.

    An unfunded wallet should trigger swaps for all target chains
    since all balances are zero (below min_gas_usd).
    """
    target_web3s = {
        8453: base_web3,
    }

    swaps = prepare_crosschain_swaps(
        wallet=wallet,
        source_web3=ethereum_web3,
        target_web3s=target_web3s,
        min_gas_usd=Decimal("5"),
        top_up_usd=Decimal("10"),
        slippage=0.03,
        progress=False,
    )

    # Should get a swap for Base since balance is zero
    assert len(swaps) == 1

    swap = swaps[0]
    assert isinstance(swap, CrossChainSwap)
    assert swap.source_chain_id == 1
    assert swap.target_chain_id == 8453
    assert swap.target_balance_usd == Decimal("0")
    assert swap.min_gas_usd == Decimal("5")
    assert swap.top_up_usd == Decimal("10")
    assert swap.from_amount_raw > 0
    assert swap.from_amount_usd == Decimal("10")
    assert swap.transaction_request
    assert "to" in swap.transaction_request
    assert "data" in swap.transaction_request

    # Verify str output works
    swap_str = str(swap)
    assert "Ethereum" in swap_str
    assert "Base" in swap_str


def test_prepare_crosschain_swaps_usdc(ethereum_web3, base_web3, wallet):
    """Dry run: prepare cross-chain swaps using USDC as source token.

    Uses the USDC address from the token mapping and verifies that
    LI.FI returns a valid quote for swapping USDC to native gas token.
    """
    target_web3s = {
        8453: base_web3,
    }

    usdc_address = USDC_NATIVE_TOKEN[1]  # Ethereum USDC

    swaps = prepare_crosschain_swaps(
        wallet=wallet,
        source_web3=ethereum_web3,
        target_web3s=target_web3s,
        min_gas_usd=Decimal("5"),
        top_up_usd=Decimal("10"),
        source_token_address=usdc_address,
        slippage=0.03,
        progress=False,
    )

    assert len(swaps) == 1

    swap = swaps[0]
    assert isinstance(swap, CrossChainSwap)
    assert swap.source_chain_id == 1
    assert swap.target_chain_id == 8453
    assert swap.from_amount_usd == Decimal("10")
    # USDC has 6 decimals, so $10 worth should be ~10_000_000 raw
    assert swap.from_amount_raw > 0
    assert swap.from_amount_raw < 100_000_000  # sanity: less than $100 USDC raw
    assert swap.transaction_request
    assert "to" in swap.transaction_request
    assert "data" in swap.transaction_request
