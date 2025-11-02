"""Oracle provider mock setup for GMX fork testing.

This module compiles the MockOracleProvider contract from the Foundry test
and deploys it on the fork, replacing the production oracle provider address.
"""

import logging
import subprocess
import json
from pathlib import Path
from cchecksum import to_checksum_address
from web3 import Web3
from web3.contract import Contract

logger = logging.getLogger(__name__)

# Path to MockOracleProvider Solidity contract
MOCK_ORACLE_CONTRACT_PATH = Path(__file__).parent.parent / "forked-env-example" / "contracts" / "mock" / "MockOracleProvider.sol"
FOUNDRY_OUT_DIR = Path(__file__).parent.parent / "forked-env-example" / "out"


def compile_mock_oracle_provider() -> dict:
    """Compile the MockOracleProvider contract using forge.

    Returns:
        Dictionary with 'bytecode' and 'abi' keys

    Raises:
        RuntimeError: If compilation fails
    """
    if not MOCK_ORACLE_CONTRACT_PATH.exists():
        raise FileNotFoundError(f"MockOracleProvider contract not found at {MOCK_ORACLE_CONTRACT_PATH}")

    logger.info(f"Compiling MockOracleProvider from {MOCK_ORACLE_CONTRACT_PATH}")

    try:
        # Try to use forge to compile
        result = subprocess.run(["forge", "build"], cwd=MOCK_ORACLE_CONTRACT_PATH.parent.parent, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            logger.warning(f"forge build failed: {result.stderr}")
            # Try to find compiled artifacts
            return _find_compiled_artifacts()

        # Look for compiled contract in out directory
        return _find_compiled_artifacts()

    except FileNotFoundError:
        logger.warning("forge not found, trying to use pre-compiled artifacts")
        return _find_compiled_artifacts()
    except subprocess.TimeoutExpired:
        logger.warning("forge compilation timed out, trying pre-compiled artifacts")
        return _find_compiled_artifacts()


def _find_compiled_artifacts() -> dict:
    """Find pre-compiled contract artifacts in the Foundry out directory.

    Returns:
        Dictionary with 'bytecode' and 'abi' keys

    Raises:
        FileNotFoundError: If compiled artifacts not found
    """
    # Look for MockOracleProvider.json in out directory
    artifact_paths = list(FOUNDRY_OUT_DIR.glob("**/MockOracleProvider.json"))

    if not artifact_paths:
        raise FileNotFoundError(f"MockOracleProvider compiled artifacts not found in {FOUNDRY_OUT_DIR}. Run 'forge build' in the forked-env-example directory first.")

    artifact_path = artifact_paths[0]
    logger.info(f"Found compiled artifact at {artifact_path}")

    with open(artifact_path, "r") as f:
        artifact = json.load(f)

    return {
        "bytecode": artifact.get("bytecode", {}).get("object", "0x"),
        "abi": artifact.get("abi", []),
        "deployedBytecode": artifact.get("deployedBytecode", {}).get("object", "0x"),
    }


def setup_mock_oracle_provider(web3: Web3, provider_address: str) -> None:
    """Setup mock oracle provider on fork by replacing bytecode.

    This mimics Foundry's vm.etch() - it replaces the bytecode at the production
    oracle provider address with our mock implementation.

    Args:
        web3: Web3 instance connected to fork
        provider_address: Address of the oracle provider to replace

    Raises:
        RuntimeError: If setup fails
    """
    provider_address = to_checksum_address(provider_address)

    try:
        # Compile or find the mock contract
        contract_data = compile_mock_oracle_provider()
        bytecode = contract_data.get("bytecode", "0x")
        abi = contract_data.get("abi", [])

        if bytecode == "0x" or len(bytecode) < 4:
            raise ValueError(f"Invalid bytecode: {bytecode[:100]}")

        logger.info(f"Deploying MockOracleProvider bytecode ({len(bytecode) // 2} bytes) to {provider_address}")

        # Use anvil_setCode to replace the bytecode at the provider address
        from eth_defi.provider.named import get_provider_name
        from eth_defi.provider.anvil import is_anvil

        if not is_anvil(web3):
            raise RuntimeError(f"setup_mock_oracle_provider only works with Anvil, not {get_provider_name(web3.provider)}")

        # Set the bytecode using Anvil RPC
        response = web3.provider.make_request("anvil_setCode", [provider_address, bytecode])

        if "error" in response:
            raise RuntimeError(f"anvil_setCode failed: {response['error']}")

        logger.info(f"Successfully set MockOracleProvider bytecode at {provider_address}")

        # Create contract instance for future interactions
        mock_oracle = web3.eth.contract(address=provider_address, abi=abi)

        # Set default prices (if contract has setPrice function)
        try:
            # Example: set some default prices
            # This depends on the MockOracleProvider interface
            logger.info("Mock oracle provider deployed successfully")
        except Exception as e:
            logger.warning(f"Could not set default prices: {e}")

    except Exception as e:
        logger.error(f"Failed to setup mock oracle provider: {e}")
        raise RuntimeError(f"Failed to setup mock oracle provider: {e}") from e
