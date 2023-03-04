"""Enzyme protocol deployment.

Functions to fetch live on-chain Enzyme deployment or deploy your own unit testing version.
"""
import enum
from dataclasses import dataclass, field
from typing import Dict, Tuple

from eth_defi.abi import get_contract, encode_with_signature
from eth_defi.deploy import deploy_contract
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract



class RateAsset(enum.Enum):
    """See IChainlinkPriceFeedMixin.sol"""
    ETH = 1
    USD = 2


@dataclass(slots=True)
class EnzymeContracts:
    """Manage the registry of Enzyme contracts.

    Mimics Deployer.sol.
    """
    web3: Web3
    deployer: HexAddress
    Dispatcher: Contract = None
    ExternalPositionFactory: Contract = None
    ProtocolFeeReserveLib: Contract = None
    ProtocolFeeReserveProxy: Contract = None
    AddressListRegistry: Contract = None
    FundDeployer: Contract = None
    ValueInterpreter: Contract = None
    PolicyManager: Contract = None
    ExternalPositionManager: Contract = None
    FeeManager: Contract = None
    IntegrationManager: Contract = None
    ComptrollerLib: Contract = None
    ProtocolFeeTracker: Contract = None
    VaultLib: Contract = None

    def deploy(self, contract_name: str, *args):
        """Deploys a contract and stores its reference.

        Pick ABI JSON file from our precompiled package.
        """
        contract = deploy_contract(self.web3, f"enzyme/{contract_name}.json", self.deployer, *args)
        setattr(self, contract_name, contract)


