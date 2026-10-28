"""Reproduce the Conviction Pool executable-source comparison without transactions.

Requires ``JSON_RPC_ARBITRUM``, ``ETHERSCAN_API_KEY`` (or ``ARBISCAN_API_KEY``)
and ``SOLC_BINARY`` pointing to Solidity 0.8.23+commit.f704f362. The compiler
must support ``--standard-json``. All RPC reads use one current block.

Fetch the verified Arbitrum Gains GToken implementation from Etherscan v2,
reproduce its complete runtime, add the two observed wrapped-native-token
branches, and compare the resulting executable against Nova's current
implementation. Only the trailing Solidity CBOR metadata is excluded from
the final comparison. A mismatch fails rather than assigning a fee mode.

Canonical sources:
https://arbiscan.io/address/0xeb754588eff264793bb80be65866d11bc8d6cbdd#code
https://docs.nova.markets/#/contracts
"""

import copy
import json
import logging
import os
import subprocess  # noqa: S404 - Run the operator-supplied Solidity compiler without a shell.

import requests
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.nova_markets.constants import NOVA_MARKETS_CHAIN_ID, NOVA_POOL_ADDRESS
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

#: Verified source used as the reproducible baseline.
GAINS_IMPLEMENTATION = HexAddress("0xeb754588eff264793bb80be65866d11bc8d6cbdd")
#: Compiler named in the verified baseline's source metadata.
COMPILER_VERSION = "0.8.23+commit.f704f362"


def fetch_verified_source(api_key: str) -> dict:
    """Fetch the baseline Solidity standard-JSON compiler input.

    The explorer response must contain verified GToken source compiled with
    the expected version. API credentials are never included in output.

    :param api_key: Etherscan v2 API credential.
    :return: Verified compiler input, including all source dependencies.
    """
    try:
        response = requests.get(
            "https://api.etherscan.io/v2/api",
            params={"chainid": NOVA_MARKETS_CHAIN_ID, "module": "contract", "action": "getsourcecode", "address": GAINS_IMPLEMENTATION, "apikey": api_key},
            timeout=30,
        )
    except requests.RequestException:
        # Request exceptions may contain the API key in the query string.
        message = "Explorer source lookup failed; credential-bearing URL omitted"
        raise RuntimeError(message) from None
    if not response.ok:
        raise RuntimeError(f"Explorer source lookup failed with HTTP {response.status_code}")
    result = response.json()
    assert result.get("status") == "1", "Explorer did not return verified source"
    row = result["result"][0]
    assert row["ContractName"] == "GToken"
    assert row["CompilerVersion"] == f"v{COMPILER_VERSION}"
    source = row["SourceCode"]
    return json.loads(source[1:-1] if source.startswith("{{") else source)


def compile_runtime(solc_binary: str, compiler_input: dict) -> bytes:
    """Compile the complete GToken runtime with the original compiler settings.

    Compiler errors fail the review. Source and output stay in memory; no
    repository files, contracts or scanner state are modified.

    :param solc_binary: Solidity compiler executable.
    :param compiler_input: Solidity standard-JSON input.
    :return: Deployed runtime bytecode including compiler metadata.
    """
    process = subprocess.run([solc_binary, "--standard-json"], input=json.dumps(compiler_input), text=True, capture_output=True, check=True, timeout=60)  # noqa: S603 - Trusted local compiler; no shell.
    output = json.loads(process.stdout)
    errors = [item["formattedMessage"] for item in output.get("errors", []) if item["severity"] == "error"]
    assert not errors, errors
    return bytes.fromhex(output["contracts"]["contracts/core/GToken.sol"]["GToken"]["evm"]["deployedBytecode"]["object"])


