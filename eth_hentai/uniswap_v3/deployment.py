"""Uniswap v3 and compatible DEX deployments.

Compatible exchanges include Uniswap v3 deployments on:

- Ethereum mainnet

- Avalanche

- Polygon

- Optimism

- Arbitrum

"""

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_hentai.abi import get_abi_by_filename, get_contract
from eth_hentai.deploy import deploy_contract
from eth_hentai.uniswap_v3.constants import (
    DEFAULT_FEES,
    FOREVER_DEADLINE,
    UNISWAP_V3_FACTORY_BYTECODE,
    UNISWAP_V3_FACTORY_DEPLOYMENT_DATA,
)
from eth_hentai.uniswap_v3.utils import encode_sqrt_ratio_x96, get_default_tick_range


@dataclass(frozen=True)
class UniswapV3Deployment:
    """Describe Uniswap v3 deployment."""

    #: The Web3 instance for which all the contracts here are bound
    web3: Web3

    #: Factory address.
    #: `See the Solidity source code <https://github.com/Uniswap/v3-core/blob/v1.0.0/contracts/UniswapV3Factory.sol>`__.
    factory: Contract

    #: WETH9Mock address.
    #: `See the Solidity source code <https://github.com/sushiswap/sushiswap/blob/4fdfeb7dafe852e738c56f11a6cae855e2fc0046/contracts/mocks/WETH9Mock.sol>`__.
    weth: Contract

    #: Swap router address.
    #: `See the Solidity source code <https://github.com/Uniswap/v3-periphery/blob/v1.0.0/contracts/SwapRouter.sol>`__.
    swap_router: Contract

    #: Non-fungible position manager address.
    #: `See the Solidity source code <https://github.com/Uniswap/v3-periphery/blob/v1.0.0/contracts/NonfungiblePositionManager.sol>`__.
    position_manager: Contract

    # Pool contract proxy class.
    #: `See the Solidity source code <https://github.com/Uniswap/v3-core/blob/v1.0.0/contracts/UniswapV3Pool.sol>`__.
    PoolContract: Contract


def deploy_uniswap_v3_factory(web3: Web3, deployer: HexAddress) -> Contract:
    """Deploy a Uniswap v3 factory contract.

    :param web3: Web3 instance
    :param deployer: Deployer adresss
    :return: Factory contract instance
    """
    UniswapV3Factory = get_contract(
        web3,
        "uniswap_v3/UniswapV3Factory.json",
        bytecode=UNISWAP_V3_FACTORY_BYTECODE,
    )

    # https://ethereum.stackexchange.com/a/73872/620
    tx_hash = web3.eth.send_transaction(
        {"from": deployer, "data": UNISWAP_V3_FACTORY_DEPLOYMENT_DATA}
    )
    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    instance = UniswapV3Factory(address=tx_receipt.contractAddress)
    return instance


def deploy_uniswap_v3(
    web3: Web3,
    deployer: HexAddress,
    give_weth: Optional[int] = 10_000,
) -> UniswapV3Deployment:
    """Deploy v3

    Example:

    .. code-block:: python

        deployment = deploy_uniswap_v3(web3, deployer)
        factory = deployment.factory
        print(f"Uniswap factory is {factory.address}")
        swap_router = deployment.swap_router
        print(f"Uniswap swap router is {swap_router.address}")

    :param web3: Web3 instance
    :param deployer: Deployer account
    :param give_weth:
        Automatically give some Wrapped ETH to the deployer.
        Express as ETH units.
    :return: Deployment details
    """
    # Factory takes feeSetter as an argument
    factory = deploy_uniswap_v3_factory(web3, deployer)
    weth = deploy_contract(web3, "WETH9Mock.json", deployer)
    swap_router = deploy_contract(
        web3,
        "uniswap_v3/SwapRouter.json",
        deployer,
        factory.address,
        weth.address,
    )

    nft_position_descriptor = _deploy_nft_position_descriptor(web3, deployer, weth)

    position_manager = deploy_contract(
        web3,
        "uniswap_v3/NonfungiblePositionManager.json",
        deployer,
        factory.address,
        weth.address,
        nft_position_descriptor.address,
    )

    if give_weth:
        weth.functions.deposit().transact(
            {"from": deployer, "value": give_weth * 10**18}
        )

    PoolContract = get_contract(web3, "uniswap_v3/UniswapV3Pool.json")

    return UniswapV3Deployment(
        web3=web3,
        factory=factory,
        weth=weth,
        swap_router=swap_router,
        position_manager=position_manager,
        PoolContract=PoolContract,
    )


