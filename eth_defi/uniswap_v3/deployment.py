"""Uniswap v3 and compatible DEX deployments.

Compatible exchanges include Uniswap v3 deployments on:

- Ethereum mainnet

- Avalanche

- Polygon

- Optimism

- Arbitrum

"""

from dataclasses import dataclass
from typing import Optional

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError

from eth_defi.abi import get_abi_by_filename, get_contract, get_deployed_contract
from eth_defi.deploy import deploy_contract
from eth_defi.uniswap_v3.constants import (
    DEFAULT_FEES,
    FOREVER_DEADLINE,
    UNISWAP_V3_FACTORY_BYTECODE,
    UNISWAP_V3_FACTORY_DEPLOYMENT_DATA,
)
from eth_defi.uniswap_v3.utils import encode_sqrt_ratio_x96, get_nearest_usable_tick
from eth_defi.uniswap_v3.pool import fetch_pool_details


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

    quoter: Contract

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
    tx_hash = web3.eth.send_transaction({"from": deployer, "data": UNISWAP_V3_FACTORY_DEPLOYMENT_DATA})
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

    quoter = deploy_contract(
        web3,
        "uniswap_v3/Quoter.json",
        deployer,
        factory.address,
        weth.address,
    )

    if give_weth:
        weth.functions.deposit().transact({"from": deployer, "value": give_weth * 10**18})

    PoolContract = get_contract(web3, "uniswap_v3/UniswapV3Pool.json")

    return UniswapV3Deployment(
        web3=web3,
        factory=factory,
        weth=weth,
        swap_router=swap_router,
        position_manager=position_manager,
        quoter=quoter,
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
) -> Contract:
    """Deploy a new pool on Uniswap v3.

    `See UniswapV3Factory.createPool() for details <https://github.com/Uniswap/v3-core/blob/v1.0.0/contracts/UniswapV3Factory.sol#L35>`_.

    :param web3: Web3 instance
    :param deployer: Deployer account
    :param deployment: Uniswap v3 deployment
    :param token0: Base token of the pool
    :param token1: Quote token of the pool
    :param fee: Fee of the pool
    :return: Pool contract proxy
    """

    assert token0.address != token1.address
    assert fee in DEFAULT_FEES, f"Default Uniswap v3 factory only allows {len(DEFAULT_FEES)} fee levels: {', '.join(map(str, DEFAULT_FEES))}"

    factory = deployment.factory
    tx_hash = factory.functions.createPool(token0.address, token1.address, fee).transact({"from": deployer})
    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

    # https://ethereum.stackexchange.com/a/59288/620
    # AttributeDict({'args': AttributeDict({'token0': '0x2946259E0334f33A064106302415aD3391BeD384', 'token1': '0xB9816fC57977D5A786E654c7CF76767be63b966e', 'fee': 3000, 'tickSpacing': 60, 'pool': '0x2a28188cEa899849B9dd497C1E04BC2f62E54B97'}), 'event': 'PoolCreated', 'logIndex': 0, 'transactionIndex': 0, 'transactionHash': HexBytes('0xb4e137f58ba6f22ecfce572e9ca50e7e174fb5c02243b956883c4da08c3cbef9'), 'address': '0xF2E246BB76DF876Cef8b38ae84130F4F55De395b', 'blockHash': HexBytes('0x7d3eb4fceaf4df22df7644a1df2af1d00863476bcd8fc76ade7c4efe7d78c8e5'), 'blockNumber': 6})
    logs = factory.events.PoolCreated().processReceipt(tx_receipt)
    event0 = logs[0]
    pool_address = event0["args"]["pool"]
    pool = deployment.PoolContract(address=pool_address)

    return pool


