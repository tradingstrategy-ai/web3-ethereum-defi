"""CCTP V2 test helpers for Anvil forked chains.

Utilities for testing CCTP ``receiveMessage()`` on Anvil forks where
Circle's Iris attestation service is unavailable.

The approach:

1. Replace the real CCTP attester with a test account we control
2. Craft a valid CCTP message (header + burn message body)
3. Sign it with the test attester to forge an attestation
4. Call ``receiveMessage()`` — the MessageTransmitter accepts our
   attestation and triggers the real mint flow

Example::

    from eth_defi.cctp.testing import replace_attester_on_fork, craft_cctp_message, forge_attestation
    from eth_defi.cctp.receive import prepare_receive_message

    test_attester = replace_attester_on_fork(web3)
    message = craft_cctp_message(
        source_domain=0,  # Ethereum
        destination_domain=6,  # Base
        nonce=1,
        mint_recipient=safe_address,
        amount=100 * 10**6,  # 100 USDC
        burn_token=USDC_ETHEREUM,
    )
    attestation = forge_attestation(message, test_attester)
    receive_fn = prepare_receive_message(web3, message, attestation)
    tx_hash = receive_fn.transact({"from": relayer})

See `Circle's CCTP specification <https://github.com/circlefin/evm-cctp-contracts>`__
for the message format.
"""

import struct
import logging

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.cctp.constants import FINALITY_THRESHOLD_STANDARD, TOKEN_MESSENGER_V2
from eth_defi.cctp.transfer import encode_mint_recipient, get_message_transmitter_v2
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)

#: CCTP message version for V2 protocol
CCTP_MESSAGE_VERSION = 1

#: Burn message body version
BURN_MESSAGE_VERSION = 1


