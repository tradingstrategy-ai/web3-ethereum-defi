"""Token approval functions for GMX trading through Lagoon vaults.

This module provides functions to approve GMX contracts to spend tokens
from the vault's Safe multisig via TradingStrategyModuleV0.
"""

import logging
from decimal import Decimal
from typing import Callable, Any

from hexbytes import HexBytes
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.hotwallet import HotWallet
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gmx.contracts import get_contract_addresses
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation


logger = logging.getLogger(__name__)

#: Type alias for broadcast callback functions
BroadcastCallback = Callable[[Web3, HotWallet, ContractFunction, int], HexBytes]

#: Pass as ``amount`` to approve the maximum possible amount (``2**256 - 1``).
UNLIMITED = Decimal(-1)


def _default_approval_broadcast(
    web3: Web3,
    asset_manager: HotWallet,
    func: ContractFunction,
    gas_limit: int = 150_000,
) -> HexBytes:
    """Default broadcast callback for approval transactions."""
    tx = asset_manager.sign_bound_call_with_new_nonce(
        func,
        tx_params={"gas": gas_limit},
        web3=web3,
        fill_gas_price=True,
    )
    tx_hash = web3.eth.send_raw_transaction(tx.raw_transaction)
    assert_transaction_success_with_explanation(web3, tx_hash)
    return tx_hash


def approve_gmx_collateral_via_vault(
    vault: LagoonVault,
    asset_manager: HotWallet,
    collateral_token: TokenDetails,
    amount: Decimal,
    broadcast_callback: BroadcastCallback | None = None,
) -> HexBytes:
    """Approve GMX SyntheticsRouter to spend collateral from the vault's Safe.

    This wraps a token approval through the vault's TradingStrategyModuleV0,
    allowing the Safe to approve the GMX SyntheticsRouter to spend tokens.

    :param vault:
        Lagoon vault instance

    :param asset_manager:
        Hot wallet of the asset manager

    :param collateral_token:
        Token to approve (e.g., USDC)

    :param amount:
        Amount to approve (human-readable decimals).
        Pass :data:`UNLIMITED` for max uint256 approval.

    :param broadcast_callback:
        Optional custom callback for broadcasting.
        Default signs with asset_manager and waits for confirmation.

    :return:
        Transaction hash

    Example::

        from eth_defi.gmx.lagoon.approvals import approve_gmx_collateral_via_vault, UNLIMITED

        tx_hash = approve_gmx_collateral_via_vault(
            vault=vault,
            asset_manager=asset_manager,
            collateral_token=usdc,
            amount=UNLIMITED,
        )
    """
    # Duck typing for testability
    if not hasattr(vault, "transact_via_trading_strategy_module"):
        raise TypeError(f"Expected LagoonVault-like object, got {type(vault)}")
    if not hasattr(asset_manager, "sign_bound_call_with_new_nonce"):
        raise TypeError(f"Expected HotWallet-like object, got {type(asset_manager)}")
    if not hasattr(collateral_token, "convert_to_raw"):
        raise TypeError(f"Expected TokenDetails-like object, got {type(collateral_token)}")
    if not isinstance(amount, Decimal):
        raise TypeError(f"Expected Decimal, got {type(amount)}")

    web3 = vault.web3
    chain_id = web3.eth.chain_id

    # Get GMX contract addresses
    # Map chain ID to chain name for GMX
    chain_name_map = {
        42161: "arbitrum",
        421614: "arbitrum_sepolia",
        43114: "avalanche",
    }
    chain = chain_name_map.get(chain_id)
    if not chain:
        raise ValueError(f"GMX not supported on chain ID {chain_id}")

    addresses = get_contract_addresses(chain)
    spender = addresses.syntheticsrouter

    # Convert amount to raw (UNLIMITED → max uint256)
    if amount == UNLIMITED:
        amount_raw = 2**256 - 1
    else:
        amount_raw = collateral_token.convert_to_raw(amount)

    logger.info(
        "Approving %s for GMX SyntheticsRouter via vault: token=%s, spender=%s, amount=%s",
        collateral_token.symbol,
        collateral_token.address,
        spender,
        amount,
    )

    # Build approval call
    approve_func = collateral_token.contract.functions.approve(spender, amount_raw)

    # Wrap through TradingStrategyModuleV0
    wrapped_func = vault.transact_via_trading_strategy_module(approve_func)

    # Sync nonce and broadcast
    asset_manager.sync_nonce(web3)

    if broadcast_callback:
        tx_hash = broadcast_callback(web3, asset_manager, wrapped_func, 150_000)
    else:
        tx_hash = _default_approval_broadcast(web3, asset_manager, wrapped_func, 150_000)

    logger.info(
        "GMX collateral approval successful: tx_hash=%s",
        web3.to_hex(tx_hash),
    )

    return tx_hash


