"""Abstraction over different deposit/redeem flows of vaults."""

import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
import logging
from pprint import pformat

from web3 import Web3
from web3.contract.contract import ContractFunction

from hexbytes import HexBytes
from eth_typing import HexAddress, BlockIdentifier, BlockNumber

from eth_defi.timestamp import get_block_timestamp
from eth_defi.trace import assert_transaction_success_with_explanation


logger = logging.getLogger(__name__)


class VaultTransactionFailed(Exception):
    """One of vault deposit/redeem transactions reverted"""


@dataclass(slots=True)
class DepositRedeemEventFailure:
    tx_hash: HexBytes
    revert_reason: str | None


@dataclass(slots=True)
class DepositRedeemEventAnalysis:
    """Analyse a vault deposit/redeem event.

    - Done for the transaction where we get our assets into our wallet,
      so we can determine the actualy executed price of shares we received/sold
    """

    from_: HexAddress
    to: HexAddress
    denomination_amount: Decimal
    share_count: Decimal
    tx_hash: HexBytes
    block_number: BlockNumber
    block_timestamp: datetime.datetime

    def __post_init__(self):
        assert self.denomination_amount > 0
        assert self.share_count > 0

    def is_success(self):
        return self.revert_reason is None

    def get_share_price(self) -> Decimal:
        return self.denomination_amount / self.share_count


@dataclass(slots=True)
class DepositTicket:
    """In-progress deposit request.

    - `Needed for ERC-7540 <https://tradingstrategy.ai/glossary/erc-7540>`__
    """

    vault_address: HexAddress
    owner: HexAddress
    to: HexAddress
    raw_amount: int

    #: Last of transaction hashes
    tx_hash: HexBytes
    gas_used: int

    #: Last tx block number
    block_number: int

    #: Last tx block timestamp
    block_timestamp: datetime.datetime

    def __post_init__(self):
        assert self.owner.startswith("0x"), f"Got {self.owner}"
        assert self.to.startswith("0x"), f"Got {self.to}"
        assert type(self.raw_amount) == int, f"Got {type(self.raw_amount)}: {self.raw_amount}"
        assert isinstance(self.tx_hash, HexBytes), f"Got {type(self.tx_hash)}: {self.tx_hash}"


@dataclass(slots=True)
class RedemptionTicket:
    """In-progress redemption request.

    - Needs to wait until the epoch time is over or owner has settled
    - Serialisable class
    """

    vault_address: HexAddress
    owner: HexAddress
    to: HexAddress
    raw_shares: int
    tx_hash: HexBytes

    def __post_init__(self):
        assert self.owner.startswith("0x"), f"Got {self.owner}"
        assert self.to.startswith("0x"), f"Got {self.to}"
        assert type(self.raw_shares) == int, f"Got {type(self.raw_shares)}: {self.raw_shares}"
        assert isinstance(self.tx_hash, HexBytes), f"Got {type(self.tx_hash)}: {self.tx_hash}"

    @abstractmethod
    def get_request_id(self) -> int:
        """Get the redemption request id.

        - If vault uses some sort of request ids to track the withdrawals
        - Needed for settlement
        """
        raise NotImplementedError()


class CannotParseRedemptionTransaction(Exception):
    """We did no know how our redemption transaction went."""


@dataclass(slots=True)
class RedemptionRequest:
    """Wrap the different redeem functions async vaults implement."""

    #: Vault we are dealing with
    vault: "VaultBase"

    #: Owner of the shares
    owner: HexAddress

    #: Receiver of underlying asset
    to: HexAddress

    #: Human-readable shares
    shares: Decimal

    #: Raw amount of shares
    raw_shares: int

    #: Transactions we need to perform in order to open a redemption
    #:
    #: It's a list because for Gains we need 2 tx
    funcs: list[ContractFunction]

    def __post_init__(self):
        from eth_defi.vault.base import VaultBase

        assert isinstance(self.vault, VaultBase), f"Got {type(self.vault)}"
        assert self.owner.startswith("0x"), f"Got {self.owner}"
        assert self.to.startswith("0x"), f"Got {self.to}"
        assert type(self.raw_shares) == int, f"Got {type(self.raw_shares)}"
        assert self.raw_shares > 0

    @property
    def web3(self) -> Web3:
        return self.vault.web3

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> RedemptionTicket:
        """Parse the transaction receipt to get the actual shares redeemed.

        - Assumes only one redemption request per vault per transaction

        :raise CannotParseRedemptionTransaction:
            If we did not know how to parse the transaction
        """
        return RedemptionTicket(
            vault_address=self.vault.address,
            owner=self.owner,
            to=self.to,
            raw_shares=self.raw_shares,
            tx_hash=tx_hashes[-1],
        )

    def broadcast(self, from_: HexAddress = None, gas: int = 1_000_000) -> list[HexBytes]:
        """Broadcast all the transactions in this request.

        :param from_:
            Address to send the transactions from

        :param gas:
            Gas limit to use for each transaction

        :return:
            List of transaction hashes
        """

        if from_ is None:
            from_ = self.owner

        tx_hashes = []
        for func in self.funcs:
            tx_hash = func.transact({"from": from_, "gas": gas})
            assert_transaction_success_with_explanation(self.web3, tx_hash)
            tx_hashes.append(tx_hash)
        return self.parse_redeem_transaction(tx_hashes)


