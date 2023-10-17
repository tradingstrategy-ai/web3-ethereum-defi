"""Enzyme protocol event reader.

- High level interface for Enzyme deposit and withdrawal events, with unit conversion
  and token data look up

- Read different events from Enzyme vaults that are necessary for managing the available
  trading capital

"""
import logging
import datetime
from decimal import Decimal
from dataclasses import dataclass
from functools import cached_property
from typing import Iterable, Tuple, List, Collection

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3

from eth_defi.enzyme.vault import Vault
from eth_defi.event_reader.conversion import convert_uint256_bytes_to_address, decode_data, convert_int256_bytes_to_int
from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.reader import Web3EventReader
from eth_defi.token import fetch_erc20_details, TokenDetails


logger = logging.getLogger(__name__)


@dataclass
class EnzymeBalanceEvent:
    """Enzyme deposit/redeem event wrapper.

    Wrap the underlying raw JSON-RPC eth_getLogs data to something more manageable.

    """

    #: Enzyme vault instance
    #:
    #:
    vault: Vault

    #: Underlying EVM JSON-RPC log data
    #:
    #:
    event_data: dict

    def __repr__(self):
        return f"{self.__class__.__name__}: {self.event_data}"

    @staticmethod
    def wrap(vault: Vault, event_data: dict) -> "EnzymeBalanceEvent":
        """Parse Solidity events to the wrapped format.

        :param event_data:
            Raw JSON-RPC event data.

            Example:

            .. code-block:: text

                {'address': '0xbeaafda2e17fc95e69dc06878039d274e0d2b21a',
                 'blockHash': '0x5eee3d7d2f32034955f2db9c2e84c8dfabb89a4001d32d4e01bdae540f5a0c06',
                 'blockNumber': 65,
                 'chunk_id': 62,
                 'context': None,
                 'data': '0x000000000000000000000000000000000000000000000000000000001dcd6500000000000000000000000000000000000000000000000000000000001dcd6500000000000000000000000000000000000000000000000000000000001dcd6500',
                 'event': <class 'web3._utils.datatypes.SharesBought'>,
                 'logIndex': '0x4',
                 'removed': False,
                 'timestamp': 1679394381,
                 'topics': ['0x849165c18b9d0fb161bcb145e4ab523d350e5c98f1dbbb1960331e7ee3ca6767',
                            '0x00000000000000000000000070997970c51812dc3a010c7d01b50e0d17dc79c8'],
                 'transactionHash': '0xb430a5546dd43042e3d36526fbd71ebc38c8598f6ee354f17839d3cdddf74530',
                 'transactionIndex': '0x0',
                 'transactionLogIndex': '0x4'}

        """
        event_name = event_data["event"].event_name

        # web3.cotract.Contact.Event expects binary data here
        # and we cannot pass raw JSON-RPC
        event_data["topics"] = [HexBytes(t) for t in event_data["topics"]]

        match event_name:
            case "SharesBought":
                return Deposit(vault, event_data)
            case "SharesRedeemed":
                return Redemption(vault, event_data)
            case _:
                raise RuntimeError(f"Unsupported event: {event_name}")

    @property
    def timestamp(self) -> datetime.datetime:
        """Return the block mined at timestamp."""
        return datetime.datetime.utcfromtimestamp(self.event_data["timestamp"])

    @property
    def web3(self) -> Web3:
        """Our web3 connection."""
        return self.vault.web3

    @cached_property
    def arguments(self) -> List[bytes]:
        """Access the non-indexed Solidity event arguments."""
        return decode_data(self.event_data["data"])

    @property
    def denomination_token(self) -> TokenDetails:
        """Get the denominator token for withdrawal/deposit.

        Read the token on-chain details.

        :return:
            Usually ERC-20 details for USDC

        """
        return self.vault.denomination_token

    @property
    def shares_token(self) -> TokenDetails:
        """Get the shares token for withdrawal/deposit.

        Read the token on-chain details.

        :return:
            ERC-20 details for a token with the fund name/symbol and 18 decimals.

        """
        return self.vault.shares_token


@dataclass
class Deposit(EnzymeBalanceEvent):
    """Enzyme deposit event wrapper.

    - Wraps `SharesBought` event

    - See `ComptrollerLib.sol`

    The solidity event:

    .. code-block:: text

        event SharesBought(
            address indexed buyer,
            uint256 investmentAmount,
            uint256 sharesIssued,
            uint256 sharesReceived
        );
    """

    @property
    def investment_amount(self) -> Decimal:
        """Amount of deposit/withdrawal in the denominator token."""
        token = self.denomination_token
        raw_amount = self.arguments[0]
        return token.convert_to_decimals(convert_int256_bytes_to_int(raw_amount))

    @property
    def shares_issued(self) -> Decimal:
        """Amount of deposit/withdrawal in the denominator token."""
        token = self.shares_token
        raw_amount = self.arguments[1]
        return token.convert_to_decimals(convert_int256_bytes_to_int(raw_amount))

    @cached_property
    def receiver(self) -> HexAddress:
        """Address of the user who received the bought shares."""
        return convert_uint256_bytes_to_address(HexBytes(self.event_data["topics"][1]))


