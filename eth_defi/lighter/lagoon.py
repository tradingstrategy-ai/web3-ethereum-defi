"""Lagoon vault helpers for Lighter L1 custody.

This module contains reusable helpers for moving USDC between a Lagoon Safe and
the Lighter L1 contract. It deliberately does not cover Lagoon deployment or
share redemption orchestration; those remain protocol/vault concerns.

Authoritative documentation:

- Lighter deposits and withdrawals:
  https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals
- Lighter L1 proxy:
  https://etherscan.io/address/0x3b4d794a66304f130a4db8f2551b0070dfcf5ca7
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.confirmation import broadcast_and_wait_transactions_to_complete
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gas import apply_gas, estimate_gas_price
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.api import LIGHTER_MIN_MAINNET_USDC, LIGHTER_STATE_POLL_SECONDS
from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT
from eth_defi.safe.execute import execute_safe_tx
from eth_defi.token import TokenDetails

logger = logging.getLogger(__name__)

#: Perpetual balances live on route 0.
LIGHTER_ROUTE_PERP = 0


def broadcast_tx(
    web3: Web3,
    hot_wallet: HotWallet,
    bound_func,
    description: str,
    gas_limit: int = 1_000_000,
) -> str:
    """Sign, broadcast and wait for a transaction.

    :param web3:
        Web3 connection.
    :param hot_wallet:
        Transaction signer.
    :param bound_func:
        Bound contract function.
    :param description:
        Human-readable transaction description.
    :param gas_limit:
        Gas limit.
    :return:
        Transaction hash hex string.
    """
    gas_price_suggestion = estimate_gas_price(web3)
    tx_params = apply_gas({"gas": gas_limit}, gas_price_suggestion)
    tx = hot_wallet.sign_bound_call_with_new_nonce(bound_func, tx_params=tx_params)

    logger.info(f"  Broadcasting: {description}")
    logger.info(f"    TX hash: {tx.hash.hex()}")
    broadcast_and_wait_transactions_to_complete(web3, [tx])
    receipt = web3.eth.get_transaction_receipt(tx.hash)
    if receipt["status"] != 1:
        raise RuntimeError(f"Transaction failed: {description} ({tx.hash.hex()})")
    logger.info(f"    Gas used: {receipt['gasUsed']:,}")
    return tx.hash.hex()


def deposit_usdc_from_lagoon_safe_into_lighter(
    web3: Web3,
    hot_wallet: HotWallet,
    vault: LagoonVault,
    usdc: TokenDetails,
    deposit_usdc: Decimal,
) -> None:
    """Approve and deposit USDC from a Lagoon Safe into Lighter.

    :param web3:
        Web3 connection.
    :param hot_wallet:
        Asset-manager wallet.
    :param vault:
        Lagoon vault.
    :param usdc:
        USDC token details.
    :param deposit_usdc:
        Human-readable USDC amount.
    """
    safe = vault.safe_address
    zk = get_deployed_contract(web3, "lighter/ZkLighter.json", LIGHTER_L1_CONTRACT)
    asset_index = zk.functions.USDC_ASSET_INDEX().call()
    amount_raw = usdc.convert_to_raw(deposit_usdc)
    module = get_deployed_contract(web3, "safe-integration/TradingStrategyModuleV0.json", vault.trading_strategy_module_address)

    if deposit_usdc < LIGHTER_MIN_MAINNET_USDC:
        raise ValueError(f"Lighter Ethereum deposits have a {LIGHTER_MIN_MAINNET_USDC} USDC minimum, got {deposit_usdc}")

    logger.info(f"\nDepositing {deposit_usdc} USDC from Safe into Lighter...")
    logger.info(f"  Lighter contract: {LIGHTER_L1_CONTRACT}")
    logger.info(f"  Safe / Lighter owner: {safe}")
    logger.info(f"  USDC asset index: {asset_index}")

    approve_data = usdc.contract.functions.approve(LIGHTER_L1_CONTRACT, amount_raw)._encode_transaction_data()
    broadcast_tx(
        web3,
        hot_wallet,
        module.functions.performCall(usdc.address, approve_data, 0),
        "Approve USDC to Lighter from Safe",
    )

    deposit_data = zk.functions.deposit(safe, asset_index, LIGHTER_ROUTE_PERP, amount_raw)._encode_transaction_data()
    broadcast_tx(
        web3,
        hot_wallet,
        module.functions.performCall(LIGHTER_L1_CONTRACT, deposit_data, 0),
        "Deposit USDC from Safe to Lighter",
    )
    logger.info(f"  Safe USDC balance after Lighter deposit: {usdc.fetch_balance_of(safe)}")


def fetch_lighter_pending_balance(web3: Web3, owner: HexAddress, asset_index: int) -> int:
    """Fetch pending Lighter L1 withdrawal balance.

    :param web3:
        Web3 connection.
    :param owner:
        L1 account owner.
    :param asset_index:
        Lighter asset index.
    :return:
        Pending raw token amount.
    """
    abi = [
        {
            "inputs": [
                {"internalType": "address", "name": "_owner", "type": "address"},
                {"internalType": "uint16", "name": "_assetIndex", "type": "uint16"},
            ],
            "name": "getPendingBalance",
            "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}],
            "stateMutability": "view",
            "type": "function",
        }
    ]
    contract = web3.eth.contract(address=Web3.to_checksum_address(LIGHTER_L1_CONTRACT), abi=abi)
    return contract.functions.getPendingBalance(owner, asset_index).call()


def claim_lighter_pending_balance(  # noqa: PLR0917
    web3: Web3,
    hot_wallet: HotWallet,
    vault: LagoonVault,
    usdc: TokenDetails,
    expected_raw_amount: int,
    timeout: int,
) -> None:
    """Claim Lighter pending withdrawal balance back to a Lagoon Safe.

    :param web3:
        Web3 connection.
    :param hot_wallet:
        Asset-manager wallet.
    :param vault:
        Lagoon vault.
    :param usdc:
        USDC token details.
    :param expected_raw_amount:
        Expected raw USDC amount.
    :param timeout:
        Maximum wait in seconds.
    """
    zk = get_deployed_contract(web3, "lighter/ZkLighter.json", LIGHTER_L1_CONTRACT)
    module = get_deployed_contract(web3, "safe-integration/TradingStrategyModuleV0.json", vault.trading_strategy_module_address)
    asset_index = zk.functions.USDC_ASSET_INDEX().call()
    deadline = time.monotonic() + timeout
    acceptable_shortfall_raw = max(10, expected_raw_amount // 1000)
    claim_raw_amount = 0

    logger.info("\nWaiting for Lighter withdrawal to become claimable on L1...")
    while True:
        pending_raw = fetch_lighter_pending_balance(web3, vault.safe_address, asset_index)
        pending_usdc = usdc.convert_to_decimals(pending_raw)
        if pending_raw >= expected_raw_amount:
            logger.info(f"  Pending claimable balance: {pending_usdc} USDC")
            claim_raw_amount = expected_raw_amount
            break
        if expected_raw_amount - acceptable_shortfall_raw <= pending_raw < expected_raw_amount:
            shortfall_raw = expected_raw_amount - pending_raw
            logger.info(f"  Pending claimable balance: {pending_usdc} USDC; accepting {usdc.convert_to_decimals(shortfall_raw)} USDC shortfall")
            claim_raw_amount = pending_raw
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Lighter pending withdrawal did not reach {usdc.convert_to_decimals(expected_raw_amount)} USDC within {timeout} seconds; current pending {pending_usdc}")
        logger.info(f"  Pending claimable balance: {pending_usdc} USDC; polling again in {LIGHTER_STATE_POLL_SECONDS}s")
        time.sleep(LIGHTER_STATE_POLL_SECONDS)

    before = usdc.fetch_balance_of(vault.safe_address)
    before_raw = usdc.convert_to_raw(before)
    claim_data = zk.functions.withdrawPendingBalance(vault.safe_address, asset_index, claim_raw_amount)._encode_transaction_data()
    broadcast_tx(
        web3,
        hot_wallet,
        module.functions.performCall(LIGHTER_L1_CONTRACT, claim_data, 0),
        "Claim Lighter pending balance to Safe",
        gas_limit=250_000,
    )
    expected_safe_raw_balance = before_raw + claim_raw_amount
    deadline = time.monotonic() + 120
    while True:
        after = usdc.fetch_balance_of(vault.safe_address)
        if usdc.convert_to_raw(after) >= expected_safe_raw_balance:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Lighter pending balance claim was mined, but Safe USDC balance did not reach {usdc.convert_to_decimals(expected_safe_raw_balance)} within 120 seconds; current balance {after}")
        logger.info(f"  Waiting for claimed USDC to be visible on Safe; current {after}")
        time.sleep(5)
    logger.info(f"  Safe USDC after Lighter claim: {before} -> {after}")


def sweep_safe_usdc_to_hot_wallet(web3: Web3, hot_wallet: HotWallet, vault: LagoonVault) -> None:
    """Sweep any residual Lagoon Safe USDC directly to the hot wallet.

    :param web3:
        Web3 connection.
    :param hot_wallet:
        Deployer and 1-of-1 Safe owner.
    :param vault:
        Lagoon vault whose Safe holds residual USDC.
    """
    usdc = vault.underlying_token
    safe_usdc = usdc.fetch_balance_of(vault.safe_address)
    if safe_usdc <= 0:
        return

    logger.info(f"\nSweeping {safe_usdc} residual USDC from Safe to hot wallet...")
    transfer_data = usdc.contract.functions.transfer(hot_wallet.address, usdc.convert_to_raw(safe_usdc))._encode_transaction_data()
    safe_tx = vault.safe.build_multisig_tx(usdc.address, 0, bytes.fromhex(transfer_data.removeprefix("0x")))
    safe_tx.sign(hot_wallet.private_key.hex())
    gas_price = max(web3.eth.gas_price * 2, 1_000_000_000)
    tx_hash, tx = execute_safe_tx(
        safe_tx,
        tx_sender_private_key=hot_wallet.private_key.hex(),
        tx_gas_price=gas_price,
        tx_nonce=web3.eth.get_transaction_count(hot_wallet.address),
    )
    logger.info(f"  Direct Safe sweep tx: {tx_hash.hex()} (nonce {tx['nonce']}, gas price {tx['gasPrice']})")
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if receipt["status"] != 1:
        raise RuntimeError(f"Safe USDC dust sweep failed: {tx_hash.hex()}")
    hot_wallet.sync_nonce(web3)
    logger.info(f"  Hot wallet USDC balance: {usdc.fetch_balance_of(hot_wallet.address)}")
    logger.info(f"  Safe USDC balance: {usdc.fetch_balance_of(vault.safe_address)}")