def replace_attester_on_fork(web3: Web3) -> LocalAccount:
    """Replace the CCTP attester with a test account on an Anvil fork.

    Impersonates the ``attesterManager`` on the forked ``MessageTransmitterV2``
    contract and adds a new test attester that we control. This allows
    forging attestations for ``receiveMessage()`` calls.

    :param web3:
        Web3 connected to an Anvil fork

    :return:
        The test attester account (use with :func:`forge_attestation`)
    """
    message_transmitter = get_message_transmitter_v2(web3)

    # Read the attester manager address
    attester_manager = message_transmitter.functions.attesterManager().call()
    logger.info("CCTP attester manager: %s", attester_manager)

    # Read current attester configuration
    num_attesters = message_transmitter.functions.getNumEnabledAttesters().call()
    current_threshold = message_transmitter.functions.signatureThreshold().call()
    logger.info("Current attesters: %d, threshold: %d", num_attesters, current_threshold)

    # Impersonate the attester manager
    web3.provider.make_request("anvil_impersonateAccount", [attester_manager])

    # Top up attester manager with ETH for gas
    web3.eth.send_transaction(
        {
            "to": attester_manager,
            "from": web3.eth.accounts[0],
            "value": 10**18,
        }
    )

    # Create a test attester
    test_attester = Account.create()
    logger.info("Test attester address: %s", test_attester.address)

    # Enable our test attester
    tx_hash = message_transmitter.functions.enableAttester(
        test_attester.address,
    ).transact({"from": attester_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Set signature threshold to 1 (only our attester needed)
    if current_threshold != 1:
        tx_hash = message_transmitter.functions.setSignatureThreshold(
            1,
        ).transact({"from": attester_manager})
        assert_transaction_success_with_explanation(web3, tx_hash)

    # Stop impersonating
    web3.provider.make_request("anvil_stopImpersonatingAccount", [attester_manager])

    # Verify
    assert message_transmitter.functions.signatureThreshold().call() == 1
    logger.info("CCTP attester replaced successfully")

    return test_attester


def craft_cctp_message(
    source_domain: int,
    destination_domain: int,
    nonce: int,
    mint_recipient: HexAddress | str,
    amount: int,
    burn_token: HexAddress | str,
    min_finality_threshold: int = FINALITY_THRESHOLD_STANDARD,
) -> bytes:
    """Craft a CCTP V2 message for testing ``receiveMessage()`` on forks.

    Builds a valid CCTP V2 message header + burn message body using
    ``abi.encodePacked`` format matching Circle's on-chain encoding.

    Message header (148 bytes):

    - ``uint32 version`` (4 bytes)
    - ``uint32 sourceDomain`` (4 bytes)
    - ``uint32 destinationDomain`` (4 bytes)
    - ``bytes32 nonce`` (32 bytes)
    - ``bytes32 sender`` (32 bytes) — TokenMessenger on source
    - ``bytes32 recipient`` (32 bytes) — TokenMessenger on dest
    - ``bytes32 destinationCaller`` (32 bytes) — 0x00 for anyone
    - ``uint32 minFinalityThreshold`` (4 bytes)
    - ``uint32 finalityThresholdExecuted`` (4 bytes)

    Burn message body (228 bytes):

    - ``uint32 version`` (4 bytes)
    - ``bytes32 burnToken`` (32 bytes)
    - ``bytes32 mintRecipient`` (32 bytes)
    - ``uint256 amount`` (32 bytes)
    - ``bytes32 messageSender`` (32 bytes)
    - ``bytes32 maxFee`` (32 bytes) — 0 for standard
    - ``bytes32 feeExecuted`` (32 bytes) — 0 (set by attester)
    - ``bytes32 expirationBlock`` (32 bytes) — 0 (set by attester)

    :param source_domain:
        CCTP domain of the source chain (e.g. 0 for Ethereum)

    :param destination_domain:
        CCTP domain of the destination chain (e.g. 6 for Base)

    :param nonce:
        Unique nonce (must not have been used before on this chain)

    :param mint_recipient:
        Address to receive minted USDC on the destination chain

    :param amount:
        Amount of USDC in raw units (6 decimals)

    :param burn_token:
        USDC address on the **source** chain

    :param min_finality_threshold:
        Finality threshold (2000 for standard, 1000 for fast)

    :return:
        Packed message bytes (376 bytes total)
    """
    # TokenMessenger is the sender/recipient in the message header
    # (same address on all chains via CREATE2)
    token_messenger_bytes32 = encode_mint_recipient(TOKEN_MESSENGER_V2)
    mint_recipient_bytes32 = encode_mint_recipient(mint_recipient)
    burn_token_bytes32 = encode_mint_recipient(burn_token)
    destination_caller = b"\x00" * 32

    # Burn message body (228 bytes)
    body = struct.pack(">I", BURN_MESSAGE_VERSION)  # uint32 version
    body += burn_token_bytes32  # bytes32 burnToken
    body += mint_recipient_bytes32  # bytes32 mintRecipient
    body += amount.to_bytes(32, byteorder="big")  # uint256 amount
    body += token_messenger_bytes32  # bytes32 messageSender
    body += b"\x00" * 32  # bytes32 maxFee (0)
    body += b"\x00" * 32  # bytes32 feeExecuted (0)
    body += b"\x00" * 32  # bytes32 expirationBlock (0)

    # Message header (148 bytes) + body
    header = struct.pack(">I", CCTP_MESSAGE_VERSION)
    header += struct.pack(">I", source_domain)
    header += struct.pack(">I", destination_domain)
    header += nonce.to_bytes(32, byteorder="big")  # bytes32 nonce
    header += token_messenger_bytes32  # bytes32 sender
    header += token_messenger_bytes32  # bytes32 recipient
    header += destination_caller  # bytes32 destinationCaller
    header += struct.pack(">I", min_finality_threshold)  # uint32 minFinalityThreshold
    header += struct.pack(">I", min_finality_threshold)  # uint32 finalityThresholdExecuted

    message = header + body
    assert len(message) == 376, f"Expected 376 bytes, got {len(message)}"

    return message


def forge_attestation(message: bytes, attester: LocalAccount) -> bytes:
    """Sign a CCTP message with a test attester to create a valid attestation.

    The attestation is an ECDSA signature over ``keccak256(message)``.
    For CCTP, the signature format is 65 bytes: ``r (32) + s (32) + v (1)``.

    :param message:
        The CCTP message bytes (from :func:`craft_cctp_message`)

    :param attester:
        The test attester account (from :func:`replace_attester_on_fork`)

    :return:
        65-byte attestation (ECDSA signature)
    """
    message_hash = Web3.keccak(message)

    signed = attester.unsafe_sign_hash(message_hash)

    # CCTP expects signature as r (32 bytes) + s (32 bytes) + v (1 byte)
    # eth_account's SignedMessage has .v, .r, .s
    r = signed.r.to_bytes(32, byteorder="big")
    s = signed.s.to_bytes(32, byteorder="big")
    v = signed.v.to_bytes(1, byteorder="big")

    attestation = r + s + v
    assert len(attestation) == 65, f"Expected 65 bytes, got {len(attestation)}"

    return attestation
