"""Utilities for managing hot wallets."""

from typing import Optional

from eth_account.datastructures import SignedTransaction
from eth_account.signers.local import LocalAccount
from web3 import Web3


class HotWallet:
    """Hot wallet.

    A hot wallet maintains an unecrypted private key of an Ethereum address in the process memory.
    It is able to sign transactions.

    This particular hot wallet implementation carries the information of allocated tx nonces with us.
    This allows us to prepare multiple transactions from the same account upfront.
    """

    def __init__(self, account: LocalAccount):
        self.account = account
        self.current_nonce: Optional[int] = None

    @property
    def address(self):
        return self.account.address

    def sync_nonce(self, web3: Web3):
        """Read the current nonce """
        self.current_nonce = web3.eth.get_transaction_count(self.account.address)

    def allocate_nonce(self) -> int:
        """Get the next free available nonce to be used with a transaction.

        Ethereum tx nonces are a counter.

        Increase the nonce counter
        """
        assert self.current_nonce is not None, "Nonce is not yet synced from the blockchain"
        nonce = self.current_nonce
        self.current_nonce += 1
        return nonce

    def sign_transaction_with_new_nonce(self, tx: dict) -> SignedTransaction:
        """Signs a transaction and allocates a nonce for it.

        :param: Ethereum transaction data as a dict. This is modified in-place to include nonce.
        """
        assert "nonce" not in tx
        tx["nonce"] = self.allocate_nonce()
        signed = self.account.sign_transaction(tx)
        return signed
