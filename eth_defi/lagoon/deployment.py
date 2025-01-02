"""Deploy new Lagoon vaults.

Lagoon automatised vault consists of

- Safe multisig - we use 1.4.1 here
- Vault module
- Lagoon protocol smart contracts
- TradingStrategyModuleV0 module enabling guarded automated trade executor for the Safe

Any Safe must be deployed as 1-of-1 deployer address multisig and multisig holders changed after the deployment.
"""
import logging
from dataclasses import dataclass, asdict
from pprint import pformat

from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from safe_eth.safe.safe import Safe
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_contract, encode_function_call
from eth_defi.deploy import deploy_contract
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.safe.deployment import deploy_safe
from eth_defi.safe.safe_compat import create_safe_ethereum_client
from eth_defi.token import get_wrapped_native_token_address
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment
from eth_defi.uniswap_v3.deployment import UniswapV3Deployment

logger = logging.getLogger(__name__)

DEFAULT_RATE_UPDATE_COOLDOWN = 86400

DEFAULT_MANAGEMENT_RATE = 200

DEFAULT_PERFORMANCE_RATE = 2000


@dataclass(slots=True)
class LagoonDeploymentParameters:
    """Capture core parameters needed to deploy a Lagoon vault"""
    underlying: HexAddress
    name: str
    symbol: str
    safe: str | None = None
    whitelistManager: str | None = None
    valuationManager: str | None = None
    admin: str = None
    feeReceiver: str = None
    feeRegistry: str = None

    #: Management fee in BPS
    managementRate: int = DEFAULT_MANAGEMENT_RATE  # Assuming these are integers, adjust type if different

    #: Performance fee in BPS
    performanceRate: int = DEFAULT_PERFORMANCE_RATE
    enableWhitelist: bool = False

    #: Max rate update frequency, seconds
    rateUpdateCooldown: int = DEFAULT_RATE_UPDATE_COOLDOWN

    #: If set None, then autoresolve
    wrappedNativeToken: HexAddress | None = None

    def as_solidity_struct(self) -> dict:
        # Return Vault.InitStruct to be passed to the constructor
        return asdict(self)


@dataclass(slots=True, frozen=True)
class LagoonDeployment:
    chain_id: int
    vault: LagoonVault
    trading_strategy_module: Contract



def deploy_lagoon(
    web3: Web3,
    deployer: LocalAccount,
    safe: Safe,
    asset_manager: HexAddress,
    parameters: LagoonDeploymentParameters,
    owner: HexAddress | None,
) -> Contract:
    """Deploy a new Lagoon vault.

    - Create a new Safe

    - Create a new Lagoon vault

    - Set guard policies

    - Set owership

    For Foundry recipe see https://github.com/hopperlabsxyz/lagoon-v0/blob/main/script/deploy_vault.s.sol

    :param deployer:
        The initial account used to deploy smart contracts

    :param owner:
        All transfership is transferred to this user after

    :param asset_manager:
        Able to perform trades, valuations

    :return:
        Vault contract.

        Is a proxy contract.
    """

    assert isinstance(safe, Safe)
    assert isinstance(deployer, LocalAccount)

    chain_id = web3.eth.chain_id

    logger.info(
        "Deploying Lagoon vault on chain %d, deployer is %s",
        chain_id,
        deployer,
    )

    if owner is None:
        owner = safe.address

    # Autoresolve some parameters based on our deployment structure

    if parameters.wrappedNativeToken is None:
        parameters.wrappedNativeToken = get_wrapped_native_token_address(chain_id)

    if parameters.feeReceiver is None:
        parameters.feeReceiver = owner

    if parameters.whitelistManager is None:
        parameters.whitelistManager = owner

    if parameters.valuationManager is None:
        parameters.valuationManager = asset_manager

    if parameters.feeRegistry is None:
        parameters.feeRegistry = LAGOON_FEE_REGISTRIES[chain_id]

    if parameters.admin is None:
        parameters.admin = owner

    init_struct = parameters.as_solidity_struct()

    logger.info(
        "Parameters are:\n%s",
        pformat(init_struct)
    )

    VaultContract = get_contract(
        web3,
        "lagoon/Vault.json",
    )

    # payable(Upgrades.deployBeaconProxy(beacon, abi.encodeWithSelector(Vault.initialize.selector, init)))
    # E           Could not identify the intended function with name `initialize`, positional arguments with type(s) `address,str,str,address,address,address,address,address,address,int,int,bool,int,address` and keyword arguments with type(s) `{}`.

    abi_packed_init_args = encode_function_call(
        VaultContract.functions.initialize,
        [init_struct],  # Solidity struct encoding is a headache
    )

    beacon_proxy = deploy_contract(
        web3,
        "lagoon/BeaconProxy.json",
        deployer,
        owner,
        abi_packed_init_args,
    )

    return beacon_proxy


