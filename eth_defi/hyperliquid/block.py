"""HyperEVM dual-block architecture helpers.

HyperEVM produces two types of blocks:

- **Small blocks** (~2-3M gas, every ~1 second): normal transactions
- **Large blocks** (30M gas, every ~1 minute): contract deployments, heavy computation

Transactions are routed to independent mempools based on the sender's
account-level ``usingBigBlocks`` flag. To deploy contracts that exceed
the small block gas limit (e.g. ``TradingStrategyModuleV0`` at ~5.4M gas),
the deployer must first enable large blocks via the ``evmUserModify``
HyperCore action.

Example::

    from eth_defi.hyperliquid.block import big_blocks_for_deployment

    # Wrap each contract deployment — no-op on non-HyperEVM chains
    with big_blocks_for_deployment(web3, private_key):
        deploy_contract(...)

    # Configuration transactions run in small blocks (fast confirmation)
    setup_guard(...)

See `Dual-block architecture <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/dual-block-architecture>`__
for details.
"""

import logging
import time
from contextlib import contextmanager

import msgpack
import requests
from eth_account import Account
from eth_account import messages as eth_messages
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from eth_utils import keccak, to_hex
from web3 import Web3

logger = logging.getLogger(__name__)

#: HyperEVM chain IDs where dual-block architecture applies.
HYPEREVM_CHAIN_IDS: set[int] = {998, 999}

#: Gas limit for HyperEVM large blocks (30M).
#:
#: Small blocks have ~2-3M gas; large blocks always have 30M.
#: Used to override ``eth_getBlock("latest")["gasLimit"]`` which may return
#: a small block's limit even when the deployer has big blocks enabled.
HYPEREVM_BIG_BLOCK_GAS_LIMIT: int = 30_000_000

#: Hyperliquid exchange API URL (mainnet).
HYPERLIQUID_EXCHANGE_API_MAINNET = "https://api.hyperliquid.xyz"

#: Hyperliquid exchange API URL (testnet).
HYPERLIQUID_EXCHANGE_API_TESTNET = "https://api.hyperliquid-testnet.xyz"

#: EIP-712 domain for Hyperliquid L1 action signing.
_EIP712_DOMAIN = {
    "chainId": 1337,
    "name": "Exchange",
    "verifyingContract": "0x0000000000000000000000000000000000000000",
    "version": "1",
}

#: EIP-712 types for the phantom agent.
_EIP712_TYPES = {
    "Agent": [
        {"name": "source", "type": "string"},
        {"name": "connectionId", "type": "bytes32"},
    ],
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
}


def is_hyperevm(chain_id: int) -> bool:
    """Check if a chain ID is HyperEVM (mainnet or testnet).

    :param chain_id:
        EVM chain ID.

    :return:
        ``True`` if the chain is HyperEVM mainnet (999) or testnet (998).
    """
    return chain_id in HYPEREVM_CHAIN_IDS


def fetch_using_big_blocks(web3: Web3, address: HexAddress | str) -> bool:
    """Check if an address is currently using large blocks.

    Calls the HyperEVM-specific ``eth_usingBigBlocks`` JSON-RPC method.

    :param web3:
        Web3 connected to a HyperEVM node.

    :param address:
        Address to check.

    :return:
        ``True`` if the address is flagged for large blocks.
    """
    result = web3.provider.make_request(
        "eth_usingBigBlocks",
        [Web3.to_checksum_address(address)],
    )
    return bool(result.get("result", False))


def _action_hash(
    action: dict,
    nonce: int,
    vault_address: str | None = None,
) -> bytes:
    """Hash a Hyperliquid L1 action using msgpack.

    Follows the phantom agent signing protocol used by the
    `Hyperliquid Python SDK <https://github.com/hyperliquid-dex/hyperliquid-python-sdk>`__.
    """
    data = msgpack.packb(action)
    data += nonce.to_bytes(8, "big")
    if vault_address is None:
        data += b"\x00"
    else:
        data += b"\x01"
        data += bytes.fromhex(vault_address[2:] if vault_address.startswith("0x") else vault_address)
    return keccak(data)


def _sign_l1_action(
    wallet: LocalAccount,
    action: dict,
    nonce: int,
    is_mainnet: bool,
) -> dict:
    """Sign a Hyperliquid L1 action with EIP-712.

    Uses the phantom agent pattern: the action is hashed with msgpack,
    then wrapped in an EIP-712 ``Agent`` struct for signing.

    :return:
        Signature dict with ``r``, ``s``, ``v`` fields.
    """
    hash_bytes = _action_hash(action, nonce)
    phantom_agent = {
        "source": "a" if is_mainnet else "b",
        "connectionId": hash_bytes,
    }
    full_message = {
        "domain": _EIP712_DOMAIN,
        "types": _EIP712_TYPES,
        "primaryType": "Agent",
        "message": phantom_agent,
    }
    structured_data = eth_messages.encode_typed_data(full_message=full_message)
    signed = wallet.sign_message(structured_data)
    return {"r": to_hex(signed["r"]), "s": to_hex(signed["s"]), "v": signed["v"]}


