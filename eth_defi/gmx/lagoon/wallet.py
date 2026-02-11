"""Lagoon vault wallet for GMX trading.

This module provides a `LagoonWallet` class that implements the `BaseWallet`
interface, allowing the standard GMX CCXT adapter to trade through a Lagoon
vault without any modifications.

The wallet intercepts transaction signing and wraps all transactions through
the vault's `TradingStrategyModuleV0.performCall()` before signing with the
asset manager's hot wallet.

Example usage::

    from eth_defi.gmx.ccxt import GMX
    from eth_defi.gmx.lagoon import LagoonWallet
    from eth_defi.erc_4626.vault_protocol.lagoon import LagoonVault
    from eth_defi.hotwallet import HotWallet

    # Set up vault and asset manager
    vault = LagoonVault(web3, vault_address)
    asset_manager = HotWallet.from_private_key("0x...")

    # Create vault wallet
    wallet = LagoonWallet(vault, asset_manager)

    # Use standard GMX adapter with vault wallet
    gmx = GMX(params={"rpcUrl": rpc_url}, wallet=wallet)
    gmx.load_markets()

    # Trade through vault - standard GMX API
    order = gmx.create_order("ETH/USD", "market", "buy", 0, params={"size_usd": 1000, "leverage": 2.0})

.. note::

    The vault's TradingStrategyModuleV0 must have the GMX ExchangeRouter
    and collateral tokens whitelisted before trading will work.
"""

import logging
from decimal import Decimal
from typing import Optional

from eth_typing import HexAddress
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.basewallet import BaseWallet
from eth_defi.hotwallet import HotWallet, SignedTransactionWithNonce


logger = logging.getLogger(__name__)

#: Default gas buffer to add for performCall overhead
PERFORM_CALL_GAS_BUFFER = 200_000


