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
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import encode_function_call, get_contract

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


def get_core_deposit_wallet_contract(web3: Web3, address: HexAddress | str) -> Contract:
    """Get a Contract instance for the CoreDepositWallet.

    Uses the MockCoreDepositWallet ABI which has the same ``deposit(uint256,uint32)``
    signature as the real CoreDepositWallet.

    :param web3:
        Web3 connection.

    :param address:
        CoreDepositWallet address (use :py:data:`CORE_DEPOSIT_WALLET_MAINNET`
        or :py:data:`CORE_DEPOSIT_WALLET_TESTNET`).

    :return:
        Contract instance with the CoreDepositWallet ABI.
    """
    ContractClass = get_contract(web3, "guard/MockCoreDepositWallet.json")
    return ContractClass(address=Web3.to_checksum_address(address))


def _encode_perform_call(
    module: Contract,
    target: HexAddress | str,
    fn_call: ContractFunction,
) -> bytes:
    """Encode a single ``performCall(target, data)`` invocation as bytes.

    :param module:
        TradingStrategyModuleV0 contract.

    :param target:
        Target contract address.

    :param fn_call:
        Bound contract function call (e.g. ``usdc.functions.approve(spender, amount)``).

    :return:
        ABI-encoded bytes for ``module.performCall(target, data)``.
    """
    data_payload = encode_function_call(fn_call, fn_call.arguments)
    return encode_function_call(
        module.functions.performCall(target, data_payload),
    )


def build_hypercore_deposit_multicall(
    module: Contract,
    usdc_contract: Contract,
    core_deposit_wallet: Contract,
    core_writer: Contract,
    evm_usdc_amount: int,
    hypercore_usdc_amount: int,
    vault_address: HexAddress | str,
) -> ContractFunction:
    """Build a single multicall transaction for the full Hypercore deposit flow.

    Batches the 4-step deposit into one EVM transaction:

    1. ``approve(CoreDepositWallet, amount)`` — approve USDC transfer
    2. ``CoreDepositWallet.deposit(amount, SPOT_DEX)`` — bridge USDC to HyperCore spot
    3. ``CoreWriter.sendRawAction(transferUsdClass)`` — move USDC from spot to perp
    4. ``CoreWriter.sendRawAction(vaultTransfer)`` — deposit into vault

    When the EVM block finishes execution, all queued CoreWriter actions
    are processed sequentially on HyperCore (~47k gas per action).

    Example::

        from eth_defi.hyperliquid.core_writer import (
            build_hypercore_deposit_multicall,
            get_core_deposit_wallet_contract,
            CORE_DEPOSIT_WALLET_MAINNET,
            CORE_WRITER_ADDRESS,
        )

        cdw = get_core_deposit_wallet_contract(web3, CORE_DEPOSIT_WALLET_MAINNET)
        core_writer = web3.eth.contract(address=CORE_WRITER_ADDRESS, abi=cw_abi)

        fn = build_hypercore_deposit_multicall(
            module=module,
            usdc_contract=usdc.contract,
            core_deposit_wallet=cdw,
            core_writer=core_writer,
            evm_usdc_amount=10_000 * 10**6,
            hypercore_usdc_amount=10_000 * 10**6,
            vault_address="0x...",
        )
        tx_hash = fn.transact({"from": asset_manager})

    :param module:
        TradingStrategyModuleV0 contract instance.

    :param usdc_contract:
        ERC-20 USDC contract on HyperEVM.

    :param core_deposit_wallet:
        CoreDepositWallet contract (use :py:func:`get_core_deposit_wallet_contract`).

    :param core_writer:
        CoreWriter contract instance.

    :param evm_usdc_amount:
        USDC amount in EVM wei (uint256) for approve and CDW deposit.

    :param hypercore_usdc_amount:
        USDC amount in HyperCore wei (uint64) for CoreWriter actions.

    :param vault_address:
        Hypercore native vault address.

    :return:
        Bound ``module.functions.multicall(data)`` ready to ``.transact()``.
    """
    calls = [
        # 1. Approve USDC to CoreDepositWallet
        _encode_perform_call(
            module,
            usdc_contract.address,
            usdc_contract.functions.approve(
                Web3.to_checksum_address(core_deposit_wallet.address),
                evm_usdc_amount,
            ),
        ),
        # 2. CoreDepositWallet.deposit(amount, SPOT_DEX)
        _encode_perform_call(
            module,
            core_deposit_wallet.address,
            core_deposit_wallet.functions.deposit(evm_usdc_amount, SPOT_DEX),
        ),
        # 3. CoreWriter.sendRawAction(transferUsdClass(amount, true))
        _encode_perform_call(
            module,
            core_writer.address,
            core_writer.functions.sendRawAction(
                encode_transfer_usd_class(hypercore_usdc_amount, to_perp=True),
            ),
        ),
        # 4. CoreWriter.sendRawAction(vaultTransfer(vault, true, amount))
        _encode_perform_call(
            module,
            core_writer.address,
            core_writer.functions.sendRawAction(
                encode_vault_deposit(vault_address, hypercore_usdc_amount),
            ),
        ),
    ]
    return module.functions.multicall(calls)


def build_hypercore_withdraw_multicall(
    module: Contract,
    core_writer: Contract,
    hypercore_usdc_amount: int,
    vault_address: HexAddress | str,
    safe_address: HexAddress | str,
) -> ContractFunction:
    """Build a single multicall transaction for the full Hypercore withdrawal flow.

    Batches the 3-step withdrawal into one EVM transaction:

    1. ``CoreWriter.sendRawAction(vaultTransfer)`` — withdraw from vault
    2. ``CoreWriter.sendRawAction(transferUsdClass)`` — move USDC from perp to spot
    3. ``CoreWriter.sendRawAction(spotSend)`` — bridge USDC back to EVM Safe

    When the EVM block finishes execution, all queued CoreWriter actions
    are processed sequentially on HyperCore (~47k gas per action).

    :param module:
        TradingStrategyModuleV0 contract instance.

    :param core_writer:
        CoreWriter contract instance.

    :param hypercore_usdc_amount:
        USDC amount in HyperCore wei (uint64) for all CoreWriter actions.

    :param vault_address:
        Hypercore native vault address.

    :param safe_address:
        Safe address to receive USDC back on EVM.

    :return:
        Bound ``module.functions.multicall(data)`` ready to ``.transact()``.
    """
    calls = [
        # 1. CoreWriter.sendRawAction(vaultTransfer(vault, false, amount))
        _encode_perform_call(
            module,
            core_writer.address,
            core_writer.functions.sendRawAction(
                encode_vault_withdraw(vault_address, hypercore_usdc_amount),
            ),
        ),
        # 2. CoreWriter.sendRawAction(transferUsdClass(amount, false))
        _encode_perform_call(
            module,
            core_writer.address,
            core_writer.functions.sendRawAction(
                encode_transfer_usd_class(hypercore_usdc_amount, to_perp=False),
            ),
        ),
        # 3. CoreWriter.sendRawAction(spotSend(safe, USDC, amount))
        _encode_perform_call(
            module,
            core_writer.address,
            core_writer.functions.sendRawAction(
                encode_spot_send(safe_address, USDC_TOKEN_INDEX, hypercore_usdc_amount),
            ),
        ),
    ]
    return module.functions.multicall(calls)
