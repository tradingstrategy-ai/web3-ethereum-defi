"""Helper functions for Uniswap.

`Mostly lifted from Uniswap-v2-py MIT licensed by Asynctomatic <https://github.com/nosofa/uniswap-v2-py>`_.
"""

# Liften from uniswap-v2-py by Asynctomatic
from typing import List, Tuple

from eth_typing import HexAddress, HexStr
from web3 import Web3


#: Ethereum 0x000000000 addresss
from smart_contracts_for_testing.abi import get_abi_by_filename, get_contract

ZERO_ADDRESS = Web3.toHex(0x0)


def sort_tokens(token_a: HexAddress, token_b: HexAddress) -> Tuple[HexAddress, HexAddress]:
    """Put lower address first, as Uniswap wants."""
    assert token_a != token_b
    (token_0, token_1) = (token_a, token_b) if int(token_a, 16) < int(token_b, 16) else (token_b, token_a)
    assert token_0 != ZERO_ADDRESS
    return token_0, token_1


def get_amount_out(amount_in: int, reserve_in: int, reserve_out: int):
    """Given an input asset amount, returns the maximum output amount of the other asset (accounting for fees) given reserves.

    :param amount_in: Amount of input asset.
    :param reserve_in: Reserve of input asset in the pair contract.
    :param reserve_out: Reserve of input asset in the pair contract.
    :return: Maximum amount of output asset.
    """
    assert amount_in > 0
    assert reserve_in > 0 and reserve_out > 0
    amount_in_with_fee = amount_in*997  # 30 bps fee baked in
    numerator = amount_in_with_fee*reserve_out
    denominator = reserve_in*1000 + amount_in_with_fee
    return int(numerator/denominator)


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
    encoded_tokens = Web3.solidityKeccak(["address", "address"], sort_tokens(token_a, token_b))
    suffix = Web3.toHex(hexstr=magical_hash)
    raw = Web3.solidityKeccak(["bytes", "address", "bytes", "bytes"], [prefix, factory, encoded_tokens, suffix])
    return Web3.toChecksumAddress(Web3.toHex(raw)[-40:])


class UniswapFeeHelper:
    """A helper class to estimate Uniswap fees."""

    def __init__(self, web3: Web3, factory: HexAddress, init_code_hash: HexStr):
        self.web3 = web3
        self.factory = factory
        self.init_code_hash = init_code_hash
        self.PairContract = get_contract(web3, "UniswapV2Pair.json")

    def get_reserves(self, token_a: HexAddress, token_b: HexAddress):
        """
        Gets the reserves of token_0 and token_1 used to price trades
        and distribute liquidity as well as the timestamp of the last block
        during which an interaction occurred for the pair.
        :param pair: Address of the pair.
        :return:
            - reserve_0 - Amount of token_0 in the contract.
            - reserve_1 - Amount of token_1 in the contract.
            - liquidity - Unix timestamp of the block containing the last pair interaction.
        """
        (token0, token1) = sort_tokens(token_a, token_b)
        pair_contract = self.PairContract(
            address=Web3.toChecksumAddress(
                pair_for(self.factory, token_a, token_b, self.init_code_hash)),
            )
        reserve = pair_contract.functions.getReserves().call()
        return reserve if token0 == token_a else [reserve[1], reserve[0], reserve[2]]

    # Liften from uniswap-v2-py by Asynctomatic
    def get_amounts_out(self, amount_in: int, path: List[HexAddress]) -> List[int]:
        """Get how much token we are going to receive.

        :param amount_in:
        :param path: List of token addresses how to route the trade
        :return:
        """
        assert len(path) >= 2
        amounts = [amount_in]
        current_amount = amount_in
        for p0, p1 in zip(path, path[1:]):
            r = self.get_reserves(p0, p1)
            current_amount = get_amount_out(
                current_amount, r[0], r[1]
            )
            amounts.append(current_amount)
        return amounts
