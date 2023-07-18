"""Enzyme protocol deployment.

Functions to fetch live on-chain Enzyme deployment or deploy your own unit testing version.

Setting the Enzyme to debug mode:

.. code-block:: javascript

    window.enzymeDebug = true;

Enables

- Testnet deployments

- Impersonator wallet

See Enzyme Subgraphs: ---


"""
import enum
import re
from dataclasses import asdict, dataclass, field, fields
from pprint import pformat
from typing import Dict, Optional, Tuple

from eth_typing import HexAddress
from web3 import Web3
from web3._utils.events import EventLogErrorFlags
from web3.contract import Contract

from eth_defi.abi import encode_with_signature, get_contract, get_deployed_contract
from eth_defi.deploy import deploy_contract
from eth_defi.revert_reason import fetch_transaction_revert_reason

#: Enzyme deployment details for Polygon
#:
#: See :py:meth:`EnzymeDeployment.fetch_deployment`
#:
#: See https://docs.enzyme.finance/developers/contracts/polygon
#:
POLYGON_DEPLOYMENT = {
    "comptroller_lib": "0xf5fc0e36c85552E44354132D188C33D9361eB441",
    "usdc": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    "weth": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
    "wmatic": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
    "fund_value_calculator": "0xcdf038Dd3b66506d2e5378aee185b2f0084B7A33",
    "deployed_at": 25_825_795,  # When comptroller lib was deployed
}


class RateAsset(enum.Enum):
    """See IChainlinkPriceFeedMixin.sol"""

    ETH = 0
    USD = 1


class EnzymeDeploymentError(Exception):
    """Something is not so right."""


