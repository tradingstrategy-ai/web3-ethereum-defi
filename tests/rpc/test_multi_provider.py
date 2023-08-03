from eth_defi.provider.multi_provider import create_multi_provider_web3


def test_multi_provider():
    config = """ 
    mev+https://rpc.mevblocker.io
    https://polygon-rpc.com
    https://bsc‑dataseed2.bnbchain.org
    """

    provider = create_multi_provider_web3(config)



def test_multi_provider_empty_config():
    config = """
    """

    provider =

