"""Provider-based wallet implementation.

This module provides a wallet implementation that delegates transaction signing to a connected
provider (like MetaMask or other browser wallets).
"""

from typing import Optional
from decimal import Decimal

from eth_typing import HexAddress, ChecksumAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.basewallet import BaseWallet
from eth_defi.hotwallet import SignedTransactionWithNonce
from eth_defi.gas import estimate_gas_fees, apply_gas, estimate_gas_price


class Web3ProviderWallet(BaseWallet):
    """Wallet implementation that delegates operations to a connected Web3 provider.

    This wallet is designed to work with browser wallets (like MetaMask) or other
    external signers connected via a Web3 provider. It manages nonce locally but
    relies on the provider for actual transaction signing and submission.

    Example:
    ```python
    # Connect to a Web3 provider with an account already unlocked
    web3 = Web3(Web3.HTTPProvider("http://localhost:8545"))
    wallet = Web3ProviderWallet(web3)
    wallet.sync_nonce(web3)

    # Use with contract functions
    bound_call = token_contract.functions.transfer(recipient, amount)
    tx_hash = wallet.transact_and_broadcast_with_contract(bound_call)
    ```
    """

    def __init__(self, web3: Web3):
        """Create a wallet using a connected Web3 provider.

        Parameters
        ----------
        web3 : Web3
            Web3 instance with connected accounts
        """
        self.web3 = web3

        # Ensure we have a connected account
        if not self.web3.eth.accounts:
            raise ValueError("No accounts available in the connected Web3 provider")

        self.current_nonce: Optional[int] = None

    @property
    def address(self) -> ChecksumAddress:
        """Get the wallet's Ethereum address."""
        return self.web3.eth.accounts[0]

    def get_main_address(self) -> HexAddress:
        """Get the main Ethereum address for this wallet."""
        return self.address

    def sync_nonce(self, web3: Web3) -> None:
        """Synchronize the nonce with the blockchain."""
        self.current_nonce = web3.eth.get_transaction_count(self.address)

    def allocate_nonce(self) -> int:
        """Get the next available nonce."""
        if self.current_nonce is None:
            raise ValueError("Nonce not synchronized. Call sync_nonce() first.")
        nonce = self.current_nonce
        self.current_nonce += 1
        return nonce

    def sign_transaction_with_new_nonce(self, tx: dict) -> SignedTransactionWithNonce:
        """Sign a transaction with a new nonce.

        Note: With Web3ProviderWallet, this method doesn't actually sign the transaction,
        as the signing is handled by the provider when the transaction is sent.
        Instead, it prepares the transaction with a nonce and returns a placeholder.

        Call send_raw_transaction on the returned object's rawTransaction to broadcast.
        """
        assert "nonce" not in tx, "Transaction already has a nonce"
        tx["nonce"] = self.allocate_nonce()
        tx["from"] = self.address

        # A placeholder for SignedTransactionWithNonce
        # The actual signing will happen when send_raw_transaction is called
        return SignedTransactionWithNonce(
            rawTransaction=tx,  # Not actually raw, but will be processed by send_raw_transaction
            hash=None,
            r=0,
            s=0,
            v=0,
            nonce=tx["nonce"],
            address=self.address,
            source=tx,
        )

    def sign_bound_call_with_new_nonce(
        self,
        func: ContractFunction,
        tx_params: Optional[dict] = None,
        web3: Optional[Web3] = None,
        fill_gas_price: bool = False,
    ) -> SignedTransactionWithNonce:
        """Sign a contract function call with a new nonce."""
        if tx_params is None:
            tx_params = {}

        tx_params["from"] = self.address

        if "chainId" not in tx_params:
            tx_params["chainId"] = func.w3.eth.chain_id

        if fill_gas_price and web3:
            gas_price_suggestion = estimate_gas_price(web3)
            apply_gas(tx_params, gas_price_suggestion)

        # Build the transaction
        tx = func.build_transaction(tx_params)

        # Return a SignedTransactionWithNonce placeholder
        return self.sign_transaction_with_new_nonce(tx)

    def get_native_currency_balance(self, web3: Web3) -> Decimal:
        """Get the wallet's native currency balance."""
        balance = web3.eth.get_balance(self.address)
        return web3.from_wei(balance, "ether")

    @staticmethod
    def fill_in_gas_price(web3: Web3, tx: dict) -> dict:
        """Fill in gas price details for a transaction."""
        price_data = estimate_gas_fees(web3)
        apply_gas(tx, price_data)
        return tx

    def send_transaction(self, unsigned_tx: dict) -> HexBytes:
        """Send a transaction using the provider.

        This method delegates the actual sending to the web3 provider.
        """
        # The provider will handle signing
        return self.web3.eth.send_transaction(unsigned_tx)

    def transact_and_broadcast_with_contract(
        self,
        func: ContractFunction,
        gas_limit: Optional[int] = None,
    ) -> HexBytes:
        """Transact with a contract and broadcast the transaction.

        Parameters
        ----------
        func : ContractFunction
            Bound contract function call
        gas_limit : int, optional
            Gas limit for the transaction

        Returns
        -------
        HexBytes
            Transaction hash
        """
        web3 = func.w3

        tx_data = func.build_transaction(
            {
                "from": self.address,
            }
        )

        if gas_limit is not None:
            tx_data["gas"] = gas_limit

        self.fill_in_gas_price(web3, tx_data)

        # Provider requires nonce
        if "nonce" not in tx_data:
            tx_data["nonce"] = self.allocate_nonce()

        # Send the transaction through the provider
        return web3.eth.send_transaction(tx_data)
