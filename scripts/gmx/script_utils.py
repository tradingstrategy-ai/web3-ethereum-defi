"""Shared utilities for GMX example scripts.

Provides helpers that are used across multiple scripts to avoid duplication.

.. note::
    Scripts import this module as ``from scripts.gmx.script_utils import ...``.
    This requires the scripts to be run from the repository root (where ``scripts/``
    is on the Python path), e.g. ``poetry run python scripts/gmx/my_script.py``.
"""

from eth_defi.gmx.contracts import get_token_address_normalized
from eth_defi.gmx.core.oracle import OraclePrices

#: Canonical WETH address on Arbitrum One — used as fallback when the oracle
#: does not return a token address for the configured chain.
WETH_MAINNET_ARBITRUM = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"


def fetch_eth_spot_price(chain: str) -> float:
    """Fetch current ETH spot price from the GMX oracle API.

    For Arbitrum Sepolia the oracle uses mainnet prices since the testnet
    does not have its own oracle.

    :param chain: GMX chain name, e.g. ``"arbitrum"`` or ``"arbitrum_sepolia"``.
    :returns: Current ETH price in USD as a float.
    :raises RuntimeError: If the oracle API cannot be reached or ETH price is not found.
    """
    oracle = OraclePrices(chain)
    weth_address = get_token_address_normalized(chain, "WETH") or WETH_MAINNET_ARBITRUM
    price_data = oracle.get_price_for_token(weth_address)

    if price_data is None:
        # Fallback: iterate all returned prices looking for the known WETH address
        all_prices = oracle.get_recent_prices()
        for addr, data in all_prices.items():
            if addr.lower() == WETH_MAINNET_ARBITRUM.lower():
                price_data = data
                break

    if price_data is None:
        raise RuntimeError(f"Could not find ETH/WETH price in GMX oracle response for chain '{chain}'")

    # GMX stores WETH price as USD × 10^12 (30-decimal precision, WETH has 18 decimals)
    return int(price_data["maxPriceFull"]) / 10**12
