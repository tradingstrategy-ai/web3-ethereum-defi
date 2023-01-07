"""Utilities for managing hot wallets."""

import logging
from decimal import Decimal
from typing import Optional, NamedTuple

from eth_account import Account
from eth_account.datastructures import __getitem__
from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from web3 import Web3

from eth_defi.tx import decode_signed_transaction

logger = logging.getLogger(__name__)


class SignedTransactionWithNonce(NamedTuple):
    """Helper class to pass around the used nonce when signing txs from the wallet."""

    rawTransaction: HexBytes
    hash: HexBytes
    r: int
    s: int
    v: int
    nonce: int

    def __getitem__(self, index):
        return __getitem__(self, index)


class HotWallet:
    """Hot wallet.

    A hot wallet maintains an unecrypted private key of an Ethereum address in the process memory.
    It is able to sign transactions.

    This particular hot wallet implementation carries the information of allocated tx nonces with us.
    This allows us to prepare multiple transactions from the same account upfront.

    `See also how to create private keys from command line <https://ethereum.stackexchange.com/q/82926/620>`_.

    .. note ::

        Not thread safe. Manages consumed nonce counter locally.

    """

    def __init__(self, account: LocalAccount):
        self.account = account
        self.current_nonce: Optional[int] = None

    @property
    def address(self):
        """Get address of the private key of the wallet."""
        return self.account.address

    def sync_nonce(self, web3: Web3):
        """Read the current nonce"""
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

    def sign_transaction_with_new_nonce(self, tx: dict) -> SignedTransactionWithNonce:
        """Signs a transaction and allocates a nonce for it.

        :param: Ethereum transaction data as a dict. This is modified in-place to include nonce.
        """
        assert "nonce" not in tx
        tx["nonce"] = self.allocate_nonce()
        _signed = self.account.sign_transaction(tx)
        decode_signed_transaction(_signed.rawTransaction)
        signed = SignedTransactionWithNonce(
            rawTransaction=_signed.rawTransaction,
            hash=_signed.hash,
            v=_signed.v,
            r=_signed.r,
            s=_signed.s,
            nonce=tx["nonce"],
        )
        return signed

    def get_native_currency_balance(self, web3: Web3) -> Decimal:
        """Get the balance of the native currency (ETH, BNB, MATIC) of the wallet.

        Useful to check if you have enough cryptocurrency for the gas fees.
        """
        balance = web3.eth.get_balance(self.address)
        return web3.fromWei(balance, "ether")

    @staticmethod
    def from_private_key(key: str) -> "HotWallet":
        """Create a hot wallet from a private key that is passed in as a hex string.

        Add the key to web3 signing chain.

        Example:

        .. code-block::

            # Generated with  openssl rand -hex 32
            wallet = HotWallet.from_private_key("0x54c137e27d2930f7b3433249c5f07b37ddcfea70871c0a4ef9e0f65655faf957")

        :param key: 0x prefixed hex string
        :return: Ready to go hot wallet account
        """
        assert key.startswith("0x")
        account = Account.from_key(key)
        return HotWallet(account)
