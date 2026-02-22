"""Deploy new Lagoon vaults.

Lagoon automatised vault consists of

- Safe multisig - we use 1.4.1 here
- Vault module
- Lagoon protocol smart contracts
- TradingStrategyModuleV0 module enabling guarded automated trade executor for the Safe
- Support deployments with Forge and Etherscan verification

Any Safe must be deployed as 1-of-1 deployer address multisig and multisig holders changed after the deployment.
"""

import logging
import os
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from io import StringIO
from pathlib import Path
from pprint import pformat
from typing import Any, Callable, Literal

import eth_abi
from eth_account.signers.local import LocalAccount
from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.abi import ZERO_ADDRESS, ZERO_ADDRESS_STR, encode_multicalls, get_deployed_contract
from eth_defi.cow.constants import COWSWAP_SETTLEMENT, COWSWAP_VAULT_RELAYER
from eth_defi.cctp.whitelist import CCTPDeployment
from eth_defi.gmx.whitelist import GMXDeployment
from eth_defi.velora.api import get_augustus_swapper, get_token_transfer_proxy
from eth_defi.deploy import build_guard_forge_libraries, deploy_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.lagoon.beacon_proxy import deploy_beacon_proxy
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.foundry.forge import deploy_contract_with_forge
from eth_defi.gas import apply_gas, estimate_gas_price
from eth_defi.hotwallet import HotWallet
from eth_defi.orderly.vault import OrderlyVault
from eth_defi.provider.anvil import is_anvil
from eth_defi.safe.deployment import add_new_safe_owners, deploy_safe, deploy_safe_with_deterministic_address, fetch_safe_deployment
from eth_defi.safe.execute import execute_safe_tx
from eth_defi.token import WRAPPED_NATIVE_TOKEN, fetch_erc20_details, get_wrapped_native_token_address
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.tx import get_tx_broadcast_data
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment
from eth_defi.uniswap_v3.deployment import UniswapV3Deployment
from eth_defi.utils import chunked
from eth_defi.vault.base import VaultSpec
from eth_typing import BlockNumber, HexAddress
from hexbytes import HexBytes
from safe_eth.safe.safe import Safe
from web3 import Web3
from web3._utils.events import EventLogErrorFlags
from web3.contract import Contract
from web3.contract.contract import ContractFunction

logger = logging.getLogger(__name__)

DEFAULT_RATE_UPDATE_COOLDOWN = 86400

DEFAULT_MANAGEMENT_RATE = 200

DEFAULT_PERFORMANCE_RATE = 2000


CONTRACTS_ROOT = Path(os.path.dirname(__file__)) / ".." / ".." / ".." / ".." / "contracts"

DEFAULT_LAGOON_VAULT_ABI = "v0.5.0/Vault.sol"

# struct InitStruct {
#     IERC20 underlying;
#     string name;
#     string symbol;
#     address safe;
#     address whitelistManager;
#     address valuationManager;
#     address admin;
#     address feeReceiver;
#     uint16 managementRate;
#     uint16 performanceRate;
#     bool enableWhitelist;
#     uint256 rateUpdateCooldown;
# }


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

    def __post_init__(self):
        if self.underlying:
            assert self.underlying.startswith("0x"), f"Underlying token address must be a valid hex address, got {self.underlying}"
            self.underlying = Web3.to_checksum_address(self.underlying)

        if self.managementRate:
            assert type(self.managementRate) == int

    def as_solidity_struct(self) -> dict:
        # Return Vault.InitStruct to be passed to the constructor
        return asdict(self)

    def as_solidity_struct_v_0_1_0(self) -> list:
        parameters = asdict(self)
        return [
            parameters["underlying"],
            parameters["name"],
            parameters["symbol"],
            parameters["safe"],
            parameters["whitelistManager"],
            parameters["valuationManager"],
            parameters["admin"],
            parameters["feeReceiver"],
            parameters["feeRegistry"],
            parameters["wrappedNativeToken"],
            parameters["managementRate"],
            parameters["performanceRate"],
            parameters["enableWhitelist"],
            parameters["rateUpdateCooldown"],
        ]
        # Return Vault.InitStruct to be passed to the constructor
        #     struct InitStruct {

    #         IERC20 underlying;
    #         string name;
    #         string symbol;
    #         address safe;
    #         address whitelistManager;
    #         address valuationManager;
    #         address admin;
    #         address feeReceiver;
    #         address feeRegistry;
    #         address wrappedNativeToken;
    #         uint16 managementRate;
    #         uint16 performanceRate;
    #         bool enableWhitelist;
    #         uint256 rateUpdateCooldown;
    #     }

    def as_abi_encoded_bytes(self) -> HexBytes:
        """Return Lagoon vault initialization struct ABI encoded.

        - Before was passed as is, was changed to ABI encoded bytes in Lagoon v0.5.0.
        - Does **not** include wrappedNativeToken
        - Does **not** include feeRegistry, as it is passed separately.
        """
        abi_types = [
            "address",  # underlying (IERC20)
            "string",  # name
            "string",  # symbol
            "address",  # safe
            "address",  # whitelistManager
            "address",  # valuationManager
            "address",  # admin
            "address",  # feeReceiver
            "uint16",  # managementRate
            "uint16",  # performanceRate
            "bool",  # enableWhitelist
            "uint256",  # rateUpdateCooldown
        ]

        export_data = {"underlying": self.underlying, "name": self.name, "symbol": self.symbol, "safe": self.safe, "whitelistManager": self.whitelistManager, "valuationManager": self.valuationManager, "admin": self.admin, "feeReceiver": self.feeReceiver, "managementRate": self.managementRate, "performanceRate": self.performanceRate, "enableWhitelist": self.enableWhitelist, "rateUpdateCooldown": self.rateUpdateCooldown}

        abi_data = list(export_data.values())
        assert len(abi_data) == len(abi_types), f"ABI data length {len(abi_data)} does not match ABI types length {len(abi_types)}"
        return eth_abi.encode(abi_types, abi_data)

    def get_create_vault_proxy_arguments(self) -> list[Any]:
        """For createVaultProxy()"""
        export_data = {"underlying": self.underlying, "name": self.name, "symbol": self.symbol, "safe": self.safe, "whitelistManager": self.whitelistManager, "valuationManager": self.valuationManager, "admin": self.admin, "feeReceiver": self.feeReceiver, "managementRate": self.managementRate, "performanceRate": self.performanceRate, "enableWhitelist": self.enableWhitelist, "rateUpdateCooldown": self.rateUpdateCooldown}
        return list(export_data.values())