class LagoonWallet(BaseWallet):
    """Wallet implementation that routes transactions through a Lagoon vault.

    This wallet wraps all transactions through the vault's
    `TradingStrategyModuleV0.performCall()` method, enabling vault-based
    trading with protocols like GMX.

    The wallet implements the full `BaseWallet` interface, making it a
    drop-in replacement for `HotWallet` when used with the GMX CCXT adapter.

    Architecture::

        GMX CCXT Adapter
        └── wallet: LagoonWallet
            ├── vault: LagoonVault (holds assets in Safe multisig)
            ├── asset_manager: HotWallet (signs wrapped transactions)
            └── sign_transaction_with_new_nonce():
                1. Wrap tx in performCall(to, data, value)
                2. Sign with asset_manager
                3. Return SignedTransactionWithNonce

    The Safe address is used as the trading account, meaning:
    - Positions are owned by the Safe, not the asset manager
    - Collateral tokens must be in the Safe
    - Token approvals are done from the Safe via performCall()

    Example::

        vault = LagoonVault(web3, vault_address)
        asset_manager = HotWallet.from_private_key("0x...")

        wallet = LagoonWallet(vault, asset_manager)

        # Use with GMX
        gmx = GMX(params={"rpcUrl": rpc_url}, wallet=wallet)
        order = gmx.create_order("ETH/USD", "market", "buy", 0, params={"size_usd": 1000})
    """

    def __init__(
        self,
        vault,
        asset_manager: HotWallet,
        gas_buffer: int = PERFORM_CALL_GAS_BUFFER,
    ):
        """Initialise the Lagoon wallet.

        :param vault:
            Lagoon vault with TradingStrategyModuleV0 configured.
            The vault's Safe address will be used as the trading account.

        :param asset_manager:
            Hot wallet of the asset manager who will sign transactions.
            Must have the asset manager role on the vault.

        :param gas_buffer:
            Additional gas to add for performCall overhead.
            Default is 200,000 gas.
        """
        # Duck typing for testability
        if not hasattr(vault, "trading_strategy_module"):
            raise TypeError(f"Expected LagoonVault-like object with trading_strategy_module, got {type(vault)}")
        if not hasattr(asset_manager, "sign_bound_call_with_new_nonce"):
            raise TypeError(f"Expected HotWallet-like object, got {type(asset_manager)}")
        if vault.trading_strategy_module_address is None:
            raise ValueError(f"Vault {vault.vault_address} has no TradingStrategyModuleV0 configured")

        self.vault = vault
        self.asset_manager = asset_manager
        self.web3 = vault.web3
        self.gas_buffer = gas_buffer

        logger.info(
            "LagoonWallet initialised: safe=%s, asset_manager=%s",
            vault.safe_address,
            asset_manager.address,
        )

    @property
    def address(self) -> HexAddress:
        """Get the wallet's Ethereum address.

        Returns the vault's Safe address, as this is the account that
        owns positions and holds collateral.
        """
        return self.vault.safe_address

    def get_main_address(self) -> HexAddress:
        """Get the main Ethereum address for this wallet.

        Returns the vault's Safe address.
        """
        return self.vault.safe_address

    def sync_nonce(self, web3: Web3) -> None:
        """Synchronise the nonce with the blockchain.

        Delegates to the asset manager's nonce, as the asset manager
        is the one signing and broadcasting transactions.
        """
        self.asset_manager.sync_nonce(web3)

    def allocate_nonce(self) -> int:
        """Get the next available nonce.

        Delegates to the asset manager.
        """
        return self.asset_manager.allocate_nonce()

    def sign_transaction_with_new_nonce(self, tx: dict) -> SignedTransactionWithNonce:
        """Sign a transaction with a new nonce.

        This is the key method that wraps the transaction through
        the vault's `performCall()` before signing.

        :param tx:
            Transaction dict with 'to', 'data', and optionally 'value' and 'gas'.

        :return:
            Signed transaction ready for broadcasting.
        """
        # Extract transaction details
        target = tx["to"]
        calldata = tx.get("data", b"")
        value = tx.get("value", 0)
        original_gas = tx.get("gas", 1_500_000)

        logger.debug(
            "Wrapping transaction through performCall: target=%s, value=%s, gas=%s",
            target,
            value,
            original_gas,
        )

        # Wrap through TradingStrategyModuleV0.performCall()
        wrapped_func = self.vault.trading_strategy_module.functions.performCall(
            target,
            calldata,
            value,
        )

        # Add gas buffer for performCall overhead
        gas_with_buffer = original_gas + self.gas_buffer

        # Sign with asset manager
        return self.asset_manager.sign_bound_call_with_new_nonce(
            wrapped_func,
            tx_params={"gas": gas_with_buffer},
            web3=self.web3,
            fill_gas_price=True,
        )

    def sign_bound_call_with_new_nonce(
        self,
        func: ContractFunction,
        tx_params: Optional[dict] = None,
        web3: Optional[Web3] = None,
        fill_gas_price: bool = False,
        value: Optional[int] = None,
    ) -> SignedTransactionWithNonce:
        """Sign a contract function call with a new nonce.

        Wraps the function call through performCall before signing.

        :param func:
            Contract function to call.

        :param tx_params:
            Additional transaction parameters.

        :param web3:
            Web3 instance (uses self.web3 if not provided).

        :param fill_gas_price:
            Whether to fill in gas price from network.

        :param value:
            ETH value to send with transaction.

        :return:
            Signed transaction ready for broadcasting.
        """
        # Get the target address and calldata from the function
        target = func.address
        # Build the calldata
        calldata = func._encode_transaction_data()
        tx_value = value or (tx_params.get("value", 0) if tx_params else 0)

        logger.debug(
            "Wrapping bound call through performCall: target=%s, function=%s",
            target,
            func.fn_name,
        )

        # Wrap through performCall
        wrapped_func = self.vault.trading_strategy_module.functions.performCall(
            target,
            calldata,
            tx_value,
        )

        # Build wrapped tx_params with gas buffer
        wrapped_params = dict(tx_params) if tx_params else {}
        if "gas" in wrapped_params:
            wrapped_params["gas"] = wrapped_params["gas"] + self.gas_buffer

        # Sign with asset manager
        return self.asset_manager.sign_bound_call_with_new_nonce(
            wrapped_func,
            tx_params=wrapped_params,
            web3=web3 or self.web3,
            fill_gas_price=fill_gas_price,
            value=None,  # Value is already in performCall
        )

    def get_native_currency_balance(self, web3: Web3) -> Decimal:
        """Get the wallet's native currency balance.

        Returns the Safe's ETH balance, as this is where trading funds are held.
        """
        balance_wei = web3.eth.get_balance(self.vault.safe_address)
        return Decimal(balance_wei) / Decimal(10**18)

    @staticmethod
    def fill_in_gas_price(web3: Web3, tx: dict) -> dict:
        """Fill in gas price details for a transaction.

        Delegates to HotWallet's implementation.
        """
        return HotWallet.fill_in_gas_price(web3, tx)