@dataclass(slots=True)
class EnzymeContracts:
    """Manage the registry of Enzyme contracts.

    `See Enzyme specification documentation for overview of different contracts <https://specs.enzyme.finance/>`__.

    Mimics Deployer.sol from Enzyme unit tests.
    """

    web3: Web3
    deployer: Optional[HexAddress]
    dispatcher: Contract = None
    external_position_factory: Contract = None
    protocol_fee_reserve_lib: Contract = None
    protocol_fee_reserve_proxy: Contract = None

    #: Enzyme Council maintained address list.
    #:
    #: Audited adapters.
    #:
    address_list_registry: Contract = None

    fund_deployer: Contract = None
    value_interpreter: Contract = None
    policy_manager: Contract = None
    external_position_manager: Contract = None
    fee_manager: Contract = None
    integration_manager: Contract = None
    comptroller_lib: Contract = None
    protocol_fee_tracker: Contract = None
    vault_lib: Contract = None
    gas_relay_paymaster_lib: Contract = None
    gas_relay_paymaster_factory: Contract = None

    #
    # Perihelia
    #
    fund_value_calculator: Contract = None

    def deploy(self, contract_name: str, *args):
        """Deploys a contract and stores its reference.

        Pick ABI JSON file from our precompiled package.
        """
        # Convert to snake case
        # https://stackoverflow.com/a/1176023/315168
        var_name = re.sub(r"(?<!^)(?=[A-Z])", "_", contract_name).lower()
        contract = deploy_contract(self.web3, f"enzyme/{contract_name}.json", self.deployer, *args)
        setattr(self, var_name, contract)

    def get_deployed_contract(self, contract_name: str, address: HexAddress) -> Contract:
        """Helper access for IVault and IComptroller"""
        contract = get_deployed_contract(self.web3, f"enzyme/{contract_name}.json", address)
        return contract

    def get_all_addresses(self) -> Dict[str, str]:
        """Return all labeled addresses as a dict.

        :return:
            Contract name -> address mapping
        """
        addresses = {}
        for k in fields(self):
            v = getattr(self, k.name)
            if isinstance(v, Contract):
                addresses[k.name] = v.address
            elif v is None:
                addresses[k.name] = None
        return addresses


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

    def __repr__(self):
        return f"Enzyme deployment on {self.web3.eth.chain_id}, comptroller is {self.contracts.comptroller_lib.address}"

    def add_primitive(
        self,
        token: Contract,
        aggregator: Contract,
        rate_asset: RateAsset,
    ) -> str:
        """Add a primitive asset to a Enzyme protocol.

        This will tell Enzyme how to value this asset.

        - See ValueInterpreter.sol

        - See ChainlinkPriceFeedMixin.sol

        :return:
            Transaction hash for the addition
        """

        assert isinstance(token, Contract), f"Got bad token: {token}"

        assert isinstance(rate_asset, RateAsset)
        assert token.functions.decimals().call() >= 6
        latest_round_data = aggregator.functions.latestRoundData().call()
        assert len(latest_round_data) == 5

        value_interpreter = self.contracts.value_interpreter
        primitives = [token.address]
        aggregators = [aggregator.address]
        rate_assets = [rate_asset.value]
        tx_hash = value_interpreter.functions.addPrimitives(primitives, aggregators, rate_assets).transact({"from": self.deployer})
        return tx_hash

    def remove_primitive(
        self,
        token: Contract,
    ) -> str:
        """Remove a primitive asset to a Enzyme protocol.

        This will tell Enzyme how to value this asset.

        - See ChainlinkPriceFeedMixin.sol

        :return:
            Transaction hash for the addition
        """
        assert isinstance(token, Contract), f"Got bad token: {token}"
        value_interpreter = self.contracts.value_interpreter
        primitives = [token.address]
        tx_hash = value_interpreter.functions.removePrimitives(primitives).transact({"from": self.deployer})
        return tx_hash

    def create_new_vault(
        self,
        owner: HexAddress,
        denomination_asset: Contract,
        fund_name="Example Fund",
        fund_symbol="EXAMPLE",
        shares_action_time_lock: int = 0,
        fee_manager_config_data=b"",
        policy_manager_config_data=b"",
        deployer=None,
    ) -> Tuple[Contract, Contract]:
        """
        Creates a new fund (vault).

        - See `CreateNewVault.sol`.

        - See `FundDeployer.sol`.

        :return:
            Tuple (Comptroller contract, vault contract)
        """

        if not deployer:
            deployer = self.deployer

        assert deployer, "No deployer account set up"

        fund_deployer = self.contracts.fund_deployer
        tx_hash = fund_deployer.functions.createNewFund(
            owner,
            fund_name,
            fund_symbol,
            denomination_asset.address,
            shares_action_time_lock,
            fee_manager_config_data,
            policy_manager_config_data,
        ).transact(
            {
                "from": deployer,
            }
        )
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt["status"] != 1:
            reason = fetch_transaction_revert_reason(self.web3, tx_hash)
            raise EnzymeDeploymentError(f"createNewFund() failed: {reason}")

        events = list(self.contracts.fund_deployer.events.NewFundCreated().process_receipt(receipt, EventLogErrorFlags.Discard))
        assert len(events) == 1
        new_fund_created_event = events[0]
        comptroller_proxy = new_fund_created_event["args"]["comptrollerProxy"]
        vault_proxy = new_fund_created_event["args"]["vaultProxy"]

        comptroller_contract = self.contracts.get_deployed_contract("ComptrollerLib", comptroller_proxy)
        vault_contract = self.contracts.get_deployed_contract("VaultLib", vault_proxy)
        return comptroller_contract, vault_contract

    @staticmethod
    def deploy_core(
        web3: Web3,
        deployer: HexAddress,
        mln: Contract,
        weth: Contract,
        chainlink_stale_rate_threshold=3650 * 24 * 3600,  # 10 years
        vault_position_limit=20,
        vault_mln_burner="0x0000000000000000000000000000000000000000",
    ) -> "EnzymeDeployment":
        """Make a test Enzyme deployment.

        Designed to be used in unit testing.

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
            contracts.deploy("ExternalPositionFactory", contracts.dispatcher.address)
            contracts.deploy("ProtocolFeeReserveLib", contracts.dispatcher.address)

            # deployProtocolFeeReserveProxy()
            construct_data = encode_with_signature("init(address)", [contracts.dispatcher.address])
            contracts.deploy("ProtocolFeeReserveProxy", construct_data, contracts.protocol_fee_reserve_lib.address)
            contracts.deploy("AddressListRegistry", contracts.dispatcher.address)

            contracts.deploy("GasRelayPaymasterLib", weth_address, "0x0000000000000000000000000000000000000000", "0x0000000000000000000000000000000000000000")
            contracts.deploy("GasRelayPaymasterFactory", contracts.dispatcher.address, contracts.gas_relay_paymaster_lib.address)

        def _deploy_release_contracts():
            # Mimic deployReleaseContracts()
            contracts.deploy("FundDeployer", contracts.dispatcher.address, contracts.gas_relay_paymaster_factory.address)
            contracts.deploy("ValueInterpreter", contracts.fund_deployer.address, weth_address, chainlink_stale_rate_threshold)
            contracts.deploy("PolicyManager", contracts.fund_deployer.address, contracts.gas_relay_paymaster_factory.address)
            contracts.deploy("ExternalPositionManager", contracts.fund_deployer.address, contracts.external_position_factory.address, contracts.policy_manager.address)
            contracts.deploy("FeeManager", contracts.fund_deployer.address)
            contracts.deploy("IntegrationManager", contracts.fund_deployer.address, contracts.policy_manager.address, contracts.value_interpreter.address)
            contracts.deploy(
                "ComptrollerLib",
                contracts.dispatcher.address,
                contracts.protocol_fee_reserve_proxy.address,
                contracts.fund_deployer.address,
                contracts.value_interpreter.address,
                contracts.external_position_manager.address,
                contracts.fee_manager.address,
                contracts.integration_manager.address,
                contracts.policy_manager.address,
                contracts.gas_relay_paymaster_factory.address,
                mln_address,
                weth_address,
            )
            contracts.deploy("ProtocolFeeTracker", contracts.fund_deployer.address)
            contracts.deploy("VaultLib", contracts.external_position_manager.address, contracts.gas_relay_paymaster_factory.address, contracts.protocol_fee_reserve_proxy.address, contracts.protocol_fee_tracker.address, mln_address, vault_mln_burner, weth_address, vault_position_limit)
            contracts.deploy("FundValueCalculator", contracts.fee_manager.address, contracts.protocol_fee_tracker.address, contracts.value_interpreter.address)

        def _set_fund_deployer_pseudo_vars():
            # Mimic setFundDeployerPseudoVars()
            contracts.fund_deployer.functions.setComptrollerLib(contracts.comptroller_lib.address).transact({"from": deployer})
            contracts.fund_deployer.functions.setProtocolFeeTracker(contracts.protocol_fee_tracker.address).transact({"from": deployer})
            contracts.fund_deployer.functions.setVaultLib(contracts.vault_lib.address).transact({"from": deployer})

        def _set_external_position_factory_position_deployers():
            # Mimic setExternalPositionFactoryPositionDeployers
            deployers = [contracts.external_position_manager.address]
            contracts.external_position_factory.functions.addPositionDeployers(deployers).transact({"from": deployer})

        def _set_release_live():
            # Mimic setReleaseLive()
            contracts.fund_deployer.functions.setReleaseLive().transact({"from": deployer})
            contracts.dispatcher.functions.setCurrentFundDeployer(contracts.fund_deployer.address).transact({"from": deployer})

        _deploy_persistent()
        _deploy_release_contracts()
        _set_fund_deployer_pseudo_vars()
        _set_external_position_factory_position_deployers()
        _set_release_live()

        # Some sanity checks
        assert contracts.gas_relay_paymaster_factory.functions.getCanonicalLib().call() != "0x0000000000000000000000000000000000000000"
        assert contracts.fund_deployer.functions.getOwner().call() == deployer
        assert contracts.value_interpreter.functions.getOwner().call() == deployer
        assert contracts.fund_deployer.functions.releaseIsLive().call() is True

        return EnzymeDeployment(
            web3,
            deployer,
            contracts,
            mln,
            weth,
        )

    def fetch_vault(self, vault_address: HexAddress | str) -> Tuple[Contract, Contract]:
        """Fetch existing Enzyme vault contracts.

        :return:
            Tuple (Comptroller contract, vault contract)
        """
        vault = self.contracts.get_deployed_contract("VaultLib", vault_address)
        comptroller_address = vault.functions.getAccessor().call()
        comptroller = self.contracts.get_deployed_contract("ComptrollerLib", comptroller_address)
        return comptroller, vault

    @staticmethod
    def fetch_deployment(web3: Web3, contract_addresses: dict) -> "EnzymeDeployment":
        """Fetch enzyme deployment and some of its contract.

        Read existing Enzyme deployment from on-chain.

        .. note::

            Does not do complete contract resolution yet.

        Example:

        .. code-block:: python

            from eth_defi.enzyme.deployment import EnzymeDeployment, POLYGON_DEPLOYMENT

            deployment = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT)
            assert deployment.mln.functions.symbol().call() == "MLN"
            assert deployment.weth.functions.symbol().call() == "WMATIC"

        :param contract_addresses:
            Dictionary of contract addresses required to resolve Enzyme deployment

        :return:
            Enzyme deployment details

        """

        contracts = EnzymeContracts(web3, None)
        contracts.comptroller_lib = contracts.get_deployed_contract("ComptrollerLib", contract_addresses["comptroller_lib"])

        fund_value_calculator = contract_addresses.get("fund_value_calculator")

        # FundValueCalculator might not be available in tests,
        # as legacy
        if fund_value_calculator:
            contracts.fund_value_calculator = contracts.get_deployed_contract("FundValueCalculator", contract_addresses["fund_value_calculator"])
        else:
            contracts.fund_value_calculator = None

        contracts.fund_deployer = contracts.get_deployed_contract("FundDeployer", contracts.comptroller_lib.functions.getFundDeployer().call())
        contracts.integration_manager = contracts.get_deployed_contract("IntegrationManager", contracts.comptroller_lib.functions.getIntegrationManager().call())
        contracts.value_interpreter = contracts.get_deployed_contract("ValueInterpreter", contracts.comptroller_lib.functions.getValueInterpreter().call())

        mln = get_deployed_contract(web3, "ERC20MockDecimals.json", contracts.comptroller_lib.functions.getMlnToken().call())
        weth = get_deployed_contract(web3, "ERC20MockDecimals.json", contracts.comptroller_lib.functions.getWethToken().call())

        return EnzymeDeployment(
            web3,
            None,
            contracts,
            mln,
            weth,
        )
