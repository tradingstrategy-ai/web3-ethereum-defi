"""Circle CCTP V2 message receiving.

Complete cross-chain USDC transfers by relaying attestation to
the destination chain's MessageTransmitterV2.

After obtaining the attestation from :mod:`eth_defi.cctp.attestation`,
call ``receiveMessage()`` on the destination chain to mint USDC.

Example::

    from eth_defi.cctp.receive import prepare_receive_message

    receive_fn = prepare_receive_message(
        web3_destination,
        message=attestation.message,
        attestation=attestation.attestation,
    )
    tx_hash = receive_fn.transact({"from": relayer})
"""

import logging

from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.cctp.constants import CCTP_MESSAGE_VERSION
from eth_defi.cctp.transfer import get_message_transmitter_v2

logger = logging.getLogger(__name__)

#: Byte length of the fixed CCTP V2 message header.
MESSAGE_HEADER_LENGTH = 148


def fetch_message_received(web3: Web3, message: bytes) -> bool:
    """Check whether the destination has consumed this exact CCTP message.

    The V2 header stores its 32-byte nonce after three uint32 fields. Checking
    the transmitter avoids confusing an unrelated token receipt with a relay.
    See https://developers.circle.com/cctp/technical-guide#message-format.

    :param web3: Destination chain connection.
    :param message: Original complete Circle-attested V2 message.
    :return: Whether the message nonce has already been used on this chain.
    """
    assert len(message) >= MESSAGE_HEADER_LENGTH and int.from_bytes(message[:4], "big") == CCTP_MESSAGE_VERSION
    return bool(get_message_transmitter_v2(web3).functions.usedNonces(message[12:44]).call())


def prepare_receive_message(
    web3: Web3,
    message: bytes,
    attestation: bytes,
) -> ContractFunction:
    """Build a bound ``receiveMessage()`` call on MessageTransmitterV2.

    This relays the attestation to the destination chain, causing
    USDC to be minted to the recipient specified in the original
    ``depositForBurn()`` call.

    Anyone can call this function (unless ``destinationCaller`` was
    set in the original burn). No special permissions are required.

    :param web3:
        Web3 connection to the **destination** chain

    :param message:
        The CCTP message bytes from the attestation service

    :param attestation:
        The signed attestation bytes from the attestation service

    :return:
        Bound contract function ready to be transacted
    """
    message_transmitter = get_message_transmitter_v2(web3)

    logger.info(
        "Preparing CCTP receiveMessage: message_len=%d, attestation_len=%d",
        len(message),
        len(attestation),
    )

    return message_transmitter.functions.receiveMessage(
        message,
        attestation,
    )
