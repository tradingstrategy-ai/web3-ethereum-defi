"""ABI-backed YieldBasis contract constructors.

The minimal read-only interfaces follow the canonical
`YieldBasis yb-core contracts <https://github.com/yield-basis/yb-core>`__ and
are documented in :file:`eth_defi/abi/yield_basis/README.md`.
"""

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.yield_basis.addresses import YIELD_BASIS_FACTORY


def fetch_yield_basis_factory(web3: Web3) -> Contract:
    """Bind the reviewed Ethereum Factory contract.

    :param web3:
        Ethereum connection used by the contract binding.
    :return:
        Factory contract proxy backed by the stored minimal ABI.
    """

    return get_deployed_contract(web3, "yield_basis/Factory.json", YIELD_BASIS_FACTORY)


def fetch_yield_basis_lt(web3: Web3, address: HexAddress) -> Contract:
    """Bind an LT share contract to ``address``.

    :param web3:
        Ethereum connection used by the contract binding.
    :param address:
        Reviewed LT/yb-LP share-token address.
    :return:
        LT contract proxy backed by the stored minimal ABI.
    """

    return get_deployed_contract(web3, "yield_basis/LT.json", address)


def fetch_yield_basis_amm(web3: Web3, address: HexAddress) -> Contract:
    """Bind a YieldBasis AMM contract to ``address``.

    :param web3:
        Ethereum connection used by the contract binding.
    :param address:
        Factory-reported AMM component address.
    :return:
        AMM contract proxy backed by the stored minimal ABI.
    """

    return get_deployed_contract(web3, "yield_basis/AMM.json", address)


def fetch_yield_basis_curve_pool(web3: Web3, address: HexAddress) -> Contract:
    """Bind the Curve Cryptoswap pool used by an LT.

    :param web3:
        Ethereum connection used by the contract binding.
    :param address:
        Factory-reported Curve pool address.
    :return:
        Curve pool proxy backed by the stored minimal ABI.
    """

    return get_deployed_contract(web3, "yield_basis/CurveCryptoPool.json", address)
