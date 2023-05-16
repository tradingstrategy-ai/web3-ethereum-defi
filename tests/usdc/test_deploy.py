from eth_defi.token import TokenDetails


def test_usdc_deployment(usdc: TokenDetails):
    """USDC deploys correctly."""
    assert usdc.symbol == "USDC"
    assert usdc.decimals == 6
    assert usdc.contract.functions.totalSupply().call() == 1_000_000 * 10**6
