import pytest

from eth_defi.lagoon.vault import LagoonVault
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.token import TokenDetails
from tests.lagoon.conftest import base_weth

from eth_defi.vault.base import TradingUniverse


def test_lagoon_info(lagoon_vault: LagoonVault):
    vault = lagoon_vault
    info = vault.fetch_info()
    assert info["safe_address"] == "0x205e80371f6d1b33dff7603ca8d3e92bebd7dc25"


def test_lagoon_get_portfolio(
    lagoon_vault: LagoonVault,
    base_weth: TokenDetails,
    base_usdc: TokenDetails,
):
    """Read vault assets.

    https://app.safe.global/home?safe=base:0x20415f3Ec0FEA974548184bdD6e67575D128953F
    """
    vault = lagoon_vault

    universe = TradingUniverse(
        spot_token_addresses={
            base_weth.address,
            base_usdc.address,
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)

    assert portfolio.spot_erc20 == {
        base_usdc.address: pytest.approx(0.35),
        base_weth.address: pytest.approx(0.35),
    }