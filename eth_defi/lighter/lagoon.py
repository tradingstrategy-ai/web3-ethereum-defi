"""Lagoon Safe helpers for Lighter L1 deposits.

The deployment flow transfers the minimum accounted USDC balance from a Lagoon
Safe to Lighter through ``TradingStrategyModuleV0``. Trading and withdrawals
are intentionally outside this custody helper.

Authoritative Lighter deposit documentation:
https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals
"""

import logging
from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract
from eth_defi.confirmation import broadcast_and_wait_transactions_to_complete
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gas import apply_gas, estimate_gas_price
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.api import LIGHTER_MIN_MAINNET_USDC
from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT
from eth_defi.token import TokenDetails

logger = logging.getLogger(__name__)

#: Perpetual balances live on route 0.
LIGHTER_ROUTE_PERP = 0


def broadcast_tx(
    web3: Web3,
    hot_wallet: HotWallet,
    bound_func: ContractFunction,
    description: str,
    gas_limit: int = 1_000_000,
) -> str:
    """Sign, broadcast and wait for one transaction.

    :param web3:
        Web3 connection.
    :param hot_wallet:
        Transaction signer.
    :param bound_func:
        Bound contract function to execute.
    :param description:
        Human-readable transaction description for logs.
    :param gas_limit:
        Transaction gas limit.
    :return:
        Confirmed transaction hash in hexadecimal form.
    """
    gas_price_suggestion = estimate_gas_price(web3)
    tx_params = apply_gas({"gas": gas_limit}, gas_price_suggestion)
    tx = hot_wallet.sign_bound_call_with_new_nonce(bound_func, tx_params=tx_params)
    logger.info("Broadcasting: %s", description)
    logger.info("Transaction hash: %s", tx.hash.hex())
    broadcast_and_wait_transactions_to_complete(web3, [tx])
    receipt = web3.eth.get_transaction_receipt(tx.hash)
    if receipt["status"] != 1:
        raise RuntimeError(f"Transaction failed: {description} ({tx.hash.hex()})")
    logger.info("Gas used: %s", receipt["gasUsed"])
    return Web3.to_hex(tx.hash)


def deposit_usdc_from_lagoon_safe_into_lighter(
    web3: Web3,
    hot_wallet: HotWallet,
    *,
    vault: LagoonVault,
    usdc: TokenDetails,
    deposit_usdc: Decimal,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
) -> str:
    """Approve and deposit Safe-owned USDC into Lighter through the guard.

    The caller must fund the Safe through Lagoon settlement before this call;
    direct USDC donations are not an accounted Lagoon subscription.

    :param web3:
        Web3 connection.
    :param hot_wallet:
        Whitelisted Lagoon asset-manager wallet.
    :param vault:
        Lagoon vault whose Safe owns the Lighter account.
    :param usdc:
        Configured native Ethereum USDC token details.
    :param deposit_usdc:
        Human-readable USDC amount to deposit.
    :param zk_lighter:
        Whitelisted ZkLighter L1 contract address.
    :return:
        Confirmed Lighter deposit transaction hash.
    """
    if deposit_usdc < LIGHTER_MIN_MAINNET_USDC:
        raise ValueError(f"Lighter Ethereum deposits have a {LIGHTER_MIN_MAINNET_USDC} USDC minimum, got {deposit_usdc}")

    zk_lighter = Web3.to_checksum_address(zk_lighter)
    safe = vault.safe_address
    zk = get_deployed_contract(web3, "lighter/ZkLighter.json", zk_lighter)
    asset_index = zk.functions.USDC_ASSET_INDEX().call()
    amount_raw = usdc.convert_to_raw(deposit_usdc)
    module = get_deployed_contract(web3, "safe-integration/TradingStrategyModuleV0.json", vault.trading_strategy_module_address)

    logger.info("Depositing %s USDC from Safe %s into Lighter %s", deposit_usdc, safe, zk_lighter)
    approve_data = usdc.contract.functions.approve(zk_lighter, amount_raw)._encode_transaction_data()
    broadcast_tx(
        web3,
        hot_wallet,
        module.functions.performCall(usdc.address, approve_data, 0),
        "Approve USDC to Lighter from Safe",
    )

    deposit_data = zk.functions.deposit(safe, asset_index, LIGHTER_ROUTE_PERP, amount_raw)._encode_transaction_data()
    tx_hash = broadcast_tx(
        web3,
        hot_wallet,
        module.functions.performCall(zk_lighter, deposit_data, 0),
        "Deposit USDC from Safe to Lighter",
    )
    logger.info("Safe USDC balance after Lighter deposit: %s", usdc.fetch_balance_of(safe))
    return tx_hash
