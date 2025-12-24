from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional

from eth_typing import HexAddress
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.hotwallet import SignedTransactionWithNonce


class BaseWallet(ABC):
    """Abstract base class for Ethereum wallets.

    This interface defines the common contract that both HotWallet and HSM-based
    wallets must implement.
    """

    @property
    @abstractmethod
    def address(self) -> HexAddress:
        """Get the wallet's Ethereum address."""
        pass

    @abstractmethod
    def get_main_address(self) -> HexAddress:
        """Get the main Ethereum address for this wallet."""
        pass

    @abstractmethod
    def sync_nonce(self, web3: Web3) -> None:
        """Synchronize the nonce with the blockchain."""
        pass

    @abstractmethod
    def allocate_nonce(self) -> int:
        """Get the next available nonce."""
        pass

    @abstractmethod
    def sign_transaction_with_new_nonce(self, tx: dict) -> SignedTransactionWithNonce:
        """Sign a transaction with a new nonce."""
        pass

    @abstractmethod
    def sign_bound_call_with_new_nonce(self, func: ContractFunction, tx_params: Optional[dict] = None) -> SignedTransactionWithNonce:
        """Sign a contract function call with a new nonce."""
        pass

    @abstractmethod
    def get_native_currency_balance(self, web3: Web3) -> Decimal:
        """Get the wallet's native currency balance."""
        pass

    @staticmethod
    @abstractmethod
    def fill_in_gas_price(web3: Web3, tx: dict) -> dict:
        """Fill in gas price details for a transaction."""
        pass
