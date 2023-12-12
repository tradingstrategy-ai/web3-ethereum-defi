"""Manage the 1delta deployer.

Deploy 1delta to a local Anvil test backend using the official 1delta deployer.
"""
import json
import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from shutil import which
from typing import Type

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_linked_contract

logger = logging.getLogger(__name__)


#: What is the default location of our aave deployer script
#:
#: aave-v3-deploy is not packaged with eth_defi as they exist outside the Python package,
#: in git repository. However, it is only needed for tests and normal users should not need these files.
DEFAULT_REPO_PATH = Path(__file__).resolve().parents[2] / "contracts/1delta"


#: Default location of Aave v3 compiled ABI
DEFAULT_ABI_PATH = Path(__file__).resolve().parents[1] / "abi/1delta"

DEFAULT_HARDHAT_EXPORT_PATH = Path(__file__).resolve().parent / "1delta-hardhat-deployment-export.json"


#: We maintain our forked and patched deployer
#:
#: (No patches yet)
ONE_DELTA_REPO = "https://github.com/1delta-DAO/contracts-delegation/"


#: List of manually parsed addressed from Hardhat deployment
#:
#:
HARDHAT_CONTRACTS = {
    # "PoolAdderssProvider": "0xa85233C63b9Ee964Add6F2cffe00Fd84eb32338f",
    # "PoolImplementation": "0xf5059a5D33d5853360D16C683c16e67980206f36",
    # PoolImplementation can't be used directly, we should interact with PoolProxy
    # this is the same as mainnet deployment
    "PoolProxy": "0x763e69d24a03c0c8B256e470D9fE9e0753504D07",
    "PoolDataProvider": "0x09635F643e140090A9A8Dcd712eD6285858ceBef",
    "PoolAddressProvider": "0xa85233C63b9Ee964Add6F2cffe00Fd84eb32338f",
    # https://github.com/aave/aave-v3-periphery/blob/1fdd23b38cc5b6c095687b3c635c4d761ff75c4c/contracts/mocks/testnet-helpers/Faucet.sol
    "Faucet": "0x0B306BF915C4d645ff596e518fAf3F9669b97016",
    # TestnetERC20 https://github.com/aave/aave-v3-periphery/blob/1fdd23b38cc5b6c095687b3c635c4d761ff75c4c/contracts/mocks/testnet-helpers/TestnetERC20.sol#L12
    "USDC": "0x68B1D87F95878fE05B998F19b66F4baba5De1aed",
    "WBTC": "0x3Aa5ebB10DC797CAC828524e59A333d0A371443c",
    "WETH": "0xc6e7DF5E7b4f2A278906862b61205850344D4e7d",
    "aUSDC": "0x07AA7A1a1eAE23162130ac661Ef9D37868A6D91C",
    "vWETH": "0x5042DDe6a13212aadFE8Ed62F0796CC0A0d45fcf",
    "AaveOracle": "0x36C02dA8a0983159322a80FFE9F24b1acfF8B570",
    "WETHAgg": "0x9E545E3C0baAB3E08CdfD552C960A1050f373042",
    "USDCAgg": "0xc3e53F4d16Ae77Db1c982e75a937B9f60FE63690",
}