def add_liquidity(
    web3: Web3,
    deployer: HexAddress,
    *,
    deployment: UniswapV3Deployment,
    pool: Contract,
    amount0: int,
    amount1: int,
    lower_tick: int,
    upper_tick: int,
) -> tuple[dict, int, int]:
    """Add liquidity to a pool.

    `See Uniswap V3 documentation for details <https://docs.uniswap.org/protocol/guides/providing-liquidity/mint-a-position>`_.

    :param web3: Web3 instance
    :param deployer: Deployer account
    :param deployment: Uniswap v3 deployment
    :param pool: Pool contract proxy
    :param amount0: Amount of `token0` to be added
    :param amount1: Amount of `token1` to be added
    :param lower_tick: Lower tick of the position
    :param upper_tick: Upper tick of the position
    :return:
        - tx_receipt: Transaction receipt of the mint transaction
        - lower_tick: Corrected lower tick of the position with correct tick spacing
        - upper_tick: Corrected upper tick of the position with correct tick spacing
    """
    token0_address = pool.functions.token0().call()
    token1_address = pool.functions.token1().call()
    token0 = get_deployed_contract(web3, "ERC20MockDecimals.json", token0_address)
    token1 = get_deployed_contract(web3, "ERC20MockDecimals.json", token1_address)

    assert token0.functions.balanceOf(deployer).call() > amount0
    assert token1.functions.balanceOf(deployer).call() > amount1

    # since provided lower and upper tick might not be correct (due to tick spacing), we
    fee = pool.functions.fee().call()
    lower_tick = get_nearest_usable_tick(lower_tick, fee)
    upper_tick = get_nearest_usable_tick(upper_tick, fee)
    assert lower_tick < upper_tick, "Upper tick is too close to lower tick"

    # pool is locked until initialize with initial sqrtPriceX96
    # https://github.com/Uniswap/v3-core/blob/v1.0.0/contracts/UniswapV3Pool.sol#L271
    *_, initialized = pool.functions.slot0().call()
    if initialized is False:
        sqrt_price_x96 = encode_sqrt_ratio_x96(amount0=amount0, amount1=amount1)
        pool.functions.initialize(sqrt_price_x96).transact({"from": deployer})

    position_manager = deployment.position_manager
    token0.functions.approve(position_manager.address, amount0).transact({"from": deployer})
    token1.functions.approve(position_manager.address, amount1).transact({"from": deployer})

    # mint a new position
    tx_hash = position_manager.functions.mint(
        (
            token0.address,
            token1.address,
            fee,
            lower_tick,
            upper_tick,
            amount0,
            amount1,
            0,  # min amount0 desired, this is used as safety check
            0,  # min amount1 desired, this is used as safety check
            deployer,
            FOREVER_DEADLINE,
        )
    ).transact({"from": deployer})
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)

    return tx_receipt, lower_tick, upper_tick


def increase_liquidity(
    web3: Web3,
    position_owner: HexAddress,
    position_id: int,
    deployment: UniswapV3Deployment,
    amount0: int,
    amount1: int,
    amount0_min: int = 0,
    amount1_min: int = 0,
) -> dict:
    """
    Increase liquidity in an existing Uniswap V3 position.
    `See Uniswap V3 documentation for details <https://docs.uniswap.org/contracts/v3/reference/periphery/interfaces/INonfungiblePositionManager>`_.

    :param web3: Web3 instance
    :param position_owner: The address of the position_owner.
    :param position_id:  The id of the position to be increased, should be a positive integer.
    :param deployment: Uniswap v3 deployment
    :param amount0: Amount of `token0` to be added
    :param amount1: Amount of `token1` to be added
    :param amount0_min: min amount0 desired, this is used as slippage check
    :param amount1_min: min amount1 desired, this is used as slippage check
    :return: tx_receipt: Transaction receipt of the increaseLiquidity transaction
    """
    # get the pool from the position manager and factory
    position_manager = deployment.position_manager

    # returns: [nonce, operator, token0, token1, fee, tickLower, tickUpper,
    # liquidity, feeGrowthInside0, feeGrowthInside1, tokensOwed0, tokensOwed1]
    position_details = position_manager.functions.positions(position_id).call()  # get pool contract address
    print(position_details)

    # get the pool address from token0_address, token1_address, and fee
    pool_address = deployment.factory.functions.getPool(position_details[2], position_details[3], position_details[4]).call()
    # make sure the returned address is not 0x0  (that means it does not exist)
    assert "0x0000000000000000000000000000000000000000" != pool_address

    pool_details = fetch_pool_details(web3, pool_address)

    # make sure there is sufficient balance to cover the increase.
    assert pool_details.token0.contract.functions.balanceOf(position_owner).call() > amount0
    assert pool_details.token1.contract.functions.balanceOf(position_owner).call() > amount1

    pool_details.token0.contract.functions.approve(position_manager.address, amount0).transact({"from": position_owner})
    pool_details.token1.contract.functions.approve(position_manager.address, amount1).transact({"from": position_owner})

    tx_hash = position_manager.functions.increaseLiquidity(
        (
            position_id,
            amount0,
            amount1,
            amount0_min,
            amount1_min,
            FOREVER_DEADLINE,
        )
    ).transact({"from": position_owner})
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)

    return tx_receipt


