from dataclasses import dataclass
from typing import Optional, Union

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_hentai.abi import get_contract, get_deployed_contract
from eth_hentai.deploy import deploy_contract
from eth_hentai.uniswap_v2.deployment import FOREVER_DEADLINE
from eth_hentai.uniswap_v3.bytecodes import (
    UNISWAP_V3_FACTORY_BYTECODE,
    UNISWAP_V3_FACTORY_DEPLOYMENT_DATA,
    UNISWAP_V3_NFT_DESCRIPTOR_BYTECODE,
    UNISWAP_V3_NFT_DESCRIPTOR_DEPLOYMENT_DATA,
)
from eth_hentai.uniswap_v3.utils import get_sqrt_price_x96


@dataclass(frozen=True)
class UniswapV3Deployment:
    """Describe Uniswap v3 deployment."""

    #: The Web3 instance for which all the contracts here are bound
    web3: Web3

    #: Factory address.
    #: `See the Solidity source code <https://github.com/Uniswap/v3-core/blob/main/contracts/UniswapV3Factory.sol>`__.
    factory: Contract

    #: WETH9Mock address.
    #: `See the Solidity source code <https://github.com/sushiswap/sushiswap/blob/4fdfeb7dafe852e738c56f11a6cae855e2fc0046/contracts/mocks/WETH9Mock.sol>`__.
    weth: Contract

    #: Router address.
    #: `See the Solidity source code <https://github.com/Uniswap/v3-periphery/blob/main/contracts/SwapRouter.sol>`__.
    router: Contract

    # Pool contract proxy class
    #: `See the Solidity source code <https://github.com/Uniswap/v3-core/blob/main/contracts/UniswapV3Pool.sol>`__.
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
        router = deployment.router
        print(f"Uniswap router is {router.address}")

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
    router = deploy_contract(
        web3,
        "uniswap_v3/SwapRouter.json",
        deployer,
        factory.address,
        weth.address,
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
        router=router,
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
    amount0: int = 0,
    amount1: int = 0,
) -> Contract:
    """Deploy a new pool on Uniswap v3.

    Assumes `deployer` has enough token balance to add the initial liquidity.
    The deployer will also receive LP tokens for newly added liquidity.

    `See UniswapV3Factory.createPool() for details <https://github.com/Uniswap/v3-core/blob/main/contracts/UniswapV3Factory.sol#L35>`_.

    :param web3: Web3 instance
    :param deployer: Deployer account
    :param deployment: Uniswap v3 deployment
    :param token0: Base token of the pool
    :param token1: Quote token of the pool
    :param fee: Fee of the pool
    :param amount0: Initial liquidity added for `token0`. Set zero if no liquidity will be added.
    :param amount1: Initial liquidity added for `token1`. Set zero if no liquidity will be added.
    :return: Pool contract proxy
    """

    assert token0.address != token1.address

    # https://github.com/Uniswap/v3-core/blob/main/contracts/UniswapV3Factory.sol#L26-L31
    assert fee in [
        500,
        3_000,
        10_000,
    ], "Default Uniswap v3 factory only allows 3 fee levels: 500, 3000, 10000"

    # NOTE: later we can support custom fee by using enableFeeAmount() in the factory:
    # https://github.com/Uniswap/v3-core/blob/main/contracts/UniswapV3Factory.sol#L61

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

    if amount0 > 0:
        assert token0.functions.balanceOf(deployer).call() > amount0
        assert token1.functions.balanceOf(deployer).call() > amount1

        # pool is locked until initialize with initial sqrt_price_x96
        sqrt_price_x96 = get_sqrt_price_x96(amount0, amount1)
        pool.functions.initialize(sqrt_price_x96).transact({"from": deployer})

        # NOTE: it currently isn't possible to deploy NFT descriptor contract direclty since the bytecode contains non-hexstr characters
        # token_descriptor = deploy_contract(
        #     web3,
        #     "uniswap_v3/NonfungibleTokenPositionDescriptor.json",
        #     deployer,
        #     deployment.weth.address,
        #     b"TEST",
        # )
        TokenDescriptor = get_contract(
            web3,
            "uniswap_v3/NonfungibleTokenPositionDescriptor.json",
            bytecode=UNISWAP_V3_NFT_DESCRIPTOR_BYTECODE,
        )

        tx_hash = web3.eth.send_transaction(
            {"from": deployer, "data": UNISWAP_V3_NFT_DESCRIPTOR_DEPLOYMENT_DATA}
        )
        tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
        token_descriptor = TokenDescriptor(address=tx_receipt.contractAddress)

        position_manager = deploy_contract(
            web3,
            "uniswap_v3/NonfungiblePositionManager.json",
            deployer,
            factory.address,
            deployment.weth.address,
            token_descriptor.address,
        )

        token0.functions.approve(position_manager.address, amount0).transact(
            {"from": deployer}
        )
        token1.functions.approve(position_manager.address, amount1).transact(
            {"from": deployer}
        )

        tx_hash = position_manager.functions.mint(
            (
                token0.address,
                token1.address,
                fee,
                -887272,  # copied from TickMath.MIN_TICK
                887272,  # copied from TickMath.MAX_TICK
                amount0,
                amount1,
                0,  # Have dummy value here
                0,  # Have dummy value here
                deployer,
                FOREVER_DEADLINE,
            )
        ).transact({"from": deployer})

    return pool_address
