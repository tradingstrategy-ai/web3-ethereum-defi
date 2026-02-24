"""HyperEVM Anvil fork testing helpers.

Reusable utilities for setting up mock Hypercore contracts
on HyperEVM Anvil forks.

The real CoreWriter and CoreDepositWallet are system precompiles
that do not work in Anvil forks. These helpers inject mock contract
bytecode at the system addresses so that guard integration tests
can exercise the full deposit/withdrawal flows locally.

For general-purpose Anvil helpers (account constants, ERC-20 funding),
see :py:mod:`eth_defi.provider.anvil`.

Example::

    from eth_defi.hyperliquid.testing import setup_anvil_hypercore_mocks
    from eth_defi.provider.anvil import fund_erc20_on_anvil

    # Inject mock CoreWriter + CoreDepositWallet at system addresses
    setup_anvil_hypercore_mocks(web3, deployer_address)

    # Fund an address with ERC-20 tokens via storage manipulation
    fund_erc20_on_anvil(web3, usdc_address, recipient, amount)
"""

import logging

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_abi_by_filename
from eth_defi.hyperliquid.core_writer import (
    CORE_DEPOSIT_WALLET,
    CORE_WRITER_ADDRESS,
)
from eth_defi.provider.anvil import (
    ANVIL_DEPLOYER,
    ANVIL_OWNER_1,
    ANVIL_OWNER_2,
    ANVIL_PRIVATE_KEY,
    find_erc20_balance_slot,
    fund_erc20_on_anvil,
)

logger = logging.getLogger(__name__)

# Re-export for backwards compatibility
__all__ = [
    "ANVIL_PRIVATE_KEY",
    "ANVIL_DEPLOYER",
    "ANVIL_OWNER_1",
    "ANVIL_OWNER_2",
    "find_erc20_balance_slot",
    "fund_erc20_on_anvil",
    "load_deployed_bytecode",
    "deploy_mock_core_writer",
    "deploy_mock_core_deposit_wallet",
    "setup_anvil_hypercore_mocks",
]


def load_deployed_bytecode(abi_filename: str) -> str:
    """Load deployed bytecode from a compiled ABI JSON file.

    Used to inject mock contract bytecode via ``anvil_setCode``.

    :param abi_filename:
        ABI JSON filename relative to the ``eth_defi`` ABI directory,
        e.g. ``"guard/MockCoreWriter.json"``.

    :return:
        Hex-encoded deployed bytecode string starting with ``0x``.
    """
    abi_data = get_abi_by_filename(abi_filename)
    bytecode = abi_data["deployedBytecode"]["object"]
    if not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode
    return bytecode


def deploy_mock_core_writer(web3: Web3) -> Contract:
    """Inject MockCoreWriter bytecode at the CoreWriter system address.

    The real CoreWriter at ``0x3333...3333`` is a precompile that does
    not work in Anvil forks. This injects a mock Solidity contract
    that records ``sendRawAction()`` calls for later verification.

    :param web3:
        Web3 connected to an Anvil fork.

    :return:
        Contract instance for the MockCoreWriter at the system address.
    """
    bytecode = load_deployed_bytecode("guard/MockCoreWriter.json")
    address = Web3.to_checksum_address(CORE_WRITER_ADDRESS)
    web3.provider.make_request("anvil_setCode", [address, bytecode])
    # Clear storage slot 0 (actions array length) to avoid conflicts
    # with existing storage at the real CoreWriter address
    web3.provider.make_request(
        "anvil_setStorageAt",
        [address, "0x" + "0" * 64, "0x" + "0" * 64],
    )
    deployed_code = web3.eth.get_code(address)
    assert len(deployed_code) > 0, "MockCoreWriter bytecode not set"
    logger.info("MockCoreWriter deployed at %s", address)
    abi_data = get_abi_by_filename("guard/MockCoreWriter.json")
    return web3.eth.contract(address=address, abi=abi_data["abi"])


def deploy_mock_core_deposit_wallet(web3: Web3) -> Contract:
    """Inject MockCoreDepositWallet bytecode at the correct chain address.

    Auto-selects the address based on chain ID:

    - Chain 998 (testnet): ``CORE_DEPOSIT_WALLET[998]``
    - Chain 999 (mainnet): ``CORE_DEPOSIT_WALLET[999]``

    :param web3:
        Web3 connected to an Anvil fork.

    :return:
        Contract instance for the MockCoreDepositWallet.
    """
    bytecode = load_deployed_bytecode("guard/MockCoreDepositWallet.json")
    chain_id = web3.eth.chain_id
    cdw_address = Web3.to_checksum_address(CORE_DEPOSIT_WALLET[chain_id])
    web3.provider.make_request("anvil_setCode", [cdw_address, bytecode])
    # Clear storage slot 0 (deposits array length) to avoid conflicts
    web3.provider.make_request(
        "anvil_setStorageAt",
        [cdw_address, "0x" + "0" * 64, "0x" + "0" * 64],
    )
    deployed_code = web3.eth.get_code(cdw_address)
    assert len(deployed_code) > 0, "MockCoreDepositWallet bytecode not set"
    logger.info("MockCoreDepositWallet deployed at %s", cdw_address)
    abi_data = get_abi_by_filename("guard/MockCoreDepositWallet.json")
    return web3.eth.contract(address=cdw_address, abi=abi_data["abi"])


def setup_anvil_hypercore_mocks(
    web3: Web3,
    deployer_address: HexAddress | str | None = None,
    hype_balance: int = 1_000 * 10**18,
) -> tuple[Contract, Contract]:
    """Set up mock Hypercore contracts and optionally fund the deployer.

    Convenience function that calls :func:`deploy_mock_core_writer` and
    :func:`deploy_mock_core_deposit_wallet`, and optionally sets the
    deployer's HYPE balance for gas.

    :param web3:
        Web3 connected to an Anvil fork.

    :param deployer_address:
        If provided, fund this address with ``hype_balance`` HYPE for gas.

    :param hype_balance:
        Amount of HYPE (in wei) to give the deployer. Default: 1000 HYPE.

    :return:
        Tuple of ``(mock_core_writer, mock_core_deposit_wallet)`` contracts.
    """
    mock_cw = deploy_mock_core_writer(web3)
    mock_cdw = deploy_mock_core_deposit_wallet(web3)

    if deployer_address:
        web3.provider.make_request("anvil_setBalance", [deployer_address, hex(hype_balance)])
        logger.info("Funded deployer %s with %d HYPE (wei)", deployer_address, hype_balance)

    return mock_cw, mock_cdw