def decrease_liquidity(
    web3: Web3,
    position_owner: HexAddress,
    position_id: int,
    deployment: UniswapV3Deployment,
    liquidity_decrease_amount: int,
    amount0_min: int = 0,
    amount1_min: int = 0,
) -> dict:
    """
    Decrease liquidity in an existing Uniswap V3 position.
    `See Uniswap V3 documentation for details <https://docs.uniswap.org/contracts/v3/reference/periphery/interfaces/INonfungiblePositionManager>`_.

    :param web3: Web3 instance
    :param position_owner: The address of the position_owner.
    :param position_id:  The id of the position to be decreased, should be a positive integer.
    :param deployment: Uniswap v3 deployment
    :param liquidity_decrease_amount: The amount of liquidity we want to reduce our position by.
    :param amount0_min: Optional min amount0 desired, this is used as slippage check.  Default is 0.
    :param amount1_min: Optional min amount1 desired, this is used as slippage check.  Default is 0.
    :return: tx_receipt: Transaction receipt of the decreaseLiquidity transaction
    """
    # check to make sure we have sufficient liquidity to meet decrease amount
    *_, liquidity, _, _, _, _ = deployment.position_manager.functions.positions(position_id).call()
    assert liquidity >= liquidity_decrease_amount

    tx_hash = deployment.position_manager.functions.decreaseLiquidity(
        (
            position_id,
            liquidity_decrease_amount,
            amount0_min,
            amount1_min,
            FOREVER_DEADLINE,
        )
    ).transact({"from": position_owner})
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)

    return tx_receipt


def _deploy_nft_position_descriptor(web3: Web3, deployer: HexAddress, weth: Contract):
    """Deploy NFT position descriptor.

    `See the solidity source code <https://github.com/Uniswap/v3-periphery/blob/v1.0.0/contracts/NonfungibleTokenPositionDescriptor.sol>`__.

    Currently this is a separate function since we need to link references in bytecode
    in ad-hoc manner.
    """
    # linkReferences can be found in compiled `abi/uniswap_v3/NonfungibleTokenPositionDescriptor.json`
    nft_descriptor = deploy_contract(web3, "uniswap_v3/NFTDescriptor.json", deployer)

    contract_interface = get_abi_by_filename("uniswap_v3/NonfungibleTokenPositionDescriptor.json")
    abi = contract_interface["abi"]
    bytecode = contract_interface["bytecode"].replace("__$cea9be979eee3d87fb124d6cbb244bb0b5$__", nft_descriptor.address[2:])
    NonfungibleTokenPositionDescriptor = web3.eth.contract(abi=abi, bytecode=bytecode)

    return deploy_contract(
        web3,
        NonfungibleTokenPositionDescriptor,
        deployer,
        weth.address,
    )


def fetch_deployment(
    web3: Web3,
    factory_address: HexAddress | str,
    router_address: HexAddress | str,
    position_manager_address: HexAddress | str,
    quoter_address: HexAddress | str,
) -> UniswapV3Deployment:
    """Construct Uniswap v3 deployment based on on-chain data.

    :param allow_different_weth_var:
        We assume Uniswap v3 ABI that has router.WETH() accessor.
        Some other DEXes might not have it.
        If set (default) ignore this error and just have
        `None` as the value for the wrapped token.

    :return:
        Data class representing Uniswap v3 exchange deployment
    """
    factory = get_deployed_contract(web3, "uniswap_v3/UniswapV3Factory.json", factory_address)
    router = get_deployed_contract(web3, "uniswap_v3/SwapRouter.json", router_address)
    position_manager = get_deployed_contract(web3, "uniswap_v3/NonfungiblePositionManager.json", position_manager_address)
    quoter = get_deployed_contract(web3, "uniswap_v3/Quoter.json", quoter_address)
    PoolContract = get_contract(web3, "uniswap_v3/UniswapV3Pool.json")

    # https://github.com/Uniswap/v3-periphery/blob/6cce88e63e176af1ddb6cc56e029110289622317/contracts/SwapRouter.sol#L40
    weth_address = router.functions.WETH9().call()
    weth = get_deployed_contract(web3, "WETH9Mock.json", weth_address)

    return UniswapV3Deployment(
        web3=web3,
        factory=factory,
        weth=weth,
        swap_router=router,
        position_manager=position_manager,
        quoter=quoter,
        PoolContract=PoolContract,
    )


def mock_partial_deployment_for_analysis(web3: Web3, router_address: str):
    """Only need swap_router and PoolContract?"""

    factory = None
    swap_router = get_deployed_contract(web3, "uniswap_v3/SwapRouter.json", router_address)
    weth = None
    position_manager = None
    quoter = None
    PoolContract = get_contract(web3, "uniswap_v3/UniswapV3Pool.json")
    return UniswapV3Deployment(
        web3,
        factory,
        weth,
        swap_router,
        position_manager,
        quoter,
        PoolContract,
    )
