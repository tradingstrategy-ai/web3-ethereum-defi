"""EVM Vault valuation."""
from eth_typing import HexAddress
from web3.contract import Contract

from eth_defi.vault.base import VaultPortfolio


def map_best_routes(
    quoter: Contract,
    tokens: set[HexAddress],
    target_asset: HexAddress,
    intermedia_tokens: set[HexAddress],
):
    """Find best available routes to sell assets.

    - Use Uniswap v2 and brute force loop to figure out routes for each asset.
    """




def calculate_nav_on_market_sell(
    portfolio: VaultPortfolio,
    quoter: Contract,
    valuation_asset: HexAddress,
    intermedia_tokens: set[HexAddress],
):
    """Calculate valuation of all vault spot assets, assuming we would sell them on Uniswap market sell.

    :param portfolio:
        The gathered portfolio of current assets

    :param quoter:
        Uniswap QuoterV2 smart contract.

    :param intermedia_token:
        The supported intermediate token if we cannot do direct market sell.

    :param valuation_asset:
        The asset in which we value the portfolio.

        E.g. `USDC`.
    """

    calls = []
    for token in portfolio.spot_erc20:
        pass