class Redemption(EnzymeBalanceEvent):
    """Enzyme deposit event wrapper.

    Currently only supports `redeemSharesInKind` withdrawal method.
    This means we get the tokens of the undetlying positions directly to the investor wallet
    without sellign them.

    - Wraps `SharesRedeemed` event

    - See `ComptrollerLib.sol`

    - See `redeemSharesInKind()`

    The solidity event:

    .. code-block:: text

        event SharesRedeemed(
            address indexed redeemer,
            address indexed recipient,
            uint256 sharesAmount,
            address[] receivedAssets,
            uint256[] receivedAssetAmounts
        );
    """

    @property
    def redeem_amount(self) -> Decimal:
        """Amount of withdrawal in the number of shares."""
        token = self.shares_token
        raw_amount = self.arguments[0]
        return token.convert_to_decimals(convert_int256_bytes_to_int(raw_amount))

    @cached_property
    def redeemed_assets(self) -> List[Tuple[TokenDetails, int]]:
        """Get the list of assets in this withdrawal.

        :return:
            List of (redeemed token, raw token amount) tuples
        """
        web3 = self.web3

        # Decode using Web3.py to handle list decoding nicely
        # Slower, but we do not care

        SharesRedeemed = self.event_data["event"]
        processed = SharesRedeemed().process_log(self.event_data)
        addresses = processed["args"]["receivedAssets"]
        amounts = processed["args"]["receivedAssetAmounts"]
        details = [fetch_erc20_details(web3, address) for address in addresses]
        return list(zip(details, amounts))

    @property
    def receiver(self) -> HexAddress:
        """Address of the user who received the assets.

        Can be different from the redeemer.
        """
        return convert_uint256_bytes_to_address(HexBytes(self.event_data["topics"][2]))

    @property
    def redeemer(self) -> HexAddress:
        """Address of the user who did the redemption transaction.

        Can be different from the receiver.
        """
        return convert_uint256_bytes_to_address(HexBytes(self.event_data["topics"][1]))


@dataclass(frozen=True, slots=True)
class LiveBalance:
    """Current balance of a position in Enzyme vault.

    See :py:func:`fetch_vault_balances` for details.
    """

    #: Enzyme vault instance
    #:
    #:
    vault: Vault

    #: Which token is this event for
    #:
    #:
    token: TokenDetails

    #: Underlying raw token balance, converted to decimal
    #:
    balance: Decimal

    def __repr__(self):
        return f"<Token {self.token}, balance {self.balance}>"


def fetch_vault_balance_events(
    vault: Vault,
    start_block: int,
    end_block: int,
    read_events: Web3EventReader,
) -> Iterable[EnzymeBalanceEvent]:
    """Get the deposits to Enzyme vault in a specific time range.

    - Uses eth_getLogs ABI

    - Read both deposits and withdrawals in one go

    - Serial read

    - Slow over long block ranges

    - See `ComptrollerLib.sol`

    :param vault:
        Enzyme vault of which events to get

    :param start_block:
        Scan start range (inclusive)

    :param end_block:
        Scan end range (inclusive)

    :param read_events:
        The event reader interface used to iterate eth_getLogs
    """

    web3 = vault.web3

    filter = Filter.create_filter(
        vault.comptroller.address,
        [vault.comptroller.events.SharesBought, vault.comptroller.events.SharesRedeemed],
    )

    logger.info("Reading SharesBought/SharesRedeemed at %d-%d using reader %s", start_block, end_block, read_events)

    for solidity_event in read_events(
        web3,
        start_block,
        end_block,
        filter=filter,
    ):
        yield EnzymeBalanceEvent.wrap(vault, solidity_event)


def fetch_vault_balances(
    vault: Vault,
    block_identifier="latest",
) -> Iterable[LiveBalance]:
    """Get the live balances of the vault tokens at a specific block.

    Does EVM state based reading instead of event based reading.

    - Gets the total balances of positions held by vault

    - Does not get shares of individual investors

    .. warning::

        Enzyme returns positions with zero balance so you need to filter these out.

    Example:

    .. code-block:: python

        balance_map = {b.token.address: b for b in fetch_vault_balances(vault) if b.balance > 0}
        assert len(balance_map) == 2
        assert balance_map[usdc.address].balance == 1300
        assert balance_map[weth.address].balance == pytest.approx(Decimal("0.124500872629987902"))

    :param vault:
        Enzyme vault we are interested in

    :param block_identifier:
        Specific block to query

    :return:
        The balances at the current or specific block

    """
    web3 = vault.web3
    vault_contract = vault.vault
    token_addresses = vault_contract.functions.getTrackedAssets().call(block_identifier=block_identifier)
    for addr in token_addresses:
        token = fetch_erc20_details(web3, addr)
        balance = token.fetch_balance_of(vault.address, block_identifier=block_identifier)
        yield LiveBalance(vault, token, balance)