class OneDeltaDeployer:
    """1delta deployer wrapper.

    - Install 1delta deployer locally

    - Run the deployment command against the local Anvil installation

    """

    def __init__(
        self,
        repo_path: Path = DEFAULT_REPO_PATH,
        abi_path: Path = DEFAULT_ABI_PATH,
    ):
        """Create Aave deployer.

        :param repo_path:
            Path to 1delta git checkout.

        :param abi_path:
            Path to Aave v3 compiled ABI.
        """
        assert isinstance(repo_path, Path)
        assert isinstance(abi_path, Path)
        self.repo_path = repo_path
        self.abi_path = abi_path

    def is_checked_out(self) -> bool:
        """Check if we have a Github repo of the deployer."""
        return (self.repo_path / "package.json").exists()

    def is_installed(self) -> bool:
        """Check if we have a complete Aave deployer installation."""
        return (self.repo_path / "node_modules/.bin/hardhat").exists()

    def checkout(self, echo=False):
        """Clone aave-v3-deploy repo."""

        if echo:
            out = subprocess.STDOUT
        else:
            out = subprocess.DEVNULL

        logger.info("Checking out Aave deployer installation at %s", self.repo_path)
        git = which("git")
        assert git is not None, "No git command in path, needed for Aave v3 deployer installation"

        logger.info("Cloning %s", ONE_DELTA_REPO)
        result = subprocess.run(
            [git, "clone", ONE_DELTA_REPO, self.repo_path],
            stdout=out,
            stderr=out,
        )
        assert result.returncode == 0

        assert self.repo_path.exists()

    def install(self, echo=False) -> bool:
        """Make sure we have Aave deployer installed.

        .. note ::

            Running this function takes long time on the first attempt,
            as it downloads 1000+ NPM packages and few versions of Solidity compiler.

        - Aave v3 deployer is a NPM/Javascript package we need to checkout with `git clone`

        - We install it via NPM modules and run programmatically using subprocesses

        - If already installed do nothing

        :param echo:
            Mirror NPM output live  to stdout

        :return:
            False is already installed, True if we did perform the installation.
        """

        logger.info("Installing Aave deployer installation at %s", self.repo_path)

        yarn = which("yarn")
        assert yarn is not None, "No yarn command in path, needed for 1delta deployer installation"

        if self.is_installed():
            logger.info("1delta yarn installation already complete")
            return False

        assert self.is_checked_out(), f"{self.repo_path.absolute()} does not contain 1delta checkout"

        if echo:
            out = None
        else:
            out = subprocess.DEVNULL

        logger.info("yarn install on %s - may take long time", self.repo_path)

        result = subprocess.run(
            [yarn, "install"],
            cwd=self.repo_path,
            stdout=out,
            stderr=out,
        )
        assert result.returncode == 0, f"npm install failed: {result.stderr and result.stderr.decode('utf-8')}"

        logger.info("Installation complete")
        return True

    def deploy_local(self, web3: Web3, echo=False):
        """Deploy Aave v3 at Anvil.

        Deploys all infrastructure mentioned in the :py:mod:`eth_defi.aave_v3.deployer` documentation,
        in those fixed addresses.

        .. note ::

            Currently Aave v3 deployer is hardcoded to deploy at localhost:8545
            Anvil cannot run in other ports.

        :param echo:
            Mirror NPM output live to stdout
        """

        assert self.is_installed(), "Deployer not installed"

        assert not self.is_deployed(web3), "Already deployed on this chain"

        npx = which("npx")
        assert npx is not None, "No npx command in path, needed for Aave v3 deployment"

        if echo:
            out = None
        else:
            out = subprocess.PIPE

        logger.info("Running Aave deployer at %s", self.repo_path)

        env = os.environ.copy()
        # env["MARKET_NAME"] = "Aave"

        result = subprocess.run(
            [npx, "hardhat", "--network", "localhost", "deploy", "--reset", "--export", "1delta-hardhat-deployment-export.json"],
            cwd=self.repo_path,
            env=env,
            stderr=out,
            stdout=out,
        )
        ret_text = result.stderr
        assert result.returncode == 0, f"Aave deployment failed:\n{ret_text}"

    def is_deployed(self, web3: Web3) -> bool:
        """Check if Aave is deployed on chain"""
        # assert web3.eth.block_number > 1, "This chain does not contain any data"
        try:
            usdc = self.get_contract_at_address(web3, "MintableERC20.json", "USDC")
            return usdc.functions.symbol().call() == "USDC"
        except Exception as e:
            print(e)
            return False

    def get_contract(self, web3: Web3, name: str) -> Type[Contract]:
        """Get Aave deployer ABI file.

        ABI files contain hardcoded library addresses from the deployment
        and cannot be reused.

        This function links the contract against other deployed contracts.

        See :py:meth:`get_contract_at_address`.

        :return:
            A Contract proxy class
        """
        path = self.abi_path / name
        assert path.exists(), f"No ABI file at: {path.absolute()}"
        return get_linked_contract(web3, path, get_aave_hardhard_export())

    def get_contract_address(self, contract_name: str) -> ChecksumAddress:
        """Get a deployed contract address.

        See :py:data:`HARDHAT_CONTRACTS` for the list.

        See :py:meth:`get_contract_at_address`.
        """
        assert contract_name in HARDHAT_CONTRACTS, f"Does not know Aave contract {contract_name}"
        return Web3.to_checksum_address(HARDHAT_CONTRACTS[contract_name])

    def get_contract_at_address(self, web3: Web3, contract_fname: str, address_name: str) -> Contract:
        """Get a singleton Aave deployed contract.

        Example:

        .. code-block:: python

            pool = aave_deployer.get_contract_at_address(web3, "Pool.json", "Pool")
            assert pool.functions.POOL_REVISION().call() == 1

        """
        address = self.get_contract_address(address_name)
        ContractProxy = self.get_contract(web3, contract_fname)
        instance = ContractProxy(address)
        return instance


@lru_cache(maxsize=1)
def get_aave_hardhard_export() -> dict:
    """Read the bunled hardhad localhost deployment export.

    Precompiled hardhat for a localhost deployment.
    Needed to deploy any contracts that contain linked libraries.

    See :py:func:`eth_defi.abi.get_linked_contract`.
    """
    return json.loads(DEFAULT_HARDHAT_EXPORT_PATH.read_bytes())


def install_aave_for_testing():
    """Entry-point to ensure Aave dev env is installedon Github Actions.

    Because pytest-xdist does not have very good support for preventing
    race conditions with fixtures, we run this problematic test
    before test suite.

    It will do npm install for Aave deployer.
    """
    deployer = AaveDeployer()
    deployer.install(echo=True)
