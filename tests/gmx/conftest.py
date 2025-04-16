import pytest

from eth_typing import HexAddress, HexStr


@pytest.fixture()
def large_eth_holder() -> HexAddress:
    """A random account picked from Arbitrum Smart chain that holds a lot of ETH.

    This account is unlocked on Ganache, so you have access to good ETH stash.

    `To find large holder accounts, use bscscan <https://arbiscan.io/accounts>`_.
    """
    # Binance Hot Wallet 20
    return HexAddress(HexStr("0xF977814e90dA44bFA03b6295A0616a897441aceC"))

@pytest.fixture()
def large_wbtc_holder() -> HexAddress:
    """A random account picked from Arbitrum Smart chain that holds a lot of WBTC.

    This account is unlocked on Ganache, so you have access to good WBTC stash.

    `To find large holder accounts, use arbiscan <https://arbiscan.io/accounts>`_.
    """
    # https://arbiscan.io/address/0xdcf711cb8a1e0856ff1cb1cfd52c5084f5b28030
    return HexAddress(HexStr("0xdcF711cB8A1e0856fF1cB1CfD52C5084f5B28030"))