def set_big_blocks(
    private_key: str,
    enable: bool,
    is_mainnet: bool = True,
    timeout: float = 10.0,
) -> dict:
    """Enable or disable large blocks for a deployer address.

    Sends an ``evmUserModify`` action to the Hyperliquid exchange API.
    After enabling, **all** transactions from the address are routed
    to the large block mempool (~1 minute confirmation) until disabled.

    :param private_key:
        Hex-encoded private key (with or without ``0x`` prefix).

    :param enable:
        ``True`` to enable large blocks, ``False`` to disable.

    :param is_mainnet:
        ``True`` for HyperEVM mainnet (chain 999),
        ``False`` for testnet (chain 998).

    :param timeout:
        HTTP request timeout in seconds.

    :return:
        API response dict.

    :raises requests.HTTPError:
        If the API returns an error status code.
    """
    wallet = Account.from_key(private_key)
    base_url = HYPERLIQUID_EXCHANGE_API_MAINNET if is_mainnet else HYPERLIQUID_EXCHANGE_API_TESTNET

    nonce_ms = int(time.time() * 1000)
    action = {
        "type": "evmUserModify",
        "usingBigBlocks": enable,
    }

    signature = _sign_l1_action(
        wallet=wallet,
        action=action,
        nonce=nonce_ms,
        is_mainnet=is_mainnet,
    )

    payload = {
        "action": action,
        "nonce": nonce_ms,
        "signature": signature,
        "vaultAddress": None,
    }

    logger.info(
        "Setting big blocks %s for %s on %s",
        "enabled" if enable else "disabled",
        wallet.address,
        "mainnet" if is_mainnet else "testnet",
    )

    response = requests.post(
        f"{base_url}/exchange",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    result = response.json()
    logger.info("Big blocks API response: %s", result)
    return result


@contextmanager
def big_blocks_enabled(
    private_key: str,
    is_mainnet: bool = True,
    web3: Web3 | None = None,
):
    """Context manager that enables large blocks and disables them on exit.

    Checks whether the address already has large blocks enabled
    and only toggles if needed. Always restores the original state
    on exit (even if an exception occurs).

    Example::

        with big_blocks_enabled(private_key, is_mainnet=False, web3=web3):
            deploy_automated_lagoon_vault(...)

    :param private_key:
        Hex-encoded deployer private key.

    :param is_mainnet:
        ``True`` for mainnet, ``False`` for testnet.

    :param web3:
        Optional Web3 instance for checking current status via
        ``eth_usingBigBlocks``. If not provided, always toggles.
    """
    wallet = Account.from_key(private_key)
    address = wallet.address

    already_enabled = False
    if web3 is not None:
        already_enabled = fetch_using_big_blocks(web3, address)

    if already_enabled:
        logger.info("Big blocks already enabled for %s, skipping toggle", address)
        yield
    else:
        set_big_blocks(private_key, enable=True, is_mainnet=is_mainnet)
        try:
            yield
        finally:
            set_big_blocks(private_key, enable=False, is_mainnet=is_mainnet)


def enable_big_blocks(
    web3: Web3,
    private_key: str,
) -> bool:
    """Enable large blocks if needed for contract deployment on HyperEVM.

    Checks the chain ID and current block gas limit. If the chain is
    HyperEVM and the block gas limit is below 10M (small block),
    enables large blocks for the deployer.

    Does nothing on non-HyperEVM chains or Anvil forks (which override
    the gas limit).

    :param web3:
        Web3 connection.

    :param private_key:
        Hex-encoded deployer private key.

    :return:
        ``True`` if big blocks were enabled (caller should disable after),
        ``False`` if no action was taken.
    """
    from eth_defi.provider.anvil import is_anvil

    chain_id = web3.eth.chain_id
    if not is_hyperevm(chain_id):
        return False

    if is_anvil(web3):
        logger.info("Anvil fork detected, skipping big blocks toggle")
        return False

    wallet = Account.from_key(private_key)
    address = wallet.address

    if fetch_using_big_blocks(web3, address):
        logger.info("Big blocks already enabled for %s", address)
        return False

    block_gas_limit = web3.eth.get_block("latest")["gasLimit"]
    if block_gas_limit >= 10_000_000:
        logger.info(
            "Block gas limit is %d (>= 10M), big blocks not needed",
            block_gas_limit,
        )
        return False

    is_mainnet = chain_id == 999
    set_big_blocks(private_key, enable=True, is_mainnet=is_mainnet)
    return True


def disable_big_blocks(
    web3: Web3,
    private_key: str,
):
    """Disable large blocks after contract deployment.

    Counterpart to :func:`enable_big_blocks`.
    Only call this if that function returned ``True``.

    :param web3:
        Web3 connection.

    :param private_key:
        Hex-encoded deployer private key.
    """
    chain_id = web3.eth.chain_id
    is_mainnet = chain_id == 999
    set_big_blocks(private_key, enable=False, is_mainnet=is_mainnet)


@contextmanager
def big_blocks_for_deployment(
    web3: Web3,
    private_key: str,
):
    """Context manager that enables large blocks for a single contract deployment.

    Use this to wrap individual contract deployment calls so that
    configuration transactions between deployments run in small blocks
    (fast ~1 second confirmation) rather than large blocks (~1 minute).

    On non-HyperEVM chains or Anvil forks this is a no-op.

    Example::

        with big_blocks_for_deployment(web3, private_key):
            deploy_contract(...)

    :param web3:
        Web3 connection.

    :param private_key:
        Hex-encoded deployer private key.
    """
    from eth_defi.provider.anvil import is_anvil

    chain_id = web3.eth.chain_id
    if not is_hyperevm(chain_id) or is_anvil(web3):
        yield
        return

    is_mainnet = chain_id == 999

    # Always toggle rather than checking eth_usingBigBlocks first.
    # The check reads from the EVM RPC while set_big_blocks writes
    # via the exchange API; there is a propagation delay between the
    # two, so back-to-back context managers can see stale state and
    # skip the enable, causing "exceeds block gas limit" failures.
    set_big_blocks(private_key, enable=True, is_mainnet=is_mainnet)
    try:
        yield
    finally:
        set_big_blocks(private_key, enable=False, is_mainnet=is_mainnet)
