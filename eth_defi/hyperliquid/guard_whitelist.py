"""Hypercore guard whitelisting for CoreWriter vault interactions.

Utilities for whitelisting CoreWriter and Hypercore native vaults
in Guard contracts for vault deposits/withdrawals through a managed Safe.

When a Safe needs to deposit into Hypercore native vaults,
the guard contract must whitelist:

1. The CoreWriter system contract (for ``sendRawAction()`` calls)
2. The CoreDepositWallet (for USDC bridging via ``deposit()``)
3. Each allowed Hypercore vault address

Example::

    from eth_defi.hyperliquid.guard_whitelist import setup_hypercore_whitelisting

    setup_hypercore_whitelisting(
        web3=web3,
        guard=guard_contract,
        owner=safe_address,
        vault_addresses=["0x1234..."],
    )
"""

import logging

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract

from eth_defi.hyperliquid.core_writer import (
    CORE_DEPOSIT_WALLET_MAINNET,
    CORE_DEPOSIT_WALLET_TESTNET,
    CORE_WRITER_ADDRESS,
)
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)


def get_core_deposit_wallet(chain_id: int) -> HexAddress:
    """Get the CoreDepositWallet address for a given chain.

    :param chain_id:
        EVM chain ID (999 = HyperEVM mainnet, 998 = testnet).

    :return:
        CoreDepositWallet address.
    """
    if chain_id == 998:
        return CORE_DEPOSIT_WALLET_TESTNET
    return CORE_DEPOSIT_WALLET_MAINNET


def setup_hypercore_whitelisting(
    web3: Web3,
    guard: Contract,
    owner: HexAddress | str,
    vault_addresses: list[HexAddress | str] | None = None,
    core_writer: HexAddress | str = CORE_WRITER_ADDRESS,
    core_deposit_wallet: HexAddress | str | None = None,
    notes: str = "Hypercore vault trading",
) -> list[HexBytes]:
    """Whitelist CoreWriter and Hypercore vaults in a guard contract.

    Calls the guard's ``whitelistCoreWriter()`` and
    ``whitelistHypercoreVault()`` functions.

    :param web3:
        Web3 connection.

    :param guard:
        Guard contract (GuardV0, TradingStrategyModuleV0, or similar).

    :param owner:
        Address of the guard owner (typically the Safe).

    :param vault_addresses:
        List of Hypercore native vault addresses to whitelist.

    :param core_writer:
        CoreWriter system contract address. Defaults to the standard address.

    :param core_deposit_wallet:
        CoreDepositWallet address. If ``None``, auto-detected from chain ID.

    :param notes:
        Annotation for the whitelisting event logs.

    :return:
        List of transaction hashes.
    """
    tx_hashes = []

    if core_deposit_wallet is None:
        chain_id = web3.eth.chain_id
        core_deposit_wallet = get_core_deposit_wallet(chain_id)

    # Whitelist CoreWriter + CoreDepositWallet
    logger.info(
        "Whitelisting CoreWriter %s and CoreDepositWallet %s",
        core_writer,
        core_deposit_wallet,
    )
    tx_hash = guard.functions.whitelistCoreWriter(
        Web3.to_checksum_address(core_writer),
        Web3.to_checksum_address(core_deposit_wallet),
        notes,
    ).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hashes.append(tx_hash)

    # Whitelist each vault
    if vault_addresses:
        for vault in vault_addresses:
            logger.info("Whitelisting Hypercore vault: %s", vault)
            tx_hash = guard.functions.whitelistHypercoreVault(
                Web3.to_checksum_address(vault),
                f"Hypercore vault: {vault}",
            ).transact({"from": owner})
            assert_transaction_success_with_explanation(web3, tx_hash)
            tx_hashes.append(tx_hash)

    logger.info(
        "Hypercore whitelisting complete: %d vault(s)",
        len(vault_addresses) if vault_addresses else 0,
    )
    return tx_hashes