@dataclass(slots=True)
class LagoonConfig:
    """Configuration for Lagoon vault deployment.

    Captures all parameters needed to deploy a Lagoon vault except the
    chain connection (``web3``) and deployer account.

    Can be passed to :func:`deploy_automated_lagoon_vault` (single chain)
    or :func:`deploy_multichain_lagoon_vault` (multiple chains).
    """

    #: Vault parameters (name, symbol, underlying token, fees)
    parameters: LagoonDeploymentParameters

    #: Address that manages vault assets and executes trades
    asset_manager: HexAddress

    #: Addresses of Safe multisig owners
    safe_owners: list[HexAddress | str]

    #: Number of owner signatures required for Safe transactions
    safe_threshold: int

    #: Uniswap V2 deployment for router whitelisting
    uniswap_v2: UniswapV2Deployment | None = None

    #: Uniswap V3 deployment for router/quoter whitelisting
    uniswap_v3: UniswapV3Deployment | None = None

    #: Orderly perps vault for whitelisting
    orderly_vault: OrderlyVault | None = None

    #: Aave V3 deployment for lending whitelisting
    aave_v3: AaveV3Deployment | None = None

    #: Enable CowSwap settlement contract whitelisting
    cowswap: bool = False

    #: Enable Velora contract whitelisting
    velora: bool = False

    #: GMX perpetuals deployment for whitelisting
    gmx_deployment: GMXDeployment | None = None

    #: CCTP V2 deployment for cross-chain USDC transfers
    cctp_deployment: CCTPDeployment | None = None

    #: Allow any ERC-20 asset instead of explicit whitelist
    any_asset: bool = False

    #: Etherscan API key for contract verification
    etherscan_api_key: str | None = None

    #: Block explorer for contract verification
    verifier: Literal["etherscan", "blockscout", "sourcify", "oklink"] | None = None

    #: Custom block explorer URL
    verifier_url: str | None = None

    #: Use Forge for contract deployment
    use_forge: bool = False

    #: Delay between contract deployments (seconds) for nonce propagation
    between_contracts_delay_seconds: float = 45.0

    #: ERC-4626 vaults to whitelist for deposit/withdrawal
    erc_4626_vaults: list[ERC4626Vault] | None = None

    #: Deploy only the guard, skip vault deployment
    guard_only: bool = False

    #: Reuse an existing vault (requires guard_only=True)
    existing_vault_address: HexAddress | str | None = None

    #: Reuse an existing Safe instead of deploying a new one
    existing_safe_address: HexAddress | str | None = None

    #: Vault contract ABI file path
    vault_abi: str = "lagoon/v0.5.0/Vault.json"

    #: Use BeaconProxyFactory for vault deployment
    factory_contract: bool = True

    #: Deploy fresh Lagoon protocol (fee registry + vault implementation + factory)
    from_the_scratch: bool = False

    #: ERC-20 token addresses to whitelist
    assets: list[HexAddress | str] | None = None

    #: CREATE2 salt for deterministic Safe address across chains
    safe_salt_nonce: int | None = None

    #: Override Safe ProxyFactory address (default: v1.4.1 canonical).
    #: See `Safe canonical deployments <https://github.com/safe-global/safe-deployments>`__
    #: and `Safe contract deployment docs <https://docs.safe.global/core-api/safe-contracts-deployment>`__.
    safe_proxy_factory_address: HexAddress | str | None = None


@dataclass(slots=True, frozen=True)
class LagoonAutomatedDeployment:
    """Capture information of the lagoon automated deployment.

    - Have the deployment report for the users for diagnostics
    """

    chain_id: int
    vault: LagoonVault
    trading_strategy_module: Contract
    asset_manager: HexAddress
    multisig_owners: list[HexAddress]
    deployer: HexAddress
    block_number: BlockNumber
    parameters: LagoonDeploymentParameters

    #: Vault ABI file we use
    vault_abi: str

    #: In redeploy guard, the old module
    old_trading_strategy_module: Contract | None = None

    #: Address of beacon proxy factory
    beacon_proxy_factory: HexAddress | None = None

    #: How much ETH deployment used
    gas_used: Decimal | None = None

    @property
    def safe(self) -> Safe:
        return self.vault.safe

    def is_asset_manager(self, address: HexAddress) -> bool:
        return self.trading_strategy_module.functions.isAllowedSender(address).call()

    def get_deployment_data(self) -> dict:
        """Get JSON data describing the deployment.

        Store all addresses etc.
        """
        vault = self.vault
        safe = vault.safe
        fields = {
            "Deployer": self.deployer,
            "Safe": safe.address,
            "Vault": vault.address,
            "Beacon proxy factory": self.beacon_proxy_factory,
            "Trading strategy module": self.trading_strategy_module.address,
            "Asset manager": self.asset_manager,
            "Underlying token": self.vault.underlying_token.address,
            "Underlying symbol": self.vault.underlying_token.symbol,
            "Share token": self.vault.share_token.address,
            "Share token symbol": self.vault.share_token.symbol,
            "Multisig owners": ", ".join(self.multisig_owners),
            "Block number": f"{self.block_number:,}",
            "Performance fee": f"{self.parameters.performanceRate / 100:,} %",
            "Management fee": f"{self.parameters.managementRate / 100:,} %",
            "ABI": self.vault_abi,
            "Gas used": float(self.gas_used),
        }

        return fields

    def pformat(self) -> str:
        """Return pretty print of deployment info."""
        fields = self.get_deployment_data()
        # https://stackoverflow.com/a/17330263/315168
        io = StringIO()
        print("{:<30} {:30}".format("Key", "Label"), file=io)
        for k, v in fields.items():
            print("{:<30} {:<30}".format(k, v or "-"), file=io)

        return io.getvalue()


@dataclass(slots=True, frozen=True)
class LagoonMultichainDeployment:
    """Result of deploying Lagoon vaults across multiple chains with a shared deterministic Safe.

    All vaults share the same deterministic Safe address created via CREATE2
    with the same salt nonce on each chain.
    """

    #: The deterministic Safe address shared by all chains
    safe_address: HexAddress

    #: Per-chain deployment results keyed by chain name (lowercase)
    deployments: dict[str, LagoonAutomatedDeployment]

    #: The salt nonce used for deterministic Safe deployment
    safe_salt_nonce: int