def deploy_safe_trading_strategy_module(
    web3,
    deployer: LocalAccount,
    safe: Safe,
) -> Contract:
    """Deploy TradingStrategyModuleV0 for Safe and Lagoon.

    :return:
        TradingStrategyModuleV0 instance
    """

    logger.info("Deploying TradingStrategyModuleV0")

    owner = deployer

    # Deploy guard module
    module = deploy_contract(
        web3,
        "safe-integration/TradingStrategyModuleV0.json",
        deployer.address,
        owner,
        safe.address,
    )

    # Enable Safe module
    # Multisig owners can enable the module
    tx = safe.contract.functions.enableModule(module.address).build_transaction(
        {"from": deployer.address, "gas": 0, "gasPrice": 0}
    )
    safe_tx = safe.build_multisig_tx(safe.address, 0, tx["data"])
    safe_tx.sign(deployer._private_key.hex())
    tx_hash, tx = safe_tx.execute(
        tx_sender_private_key=deployer._private_key.hex(),
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    return module


def setup_guard(
    web3: Web3,
    deployer: LocalAccount,
    module: Contract,
    any_asset: bool = False,
    uniswap_v2: UniswapV2Deployment = None,
    uniswap_v3: UniswapV3Deployment = None,
):
    assert any_asset, f"Only wildcard trading supported at the moment"

    logger.info("Setting up TradingStrategyModuleV0 guard")



def deploy_automated_lagoon_vault(
    web3: Web3,
    deployer: LocalAccount,
    asset_manager: HexAddress,
    parameters: LagoonDeploymentParameters,
    safe_owners: list[HexAddress | str],
    safe_threshold: int,
    uniswap_v2: UniswapV2Deployment | None,
    uniswap_v3: UniswapV3Deployment | None,
    any_token: bool = False,
) -> LagoonDeployment:
    """Deploy a full Lagoon setup with a guard.

    Lagoon automatised vault consists of

    - Safe multisig - we use 1.4.1 here
    - Vault module
    - Lagoon protocol smart contracts
    - TradingStrategyModuleV0 module enabling guarded automated trade executor for the Safe

    For roles
    - Asset manager (Trading Straegy) and Valuation Manager (Lagoon) are the same role
    - Any Safe must be deployed as 1-of-1 deployer address multisig and multisig holders changed after the deployment.
    """

    safe = deploy_safe(
        web3,
        deployer,
        owners=[deployer.address],
        threshold=1,
    )

    parameters.safe = safe.address

    vault = deploy_lagoon(
        web3=web3,
        deployer=deployer,
        safe=safe,
        asset_manager=asset_manager,
        parameters=parameters,
        owner=safe.address,
    )

    module = deploy_safe_trading_strategy_module(
        web3=web3,
        deployer=deployer,
        safe=safe,
    )

    setup_guard(
        web3,
        deployer,
        module,
        uniswap_v2=uniswap_v2,
        uniswap_v3=uniswap_v3,
        any_token=any_token,
    )

    # After everything is deployed, fix ownership
    # 1. Set Gnosis to a true multisig

    # 2. Transfer guard ownership to Gnosis
    tx_hash = module.functions.transferOwnership(safe.addresss).transact({"from": deployer.address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    return LagoonDeployment(
        vault=vault,
        trading_strategy_module=module,
    )

#  https://github.com/hopperlabsxyz/lagoon-v0
LAGOON_BEACONS = {
    # Base
    8453: "0xD69BC314bdaa329EB18F36E4897D96A3A48C3eeF",
}


# https://github.com/hopperlabsxyz/lagoon-v0
LAGOON_FEE_REGISTRIES = {
    # Base
    8453: "0x6dA4D1859bA1d02D095D2246142CdAd52233e27C",
}


