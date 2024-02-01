"""How wallet management utilities.

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
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3._utils.contracts import prepare_transaction
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

    def __eq__(self, other):
        assert isinstance(other, SignedTransactionWithNonce)
        return self.hash == other.hash

    def __hash__(self) -> int:
        # Python hash must be int
        return hash(self.hash)

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
    """Hot wallet for signing transactions effectively.

    - A hot wallet maintains an plain text private key of an Ethereum address in the process memory
      using :py:class:`eth_account.signers.local.LocalAccount` and nonce counter.

    - It is able to sign transactions, including batches, using manual nonce management.
      See :py:meth:`sync_nonce`, :py:meth:`allocate_nonce` and :py:meth:`sign_transaction_with_new_nonce`.

    - Signed transactions carry extra debug information with them in :py:class:`SignedTransactionWithNonce`

    To use this class with the existing web3.py `Contract.functions.myFunc().transact()`
    you can add the private key as the local signing middleware. However you
    should try to use  :py:meth:`sign_bound_call_with_new_nonce` instead when possible.
    See also :py:func:`eth_defi.middleware.construct_sign_and_send_raw_middleware_anvil`
    when working with Anvil.

    Example:

    .. code-block:: python

            from eth_account import Account
            from web3.middleware import construct_sign_and_send_raw_middleware

            from eth_defi.trace import assert_transaction_success_with_explanation
            from eth_defi.hotwallet import HotWallet

            account = Account.create()

            # Move 1/2 of ETH from the first test account to ours
            test_account_1 = web3.eth.accounts[0]
            stash = web3.eth.get_balance(test_account_1)
            tx_hash = web3.eth.send_transaction({"from": test_account_1, "to": account.address, "value": stash // 2})
            assert_transaction_success_with_explanation(web3, tx_hash)

            # Attach local private key to the web3.py middleware machinery
            web3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))

            # Create a hot wallet instance
            hot_wallet = HotWallet(account)
            hot_wallet.sync_nonce(web3)

            # Use web3.py signing (NOTE: does not correctly increment nonce)
            # so you need to call hot_wallet.sync_nonce() after the tx has been confirmed
            tx_hash = usdc.functions.transfer(
                some_address,
                500 * 10**6,
            ).transact({"from": hot_wallet.address})
            assert_transaction_success_with_explanation(web3, tx_hash)
            hot_wallet.sync_nonce(web3)  # Sync nonce again, as the manual management is off

    .. note ::

        This class is not thread safe. If multiple threads try to sign transactions
        at the same time, nonce tracking may be lost.

    `See also how to create private keys from command line <https://ethereum.stackexchange.com/q/82926/620>`_.
    """

    def __init__(self, account: LocalAccount):
        """Create a hot wallet from a local account."""
        self.account = account
        self.current_nonce: Optional[int] = None

    def __repr__(self):
        return f"<Hot wallet {self.account.address}>"

    @property
    def address(self) -> HexAddress:
        """Ethereum address of the wallet."""
        return self.account.address

    @property
    def private_key(self) -> HexBytes:
        """The private key as plain text."""
        return self.account._private_key

    def sync_nonce(self, web3: Web3):
        """Initialise the current nonce from the on-chain data."""
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

    def sign_bound_call_with_new_nonce(
        self,
        func: ContractFunction,
        tx_params: dict | None = None,
    ) -> SignedTransactionWithNonce:
        """Signs a bound Web3 Contract call.

        Example:

        .. code-block:: python

            bound_func = busd_token.functions.transfer(user_2, 50*10**18)  # Transfer 50 BUDF
            signed_tx = hot_wallet.sign_bound_call_with_new_nonce(bound_func)
            web3.eth.send_raw_transaction(signed_tx.rawTransaction)

        With manual gas estimation:

        .. code-block:: python

            approve_call = usdc.contract.functions.approve(quickswap.router.address, raw_amount)
            gas_estimation = estimate_gas_fees(web3)
            tx_gas_parameters = apply_gas({"gas": 100_000}, gas_estimation)  # approve should not take more than 100k gas
            signed_tx = hot_wallet.sign_bound_call_with_new_nonce(approve_call, tx_gas_parameters)

        See also

        - :py:meth:`sign_transaction_with_new_nonce`

        :param func:
            Web3 contract function that has its arguments bound

        :param tx_params:
            Transaction parameters like `gas`
        """
        assert isinstance(func, ContractFunction)

        original_tx_params = tx_params

        if tx_params is None:
            tx_params = {}

        tx_params["from"] = self.address

        if "chainId" not in tx_params:
            tx_params["chainId"] = func.w3.eth.chain_id

        if original_tx_params is None:
            # Use the default gas filler
            tx = func.build_transaction(tx_params)
        else:
            # Use given gas parameters
            tx = prepare_transaction(
                func.address,
                func.w3,
                fn_identifier=func.function_identifier,
                contract_abi=func.contract_abi,
                fn_abi=func.abi,
                transaction=tx_params,
                fn_args=func.args,
                fn_kwargs=func.kwargs,
            )

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
