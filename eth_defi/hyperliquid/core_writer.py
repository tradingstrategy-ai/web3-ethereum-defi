"""CoreWriter transaction encoding for Hypercore native vaults.

Encodes raw action bytes for the CoreWriter system contract at
``0x3333333333333333333333333333333333333333`` on HyperEVM.

The raw action format is:

- byte 0: version (always ``1``)
- bytes 1-3: action ID (big-endian uint24)
- bytes 4+: ``abi.encode(action-specific parameters)``

See :doc:`/README-Hypercore-guard` for the full deposit/withdrawal flow.

Example::

    from eth_defi.hyperliquid.core_writer import (
        encode_vault_deposit,
        encode_transfer_usd_class,
        CORE_WRITER_ADDRESS,
    )

    # Build the raw action bytes for a vault deposit
    raw_action = encode_vault_deposit(vault_address, usdc_amount_wei)

    # Call CoreWriter.sendRawAction(raw_action) via the guard
    core_writer = web3.eth.contract(
        address=CORE_WRITER_ADDRESS,
        abi=core_writer_abi,
    )
    fn_call = core_writer.functions.sendRawAction(raw_action)
"""

from eth_abi import encode
from eth_typing import HexAddress

#: CoreWriter system contract address on HyperEVM
CORE_WRITER_ADDRESS: HexAddress = HexAddress("0x3333333333333333333333333333333333333333")

#: CoreDepositWallet address on HyperEVM mainnet
CORE_DEPOSIT_WALLET_MAINNET: HexAddress = HexAddress("0x6B9E773128f453f5c2C60935Ee2DE2CBc5390A24")

#: CoreDepositWallet address on HyperEVM testnet
CORE_DEPOSIT_WALLET_TESTNET: HexAddress = HexAddress("0x0B80659a4076E9E93C7DbE0f10675A16a3e5C206")

#: USDC token index on HyperCore
USDC_TOKEN_INDEX = 0

#: Spot dex constant (type(uint32).max)
SPOT_DEX = 0xFFFFFFFF

# CoreWriter action IDs
ACTION_VAULT_TRANSFER = 2
ACTION_SPOT_SEND = 6
ACTION_USD_CLASS_TRANSFER = 7


def _encode_raw_action(action_id: int, params: bytes) -> bytes:
    """Encode a CoreWriter raw action.

    :param action_id:
        CoreWriter action ID (1-15).

    :param params:
        ABI-encoded action parameters.

    :return:
        Raw action bytes: version(1) + actionId(uint24 BE) + params.
    """
    version = (1).to_bytes(1, "big")
    action_id_bytes = action_id.to_bytes(3, "big")
    return version + action_id_bytes + params


def encode_vault_deposit(vault: HexAddress | str, usdc_amount_wei: int) -> bytes:
    """Encode a CoreWriter vaultTransfer deposit action (action ID 2).

    :param vault:
        Hypercore native vault address.

    :param usdc_amount_wei:
        USDC amount in HyperCore wei (uint64). Note: HyperCore uses
        different decimal representations than EVM.

    :return:
        Raw action bytes for ``CoreWriter.sendRawAction()``.
    """
    params = encode(
        ["address", "bool", "uint64"],
        [vault, True, usdc_amount_wei],
    )
    return _encode_raw_action(ACTION_VAULT_TRANSFER, params)


def encode_vault_withdraw(vault: HexAddress | str, usdc_amount_wei: int) -> bytes:
    """Encode a CoreWriter vaultTransfer withdraw action (action ID 2).

    :param vault:
        Hypercore native vault address.

    :param usdc_amount_wei:
        USDC amount in HyperCore wei (uint64).

    :return:
        Raw action bytes for ``CoreWriter.sendRawAction()``.
    """
    params = encode(
        ["address", "bool", "uint64"],
        [vault, False, usdc_amount_wei],
    )
    return _encode_raw_action(ACTION_VAULT_TRANSFER, params)


def encode_transfer_usd_class(amount_wei: int, to_perp: bool) -> bytes:
    """Encode a CoreWriter transferUsdClass action (action ID 7).

    Moves USDC between spot and perp accounts on HyperCore.

    :param amount_wei:
        USDC amount in HyperCore wei (uint64).

    :param to_perp:
        ``True`` to move from spot to perp, ``False`` for perp to spot.

    :return:
        Raw action bytes for ``CoreWriter.sendRawAction()``.
    """
    params = encode(
        ["uint64", "bool"],
        [amount_wei, to_perp],
    )
    return _encode_raw_action(ACTION_USD_CLASS_TRANSFER, params)


def encode_spot_send(
    destination: HexAddress | str,
    token_id: int,
    amount_wei: int,
) -> bytes:
    """Encode a CoreWriter spotSend action (action ID 6).

    Sends tokens from HyperCore spot to an address. Used to bridge
    tokens from Core back to EVM (destination = EVM address).

    :param destination:
        Recipient address (typically the Safe address for bridging back).

    :param token_id:
        HyperCore token index (0 = USDC).

    :param amount_wei:
        Amount in HyperCore wei (uint64).

    :return:
        Raw action bytes for ``CoreWriter.sendRawAction()``.
    """
    params = encode(
        ["address", "uint64", "uint64"],
        [destination, token_id, amount_wei],
    )
    return _encode_raw_action(ACTION_SPOT_SEND, params)
