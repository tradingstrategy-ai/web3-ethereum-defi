"""Real-provider coverage for the reviewed YieldBasis valuation path."""

import os

import pytest

from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.yield_basis.addresses import YIELD_BASIS_ACTIVE_MARKETS, YIELD_BASIS_STABLECOIN
from eth_defi.yield_basis.vault import YieldBasisVault
from eth_defi.yield_basis.vault_catalog import fetch_yield_basis_scan_preparation

JSON_RPC_ETHEREUM: str | None = os.environ.get("JSON_RPC_ETHEREUM")

#: Reviewed WETH market used for the focused valuation read.
YIELD_BASIS_LIVE_TEST_MARKET_ID: int = 10

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM is required for the real YieldBasis integration test")


def test_real_yield_basis_catalogue_and_valuation_path() -> None:
    """Validate the reviewed products and read one complete live valuation.

    The test exercises the production Factory, LT, AMM and Curve ABIs through
    the configured Ethereum provider. A newly added or rewired market fails
    visibly so its address tuple can be reviewed before publication.

    :return:
        None.
    """

    assert JSON_RPC_ETHEREUM is not None
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    block_number = get_almost_latest_block_number(web3)
    preparation = fetch_yield_basis_scan_preparation(web3, block_number)

    assert preparation.factory_valid
    assert preparation.review_required == ()
    assert {product.market_id for product in preparation.products} == set(YIELD_BASIS_ACTIVE_MARKETS)

    product = next(product for product in preparation.products if product.market_id == YIELD_BASIS_LIVE_TEST_MARKET_ID)
    vault = YieldBasisVault(web3, VaultSpec(1, product.lt_address), default_block_identifier=block_number)
    native_price_per_share = vault.fetch_native_asset_price_per_share(block_number)
    asset_crvusd_price = vault.fetch_asset_crvusd_price(block_number)

    assert vault.fetch_denomination_token_address(block_number).lower() == YIELD_BASIS_STABLECOIN.lower()
    assert native_price_per_share > 0
    assert asset_crvusd_price > 0
    assert vault.fetch_share_price(block_number) == native_price_per_share * asset_crvusd_price
    assert vault.fetch_total_supply(block_number) > 0
