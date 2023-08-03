import pytest

from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderConfigurationError
from eth_defi.provider.named import get_provider_name


def test_multi_provider_mev_and_fallback():
    config = """ 
    mev+https://rpc.mevblocker.io
    https://polygon-rpc.com
    https://bsc-dataseed2.bnbchain.org
    """

    provider = create_multi_provider_web3(config)
    assert get_provider_name(provider.get_fallback_provider()) == "polygon-rpc.com"
    assert get_provider_name(provider.get_transact_provider()) == "rpc.mevblocker.io"


def test_multi_provider_fallback_only():
    config = """ 
    https://polygon-rpc.com
    """
    provider = create_multi_provider_web3(config)
    assert get_provider_name(provider.get_fallback_provider()) == "polygon-rpc.com"


def test_multi_provider_empty_config():
    """Cannot start with empty config."""
    config = """
    """
    with pytest.raises(MultiProviderConfigurationError):
        create_multi_provider_web3(config)



def test_multi_provider_bad_url():
    """Cannot start with bad urls config."""
    config = """
    mev+https:/rpc.mevblocker.io
    polygon-rpc.com    
    """
    with pytest.raises(MultiProviderConfigurationError):
        create_multi_provider_web3(config)