def deploy_lagoon_protocol_registry(
    web3: Web3,
    deployer: HotWallet,
    safe: Safe,
    broadcast_func: Callable,
    etherscan_api_key: str = None,
    verifier: Literal["etherscan", "blockscout", "sourcify", "oklink"] | None = None,
    verifier_url: str | None = None,
) -> Contract:
    """Deploy a fee registry contract.

    - This is referred by all Lagoon deployments
    """

    logger.info("Deploying ProtocolRegistry for Lagoon")

    _broadcast = broadcast_func

    lagoon_folder = CONTRACTS_ROOT / "lagoon-v0"
    full_path = CONTRACTS_ROOT / "lagoon-v0/src/protocol-v2/ProtocolRegistry.sol"
    full_path = full_path.resolve()

    assert full_path.exists(), f"Does not exist: {full_path}"

    contract, tx_hash = deploy_contract_with_forge(
        web3,
        lagoon_folder,
        "protocol-v2/ProtocolRegistry.sol",
        "ProtocolRegistry",
        deployer=deployer,
        constructor_args=["false"],
        etherscan_api_key=etherscan_api_key,
        verifier=verifier,
        verifier_url=verifier_url,
        contract_file_out="ProtocolRegistry.sol",
        verbose=True,
    )

    time.sleep(4)
    assert_transaction_success_with_explanation(web3, tx_hash)

    #     function initialize(address initialOwner, address _protocolFeeReceiver) public initializer {
    #         __Ownable_init(initialOwner);
    #         FeeRegistryStorage storage $ = _getFeeRegistryStorage();
    #         $.protocolFeeReceiver = _protocolFeeReceiver;
    #     }
    tx_hash = _broadcast(
        contract.functions.initialize(
            safe.address,
            safe.address,
        )
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    return contract


def deploy_fresh_lagoon_protocol(
    web3: Web3,
    deployer: HotWallet,
    safe: Safe,
    broadcast_func: Callable,
    etherscan_api_key: str = None,
    verifier: Literal["etherscan", "blockscout", "sourcify", "oklink"] | None = None,
    verifier_url: str | None = None,
    forge_sync_delay=4.0,
) -> Contract:
    """Deploy a fresh Lagoon implementation from the scratch.

    - Fee registry contract
    - Vault implementation
    - Beacon proxy factory contract
    """

    assert isinstance(deployer, HotWallet), f"Can be only deployed with HotWallet deployer. got: {type(deployer)}: {deployer}"

    _broadcast = broadcast_func

    wrapped_native_token_address = WRAPPED_NATIVE_TOKEN[web3.eth.chain_id]

    # Deploy fee regis
    fee_registry = deploy_lagoon_protocol_registry(
        web3=web3,
        deployer=deployer,
        safe=safe,
        etherscan_api_key=etherscan_api_key,
        verifier=verifier,
        verifier_url=verifier_url,
        broadcast_func=broadcast_func,
    )

    lagoon_folder = CONTRACTS_ROOT / "lagoon-v0"

    implementation_contract, tx_hash = deploy_contract_with_forge(
        web3,
        project_folder=lagoon_folder,
        contract_file=DEFAULT_LAGOON_VAULT_ABI,
        contract_name="Vault",
        deployer=deployer,
        etherscan_api_key=etherscan_api_key,
        verifier=verifier,
        verifier_url=verifier_url,
        constructor_args=["true"],
        contract_file_out="Vault.sol",
        verbose=True,
    )
    time.sleep(forge_sync_delay)
    assert_transaction_success_with_explanation(web3, tx_hash)

    #     constructor(
    #         address _registry,
    #         address _implementation,
    #         address _owner,
    #         address _wrappedNativeToken
    #     ) UpgradeableBeacon(_implementation, _owner) {
    #         REGISTRY = _registry;
    #         WRAPPED_NATIVE = _wrappedNativeToken;
    #     }

    beacon_proxy_factory_contract, tx_hash = deploy_contract_with_forge(
        web3,
        project_folder=lagoon_folder,
        contract_file="protocol-v1/BeaconProxyFactory.sol",
        contract_name="BeaconProxyFactory",
        deployer=deployer,
        etherscan_api_key=etherscan_api_key,
        verifier=verifier,
        verifier_url=verifier_url,
        constructor_args=[
            fee_registry.address,
            implementation_contract.address,
            safe.address,
            wrapped_native_token_address,
        ],
        contract_file_out="BeaconProxyFactory.sol",
        verbose=True,
    )
    time.sleep(forge_sync_delay)
    assert_transaction_success_with_explanation(web3, tx_hash)

    logger.info(f"Deployed Lagoon protocol. Fee registry: {fee_registry.address}, implementation: {implementation_contract.address}, beacon proxy factory: {beacon_proxy_factory_contract.address}")

    return beacon_proxy_factory_contract


def deploy_lagoon(
    web3: Web3,
    deployer: LocalAccount | HotWallet,
    safe: Safe,
    asset_manager: HexAddress,
    parameters: LagoonDeploymentParameters,
    owner: HexAddress | None,
    gas=2_000_000,
    etherscan_api_key: str = None,
    use_forge=False,
    beacon_proxy=False,
    factory_contract=True,
    beacon_address="0x652716FaD571f04D26a3c8fFd9E593F17123Ab20",
    beacon_proxy_factory_address=None,
    beacon_proxy_factory_abi="lagoon/BeaconProxyFactory.json",
    vault_abi="lagoon/v0.5.0/Vault.json",
    deploy_fee_registry: bool = True,
    fee_registry_address: HexAddress | None = None,
    legacy: bool = False,
    salt=Web3.to_bytes(hexstr="0x" + "01" * 32),
    optin_proxy_delay=3 * 24 * 3600,
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
        All transfership is transferred to this user after.

        Usually defaults to newly deployed Safe the vault is associated with.

    :param asset_manager:
        Able to perform trades, valuations

    :param beacon_address:
        Vault beacon on base.

    :param use_forge:
        Deploy a new vault contract from source with Forge and Etherscan verification.

    :param etherscan_api_key:
        For Forge.

    :param vault_abi:
        Which Lagoon vault version we deploy.

        Use "lagoon/Vault.json" for the legacy version. **Warning**: unsafe.

    :param beacon_proxy:
        TODO

    :param deploy_fee_registry:
        Deploy a fee registry contract needed for deployment.

        Set the fee receiver as the owner.

    :return:
        Vault contract.

        Is a proxy contract.
    """

    assert isinstance(safe, Safe)
    assert isinstance(deployer, LocalAccount)

    chain_id = web3.eth.chain_id

    logger.info(
        "Deploying Lagoon vault on chain %d, deployer is %s, legacy is %s",
        chain_id,
        deployer,
        legacy,
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

    if not factory_contract:
        # Factory contract takes care of fee registry for us
        if parameters.feeRegistry is None:
            parameters.feeRegistry = LAGOON_FEE_REGISTRIES[chain_id]
    else:
        parameters.feeRegistry = None

    if parameters.admin is None:
        parameters.admin = owner

    wrapped_native_token = WRAPPED_NATIVE_TOKEN.get(chain_id)
    assert wrapped_native_token is not None, f"Lagoon deployment needs WRAPPED_NATIVE_TOKEN configured for chain {chain_id}"

    logger.info("Wrapped native token is: %s", wrapped_native_token)

    if legacy:
        assert not factory_contract
        assert vault_abi == "lagoon/Vault.json", f"Legacy Lagoon vault ABI must be lagoon/Vault.json: {vault_abi}"
        logger.info("Deploying Lagoon vault in legacy mode, beacon proxy is %s", beacon_proxy)

        #     function initialize(
        #         InitStruct memory init
        #     ) public virtual initializer {
        init_struct = parameters.as_solidity_struct_v_0_1_0()

        if beacon_proxy:
            vault = deploy_beacon_proxy(
                web3,
                deployer=deployer,
                beacon_address=beacon_address,
                implementation_contract_abi=vault_abi,
            )
        else:
            vault = deploy_contract(
                web3,
                vault_abi,
                deployer,
                False,
            )

        tx_params = vault.functions.initialize(
            init_struct,
        ).build_transaction(
            {
                "gas": 2_000_000,
                "chainId": chain_id,
                "nonce": web3.eth.get_transaction_count(deployer.address),
            }
        )

        signed_tx = deployer.sign_transaction(tx_params)
        raw_bytes = get_tx_broadcast_data(signed_tx)
        tx_hash = web3.eth.send_raw_transaction(raw_bytes)
        assert_transaction_success_with_explanation(web3, tx_hash)
    elif factory_contract:
        # Latest method
        # https://docs.lagoon.finance/vault/create-your-vault
        assert not beacon_proxy
        assert not legacy
        if beacon_proxy_factory_address is None:
            beacon_proxy_factory_address = LAGOON_BEACON_PROXY_FACTORIES.get(chain_id)
        assert beacon_proxy_factory_address, f"Cannot deploy Lagoon vault beacon proxy on chain {chain_id}, no factory address found. Registered factories: {pformat(LAGOON_BEACON_PROXY_FACTORIES)}"
        beacon_proxy_factory = get_deployed_contract(
            web3,
            beacon_proxy_factory_abi,
            beacon_proxy_factory_address,
        )

        # Deal with unstable ABI madness
        match beacon_proxy_factory_abi:
            case "lagoon/BeaconProxyFactory.json":
                # Leacy
                args = [parameters.get_create_vault_proxy_arguments(), salt]
                logger.info(
                    "Transacting with factory contract %s.createVaultProxy() with args %s",
                    beacon_proxy_factory_address,
                    args,
                )
                bound_func = beacon_proxy_factory.functions.createVaultProxy(*args)
            case "lagoon/OptinProxyFactory.json":
                # https://docs.lagoon.finance/vault/create-your-vault
                assert len(salt) == 32
                args = [
                    ZERO_ADDRESS_STR,  # _logic
                    safe.address,  # __initialOwner
                    optin_proxy_delay,  # _initialDelay
                    parameters.get_create_vault_proxy_arguments(),
                    salt,
                ]
                logger.info(
                    "Transacting with OptinBeaconFactory contract %s.createVaultProxy() with args %s",
                    beacon_proxy_factory_address,
                    args,
                )
                bound_func = beacon_proxy_factory.functions.createVaultProxy(
                    *args,
                )
            case _:
                raise NotImplementedError(f"Unknown Lagoon proxy factory ABI pattern: {beacon_proxy_factory_abi}")

        tx_params = {
            "gas": 2_000_000,
            "chainId": chain_id,
            "nonce": web3.eth.get_transaction_count(deployer.address),
        }
        tx_data = bound_func.build_transaction(tx_params)
        signed_tx = deployer.sign_transaction(tx_data)
        raw_bytes = get_tx_broadcast_data(signed_tx)
        tx_hash = web3.eth.send_raw_transaction(raw_bytes)
        assert_transaction_success_with_explanation(web3, tx_hash)

        receipt = web3.eth.get_transaction_receipt(tx_hash)
        match beacon_proxy_factory_abi:
            case "lagoon/BeaconProxyFactory.json":
                events = beacon_proxy_factory.events.BeaconProxyDeployed().process_receipt(receipt, EventLogErrorFlags.Discard)
            case "lagoon/OptinProxyFactory.json":
                events = beacon_proxy_factory.events.ProxyDeployed().process_receipt(receipt, EventLogErrorFlags.Discard)
            case _:
                raise NotImplementedError(f"Unknown Lagoon proxy factory ABI pattern: {beacon_proxy_factory_abi}")
        event = events[0]
        contract_address = event["args"]["proxy"]
        vault = get_deployed_contract(
            web3,
            vault_abi,
            contract_address,
        )
    else:
        # Direct deployment without factory, new Lagoon version
        vault = deploy_beacon_proxy(
            web3,
            deployer=deployer,
            beacon_address=beacon_address,
            implementation_contract_abi="lagoon/v0.5.0/Vault.json",
        )
        logger.info("Deployed Lagoon vault at %s", vault.address)

    return vault


def deploy_safe_trading_strategy_module(
    web3,
    deployer: LocalAccount,
    safe: Safe,
    use_forge=False,
    etherscan_api_key: str = None,
    verifier: Literal["etherscan", "blockscout", "sourcify", "oklink"] | None = None,
    verifier_url: str | None = None,
    enable_on_safe=True,
    cowswap: bool = False,
    gmx_deployment: GMXDeployment | None = None,
) -> Contract:
    """Deploy TradingStrategyModuleV0 for Safe and Lagoon.

    :param use_forge:
        Deploy Etherscan verified build with Forge

    :parma enable_on_safe:
        Automatically enable this module on the Safe multisig.
        Must be 1-of-1 deployer address multisig.

    :return:
        TradingStrategyModuleV0 instance
    """

    logger.info("Deploying TradingStrategyModuleV0")

    owner = deployer.address

    # Deploy guard module
    if use_forge:
        # Unit test path
        if verifier == "etherscan" and etherscan_api_key is None:
            verifier = None

        module, tx_hash = deploy_contract_with_forge(
            web3,
            CONTRACTS_ROOT / "safe-integration",
            "TradingStrategyModuleV0.sol",
            "TradingStrategyModuleV0",
            deployer,
            [owner, safe.address],
            etherscan_api_key=etherscan_api_key,
            verifier=verifier,
            verifier_url=verifier_url,
            forge_libraries=build_guard_forge_libraries(project="safe-integration"),
        )
    else:
        # Use explicit gas to skip eth_estimateGas (very slow on HyperEVM Anvil fork).
        # HyperEVM dual-block architecture: small blocks have only 2–3M gas limit,
        # while large blocks allow 30M. TradingStrategyModuleV0 needs ~5.4M gas.
        # https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/dual-block-architecture
        block_gas_limit = web3.eth.get_block("latest")["gasLimit"]
        guard_gas = min(10_000_000, block_gas_limit - 100_000)

        # TradingStrategyModuleV0 uses external Forge libraries via DELEGATECALL:
        # - CowSwapLib: CowSwap order creation/signing
        # - GmxLib: GMX perpetuals validation
        # - HypercoreVaultLib: Hypercore vault validation (HyperEVM only)
        # Libraries not needed by this deployment are linked to zero address.
        chain_id = web3.eth.chain_id

        library_addresses = {}

        if cowswap:
            cowswap_lib = deploy_contract(
                web3,
                "guard/CowSwapLib.json",
                deployer,
                gas=guard_gas,
            )
            library_addresses["CowSwapLib"] = cowswap_lib.address
            logger.info("Deployed CowSwapLib at %s", cowswap_lib.address)
        else:
            library_addresses["CowSwapLib"] = ZERO_ADDRESS
            logger.info("CowSwapLib not needed, linking with zero address")

        if gmx_deployment:
            gmx_lib = deploy_contract(
                web3,
                "guard/GmxLib.json",
                deployer,
                gas=guard_gas,
            )
            library_addresses["GmxLib"] = gmx_lib.address
            logger.info("Deployed GmxLib at %s", gmx_lib.address)
        else:
            library_addresses["GmxLib"] = ZERO_ADDRESS
            logger.info("GmxLib not needed, linking with zero address")

        if chain_id == 999:
            hypercore_lib = deploy_contract(
                web3,
                "guard/HypercoreVaultLib.json",
                deployer,
                gas=guard_gas,
            )
            library_addresses["HypercoreVaultLib"] = hypercore_lib.address
            logger.info("Deployed HypercoreVaultLib at %s for HyperEVM", hypercore_lib.address)
        else:
            library_addresses["HypercoreVaultLib"] = ZERO_ADDRESS
            logger.info("HypercoreVaultLib not needed, linking with zero address")

        module = deploy_contract(
            web3,
            "safe-integration/TradingStrategyModuleV0.json",
            deployer,
            owner,
            safe.address,
            gas=guard_gas,
            libraries=library_addresses,
        )

    if enable_on_safe:
        gas_estimate = estimate_gas_price(web3)

        # Enable TradingStrategyModuleV0 as Safe module
        # Multisig owners can enable the module
        tx = safe.contract.functions.enableModule(module.address).build_transaction(
            {"from": deployer.address, "gas": 1_500_000},
        )
        tx = apply_gas(tx, gas_estimate)

        safe_tx = safe.build_multisig_tx(safe.address, 0, tx["data"])
        safe_tx.sign(deployer._private_key.hex())
        tx_hash, tx = execute_safe_tx(
            safe_tx,
            tx_sender_private_key=deployer._private_key.hex(),
            tx_gas=1_500_000,
            # eip1559_speed=TxSpeed.NORMAL,
            gas_fee=gas_estimate,
        )
        assert_transaction_success_with_explanation(web3, tx_hash)

    return module


def setup_guard(
    *,
    web3: Web3,
    safe: Safe,
    deployer: HotWallet,
    owner: HexAddress,
    asset_manager: HexAddress,
    vault: Contract,
    module: Contract,
    broadcast_func: Callable[[ContractFunction], HexBytes],
    any_asset: bool = False,
    uniswap_v2: UniswapV2Deployment | None = None,
    uniswap_v3: UniswapV3Deployment | None = None,
    orderly_vault: OrderlyVault | None = None,
    aave_v3: AaveV3Deployment | None = None,
    erc_4626_vaults: list[ERC4626Vault] | None = None,
    cowswap: bool = False,
    velora: bool = False,
    gmx_deployment: GMXDeployment | None = None,
    cctp_deployment: CCTPDeployment | None = None,
    hack_sleep=20.0,
    assets: list[HexAddress | str] | None = None,
    multicall_chunk_size=40,
):
    """Setups up TradingStrategyModuleV0 guard on the Lagoon vault.

    - Creates the guard smart contract (TradingStrategyModuleV0)
      and enables it on the Safe multisig as a module.

    - Runs through various whitelisting rules as transactions against this contract
    """

    assert isinstance(deployer, HotWallet), f"Got: {deployer}"
    assert isinstance(owner, str), f"Got: {owner}"
    assert isinstance(module, Contract)
    assert isinstance(vault, Contract)
    assert callable(broadcast_func), "Must have a broadcast function for txs"

    _broadcast = broadcast_func

    logger.info("Setting up TradingStrategyModuleV0 guard: %s", module.address)

    # Enable asset_manager as the whitelisted trade-executor
    logger.info("Whitelisting trade-executor as sender")
    tx_hash = _broadcast(module.functions.allowSender(asset_manager, "Whitelist trade-executor"))
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Enable safe as the receiver of tokens
    logger.info("Whitelist Safe as trade receiver")
    tx_hash = _broadcast(module.functions.allowReceiver(safe.address, "Whitelist Safe as trade receiver"))
    assert_transaction_success_with_explanation(web3, tx_hash)

    anvil = is_anvil(web3)

    if any_asset:
        assert not assets, f"Cannot use any_asset with specific assets whitelist, got: {assets}"

    # Whitelist Uniswap v2
    if uniswap_v2:
        logger.info("Whitelisting Uniswap v2 router: %s", uniswap_v2.router.address)
        tx_hash = _broadcast(module.functions.whitelistUniswapV2Router(uniswap_v2.router.address, "Allow Uniswap v2"))
        assert_transaction_success_with_explanation(web3, tx_hash)
    else:
        logger.info("Not whitelisted: Uniswap v2")

    # Whitelist Uniswap v3
    if uniswap_v3:
        logger.info("Whitelisting Uniswap v3 router: %s", uniswap_v3.swap_router.address)
        tx_hash = _broadcast(module.functions.whitelistUniswapV3Router(uniswap_v3.swap_router.address, "Allow Uniswap v3"))
        assert_transaction_success_with_explanation(web3, tx_hash)
    else:
        logger.info("Not whitelisted: Uniswap v3")

    # Whitelist Aave v3 with aUSDC deposits.
    # TODO: Add automatic whitelisting of any aToken and vToken
    if aave_v3:
        ausdc = aave_v3.ausdc
        assert ausdc is not None, f"Aave aUSDC configuration missing for chain {web3.eth.chain_id}"

        logger.info("Whitelisting Aave v3 deployment: %s (pool)", aave_v3.pool.address)
        note = "Allow Aave v3 pool"
        tx_hash = _broadcast(module.functions.whitelistAaveV3(aave_v3.pool.address, note))
        assert_transaction_success_with_explanation(web3, tx_hash)

        atokens = [ausdc]
        for token in atokens:
            logger.info("Aave whitelisting for pool %s, aUSDC %s", aave_v3.pool.address, token.address)
            note = f"Aave v3 pool whitelisting for {token.symbol}"
            tx_hash = _broadcast(module.functions.whitelistToken(ausdc.address, note))
            assert_transaction_success_with_explanation(web3, tx_hash)

    else:
        logger.info("Not whitelisted: Aave v3")

    if orderly_vault:
        logger.info("Whitelisting Orderly vault: %s", orderly_vault.address)
        tx_hash = _broadcast(module.functions.whitelistOrderly(orderly_vault.address, "Allow Orderly"))
        assert_transaction_success_with_explanation(web3, tx_hash)
    else:
        logger.info("Not whitelisted: Orderly vault")

    # Whitelist all ERC-4626 vaults
    if erc_4626_vaults:
        # Because we may list large number, do multicall bundling using built=in GuardV0Base.multicall()

        # Do N vaults per one multicall
        for chunk_id, chunk in enumerate(chunked(erc_4626_vaults, multicall_chunk_size), start=1):
            multicalls = []
            logger.info("Processing ERC-4626 vaults chunk #%d, size %d", chunk_id, len(chunk))

            for idx, erc_4626_vault in enumerate(chunk, start=1):
                assert isinstance(erc_4626_vault, ERC4626Vault), f"Expected ERC4626Vault, got {type(erc_4626_vault)}: {erc_4626_vault}"
                # This will whitelist vault deposit/withdraw and its share and denomination token.
                # USDC may be whitelisted twice because denomination tokens are shared.
                logger.info(
                    "Whitelisting #%d ERC-4626 vault %s: %s",
                    idx,
                    erc_4626_vault.name,
                    erc_4626_vault.vault_address,
                )
                note = f"Whitelisting {erc_4626_vault.name}"
                partial_cal = module.functions.whitelistERC4626(erc_4626_vault.vault_address, note)
                multicalls.append(partial_cal)

            call = module.functions.multicall(encode_multicalls(multicalls))
            tx_hash = _broadcast(call)
            assert_transaction_success_with_explanation(web3, tx_hash)

            if not anvil:
                # TODO: A hack on Base mainnet inconsitency
                logger.info("Enforce vault tx readback lag on mainnet, sleeping 10 seconds")
                time.sleep(hack_sleep)

        logger.info("Total %d ERC-4626 vaults whitelisted", len(erc_4626_vaults))

        # Double check we really whitelisted the vault,
        # e.g. not a bad contract version
        for idx, erc_4626_vault in enumerate(erc_4626_vaults, start=1):
            result = module.functions.isAllowedApprovalDestination(erc_4626_vault.vault_address).call()
            assert result == True, f"Guard {module.address} approval check for ERC-4626 vault failed, attempted to whitelist: {erc_4626_vault.vault_address}, isAllowedApprovalDestination(): {result}"
    else:
        logger.info("Not whitelisted: any ERC-4626 vaults")

    # Whitelist all ERC-4626 vaults
    if assets:
        # Because we may list large number, do multicall bundling using built=in GuardV0Base.multicall()

        # Do N vaults per one multicall
        for chunk_id, chunk in enumerate(chunked(assets, multicall_chunk_size), start=1):
            multicalls = []
            logger.info("Processing assets chunk #%d, size %d", chunk_id, len(chunk))

            for idx, asset in enumerate(chunk, start=1):
                assert asset.startswith("0x"), f"Expected hex address, got: {asset}"

                token = fetch_erc20_details(web3, asset)

                # This will whitelist vault deposit/withdraw and its share and denomination token.
                # USDC may be whitelisted twice because denomination tokens are shared.
                logger.info("Whitelisting #%d token %s:", idx, token)
                note = f"Whitelisting {token.name}"
                partial_cal = module.functions.whitelistToken(Web3.to_checksum_address(asset), note)
                multicalls.append(partial_cal)

            call = module.functions.multicall(encode_multicalls(multicalls))
            tx_hash = _broadcast(call)
            assert_transaction_success_with_explanation(web3, tx_hash)

            if not anvil:
                # TODO: A hack on Base mainnet inconsitency
                logger.info("Enforce vault tx readback lag on mainnet, sleeping 10 seconds")
                time.sleep(hack_sleep)

        logger.info("Total %d assets whitelisted", len(assets))

    else:
        logger.info("Not whitelisting specific ERC-20 tokens")

    if cowswap:
        logger.info("Whitelisting CowSwap: %s", COWSWAP_SETTLEMENT)
        tx_hash = _broadcast(module.functions.whitelistCowSwap(COWSWAP_SETTLEMENT, COWSWAP_VAULT_RELAYER, "Allow CowSwap"))
        assert_transaction_success_with_explanation(web3, tx_hash)

    if velora:
        chain_id = web3.eth.chain_id
        augustus = get_augustus_swapper(chain_id)
        proxy = get_token_transfer_proxy(chain_id)
        logger.info("Whitelisting Velora: Augustus %s, TokenTransferProxy %s", augustus, proxy)
        tx_hash = _broadcast(module.functions.whitelistVelora(augustus, proxy, "Allow Velora"))
        assert_transaction_success_with_explanation(web3, tx_hash)

    # Whitelist GMX perpetuals trading
    if gmx_deployment:
        logger.info(
            "Whitelisting GMX: ExchangeRouter=%s, SyntheticsRouter=%s, OrderVault=%s",
            gmx_deployment.exchange_router,
            gmx_deployment.synthetics_router,
            gmx_deployment.order_vault,
        )
        tx_hash = _broadcast(
            module.functions.whitelistGMX(
                gmx_deployment.exchange_router,
                gmx_deployment.synthetics_router,
                gmx_deployment.order_vault,
                "Allow GMX perpetuals",
            )
        )
        assert_transaction_success_with_explanation(web3, tx_hash)

        # Whitelist GMX markets
        for idx, market in enumerate(gmx_deployment.markets, start=1):
            logger.info("Whitelisting GMX market #%d: %s", idx, market)
            tx_hash = _broadcast(module.functions.whitelistGMXMarket(market, f"GMX market #{idx}"))
            assert_transaction_success_with_explanation(web3, tx_hash)

        # Whitelist GMX collateral tokens if specified
        if gmx_deployment.tokens:
            for idx, token in enumerate(gmx_deployment.tokens, start=1):
                logger.info("Whitelisting GMX collateral token #%d: %s", idx, token)
                tx_hash = _broadcast(module.functions.whitelistToken(token, f"GMX collateral #{idx}"))
                assert_transaction_success_with_explanation(web3, tx_hash)

        logger.info("GMX whitelisting complete: %d markets", len(gmx_deployment.markets))
    else:
        logger.info("Not whitelisted: GMX")

    # Whitelist CCTP cross-chain USDC transfers
    if cctp_deployment:
        logger.info(
            "Whitelisting CCTP: TokenMessenger=%s",
            cctp_deployment.token_messenger,
        )
        tx_hash = _broadcast(
            module.functions.whitelistCCTP(
                cctp_deployment.token_messenger,
                "Allow CCTP cross-chain transfers",
            )
        )
        assert_transaction_success_with_explanation(web3, tx_hash)

        for domain in cctp_deployment.allowed_destination_domains:
            logger.info("Whitelisting CCTP destination domain: %d", domain)
            tx_hash = _broadcast(module.functions.whitelistCCTPDestination(domain, f"CCTP domain {domain}"))
            assert_transaction_success_with_explanation(web3, tx_hash)

        logger.info(
            "CCTP whitelisting complete: %d destination(s)",
            len(cctp_deployment.allowed_destination_domains),
        )
    else:
        logger.info("Not whitelisted: CCTP")

    # Whitelist all assets
    if any_asset:
        logger.info("Allow any asset whitelist")
        tx_hash = _broadcast(module.functions.setAnyAssetAllowed(True, "Allow any asset"))
        assert_transaction_success_with_explanation(web3, tx_hash)
    else:
        logger.info("Using only whitelisted assets")

    # Whitelist vault settle
    logger.info("Whitelist vault settlement")
    tx_hash = _broadcast(module.functions.whitelistLagoon(vault.address, "Whitelist vault settlement"))
    assert_transaction_success_with_explanation(web3, tx_hash)


def deploy_automated_lagoon_vault(
    *,
    web3: Web3,
    deployer: LocalAccount | HotWallet,
    config: LagoonConfig | None = None,
    asset_manager: HexAddress | None = None,
    parameters: LagoonDeploymentParameters | None = None,
    safe_owners: list[HexAddress | str] | None = None,
    safe_threshold: int | None = None,
    uniswap_v2: UniswapV2Deployment | None = None,
    uniswap_v3: UniswapV3Deployment | None = None,
    orderly_vault: OrderlyVault | None = None,
    aave_v3: AaveV3Deployment | None = None,
    cowswap: bool = False,
    velora: bool = False,
    gmx_deployment: GMXDeployment | None = None,
    cctp_deployment: CCTPDeployment | None = None,
    any_asset: bool = False,
    etherscan_api_key: str = None,
    verifier: Literal["etherscan", "blockscout", "sourcify", "oklink"] | None = None,
    verifier_url: str | None = None,
    use_forge=False,
    between_contracts_delay_seconds=45.0,
    erc_4626_vaults: list[ERC4626Vault] | None = None,
    guard_only: bool = False,
    existing_vault_address: HexAddress | str | None = None,
    existing_safe_address: HexAddress | str | None = None,
    vault_abi="lagoon/v0.5.0/Vault.json",
    factory_contract=True,
    from_the_scratch: bool = False,
    assets: list[HexAddress | str] | None = None,
    safe_salt_nonce: int | None = None,
    safe_proxy_factory_address: HexAddress | str | None = None,
) -> LagoonAutomatedDeployment:
    """Deploy a full Lagoon setup with a guard.

    Lagoon automatised vault consists of

    - Safe multisig - we use 1.4.1 here
    - Vault module
    - Lagoon protocol smart contracts
    - TradingStrategyModuleV0 module enabling guarded automated trade executor for the Safe

    For roles
    - Asset manager (Trading Straegy) and Valuation Manager (Lagoon) are the same role
    - Any Safe must be deployed as 1-of-1 deployer address multisig and multisig holders changed after the deployment.

    .. warning::

        Because we need to mix Forge, Safe lib and Web3.py transaction nonce management becomes a madness.

    .. note ::

        Deployer account must be manually removed from the Safe by new owners.

    :param config:
        Pass a :class:`LagoonConfig` object instead of individual kwargs.
        When provided, individual kwargs are ignored.

    :param guard_only:
        Deploy a new version of the guard smart contract and skip deploying the actual vault.

    :param from_the_scratch:
        Need to deloy a fee registry contract as well.

        A new chain deployment.

    :param safe_salt_nonce:
        If set, deploy the Safe using CREATE2 via the canonical SafeProxyFactory
        for a deterministic address. Use the same value across chains to get the
        same Safe address everywhere.

    :param safe_proxy_factory_address:
        Override the Safe ProxyFactory address. Defaults to the canonical v1.4.1
        factory (``0x4e1DCf7AD4e460CfD30791CCC4F9c8a4f820ec67``).
        See `Safe canonical deployments <https://github.com/safe-global/safe-deployments>`__
        and `Safe contract deployment docs <https://docs.safe.global/core-api/safe-contracts-deployment>`__.
    """

    if config is not None:
        # New-style: extract all fields from config into local variables
        # so the rest of the function body works unchanged
        parameters = config.parameters
        asset_manager = config.asset_manager
        safe_owners = config.safe_owners
        safe_threshold = config.safe_threshold
        uniswap_v2 = config.uniswap_v2
        uniswap_v3 = config.uniswap_v3
        orderly_vault = config.orderly_vault
        aave_v3 = config.aave_v3
        cowswap = config.cowswap
        velora = config.velora
        gmx_deployment = config.gmx_deployment
        cctp_deployment = config.cctp_deployment
        any_asset = config.any_asset
        etherscan_api_key = config.etherscan_api_key
        verifier = config.verifier
        verifier_url = config.verifier_url
        use_forge = config.use_forge
        between_contracts_delay_seconds = config.between_contracts_delay_seconds
        erc_4626_vaults = config.erc_4626_vaults
        guard_only = config.guard_only
        existing_vault_address = config.existing_vault_address
        existing_safe_address = config.existing_safe_address
        vault_abi = config.vault_abi
        factory_contract = config.factory_contract
        from_the_scratch = config.from_the_scratch
        assets = config.assets
        safe_salt_nonce = config.safe_salt_nonce
        safe_proxy_factory_address = config.safe_proxy_factory_address
    else:
        # Legacy kwargs: validate required arguments
        assert asset_manager is not None, "asset_manager required when config not provided"
        assert parameters is not None, "parameters required when config not provided"
        assert safe_owners is not None, "safe_owners required when config not provided"
        assert safe_threshold is not None, "safe_threshold required when config not provided"

    legacy = vault_abi == "lagoon/Vault.json"

    logger.info("Beginning Lagoon vault deployment, legacy mode: %s, ABI is %s", legacy, vault_abi)

    start_balance = web3.eth.get_balance(deployer.address)

    if existing_vault_address:
        assert guard_only, "You cannot pass existing vault address without guard_only=True"
    else:
        assert len(safe_owners) >= 1, "Multisig owners emptty"

    if guard_only:
        assert existing_vault_address, "You must pass existing vault address if guard_only=True"

    chain_id = web3.eth.chain_id

    if isinstance(deployer, HotWallet):
        # Production nonce hack
        deployer_local_account = deployer.account
    else:
        deployer_local_account = deployer

    existing_guard_module = None
    beacon_proxy_factory_address = None

    def _broadcast(bound_func: ContractFunction):
        """Hack together a nonce management helper.

        - Update nonce before broadcast
        - Broadcast
        - Check for success
        """
        assert isinstance(bound_func, ContractFunction)
        assert bound_func.args is not None
        if isinstance(deployer, HotWallet):
            # Path must be taken with prod deployers
            deployer.sync_nonce(web3)
            tx_hash = deployer.transact_and_broadcast_with_contract(bound_func)
            assert_transaction_success_with_explanation(web3, tx_hash)
            logger.info("Sleeping for 2 seconds to wait for nonce to propagate")
            time.sleep(2)
            return tx_hash
        elif isinstance(deployer, LocalAccount):
            # Only for Anvil
            # Will cause nonce sync errors in proc
            return bound_func.transact({"from": deployer.address})
        else:
            raise NotImplementedError(f"No idea about: {deployer}")

    if not existing_vault_address:
        # Deploy a Safe multisig that forms the core of Lagoon vault
        if safe_salt_nonce is not None:
            safe = deploy_safe_with_deterministic_address(
                web3,
                deployer_local_account,
                owners=[deployer.address],
                threshold=1,
                salt_nonce=safe_salt_nonce,
                proxy_factory_address=safe_proxy_factory_address or "0x4e1DCf7AD4e460CfD30791CCC4F9c8a4f820ec67",
            )
        else:
            safe = deploy_safe(
                web3,
                deployer_local_account,
                owners=[deployer.address],
                threshold=1,
            )

        parameters.safe = safe.address
        logger.info("Deployed new Safe: %s", safe.address)
    else:
        assert existing_safe_address, "You must pass existing Safe address if existing_vault_address is set"

        vault_contract = get_deployed_contract(
            web3,
            vault_abi,
            existing_vault_address,
        )
        safe = fetch_safe_deployment(
            web3,
            existing_safe_address,
            # Only added in Lagoon v0.5
            #  vault_contract.functions.safe().call()
        )
        logger.info("Using existing Safe: %s", safe.address)
        parameters.safe = safe.address

        try:
            vault_contract.functions.totalAssets().call()
        except Exception as e:
            raise RuntimeError(f"Does not look like Lagoon vault: {existing_vault_address}") from e

        # Look up the old module
        modules = safe.retrieve_modules()
        for module_addr in modules:
            module = get_deployed_contract(web3, "safe-integration/TradingStrategyModuleV0.json", module_addr)

            try:
                module.functions.getGovernanceAddress()
                existing_guard_module = module
            except ValueError:
                continue

        assert existing_guard_module is not None, f"Cannot find TradingStrategyModuleV0 on Safe {safe.address} with vault {vault_contract.address}, modules {modules}"

    if not is_anvil(web3):
        logger.info("Between contracts deployment delay: Sleeping %s for new nonce to propagade", between_contracts_delay_seconds)
        time.sleep(between_contracts_delay_seconds)

    beacon_proxy_factory_abi = "lagoon/BeaconProxyFactory.json"  # Default ABI (legacy)
    if not existing_vault_address:
        if from_the_scratch:
            # Deploy the full Lagoon protocol with fee registry and beacon proxy factory,
            # setting out Safe as the protocol owner
            assert use_forge, "Fee registry deployment is only supported with Forge"
            beacon_proxy_factory_contract = deploy_fresh_lagoon_protocol(
                web3=web3,
                deployer=deployer,
                safe=safe,
                etherscan_api_key=etherscan_api_key,
                verifier=verifier,
                verifier_url=verifier_url,
                broadcast_func=_broadcast,
            )
            beacon_proxy_factory_address = beacon_proxy_factory_contract.address
        else:
            beacon_factory = LAGOON_BEACON_PROXY_FACTORIES.get(chain_id)
            assert beacon_factory, f"No beacon factory in LAGOON_BEACON_PROXY_FACTORIES for {chain_id}"
            beacon_proxy_factory_address = beacon_factory["address"]
            beacon_proxy_factory_abi = beacon_factory["abi"]

        assert beacon_proxy_factory_address, f"Cannot deploy Lagoon vault beacon proxy on chain {chain_id}, no factory address found. Registered factories: {pformat(LAGOON_BEACON_PROXY_FACTORIES)}"

        vault_contract = deploy_lagoon(
            web3=web3,
            deployer=deployer_local_account,
            safe=safe,
            asset_manager=asset_manager,
            parameters=parameters,
            owner=safe.address,
            etherscan_api_key=etherscan_api_key,
            use_forge=use_forge,
            vault_abi=vault_abi,
            factory_contract=factory_contract,
            legacy=legacy,
            beacon_proxy_factory_address=beacon_proxy_factory_address,
            beacon_proxy_factory_abi=beacon_proxy_factory_abi,
        )

    if not is_anvil(web3):
        logger.info("Between contracts deployment delay: Sleeping %s for new nonce to propagade", between_contracts_delay_seconds)
        time.sleep(between_contracts_delay_seconds)

    module = deploy_safe_trading_strategy_module(
        web3=web3,
        deployer=deployer_local_account,
        safe=safe,
        etherscan_api_key=etherscan_api_key,
        verifier=verifier,
        verifier_url=verifier_url,
        use_forge=use_forge,
        enable_on_safe=not guard_only,
        cowswap=cowswap,
        gmx_deployment=gmx_deployment,
    )

    if not is_anvil(web3):
        logger.info("Between contracts deployment delay: Sleeping %s for new nonce to propagade", between_contracts_delay_seconds)
        time.sleep(between_contracts_delay_seconds)

    if isinstance(deployer, HotWallet):
        deployer.sync_nonce(web3)

    # Configure TradingStrategyModuleV0 guard
    setup_guard(
        web3=web3,
        safe=safe,
        vault=vault_contract,
        deployer=deployer,
        owner=safe.address,
        asset_manager=asset_manager,
        module=module,
        uniswap_v2=uniswap_v2,
        uniswap_v3=uniswap_v3,
        orderly_vault=orderly_vault,
        aave_v3=aave_v3,
        cowswap=cowswap,
        velora=velora,
        gmx_deployment=gmx_deployment,
        cctp_deployment=cctp_deployment,
        erc_4626_vaults=erc_4626_vaults,
        any_asset=any_asset,
        broadcast_func=_broadcast,
        assets=assets,
    )

    # After everything is deployed, fix ownership
    # 1. Transfer TradingStrategyModuleV0 module ownership to Gnosis
    # 2. Approve redemptions for Safe. USDC must be transferable to Vault (not Silo).
    # 3. Set Gnosis to a true multisig

    # 1. Transfer guard ownership to Gnosis
    assert module.functions.owner().call() == deployer.address
    tx_hash = _broadcast(module.functions.transferOwnership(safe.address))
    assert_transaction_success_with_explanation(web3, tx_hash)

    gas_estimate = estimate_gas_price(web3)

    if not guard_only:
        # 2. USDC.approve() for redemptions on Safe
        underlying = fetch_erc20_details(web3, parameters.underlying, chain_id=chain_id)
        tx_data = underlying.contract.functions.approve(vault_contract.address, 2**256 - 1).build_transaction(
            {
                "from": deployer.address,
                # "gas": 0,
                # "gasPrice": 0,
            }
        )

        gas_estimate = estimate_gas_price(web3)
        tx_data = apply_gas(tx_data, gas_estimate)
        safe_tx = safe.build_multisig_tx(underlying.address, 0, tx_data["data"])
        safe_tx.sign(deployer_local_account._private_key.hex())
        tx_hash, tx = execute_safe_tx(
            safe_tx,
            tx_sender_private_key=deployer_local_account._private_key.hex(),
            tx_gas=1_500_000,
            gas_fee=gas_estimate,
        )
        assert_transaction_success_with_explanation(web3, tx_hash)

        if not is_anvil(web3):
            gnosis_sleep = 20.0
            logger.info("Gnosis GS206 sync issue sleep %s seconds", gnosis_sleep)
            time.sleep(gnosis_sleep)

        # 3. Set Gnosis to a true multisig
        # DOES NOT REMOVE DEPLOYER
        add_new_safe_owners(
            web3,
            safe,
            deployer_local_account,
            owners=safe_owners,
            threshold=safe_threshold,
        )

    vault = LagoonVault(
        web3,
        VaultSpec(chain_id, vault_contract.address),
        trading_strategy_module_address=module.address,
        vault_abi=vault_abi,
        default_block_identifier="latest",
    )

    end_balance = web3.eth.get_balance(deployer.address)

    return LagoonAutomatedDeployment(
        chain_id=chain_id,
        vault=vault,
        trading_strategy_module=module,
        asset_manager=asset_manager,
        multisig_owners=safe_owners,
        block_number=web3.eth.block_number,
        deployer=deployer.address,
        parameters=parameters,
        old_trading_strategy_module=existing_guard_module,
        vault_abi=vault_abi,
        beacon_proxy_factory=beacon_proxy_factory_address,
        gas_used=Decimal((start_balance - end_balance) / 10**18),
    )


#  https://github.com/hopperlabsxyz/lagoon-v0
LAGOON_BEACONS = {
    # Base
    8453: "0xD69BC314bdaa329EB18F36E4897D96A3A48C3eeF",
}

#  https://github.com/hopperlabsxyz/lagoon-v0
LAGOON_LEGACY_BEACONS = {
    # Base
    8453: "0xD69BC314bdaa329EB18F36E4897D96A3A48C3eeF",
}


# https://github.com/hopperlabsxyz/lagoon-v0
# https://basescan.org/address/0xc953fd298fdfa8ed0d38ee73772d3e21bf19c61b#readContract
LAGOON_FEE_REGISTRIES = {
    # Base
    8453: "0x6dA4D1859bA1d02D095D2246142CdAd52233e27C",
}

#: https://basescan.org/address/0xC953Fd298FdfA8Ed0D38ee73772D3e21Bf19c61b#writeContract
#: https://docs.lagoon.finance/vault/create-your-vault
LAGOON_BEACON_PROXY_FACTORIES = {
    # Ethereum
    # https://docs.lagoon.finance/resources/networks-and-addresses
    # https://etherscan.io/address/0x8D6f5479B14348186faE9BC7E636e947c260f9B1
    1: {
        "abi": "lagoon/OptinProxyFactory.json",
        "address": "0x8D6f5479B14348186faE9BC7E636e947c260f9B1",
    },
    # Base
    8453: {
        "abi": "lagoon/BeaconProxyFactory.json",
        "address": "0xC953Fd298FdfA8Ed0D38ee73772D3e21Bf19c61b",
    },
    # Arbitrum
    # 42161: "0x9De724B0efEe0FbA07FE21a16B9Bf9bBb5204Fb4",
    # Arbitrum new
    # Impl https://arbiscan.io/address/0xbb2de8e36eb36dbc20d71c503711763a4be3b1b2#readContract
    # Proxy https://arbiscan.io/address/0xb1ee4f77a1691696a737ab9852e389cf4cb1f1f5#writeProxyContract#F1
    42161: {
        "abi": "lagoon/OptinProxyFactory.json",
        "address": "0xb1ee4f77a1691696a737ab9852e389cf4cb1f1f5",
    },
    # Derive
    # https://explorer.derive.xyz/address/0x4058140097F313886536bd64a7C1D25FF7356931
    957: {
        "abi": "lagoon/BeaconProxyFactory.json",
        "address": "0x4058140097F313886536bd64a7C1D25FF7356931",
    },
    # HyperEVM
    # https://docs.lagoon.finance/resources/networks-and-addresses
    999: {
        "abi": "lagoon/OptinProxyFactory.json",
        "address": "0x90beB507A1BA7D64633540cbce615B574224CD84",
    },
}


def deploy_multichain_lagoon_vault(
    *,
    chain_web3: dict[str, Web3],
    deployer: LocalAccount,
    config: LagoonConfig,
    cctp_enabled: bool = False,
    max_workers: int | None = None,
) -> LagoonMultichainDeployment:
    """Deploy Lagoon vaults across multiple chains with a shared deterministic Safe.

    Uses CREATE2 via the canonical Safe v1.4.1 ProxyFactory to produce the
    same Safe address on every chain. Each chain gets its own vault and
    guard deployment, but all vaults share the same Safe multisig address.

    Deploys all chains in parallel using threads to minimise wall-clock time.

    If ``cctp_enabled`` is True, CCTP whitelisting is automatically configured
    per chain. Chains without CCTP support (e.g. HyperEVM) are silently skipped.

    :param chain_web3:
        Mapping of chain names (lowercase, matching :data:`eth_defi.chain.CHAIN_NAMES`)
        to Web3 instances. Example: ``{"ethereum": w3_eth, "arbitrum": w3_arb}``.

    :param deployer:
        The deployer account. A separate :class:`HotWallet` is created per chain
        for nonce management.

    :param config:
        Shared :class:`LagoonConfig`. Must have ``safe_salt_nonce`` set.
        The ``parameters.underlying`` field is auto-resolved per chain from
        :data:`eth_defi.token.USDC_NATIVE_TOKEN` if set to a zero/empty address.

    :param cctp_enabled:
        If True, auto-create :class:`CCTPDeployment` per chain using
        :func:`CCTPDeployment.create_for_chain`. Chains not in
        :data:`CHAIN_ID_TO_CCTP_DOMAIN` are silently skipped.

    :param max_workers:
        Maximum number of parallel deployment threads.
        Defaults to the number of chains.

    :return:
        :class:`LagoonMultichainDeployment` with per-chain results.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from copy import deepcopy
    from eth_defi.cctp.constants import CHAIN_ID_TO_CCTP_DOMAIN
    from eth_defi.token import USDC_NATIVE_TOKEN

    assert config.safe_salt_nonce is not None, "safe_salt_nonce must be set for multichain deployment"
    assert len(chain_web3) >= 1, "Must deploy to at least one chain"

    if max_workers is None:
        max_workers = len(chain_web3)

    # Pre-compute CCTP-capable chain IDs for destination resolution
    cctp_chain_ids = {name: w3.eth.chain_id for name, w3 in chain_web3.items() if w3.eth.chain_id in CHAIN_ID_TO_CCTP_DOMAIN} if cctp_enabled else {}

    def _deploy_single_chain(chain_name: str, web3: Web3) -> tuple[str, LagoonAutomatedDeployment]:
        chain_id = web3.eth.chain_id
        logger.info("Deploying Lagoon vault on %s (chain %d)", chain_name, chain_id)

        # Create a per-chain HotWallet for nonce management
        wallet = HotWallet(deployer)
        wallet.sync_nonce(web3)

        # Deep-copy config so per-chain overrides don't leak
        per_chain_config = deepcopy(config)

        # Auto-resolve underlying token (USDC) per chain if not already set
        if per_chain_config.parameters.underlying is None or per_chain_config.parameters.underlying == "":
            usdc = USDC_NATIVE_TOKEN.get(chain_id)
            assert usdc is not None, f"No USDC address known for chain {chain_id}. Set parameters.underlying manually."
            per_chain_config.parameters.underlying = usdc

        # Auto-configure CCTP per chain
        if cctp_enabled:
            if chain_id in CHAIN_ID_TO_CCTP_DOMAIN:
                other_chain_ids = [cid for name, cid in cctp_chain_ids.items() if name != chain_name]
                if other_chain_ids:
                    per_chain_config.cctp_deployment = CCTPDeployment.create_for_chain(
                        chain_id=chain_id,
                        allowed_destinations=other_chain_ids,
                    )
                    logger.info("CCTP enabled on %s with destinations: %s", chain_name, other_chain_ids)
                else:
                    logger.info("CCTP enabled on %s but no other CCTP-capable chains in deployment", chain_name)
            else:
                logger.info("CCTP not available on %s (chain %d), skipping", chain_name, chain_id)
                per_chain_config.cctp_deployment = None

        deployment = deploy_automated_lagoon_vault(
            web3=web3,
            deployer=wallet,
            config=per_chain_config,
        )

        logger.info("Lagoon vault deployed on %s: vault=%s, safe=%s", chain_name, deployment.vault.address, deployment.safe.address)
        return chain_name, deployment

    # Deploy all chains in parallel
    deployments: dict[str, LagoonAutomatedDeployment] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_deploy_single_chain, chain_name, web3): chain_name for chain_name, web3 in chain_web3.items()}
        for future in as_completed(futures):
            chain_name = futures[future]
            chain_name, deployment = future.result()
            deployments[chain_name] = deployment

    # Verify all Safe addresses are identical (deterministic deployment)
    safe_addresses = {name: d.safe.address for name, d in deployments.items()}
    unique_safe_addresses = set(safe_addresses.values())
    assert len(unique_safe_addresses) == 1, f"Safe addresses differ across chains — deterministic deployment failed: {safe_addresses}"

    return LagoonMultichainDeployment(
        safe_address=next(iter(unique_safe_addresses)),
        deployments=deployments,
        safe_salt_nonce=config.safe_salt_nonce,
    )