def approve_gmx_execution_fee_via_vault(
    vault: LagoonVault,
    asset_manager: HotWallet,
    weth_token: TokenDetails,
    amount: Decimal,
    broadcast_callback: BroadcastCallback | None = None,
) -> HexBytes:
    """Approve GMX ExchangeRouter to spend WETH from the vault's Safe.

    .. note::

        GMX execution fees are normally paid as **native ETH** via the
        ``sendWnt()`` payable function, which does not require any ERC-20
        approval. When using :py:class:`~eth_defi.gmx.lagoon.wallet.LagoonGMXTradingWallet`
        with ``forward_eth=True``, native ETH is forwarded through
        ``performCall`` and this WETH approval is **not used**.

        This approval is only relevant if the Safe holds WETH and
        the ExchangeRouter needs to pull it via ``transferFrom``
        (e.g. a non-standard fee payment path). In most setups
        this function can be skipped.

    :param vault:
        Lagoon vault instance

    :param asset_manager:
        Hot wallet of the asset manager

    :param weth_token:
        WETH token details

    :param amount:
        Amount of WETH to approve (human-readable).
        Pass :data:`UNLIMITED` for max uint256 approval.

    :param broadcast_callback:
        Optional custom callback for broadcasting

    :return:
        Transaction hash
    """
    # Duck typing for testability
    if not hasattr(vault, "transact_via_trading_strategy_module"):
        raise TypeError(f"Expected LagoonVault-like object, got {type(vault)}")
    if not hasattr(asset_manager, "sign_bound_call_with_new_nonce"):
        raise TypeError(f"Expected HotWallet-like object, got {type(asset_manager)}")
    if not hasattr(weth_token, "convert_to_raw"):
        raise TypeError(f"Expected TokenDetails-like object, got {type(weth_token)}")

    web3 = vault.web3
    chain_id = web3.eth.chain_id

    # Get GMX contract addresses
    chain_name_map = {
        42161: "arbitrum",
        421614: "arbitrum_sepolia",
        43114: "avalanche",
    }
    chain = chain_name_map.get(chain_id)
    if not chain:
        raise ValueError(f"GMX not supported on chain ID {chain_id}")

    addresses = get_contract_addresses(chain)
    spender = addresses.exchangerouter

    # Convert amount to raw (UNLIMITED → max uint256)
    if amount == UNLIMITED:
        amount_raw = 2**256 - 1
    else:
        amount_raw = weth_token.convert_to_raw(amount)

    logger.info(
        "Approving WETH for GMX ExchangeRouter via vault: spender=%s, amount=%s",
        spender,
        amount,
    )

    # Build approval call
    approve_func = weth_token.contract.functions.approve(spender, amount_raw)

    # Wrap through TradingStrategyModuleV0
    wrapped_func = vault.transact_via_trading_strategy_module(approve_func)

    # Sync nonce and broadcast
    asset_manager.sync_nonce(web3)

    if broadcast_callback:
        tx_hash = broadcast_callback(web3, asset_manager, wrapped_func, 150_000)
    else:
        tx_hash = _default_approval_broadcast(web3, asset_manager, wrapped_func, 150_000)

    logger.info(
        "GMX WETH approval successful: tx_hash=%s",
        web3.to_hex(tx_hash),
    )

    return tx_hash