@dataclass(slots=True)
class EnzymeDeployment:
    """Enzyme protocol deployment description.

    - Describe on-chain Enzyme deployment

    - Provide property access and documentation of different parts of Enzyme protocol

    - Allow vault deployments and such
    """

    #: Web3 connection this deployment is tied to
    web3: Web3

    #: The deployer account used in tests
    deployer: HexAddress

    #: Mimic Enzyme's deployer.sol
    contracts: EnzymeContracts

    #: MELON ERC-20
    mln: Contract

    #: WETH ERC-20
    weth: Contract

    def add_primitive(
            self,
            token: Contract,
            aggregator: Contract,
            rate_asset: RateAsset,
        ):
        """Add a a primitive.

        - See ValueInterpreter.sol

        - See ChainlinkPriceFeedMixin.sol
        """
        assert isinstance(rate_asset, RateAsset)
        value_interpreter = self.contracts.ValueInterpreter
        primitives = [token.address]
        aggregators = [aggregator.address]
        rate_assets = [rate_asset.value]
        value_interpreter.functions.addPrimitives(primitives, aggregators, rate_assets).transact({"from": self.deployer})

    def create_new_vault(
            self,
            owner: HexAddress,
            denomination_asset: HexAddress,
            fund_name = "Example Fund",
            fund_symbol = "EXAMPLE",
            shares_action_time_lock: int = 0,
            fee_manager_config_data = b"",
            policy_manager_config_data = b"",
    ) -> Tuple[Contract, Contract]:
        """
        Creates a new fund (vault).

        - See `CreateNewVault.sol`.

        - See `FundDeployer.sol`.

        :return:
            Tuple (Comptroller proxy address, vault address)
        """

        # function createNewVault(
        #     CoreDeployment _deployment,
        #     address _vaultOwner,
        #     address _denominationAsset,
        #     uint256 _sharesActionTimelock,
        #     bytes memory _feeManagerConfigData,
        #     bytes memory _policyManagerConfigData
        # ) public returns (IComptroller comptrollerProxy_, IVault vaultProxy_) {

        fund_deployer = self.contracts.FundDeployer
        tx_hash = fund_deployer.functions.createNewFund(
            owner,
            fund_name,
            fund_symbol,
            denomination_asset,
            shares_action_time_lock,
            fee_manager_config_data,
            policy_manager_config_data,
        ).transact({
            "from": self.deployer,
        })
        receipt = self.web3.eth.get_transaction_receipt(tx_hash)
        events = self.contracts.FundDeployer.events.NewFundCreated.process_receipt(receipt)
        import ipdb ; ipdb.set_trace()
        new_fund_created_event = events[0]

    @staticmethod
    def deploy_core(
            web3: Web3,
            deployer: HexAddress,
            mln: Contract,
            weth: Contract,
            chainlink_stale_rate_threshold = 3650 * 24 * 3600,  # 10 years
            vault_position_limit = 20,
            gas_relay_paymaster_factory = "0x0000000000000000000000000000000000000000",
            vault_mln_burner = "0x0000000000000000000000000000000000000000",
    ) -> "EnzymeDeployment":
        """Make a test Enzyme deployment.

        This is copied from the Forge test suite `deployLiveRelease()`.
        
        See
        
        - contracts/enzyme/tests/deployment
        
        :param deployer:
            EVM account used for the deployment
            
        """

        weth_address = weth.address
        mln_address = mln.address

        contracts = EnzymeContracts(web3, deployer)

        def _deploy_persistent():
            # Mimic deployPersistentContracts()
            contracts.deploy("Dispatcher")
            contracts.deploy("ExternalPositionFactory", contracts.Dispatcher.address)
            contracts.deploy("ProtocolFeeReserveLib", contracts.Dispatcher.address)

            # deployProtocolFeeReserveProxy()
            construct_data = encode_with_signature("init(address)", [contracts.Dispatcher.address])
            contracts.deploy("ProtocolFeeReserveProxy", construct_data, contracts.ProtocolFeeReserveLib.address)

            contracts.deploy("AddressListRegistry", contracts.Dispatcher.address)

        def _deploy_release_contracts():
            # Mimic deployReleaseContracts()
            contracts.deploy("FundDeployer", contracts.Dispatcher.address, gas_relay_paymaster_factory)
            contracts.deploy("ValueInterpreter", contracts.FundDeployer.address, weth_address, chainlink_stale_rate_threshold)
            contracts.deploy("PolicyManager", contracts.FundDeployer.address, gas_relay_paymaster_factory)
            contracts.deploy("ExternalPositionManager", contracts.FundDeployer.address, contracts.ExternalPositionFactory.address, contracts.PolicyManager.address)
            contracts.deploy("FeeManager", contracts.FundDeployer.address)
            contracts.deploy("IntegrationManager", contracts.FundDeployer.address, contracts.PolicyManager.address, contracts.ValueInterpreter.address)
            contracts.deploy("ComptrollerLib",
                             contracts.Dispatcher.address,
                             contracts.ProtocolFeeReserveProxy.address,
                             contracts.FundDeployer.address,
                             contracts.ValueInterpreter.address,
                             contracts.ExternalPositionManager.address,
                             contracts.FeeManager.address,
                             contracts.IntegrationManager.address,
                             contracts.PolicyManager.address,
                             gas_relay_paymaster_factory,
                             mln_address,
                             weth_address,
                             )
            contracts.deploy("ProtocolFeeTracker", contracts.FundDeployer.address)
            contracts.deploy("VaultLib",
                             contracts.ExternalPositionManager.address,
                             gas_relay_paymaster_factory,
                             contracts.ProtocolFeeReserveProxy.address,
                             contracts.ProtocolFeeTracker.address,
                             mln_address,
                             vault_mln_burner,
                             weth_address,
                             vault_position_limit
                             )

        def _set_fund_deployer_pseudo_vars():
            # Mimic setFundDeployerPseudoVars()
            contracts.FundDeployer.functions.setComptrollerLib(contracts.ComptrollerLib.address).transact({"from": deployer})
            contracts.FundDeployer.functions.setProtocolFeeTracker(contracts.ProtocolFeeTracker.address).transact({"from": deployer})
            contracts.FundDeployer.functions.setVaultLib(contracts.VaultLib.address).transact({"from": deployer})

        def _set_external_position_factory_position_deployers():
            # Mimic setExternalPositionFactoryPositionDeployers
            deployers = [contracts.ExternalPositionManager.address]
            contracts.ExternalPositionFactory.functions.addPositionDeployers(deployers).transact({"from": deployer})

        def _set_release_live():
            # Mimic setReleaseLive()
            contracts.Dispatcher.functions.setCurrentFundDeployer(contracts.FundDeployer.address).transact({"from": deployer})

        _deploy_persistent()
        _deploy_release_contracts()
        _set_fund_deployer_pseudo_vars()
        _set_external_position_factory_position_deployers()
        _set_release_live()

        return EnzymeDeployment(
            web3,
            deployer,
            contracts,
            mln,
            weth,
        )