@dataclass(slots=True)
class DepositRequest:
    """Wrap the different deposit functions async vaults implement."""

    #: Vault we are dealing with
    vault: "VaultBase"

    #: Owner of the shares
    owner: HexAddress

    #: Receiver of underlying asset
    to: HexAddress

    #: Human-readable shares
    amount: Decimal

    #: Raw amount of shares
    raw_amount: int

    #: Transactions we need to perform in order to open a redemption
    #:
    #: It's a list because for Gains we need 2 tx
    funcs: list[ContractFunction]

    #: Set transaction gas limit
    gas: int | None = None

    #: Attached ETH value to the tx
    value: Decimal | None = None

    def __post_init__(self):
        from eth_defi.vault.base import VaultBase

        assert isinstance(self.vault, VaultBase), f"Got {type(self.vault)}"
        assert self.owner.startswith("0x"), f"Got {self.owner}"
        assert self.to.startswith("0x"), f"Got {self.to}"
        assert self.raw_amount > 0
        assert type(self.raw_amount) == int, f"Got {type(self.raw_amount)}"

    @property
    def web3(self) -> Web3:
        return self.vault.web3

    def parse_deposit_transaction(
        self,
        tx_hashes: list[HexBytes],
    ) -> DepositTicket:
        """Parse the transaction receipt to get the actual shares redeemed.

        - Assumes only one redemption request per vault per transaction

        - Most throw an

        :raise CannotParseRedemptionTransaction:
            If we did not know how to parse the transaction

        :raise VaultTransactionFailed:
            One of transactions reverted
        """

        gas_used = 0

        for tx_hash in tx_hashes:
            tx = self.web3.eth.get_transaction(tx_hash)
            receipt = self.web3.eth.get_transaction_receipt(tx_hash)
            assert receipt is not None, f"Transaction was not yet mined: {tx_hash}"
            if receipt["status"] != 1:
                raise VaultTransactionFailed(f"Vault {self.vault} tranasaction {tx_hash} failed {receipt}")
            gas_used += receipt["gasUsed"]
            block_number = tx["blockNumber"]

        block_timestamp = get_block_timestamp(self.web3, block_number)

        return DepositTicket(vault_address=self.vault.address, owner=self.owner, to=self.to, raw_amount=self.raw_amount, tx_hash=tx_hash, gas_used=gas_used, block_timestamp=block_timestamp, block_number=block_number)

    def broadcast(self, from_: HexAddress = None, gas: int | None = None, check_value=True) -> RedemptionTicket:
        """Broadcast all the transactions in this request.

        :param from_:
            Address to send the transactions from

        :param gas:
            Gas limit to use for each transaction

        :return:
            List of transaction hashes

        :raise TransactionAssertionError:
            If any of the transactions revert
        """

        if from_ is None:
            from_ = self.owner

        if gas is None:
            if self.gas:
                gas = self.gas
            else:
                # Default to 1M
                gas = 1_000_000

        tx_data = {"from": from_, "gas": gas}
        if self.value:
            tx_data["value"] = Web3.to_wei(self.value, "ether")

            # If we ask for value, make sure our account is topped up
            if check_value:
                balance = self.web3.eth.get_balance(from_)
                assert balance >= tx_data["value"], f"Not enough ETH balance in {from_} to cover value {self.value} ETH, has {Web3.from_wei(balance, 'ether')} ETH"

        logger.info(
            "Broadcasting deposit request to vault %s from %s with gas %s and tx params:\n%s",
            self.vault.address,
            from_,
            gas,
            pformat(tx_data),
        )

        tx_hashes = []
        for func in self.funcs:
            tx_hash = func.transact(tx_data)

            assert_transaction_success_with_explanation(self.web3, tx_hash)
            tx_hashes.append(tx_hash)

        return self.parse_deposit_transaction(tx_hashes)