def reconstruct_nova_source(baseline: dict) -> dict:
    """Add the two chain-helper branches found in Nova's executable.

    The changes affect wrapped-native-token detection on chains 4326 and 6343.
    GToken, its ERC-4626 dependency and all fee-related paths remain the exact
    verified source. Names and whitespace need not match Nova's unpublished
    source, so this proves executable equivalence, not metadata equivalence.

    :param baseline: Original verified compiler input.
    :return: Independent compiler input containing the reconstructed helper.
    """
    candidate = copy.deepcopy(baseline)
    helper = candidate["sources"]["contracts/libraries/ChainUtils.sol"]
    replacements = {
        "    // Supported chains": "    uint256 internal constant MEGAETH_MAINNET = 4326;\n    uint256 internal constant MEGAETH_TESTNET = 6343;\n    // Supported chains",
        "        if (block.chainid == APECHAIN_MAINNET) {\n            return APECHAIN_MAINNET_WAPE;": "        if (block.chainid == MEGAETH_MAINNET) {\n            return address(0x4200000000000000000000000000000000000006);\n        }\n\n        if (block.chainid == APECHAIN_MAINNET) {\n            return APECHAIN_MAINNET_WAPE;",
        "        if (block.chainid == TESTNET) {\n            return address(421);": "        if (block.chainid == MEGAETH_TESTNET) {\n            return address(0x4200000000000000000000000000000000000006);\n        }\n\n        if (block.chainid == TESTNET) {\n            return address(421);",
    }
    for old, new in replacements.items():
        assert helper["content"].count(old) == 1, "Verified helper source changed"
        helper["content"] = helper["content"].replace(old, new)
    return candidate


def executable(code: bytes) -> bytes:
    """Remove only the length-delimited Solidity compiler metadata trailer.

    Solidity records the CBOR trailer length in the final two bytes. All
    executable instructions and embedded constants are retained for comparison.

    :param code: Solidity deployed runtime bytecode.
    :return: Bytecode preceding the compiler metadata trailer.
    """
    metadata_size = int.from_bytes(code[-2:], "big")
    assert 0 < metadata_size < len(code) - 2
    return code[: -metadata_size - 2]


def main() -> None:
    """Verify both source reproductions against a fixed current Arbitrum block.

    Configuration comes from environment variables. Logs identify the block,
    implementation and hashes so a later upgrade can be reviewed separately.

    :return: ``None`` only after both bytecode comparisons succeed.
    """
    setup_console_logging(default_log_level="info")
    api_key = os.environ.get("ETHERSCAN_API_KEY") or os.environ["ARBISCAN_API_KEY"]
    solc_binary = os.environ["SOLC_BINARY"]
    version = subprocess.run([solc_binary, "--version"], text=True, capture_output=True, check=True, timeout=10).stdout  # noqa: S603 - Trusted local compiler; no shell.
    assert COMPILER_VERSION in version, "Use the verified source's Solidity compiler version"
    web3 = create_multi_provider_web3(os.environ["JSON_RPC_ARBITRUM"])
    assert web3.eth.chain_id == NOVA_MARKETS_CHAIN_ID
    block = web3.eth.block_number
    slot = int.from_bytes(Web3.keccak(text="eip1967.proxy.implementation"), "big") - 1
    word = web3.eth.get_storage_at(Web3.to_checksum_address(NOVA_POOL_ADDRESS), slot, block_identifier=block)
    implementation = Web3.to_checksum_address(word[-20:].hex())
    logger.info("Arbitrum block %s: Nova implementation %s", block, implementation)
    nova_runtime = web3.eth.get_code(implementation, block_identifier=block)
    gains_runtime = web3.eth.get_code(Web3.to_checksum_address(GAINS_IMPLEMENTATION), block_identifier=block)
    logger.info("Fetching the verified Gains source and reproducing its full runtime")
    source = fetch_verified_source(api_key)
    assert compile_runtime(solc_binary, source) == gains_runtime, "Baseline compiler reproduction failed"
    logger.info("Verified baseline reproduced; compiling the two-branch Nova reconstruction")
    reconstructed = compile_runtime(solc_binary, reconstruct_nova_source(source))
    assert executable(reconstructed) == executable(nova_runtime), "Nova executable differs; reassess the fee classification"
    logger.info("MATCH: %s executable bytes; runtime keccak=%s; executable keccak=%s", len(executable(nova_runtime)), Web3.keccak(nova_runtime).hex(), Web3.keccak(executable(nova_runtime)).hex())


if __name__ == "__main__":
    main()
