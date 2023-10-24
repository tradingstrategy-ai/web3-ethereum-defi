"""Utilities for managing hot wallets.

- Create local wallets from a private key

- Sign transactions in batches

"""

import logging
import secrets
from decimal import Decimal
from typing import Optional, NamedTuple

from eth_account import Account
from eth_account.datastructures import __getitem__
from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.gas import estimate_gas_fees, apply_gas
from eth_defi.tx import decode_signed_transaction


logger = logging.getLogger(__name__)


class SignedTransactionWithNonce(NamedTuple):
    """A better signed transaction structure.

    Helper class to pass around the used nonce when signing txs from the wallet.

    - Compatible with :py:class:`eth_accounts.datastructures.SignedTransaction`. Emulates its behavior
      and should be backwards compatible.

    - Retains more information about the transaction source,
      to allow us to diagnose broadcasting failures better

    - Add some debugging helpers
    """

    #: See SignedTransaction
    rawTransaction: HexBytes

    #: See SignedTransaction
    hash: HexBytes

    #: See SignedTransaction
    r: int

    #: See SignedTransaction
    s: int

    #: See SignedTransaction
    v: int

    #: What was the source nonce for this transaction
    nonce: int

    #: Whas was the source address for this trasaction
    address: str

    #: Unencoded transaction data as a dict.
    #:
    #: If broadcast fails, retain the source so we can debug the cause,
    #: like the original gas parameters.
    #:
    source: Optional[dict] = None

    def __repr__(self):
        return f"<SignedTransactionWithNonce hash:{self.hash.hex()} nonce:{self.nonce} payload:{self.rawTransaction.hex()}>"

    @property
    def raw_transaction(self) -> HexBytes:
        """Get the bytes to be broadcasted to the P2P network.

        Legacy web3.py compatibility.
        """
        return self.rawTransaction

    def __getitem__(self, index):
        # Legacy web3.py compatibility.
        return __getitem__(self, index)


class HotWallet:
    """Hot wallet.

    A hot wallet maintains an unecrypted private key of an Ethereum address in the process memory.
    It is able to sign transactions.

    This particular hot wallet implementation carries the information of allocated tx nonces with us.
    This allows us to prepare multiple transactions from the same account upfront.

    `See also how to create private keys from command line <https://ethereum.stackexchange.com/q/82926/620>`_.

    .. note ::

        Not thread safe. This class manages consumed nonce counter locally.

    """

    def __init__(self, account: LocalAccount):
        self.account = account
        self.current_nonce: Optional[int] = None

    def __repr__(self):
        return f"<Hot wallet {self.account.address}>"

    @property
    def address(self):
        """Get address of the private key of the wallet."""
        return self.account.address

    def sync_nonce(self, web3: Web3):
        """Read the current nonce"""
        self.current_nonce = web3.eth.get_transaction_count(self.account.address)
        logger.info("Synced nonce for %s to %d", self.account.address, self.current_nonce)

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

        Example:

        .. code-block:: python

            web3 = Web3(mev_blocker_provider)
            wallet = HotWallet.create_for_testing(web3)

            # Send some ETH to zero address from
            # the hot wallet
            signed_tx = wallet.sign_transaction_with_new_nonce({
                "from": wallet.address,
                "to": ZERO_ADDRESS,
                "value": 1,
                "gas": 100_000,
                "gasPrice": web3.eth.gas_price,
            })
            tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)

        :param tx:
            Ethereum transaction data as a dict.
            This is modified in-place to include nonce.

        :return:
            A transaction payload and nonce with used to generate this transaction.
        """
        assert type(tx) == dict
        assert "nonce" not in tx
        tx["nonce"] = self.allocate_nonce()
        _signed = self.account.sign_transaction(tx)

        # Check that we can decode
        decode_signed_transaction(_signed.rawTransaction)

        signed = SignedTransactionWithNonce(
            rawTransaction=_signed.rawTransaction,
            hash=_signed.hash,
            v=_signed.v,
            r=_signed.r,
            s=_signed.s,
            nonce=tx["nonce"],
            source=tx,
            address=self.address,
        )
        return signed

    def sign_bound_call_with_new_nonce(self, func: ContractFunction, tx_params: dict | None = None) -> SignedTransactionWithNonce:
        """Signs a bound Web3 Contract call.

        Example:

        .. code-block:: python

            bound_func = busd_token.functions.transfer(user_2, 50*10**18)  # Transfer 50 BUDF
            signed_tx = hot_wallet.sign_bound_call_with_new_nonce(bound_func)
            web3.eth.send_raw_transaction(signed_tx.rawTransaction)

        See also

        - :py:meth:`sign_transaction_with_new_nonce`

        :param func:
            Web3 contract function that has its arguments bound

        :param tx_params:
            Transaction parameters like `gas`
        """
        assert isinstance(func, ContractFunction)
        if tx_params is None:
            tx_params = {}
        tx_params["from"] = self.address
        tx = func.build_transaction(tx_params)
        return self.sign_transaction_with_new_nonce(tx)

    def get_native_currency_balance(self, web3: Web3) -> Decimal:
        """Get the balance of the native currency (ETH, BNB, MATIC) of the wallet.

        Useful to check if you have enough cryptocurrency for the gas fees.
        """
        balance = web3.eth.get_balance(self.address)
        return web3.from_wei(balance, "ether")

    @staticmethod
    def fill_in_gas_price(web3: Web3, tx: dict) -> dict:
        """Fills in the gas value fields for a transaction.

        - Estimates raw transaction gas usage

        - Uses web3 methods to get the gas value fields for the dict

        - web3 offers different backends for this

        - likely queries the values from the node

        :return:
            Transaction data (mutated) with gas values filled in.
        """
        price_data = estimate_gas_fees(web3)
        apply_gas(tx, price_data)
        return tx

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

    @staticmethod
    def create_for_testing(web3: Web3, test_account_n=0, eth_amount=10) -> "HotWallet":
        """Creates a new hot wallet and seeds it with ETH from one of well-known test accounts.

        Shortcut method for unit testing.

        Example:

        .. code-block:: python

            web3 = Web3(test_provider)
            wallet = HotWallet.create_for_testing(web3)

            signed_tx = wallet.sign_transaction_with_new_nonce(
                {
                    "from": wallet.address,
                    "to": ZERO_ADDRESS,
                    "value": 1,
                    "gas": 100_000,
                    "gasPrice": web3.eth.gas_price,
                }
            )

            tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            assert_transaction_success_with_explanation(web3, tx_hash)

        """
        wallet = HotWallet.from_private_key("0x" + secrets.token_hex(32))
        tx_hash = web3.eth.send_transaction(
            {
                "from": web3.eth.accounts[test_account_n],
                "to": wallet.address,
                "value": eth_amount * 10**18,
            }
        )
        web3.eth.wait_for_transaction_receipt(tx_hash)
        wallet.sync_nonce(web3)
        return wallet