class VaultDepositManager(ABC):
    """Abstraction over different deposit/redeem flows of vaults."""

    def __init__(
        self,
        vault: "eth_defi.vault.base.VaultBase",
    ):
        self.vault = vault

    @property
    def web3(self) -> Web3:
        return self.vault.web3

    @abstractmethod
    def has_synchronous_deposit(self) -> bool:
        """Does this vault support synchronous deposits?

        - E.g. ERC-4626 vaults
        """

    @abstractmethod
    def has_synchronous_redemption(self) -> bool:
        """Does this vault support synchronous deposits?

        - E.g. ERC-4626 vaults
        """

    @abstractmethod
    def estimate_deposit(self, owner: HexAddress | None, amount: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """How many shares we get for a deposit."""

    @abstractmethod
    def estimate_redeem(self, owner: HexAddress | None, shares: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """How many denomination tokens we get for a redeem."""

    @abstractmethod
    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        amount: Decimal = None,
        raw_amount: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> DepositRequest:
        pass

    @abstractmethod
    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress,
        shares: Decimal = None,
        raw_shares: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> RedemptionRequest:
        """Create a redemption request.

        Abstracts IPOR, Lagoon, Gains, other vault redemption flow.

        See :py:class:`eth_defi.gains.vault.GainsVault` for an example usage.

        Flow

        1. create_redemption_request
        2. sign and broadcast the transaction
        3. parse success and redemption request id from the transaction
        4. wait until the redemption delay is over
        5. settle the redemption request

        :param owner:
            Deposit owner.

        :param shares:
            Share amount in decimal.

            Will be converted to `raw_shares` using `share_token` decimals.

        :param raw_shares:
            Raw amount in share token

        :return:
            Redemption request wrapper.
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement create_redemption_request()")

    @abstractmethod
    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        """Check if the owner has an active redemption request.

        :param owner:
            Owner of the shares

        :return:
            True if there is an active redemption request
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement is_redemption_in_proges()")

    @abstractmethod
    def is_deposit_in_progress(self, owner: HexAddress) -> bool:
        """Check if the owner has an active deposit request.

        :param owner:
            Owner of the shares

        :return:
            True if there is an active redemption request
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement is_redemption_in_proges()")

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Can we start depositing now.

        Vault can be full?
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement can_create_deposit_request()")

    def get_max_deposit(self, owner: HexAddress) -> Decimal | None:
        """How much we can deposit"""
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement can_create_redemption_request()")

    @abstractmethod
    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Gains allows request redepetion only two first days of three days epoch.

        :return:
            True if can create a redemption request now
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement can_create_redemption_request()")

    @abstractmethod
    def can_finish_redeem(
        self,
        redemption_ticket: RedemptionTicket,
    ) -> bool:
        """Check if the redemption request can be redeemed now.

        - Phase 2 of redemption, after settlement

        :param redemption_ticket:
            Redemption redemption_ticket ticket from `create_redemption_request()`

        :return:
            True if can be redeemed now
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement can_redeem()")

    @abstractmethod
    def can_finish_deposit(
        self,
        deposit_ticket: DepositTicket,
    ) -> bool:
        """Can we finish the deposit process in async reposits"""
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement can_deposit()")

    @abstractmethod
    def finish_deposit(
        self,
        deposit_ticket: DepositTicket,
    ) -> ContractFunction:
        """Can we finish the deposit process in async vault.

        - We can claim our shares from the vault now
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement can_deposit()")

    @abstractmethod
    def finish_redemption(
        self,
        redemption_ticket: RedemptionTicket,
    ) -> ContractFunction:
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement settle_redemption()")

    @abstractmethod
    def estimate_redemption_delay(self) -> datetime.timedelta:
        """Get the redemption delay for this vault.

        - What is overall redemption delay: not related to the current moment

        - How long it takes before a redemption request is allowed

        - This is not specific for any address, but the general vault rule

        - E.g. you get  0xa592703b is an IPOR Fusion error code AccountIsLocked,
          if you `try to instantly redeem from IPOR vaults <https://ethereum.stackexchange.com/questions/170119/is-there-a-way-to-map-binary-solidity-custom-errors-to-their-symbolic-sources>`__

        :return:
            Redemption delay as a :py:class:`datetime.timedelta`

        :raises NotImplementedError:
            If not implemented for this vault protocoll.
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement get_redemption_delay()")

    @abstractmethod
    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime:
        """Get the redemption timer left for an address.

        - How long it takes before a redemption request is allowed

        - This is not specific for any address, but the general vault rule

        - E.g. you get  0xa592703b is an IPOR Fusion error code AccountIsLocked,
          if you `try to instantly redeem from IPOR vaults <https://ethereum.stackexchange.com/questions/170119/is-there-a-way-to-map-binary-solidity-custom-errors-to-their-symbolic-sources>`__

        :return:
            UTC timestamp when the account can redeem.

            Naive datetime.

        :raises NotImplementedError:
            If not implemented for this vault protocoll.
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement get_redemption_delay_over()")

    @abstractmethod
    def analyse_deposit(
        self,
        claim_tx_hash: HexBytes | str,
        deposit_ticket: DepositTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse the transaction where we claim shares

        - Return information of the actual executed price for which we got the shares for
        """

    @abstractmethod
    def analyse_redemption(
        self,
        claim_tx_hash: HexBytes | str,
        redemption_ticket: RedemptionTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse the transaction where we claim our capital back.

        - Return information of the actual executed price for which we got the shares for
        """
