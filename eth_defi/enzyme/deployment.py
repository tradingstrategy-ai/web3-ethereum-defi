"""Enzyme protocol deployment.

Functions to fetch live on-chain Enzyme deployment or deploy your own unit testing version.
"""
from dataclasses import dataclass

from eth_defi.abi import get_contract
from eth_defi.deploy import deploy_contract
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract


@dataclass(slots=True)
class EnzymeDeployment:
    """Enzyme protocol deployment description.

    - Describe on-chain Enzyme deployment

    - Provide property access and documentation of different parts of Enzyme protocol

    - Allow vault deployments and such
    """

    #: Enzyme core deployment description
    #:
    #: See `contracts/enzyme/tests/deployment/CoreDeployment.sol`
    core_deployment: Contract

    @staticmethod
    def deploy_test_environment(
            web3: Web3,
            deployer: HexAddress,
            melon_token: Contract,
            weth_token: Contract,
    ) -> Contract:
        """Deploy the Enzyme test environment contract fixture."""
        # Deploy the Solidity deployer script

        assert isinstance(melon_token, Contract)
        assert isinstance(weth_token, Contract)

        test_environment = deploy_contract(
            web3,
            "EnzymeTestEnvironment.json",
            deployer,
            melon_token.address,
            weth_token.address,
        )
        return test_environment

    @staticmethod
    def deploy_core(
            web3: Web3,
            deployer: HexAddress,
            test_environment: Contract,
    ) -> "EnzymeDeployment":
        """Make a test Enzyme deployment.
        
        See
        
        - contracts/enzyme/tests/deployment
        
        :param deployer:
            EVM account used for the deployment
            
        """

        assert isinstance(test_environment, Contract)

        # struct Config {
        #     ITestEnvironment testEnvironment;
        #     uint256 chainlinkStaleRateThreshold;
        #     address gasRelayHub;
        #     address gasRelayTrustedForwarder;
        #     address vaultMlnBurner;
        #     uint256 vaultPositionsLimit;
        # }

        # Cannot pass named struct here - bug in web3.py / Sol 0.8 compatibiltiy
        config = [
            test_environment.address,
            3650 * 24 * 3600,  # 10 years
            "0x0000000000000000000000000000000000000000",
            "0x0000000000000000000000000000000000000000",
            "0x0000000000000000000000000000000000000000",
            20,
        ]

        # xconfig = {
        #     "testEnvironment": test_environment.address,
        #     "chainlinkStaleRateThreshold": 3650 * 24 * 3600,  # 10 years
        #     "gasRelayHub": "0x0000000000000000000000000000000000000000",
        #     "gasRelayTrustedForwarder": "0x0000000000000000000000000000000000000000",
        #     "vaultMlnBurner": "0x0000000000000000000000000000000000000000",
        #     "vaultPositionsLimit": 20,
        # }

        # Deploy the Solidity deployer script
        core_deployer = deploy_contract(
            web3,
            "enzyme/CoreDeployment.json",
            deployer,
            deployer,
            config,
        )

        # Call the deployer
        deployment = core_deployer.functions.deployPersistentContracts().transact({"from": deployer})
        import ipdb ; ipdb.set_trace()
