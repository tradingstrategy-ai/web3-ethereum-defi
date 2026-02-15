"""Circle CCTP V2 cross-chain USDC transfers.

Initiate cross-chain USDC transfers using Circle's CCTP V2 protocol.

Example of preparing a cross-chain transfer from Ethereum to Arbitrum::

    from web3 import Web3
    from eth_defi.cctp.transfer import prepare_deposit_for_burn, prepare_approve_for_burn

    web3 = Web3(Web3.HTTPProvider("https://..."))

    # First approve USDC spending
    approve_fn = prepare_approve_for_burn(web3, amount=1_000_000)  # 1 USDC
    approve_fn.transact({"from": sender})

    # Then initiate the cross-chain transfer
    burn_fn = prepare_deposit_for_burn(
        web3,
        amount=1_000_000,
        destination_chain_id=42161,  # Arbitrum
        mint_recipient="0x...",  # Recipient on Arbitrum
    )
    tx_hash = burn_fn.transact({"from": sender})

The ``burnToken`` is always the native USDC on the source chain.
The destination chain's ``TokenMinterV2`` automatically resolves
the local USDC address via its ``remoteTokensToLocalTokens`` mapping.
"""

import logging

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract
from eth_defi.cctp.constants import (
    CHAIN_ID_TO_CCTP_DOMAIN,
    FINALITY_THRESHOLD_STANDARD,
    MESSAGE_TRANSMITTER_V2,
    TOKEN_MESSENGER_V2,
)
from eth_defi.token import USDC_NATIVE_TOKEN

logger = logging.getLogger(__name__)


def get_token_messenger_v2(web3: Web3) -> Contract:
    """Load the TokenMessengerV2 contract at its known address.

    :param web3:
        Web3 connection

    :return:
        Contract proxy for TokenMessengerV2
    """
    return get_deployed_contract(
        web3,
        "cctp/TokenMessengerV2.json",
        TOKEN_MESSENGER_V2,
    )


def get_message_transmitter_v2(web3: Web3) -> Contract:
    """Load the MessageTransmitterV2 contract at its known address.

    :param web3:
        Web3 connection

    :return:
        Contract proxy for MessageTransmitterV2
    """
    return get_deployed_contract(
        web3,
        "cctp/MessageTransmitterV2.json",
        MESSAGE_TRANSMITTER_V2,
    )


def encode_mint_recipient(address: HexAddress | str) -> bytes:
    """Convert an Ethereum address to bytes32 format for the ``mintRecipient`` parameter.

    CCTP uses bytes32 for recipient addresses to support non-EVM chains.
    For EVM chains, the address is left-padded with zeros to 32 bytes.

    :param address:
        Ethereum address (0x-prefixed hex string)

    :return:
        32-byte representation of the address
    """
    address = Web3.to_checksum_address(address)
    # Remove 0x prefix, left-pad to 64 hex chars (32 bytes)
    return bytes.fromhex(address[2:].lower().zfill(64))


def prepare_deposit_for_burn(
    web3: Web3,
    amount: int,
    destination_chain_id: int,
    mint_recipient: HexAddress | str,
    burn_token: HexAddress | str | None = None,
    destination_caller: bytes | None = None,
    max_fee: int = 0,
    min_finality_threshold: int = FINALITY_THRESHOLD_STANDARD,
) -> ContractFunction:
    """Build a bound ``depositForBurn()`` call on TokenMessengerV2.

    This burns USDC on the source chain to be minted on the destination chain.
    USDC must be approved to TokenMessengerV2 before calling this.

    :param web3:
        Web3 connection to the source chain

    :param amount:
        Amount of USDC to transfer in raw token units (6 decimals).
        E.g. 1_000_000 for 1 USDC.

    :param destination_chain_id:
        EVM chain ID of the destination (e.g. 42161 for Arbitrum).
        Automatically converted to CCTP domain ID.

    :param mint_recipient:
        Address to receive USDC on the destination chain.

    :param burn_token:
        USDC address on the source chain. If ``None``, auto-detected
        from the source chain ID.

    :param destination_caller:
        If set, restricts who can call ``receiveMessage()`` on
        the destination chain. ``None`` means anyone can relay (bytes32 zero).

    :param max_fee:
        Maximum fee for fast finality transfers. 0 for standard transfers.

    :param min_finality_threshold:
        Finality level: 2000 for standard (finalized), 1000 for fast (confirmed).

    :return:
        Bound contract function ready to be transacted or encoded.

    :raises ValueError:
        If the destination chain or source chain is not supported by CCTP.
    """
    chain_id = web3.eth.chain_id

    # Resolve CCTP domain
    destination_domain = CHAIN_ID_TO_CCTP_DOMAIN.get(destination_chain_id)
    if destination_domain is None:
        raise ValueError(f"Destination chain {destination_chain_id} is not supported by CCTP. Supported chains: {list(CHAIN_ID_TO_CCTP_DOMAIN.keys())}")

    # Auto-detect USDC address on source chain
    if burn_token is None:
        burn_token = USDC_NATIVE_TOKEN.get(chain_id)
        if burn_token is None:
            raise ValueError(f"No USDC address known for source chain {chain_id}. Pass burn_token explicitly.")

    burn_token = Web3.to_checksum_address(burn_token)

    # Encode mint recipient as bytes32
    mint_recipient_bytes32 = encode_mint_recipient(mint_recipient)

    # Default destination_caller to bytes32(0) = any relayer can call receiveMessage
    if destination_caller is None:
        destination_caller = b"\x00" * 32

    token_messenger = get_token_messenger_v2(web3)

    logger.info(
        "Preparing CCTP depositForBurn: amount=%s, destination_domain=%s, recipient=%s",
        amount,
        destination_domain,
        mint_recipient,
    )

    return token_messenger.functions.depositForBurn(
        amount,
        destination_domain,
        mint_recipient_bytes32,
        burn_token,
        destination_caller,
        max_fee,
        min_finality_threshold,
    )


def prepare_approve_for_burn(
    web3: Web3,
    amount: int,
    burn_token: HexAddress | str | None = None,
) -> ContractFunction:
    """Build a USDC ``approve()`` call to TokenMessengerV2.

    Must be called before :func:`prepare_deposit_for_burn`.

    :param web3:
        Web3 connection to the source chain

    :param amount:
        Amount of USDC to approve in raw token units (6 decimals)

    :param burn_token:
        USDC address on the source chain. If ``None``, auto-detected.

    :return:
        Bound contract function for USDC.approve(TokenMessengerV2, amount)
    """
    chain_id = web3.eth.chain_id

    if burn_token is None:
        burn_token = USDC_NATIVE_TOKEN.get(chain_id)
        if burn_token is None:
            raise ValueError(f"No USDC address known for chain {chain_id}. Pass burn_token explicitly.")

    burn_token = Web3.to_checksum_address(burn_token)

    usdc = get_deployed_contract(web3, "ERC20MockDecimals.json", burn_token)

    return usdc.functions.approve(
        Web3.to_checksum_address(TOKEN_MESSENGER_V2),
        amount,
    )