def deploy_pool(
    web3: Web3,
    deployer: HexAddress,
    *,
    deployment: UniswapV3Deployment,
    token0: Contract,
    token1: Contract,
    fee: int,
    initial_amount0: int = 0,
    initial_amount1: int = 0,
    get_tick_range_fn: Optional[Callable[[int, int], Tuple[int, int]]] = None,
) -> Contract:
    """Deploy a new pool on Uniswap v3.

    Assumes `deployer` has enough token balance to add the initial liquidity.
    The deployer will also receive LP tokens for newly added liquidity.

    `See UniswapV3Factory.createPool() for details <https://github.com/Uniswap/v3-core/blob/v1.0.0/contracts/UniswapV3Factory.sol#L35>`_.

    :param web3: Web3 instance
    :param deployer: Deployer account
    :param deployment: Uniswap v3 deployment
    :param token0: Base token of the pool
    :param token1: Quote token of the pool
    :param fee: Fee of the pool
    :param initial_amount0: Initial liquidity added for `token0`. Set zero if no liquidity will be added.
    :param initial_amount1: Initial liquidity added for `token1`. Set zero if no liquidity will be added.
    :param get_tick_range_fn: Function to return lower tick and upper tick based on given fee and sqrt_price_x96
    :return: Pool contract proxy
    """

    assert token0.address != token1.address
    assert (
        fee in DEFAULT_FEES
    ), f"Default Uniswap v3 factory only allows 3 fee levels: {', '.join(map(str, DEFAULT_FEES))}"

    factory = deployment.factory
    tx_hash = factory.functions.createPool(
        token0.address, token1.address, fee
    ).transact({"from": deployer})
    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

    # https://ethereum.stackexchange.com/a/59288/620
    # AttributeDict({'args': AttributeDict({'token0': '0x2946259E0334f33A064106302415aD3391BeD384', 'token1': '0xB9816fC57977D5A786E654c7CF76767be63b966e', 'fee': 3000, 'tickSpacing': 60, 'pool': '0x2a28188cEa899849B9dd497C1E04BC2f62E54B97'}), 'event': 'PoolCreated', 'logIndex': 0, 'transactionIndex': 0, 'transactionHash': HexBytes('0xb4e137f58ba6f22ecfce572e9ca50e7e174fb5c02243b956883c4da08c3cbef9'), 'address': '0xF2E246BB76DF876Cef8b38ae84130F4F55De395b', 'blockHash': HexBytes('0x7d3eb4fceaf4df22df7644a1df2af1d00863476bcd8fc76ade7c4efe7d78c8e5'), 'blockNumber': 6})
    logs = factory.events.PoolCreated().processReceipt(tx_receipt)
    event0 = logs[0]
    pool_address = event0["args"]["pool"]
    pool = deployment.PoolContract(address=pool_address)

    # provide initial liquidity
    if initial_amount0 > 0 and initial_amount1 > 0:
        assert token0.functions.balanceOf(deployer).call() > initial_amount0
        assert token1.functions.balanceOf(deployer).call() > initial_amount1

        # pool is locked until initialize with initial sqrtPriceX96
        # https://github.com/Uniswap/v3-core/blob/v1.0.0/contracts/UniswapV3Pool.sol#L271
        sqrt_price_x96 = encode_sqrt_ratio_x96(
            amount0=initial_amount0, amount1=initial_amount1
        )
        tx_hash = pool.functions.initialize(sqrt_price_x96).transact({"from": deployer})

        position_manager = deployment.position_manager
        token0.functions.approve(position_manager.address, initial_amount0).transact(
            {"from": deployer}
        )
        token1.functions.approve(position_manager.address, initial_amount1).transact(
            {"from": deployer}
        )

        min_tick, max_tick = get_default_tick_range(fee)
        if get_tick_range_fn:
            lower_tick, upper_tick = get_tick_range_fn(fee, sqrt_price_x96)
            # quickly validate tick range
            assert lower_tick >= min_tick
            assert upper_tick <= max_tick
        else:
            lower_tick, upper_tick = min_tick, max_tick

        # mint initial position
        # https://docs.uniswap.org/protocol/guides/providing-liquidity/mint-a-position
        position_manager.functions.mint(
            (
                token0.address,
                token1.address,
                fee,
                lower_tick,
                upper_tick,
                initial_amount0,
                initial_amount1,
                0,  # min amount0 desired, this is used as safety check
                0,  # min amount1 desired, this is used as safety check
                deployer,
                FOREVER_DEADLINE,
            )
        ).transact({"from": deployer})

    return pool


def _deploy_nft_position_descriptor(web3: Web3, deployer: HexAddress, weth: Contract):
    """Deploy NFT position descriptor.

    `See the solidity source code <https://github.com/Uniswap/v3-periphery/blob/v1.0.0/contracts/NonfungibleTokenPositionDescriptor.sol>`__.

    Currently this is a separate function since we need to link references in bytecode
    in ad-hoc manner.
    """
    # linkReferences can be found in compiled `abi/uniswap_v3/NonfungibleTokenPositionDescriptor.json`
    nft_descriptor = deploy_contract(web3, "uniswap_v3/NFTDescriptor.json", deployer)

    contract_interface = get_abi_by_filename(
        "uniswap_v3/NonfungibleTokenPositionDescriptor.json"
    )
    abi = contract_interface["abi"]
    bytecode = contract_interface["bytecode"].replace(
        "__$cea9be979eee3d87fb124d6cbb244bb0b5$__", nft_descriptor.address[2:]
    )
    NonfungibleTokenPositionDescriptor = web3.eth.contract(abi=abi, bytecode=bytecode)

    return deploy_contract(
        web3,
        NonfungibleTokenPositionDescriptor,
        deployer,
        weth.address,
    )
