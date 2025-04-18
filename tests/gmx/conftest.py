import pytest

from eth_typing import HexAddress, HexStr


@pytest.fixture()
def large_eth_holder() -> HexAddress:
    """A random account picked from Arbitrum Smart chain that holds a lot of ETH.

    This account is unlocked on Anvil, so you have access to good ETH stash.

    `To find large holder accounts, use bscscan <https://arbiscan.io/accounts>`_.
    """
    # Binance Hot Wallet 20
    return HexAddress(HexStr("0xF977814e90dA44bFA03b6295A0616a897441aceC"))


@pytest.fixture()
def large_wbtc_holder() -> HexAddress:
    """A random account picked from Arbitrum Smart chain that holds a lot of WBTC.

    This account is unlocked on Anvil, so you have access to good WBTC stash.

    `To find large holder accounts, use arbiscan <https://arbiscan.io/accounts>`_.
    """
    # https://arbiscan.io/address/0xdcf711cb8a1e0856ff1cb1cfd52c5084f5b28030
    return HexAddress(HexStr("0xdcF711cB8A1e0856fF1cB1CfD52C5084f5B28030"))


@pytest.fixture()
def large_wavax_holder() -> HexAddress:
    """A random account picked from Avalanche Smart chain that holds a lot of WAVAX.

    This account is unlocked on Anvil, so you have access to good WAVAX stash.

    `To find large holder accounts, use bscscan <https://snowtrace.io/accounts>`_.
    """
    # https://snowtrace.io/address/0xefdc8FC1145ea88e3f5698eE7b7b432F083B4246
    # Upbit: Hot Wallet 1
    return HexAddress(HexStr("0x73AF3bcf944a6559933396c1577B257e2054D935"))


@pytest.fixture()
def large_wbtc_holder_avalanche() -> HexAddress:
    """A random account picked from Avalanche Smart chain that holds a lot of WBTC.

    This account is unlocked on Anvil, so you have access to good WBTC stash.

    `To find large holder accounts, use arbiscan <https://snowtrace.io/accounts>`_.
    """
    # https://snowtrace.io/address/0xB58163D9148EfFEdF4eF8517Ad1D3251b1ddD837
    return HexAddress(HexStr("0xB58163D9148EfFEdF4eF8517Ad1D3251b1ddD837"))


@pytest.fixture()
def large_usdc_holder_arbitrum() -> HexAddress:
    # https://arbiscan.io/address/0xb38e8c17e38363af6ebdcb3dae12e0243582891d#asset-multichain
    return HexAddress(HexStr("0xB38e8c17e38363aF6EbdCb3dAE12e0243582891D"))
