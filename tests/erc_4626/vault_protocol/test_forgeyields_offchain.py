"""Test ForgeYields offchain metadata API.

ForgeYields is a cross-chain yield aggregator. Most TVL sits on Starknet,
but the Ethereum TokenGateway only shows a small residual. The canonical
TVL comes from the proprietary API at api.forgeyields.com/strategies.

1. Fetch strategies from the live API (no RPC needed)
2. Verify fyUSDC, fyETH, fyWBTC are present with correct gateway addresses
3. Verify TVL is a realistic amount (> $10K to guard against API breakage)
4. Verify per-vault lookup by Ethereum gateway address
"""

from decimal import Decimal

import pytest

from eth_defi.erc_4626.vault_protocol.forgeyields.offchain_metadata import (
    ForgeYieldsVaultMetadata,
    fetch_forgeyields_strategies,
    fetch_forgeyields_vault_metadata,
    _cached_strategies,
)


#: fyUSDC Ethereum gateway
FYUSDC_ADDRESS = "0x943109DC7C950da4592d85ebd4Cfed007Af64670"

#: fyETH Ethereum gateway
FYETH_ADDRESS = "0x98CD770b4e9905B1263f0c9ae6cdE34E1923508E"

#: fyWBTC Ethereum gateway
FYWBTC_ADDRESS = "0xeDca8230366B9eaFf06becdD1D261577836AA507"


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the in-process cache before each test."""
    import eth_defi.erc_4626.vault_protocol.forgeyields.offchain_metadata as mod

    mod._cached_strategies = None
    yield
    mod._cached_strategies = None


def test_fetch_strategies():
    """Fetch all ForgeYields strategies from the live API.

    1. Fetch strategies (bypassing disk cache with short TTL)
    2. Verify we get at least 3 strategies (fyUSDC, fyETH, fyWBTC)
    3. Verify all three known Ethereum gateways are present
    4. Verify each has non-zero TVL in USD
    """
    import datetime
    from pathlib import Path
    import tempfile

    # 1. Fetch with a temporary cache dir to avoid polluting production cache
    with tempfile.TemporaryDirectory() as tmpdir:
        strategies = fetch_forgeyields_strategies(
            cache_path=Path(tmpdir),
            max_cache_duration=datetime.timedelta(seconds=0),
        )

    # 2. Verify we get at least 3 strategies
    assert len(strategies) >= 3, f"Expected at least 3 strategies, got {len(strategies)}"

    # 3. Verify all three known Ethereum gateways are present
    assert FYUSDC_ADDRESS.lower() in strategies
    assert FYETH_ADDRESS.lower() in strategies
    assert FYWBTC_ADDRESS.lower() in strategies

    # 4. Verify each has non-zero TVL
    for addr in [FYUSDC_ADDRESS, FYETH_ADDRESS, FYWBTC_ADDRESS]:
        meta = strategies[addr.lower()]
        assert meta["tvl_usd"] > Decimal("10000"), f"TVL too low for {meta['symbol']}: {meta['tvl_usd']}"
        assert meta["symbol"] in ("fyUSDC", "fyETH", "fyWBTC")
        assert meta["ethereum_gateway"] is not None


def test_fetch_vault_metadata_by_address():
    """Verify per-vault lookup by Ethereum gateway address.

    1. Look up fyUSDC by its gateway address
    2. Verify name, symbol, and TVL
    3. Verify APY is a reasonable number (> 0)
    """
    # 1. Look up fyUSDC
    meta = fetch_forgeyields_vault_metadata(FYUSDC_ADDRESS)

    # 2. Verify fields
    assert meta is not None
    assert meta["name"] == "ForgeYields USDC"
    assert meta["symbol"] == "fyUSDC"
    assert meta["tvl_usd"] > Decimal("10000")

    # 3. Verify APY
    assert meta["apy"] is not None
    assert meta["apy"] > 0


def test_unknown_address_returns_none():
    """Verify that an unknown address returns None.

    1. Look up a random address that is not a ForgeYields gateway
    2. Assert None is returned
    """
    # 1. Look up unknown address
    meta = fetch_forgeyields_vault_metadata("0x0000000000000000000000000000000000000001")

    # 2. Assert None
    assert meta is None
