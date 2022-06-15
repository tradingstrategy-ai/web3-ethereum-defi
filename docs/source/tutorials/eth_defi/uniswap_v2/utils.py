"""Uniswap v2 helper functions.

`Mostly lifted from Uniswap-v2-py MIT licensed by Asynctomatic <https://github.com/nosofa/uniswap-v2-py>`_.
"""
from typing import Tuple

from eth_typing import HexAddress, HexStr
from web3 import Web3

#: Ethereum 0x000000000 addresss
ZERO_ADDRESS = Web3.toHex(0x0)


def sort_tokens(token_a: HexAddress, token_b: HexAddress) -> Tuple[HexAddress, HexAddress]:
    """Put lower address first, as Uniswap wants."""
    assert token_a != token_b, f"Received bad token pair {token_a}:{token_b}"
    (token_0, token_1) = (token_a, token_b) if int(token_a, 16) < int(token_b, 16) else (token_b, token_a)
    assert token_0 != ZERO_ADDRESS
    return token_0, token_1


# Liften from uniswap-v2-py by Asynctomatic
def pair_for(factory: HexAddress, token_a: HexAddress, token_b: HexAddress, magical_hash: HexStr) -> HexAddress:
    """Deduct the Uniswap pair contract address

    :param factory: Factory contract address
    :param token_a: Base token
    :param token_b: Quote token
    :param magical_hash: Init code hash of the Uniswap instance. Set None to use the default Sushiswap hash.
    :return: Pair contract address
    """
    prefix = Web3.toHex(hexstr="ff")
    token_a = Web3.toChecksumAddress(token_a)
    token_b = Web3.toChecksumAddress(token_b)
    encoded_tokens = Web3.solidityKeccak(["address", "address"], sort_tokens(token_a, token_b))
    suffix = Web3.toHex(hexstr=magical_hash)
    raw = Web3.solidityKeccak(["bytes", "address", "bytes", "bytes"], [prefix, factory, encoded_tokens, suffix])
    return Web3.toChecksumAddress(Web3.toHex(raw)[-40:])
