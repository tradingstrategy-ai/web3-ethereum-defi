"""CoreWriter transaction encoding for Hypercore native vaults.

Encodes raw action bytes for the CoreWriter system contract at
``0x3333333333333333333333333333333333333333`` on HyperEVM.

The raw action format is:

- byte 0: version (always ``1``)
- bytes 1-3: action ID (big-endian uint24)
- bytes 4+: ``abi.encode(action-specific parameters)``

See :doc:`/README-Hypercore-guard` for the full deposit/withdrawal flow.

Bridge fee note
----------------

Core -> HyperEVM linked-token withdrawals are not fee-free. Hyperliquid
charges the bridge fee on HyperCore spot before the linked token settles
back to HyperEVM. In manual mainnet verification, bridging 9 USDC in
transaction `0x82c7ca18fed4952dfdfdffb4e7565cc768c2ab14fe7533bb42a0734cfdf36b16
<https://hyperevmscan.io/tx/0x82c7ca18fed4952dfdfdffb4e7565cc768c2ab14fe7533bb42a0734cfdf36b16>`__
returned 9 USDC to HyperEVM while reducing HyperCore spot by about
9.000783 USDC, implying an observed bridge fee of about 0.000783 USDC.
Callers should therefore not assume that ``spot_before - amount ==
spot_after`` or that the bridged EVM amount will exactly match the full
spot debit.

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

from __future__ import annotations

from typing import TYPE_CHECKING

from eth_abi import encode
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import encode_function_call, get_contract, get_deployed_contract

if TYPE_CHECKING:
    from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault

#: CoreWriter system contract address on HyperEVM
CORE_WRITER_ADDRESS: HexAddress = HexAddress("0x3333333333333333333333333333333333333333")

#: CoreDepositWallet addresses by chain ID.
#: Chain 999 = HyperEVM mainnet, chain 998 = HyperEVM testnet.
CORE_DEPOSIT_WALLET: dict[int, HexAddress] = {
    999: HexAddress("0x6B9E773128f453f5c2C60935Ee2DE2CBc5390A24"),
    998: HexAddress("0x0B80659a4076E9E93C7DbE0f10675A16a3e5C206"),
}

#: USDC token index on HyperCore
USDC_TOKEN_INDEX = 0

#: Spot dex constant (type(uint32).max)
SPOT_DEX = 0xFFFFFFFF

#: Zero address used when ``sendAsset`` targets the sender's main account.
ZERO_ADDRESS = HexAddress("0x0000000000000000000000000000000000000000")

#: USDC linked-token system address on HyperCore / HyperEVM.
USDC_SYSTEM_ADDRESS = HexAddress("0x2000000000000000000000000000000000000000")

#: Linked-token extra wei decimals relative to the EVM contract.
#:
#: Hyperliquid USDC uses 8 token wei decimals on HyperCore while the
#: linked HyperEVM ERC-20 uses 6 decimals. ``sendAsset`` therefore expects
#: USDC amounts scaled by ``10**2`` compared to raw EVM USDC amounts.
LINKED_TOKEN_EVM_EXTRA_WEI_DECIMALS: dict[int, int] = {
    USDC_TOKEN_INDEX: 2,
}

#: Minimum USDC deposit into a Hypercore vault (raw, 6 decimals).
#: Hyperliquid silently rejects vaultTransfer deposits below this amount.
#: Determined by reverse-engineering the Hyperliquid web UI.
MINIMUM_VAULT_DEPOSIT = 5_000_000

# CoreWriter action IDs
ACTION_VAULT_TRANSFER = 2
ACTION_SPOT_SEND = 6
ACTION_USD_CLASS_TRANSFER = 7
ACTION_SEND_ASSET = 13


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

    :raises AssertionError:
        If the deposit amount is below :py:data:`MINIMUM_VAULT_DEPOSIT`.
    """
    assert usdc_amount_wei >= MINIMUM_VAULT_DEPOSIT, f"Vault deposit amount {usdc_amount_wei} raw ({usdc_amount_wei / 1e6:.2f} delagoUSDC) is below the minimum {MINIMUM_VAULT_DEPOSIT} raw ({MINIMUM_VAULT_DEPOSIT / 1e6:.0f} USDC). Hyperliquid silently rejects vault deposits below this threshold."
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

    Sends tokens between HyperCore spot accounts.

    .. note::

        This does **not** bridge linked tokens back to HyperEVM. For
        Core -> HyperEVM USDC withdrawals use :py:func:`encode_send_asset`
        with the token's system address as the destination.

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


def fetch_token_system_address(token_id: int) -> HexAddress:
    """Get the HyperCore system address for a linked token index."""
    assert token_id >= 0, f"Token id must be non-negative, got {token_id}"
    return HexAddress(Web3.to_checksum_address(f"0x20{token_id:038x}"))


def convert_evm_raw_amount_to_linked_token_wei(token_id: int, evm_amount_raw: int) -> int:
    """Convert a linked-token EVM raw amount to the CoreWriter ``sendAsset`` amount."""
    extra_wei_decimals = LINKED_TOKEN_EVM_EXTRA_WEI_DECIMALS.get(token_id)
    assert extra_wei_decimals is not None, f"No linked-token EVM/Core conversion configured for token {token_id}"
    return evm_amount_raw * (10**extra_wei_decimals)


def encode_send_asset(
    destination: HexAddress | str,
    sub_account: HexAddress | str,
    source_dex: int,
    destination_dex: int,
    token_id: int,
    amount_wei: int,
) -> bytes:
    """Encode a CoreWriter sendAsset action (action ID 13).

    ``sendAsset`` is the documented CoreWriter path for linked-token
    transfers between HyperCore spot and HyperEVM spot. To bridge USDC
    from HyperCore back to HyperEVM, pass the USDC system address as
    ``destination`` and ``SPOT_DEX`` for both dex fields.
    """
    params = encode(
        ["address", "address", "uint32", "uint32", "uint64", "uint64"],
        [destination, sub_account, source_dex, destination_dex, token_id, amount_wei],
    )
    return _encode_raw_action(ACTION_SEND_ASSET, params)


def encode_send_asset_to_evm(
    token_id: int,
    evm_amount_raw: int,
    sub_account: HexAddress | str = ZERO_ADDRESS,
) -> bytes:
    """Encode a linked-token transfer from HyperCore spot back to HyperEVM.

    :param evm_amount_raw:
        Amount in the linked EVM token's raw decimals, e.g. 6 decimals for USDC.

    .. note::

        Hyperliquid charges the Core -> HyperEVM bridge fee on spot before
        settlement. In manual mainnet verification, a 9 USDC withdrawal in
        `0x82c7ca18fed4952dfdfdffb4e7565cc768c2ab14fe7533bb42a0734cfdf36b16
        <https://hyperevmscan.io/tx/0x82c7ca18fed4952dfdfdffb4e7565cc768c2ab14fe7533bb42a0734cfdf36b16>`__
        consumed about 0.000783 USDC in bridge fees on spot.
    """
    return encode_send_asset(
        destination=fetch_token_system_address(token_id),
        sub_account=sub_account,
        source_dex=SPOT_DEX,
        destination_dex=SPOT_DEX,
        token_id=token_id,
        amount_wei=convert_evm_raw_amount_to_linked_token_wei(token_id, evm_amount_raw),
    )


def get_core_deposit_wallet_contract(web3: Web3, address: HexAddress | str) -> Contract:
    """Get a Contract instance for the CoreDepositWallet.

    Uses the MockCoreDepositWallet ABI which has the same ``deposit(uint256,uint32)``
    signature as the real CoreDepositWallet.

    :param web3:
        Web3 connection.

    :param address:
        CoreDepositWallet address (use :py:data:`CORE_DEPOSIT_WALLET` with chain ID).

    :return:
        Contract instance with the CoreDepositWallet ABI.
    """
    ContractClass = get_contract(web3, "guard/MockCoreDepositWallet.json")
    return ContractClass(address=Web3.to_checksum_address(address))


def get_core_writer_contract(web3: Web3) -> Contract:
    """Get a Contract instance for the CoreWriter system contract.

    Uses the MockCoreWriter ABI which exposes the same ``sendRawAction(bytes)``
    interface as the real CoreWriter precompile.

    :param web3:
        Web3 connection.

    :return:
        Contract instance at :py:data:`CORE_WRITER_ADDRESS`.
    """
    return get_deployed_contract(web3, "guard/MockCoreWriter.json", CORE_WRITER_ADDRESS)


def _get_hypercore_contracts(
    lagoon_vault: LagoonVault,
) -> tuple[Contract, Contract, Contract]:
    """Resolve the Safe's USDC, CoreDepositWallet, and CoreWriter contracts."""
    web3 = lagoon_vault.web3
    chain_id = lagoon_vault.spec.chain_id
    asset_address = lagoon_vault.vault_contract.functions.asset().call()
    usdc_contract = get_deployed_contract(web3, "centre/ERC20.json", asset_address)
    core_deposit_wallet = get_core_deposit_wallet_contract(web3, CORE_DEPOSIT_WALLET[chain_id])
    core_writer = get_core_writer_contract(web3)
    return usdc_contract, core_deposit_wallet, core_writer


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


def build_hypercore_approve_deposit_wallet_call(
    lagoon_vault: LagoonVault,
    evm_usdc_amount: int,
) -> ContractFunction:
    """Build a single Safe transaction that approves USDC to CoreDepositWallet."""
    usdc_contract, core_deposit_wallet, _core_writer = _get_hypercore_contracts(lagoon_vault)
    return lagoon_vault.transact_via_trading_strategy_module(
        usdc_contract.functions.approve(
            Web3.to_checksum_address(core_deposit_wallet.address),
            evm_usdc_amount,
        )
    )


def build_hypercore_deposit_to_spot_call(
    lagoon_vault: LagoonVault,
    evm_usdc_amount: int,
) -> ContractFunction:
    """Build a single Safe transaction that bridges USDC from HyperEVM to HyperCore spot."""
    _usdc_contract, core_deposit_wallet, _core_writer = _get_hypercore_contracts(lagoon_vault)
    return lagoon_vault.transact_via_trading_strategy_module(
        core_deposit_wallet.functions.deposit(
            evm_usdc_amount,
            SPOT_DEX,
        )
    )


def build_hypercore_deposit_for_spot_call(
    lagoon_vault: LagoonVault,
    evm_usdc_amount: int,
    destination: HexAddress | str | None = None,
) -> ContractFunction:
    """Build a single Safe transaction that bridges USDC to a specific HyperCore spot account."""
    _usdc_contract, core_deposit_wallet, _core_writer = _get_hypercore_contracts(lagoon_vault)
    destination = destination or lagoon_vault.safe_address
    return lagoon_vault.transact_via_trading_strategy_module(
        core_deposit_wallet.functions.depositFor(
            Web3.to_checksum_address(destination),
            evm_usdc_amount,
            SPOT_DEX,
        )
    )


def build_hypercore_transfer_usd_class_call(
    lagoon_vault: LagoonVault,
    hypercore_usdc_amount: int,
    to_perp: bool,
) -> ContractFunction:
    """Build a single Safe transaction that moves USDC between spot and perp."""
    _usdc_contract, _core_deposit_wallet, core_writer = _get_hypercore_contracts(lagoon_vault)
    return lagoon_vault.transact_via_trading_strategy_module(
        core_writer.functions.sendRawAction(
            encode_transfer_usd_class(hypercore_usdc_amount, to_perp=to_perp),
        )
    )


def build_hypercore_spot_send_call(
    lagoon_vault: LagoonVault,
    destination: HexAddress | str,
    hypercore_usdc_amount: int,
) -> ContractFunction:
    """Build a single Safe transaction that sends USDC to another HyperCore spot account."""
    _usdc_contract, _core_deposit_wallet, core_writer = _get_hypercore_contracts(lagoon_vault)
    return lagoon_vault.transact_via_trading_strategy_module(
        core_writer.functions.sendRawAction(
            encode_spot_send(destination, USDC_TOKEN_INDEX, hypercore_usdc_amount),
        )
    )


def build_hypercore_send_asset_to_evm_call(
    lagoon_vault: LagoonVault,
    evm_usdc_amount: int,
) -> ContractFunction:
    """Build a single Safe transaction that bridges USDC from HyperCore spot back to HyperEVM.

    :param evm_usdc_amount:
        Amount in raw HyperEVM USDC decimals (6 decimals).

    .. note::

        The bridged amount is subject to Hyperliquid Core -> HyperEVM bridge
        fees paid from the Safe's spot balance. In manual mainnet verification,
        transaction `0x82c7ca18fed4952dfdfdffb4e7565cc768c2ab14fe7533bb42a0734cfdf36b16
        <https://hyperevmscan.io/tx/0x82c7ca18fed4952dfdfdffb4e7565cc768c2ab14fe7533bb42a0734cfdf36b16>`__
        returned 9 USDC to HyperEVM and consumed about 0.000783 USDC in spot
        bridge fees.
    """
    _usdc_contract, _core_deposit_wallet, core_writer = _get_hypercore_contracts(lagoon_vault)
    return lagoon_vault.transact_via_trading_strategy_module(
        core_writer.functions.sendRawAction(
            encode_send_asset_to_evm(USDC_TOKEN_INDEX, evm_usdc_amount),
        )
    )


def build_hypercore_deposit_multicall(
    lagoon_vault: LagoonVault,
    evm_usdc_amount: int,
    hypercore_usdc_amount: int,
    vault_address: HexAddress | str,
    check_activation: bool = False,
    chain_id: int | None = None,
    asset_address: HexAddress | str | None = None,
) -> ContractFunction:
    """Build a single multicall transaction for the full Hypercore deposit flow.

    .. warning::

        The Safe must be **activated** on HyperCore before using the batched
        deposit. Pass ``check_activation=True`` to automatically verify, or
        use :py:func:`~eth_defi.hyperliquid.evm_escrow.activate_account`
        beforehand. Without activation, deposited USDC gets permanently stuck
        in EVM escrow.

    Batches the 4-step deposit into one EVM transaction:

    1. ``approve(CoreDepositWallet, amount)`` — approve USDC transfer
    2. ``CoreDepositWallet.deposit(amount, SPOT_DEX)`` — bridge USDC to HyperCore spot
    3. ``CoreWriter.sendRawAction(transferUsdClass)`` — move USDC from spot to perp
    4. ``CoreWriter.sendRawAction(vaultTransfer)`` — deposit into vault

    When the EVM block finishes execution, all queued CoreWriter actions
    are processed sequentially on HyperCore (~47k gas per action).

    For extra safety under heavy HyperCore load, use the two-phase approach
    with :py:func:`build_hypercore_deposit_phase1` and
    :py:func:`build_hypercore_deposit_phase2` with
    :py:func:`~eth_defi.hyperliquid.evm_escrow.wait_for_evm_escrow_clear`
    between them.

    Derives all contract instances internally from the :py:class:`LagoonVault`:

    - ``module`` from :py:attr:`LagoonVault.trading_strategy_module`
    - ``usdc_contract`` from the vault's underlying asset address
    - ``core_deposit_wallet`` from the chain ID (mainnet vs testnet)
    - ``core_writer`` at the system address :py:data:`CORE_WRITER_ADDRESS`

    Example::

        from eth_defi.hyperliquid.core_writer import build_hypercore_deposit_multicall

        fn = build_hypercore_deposit_multicall(
            lagoon_vault=lagoon_vault,
            evm_usdc_amount=10_000 * 10**6,
            hypercore_usdc_amount=10_000 * 10**6,
            vault_address="0x...",
            check_activation=True,
        )
        tx_hash = fn.transact({"from": asset_manager})

    :param lagoon_vault:
        Lagoon vault instance with ``trading_strategy_module_address`` configured.

    :param evm_usdc_amount:
        USDC amount in EVM wei (uint256) for approve and CDW deposit.

    :param hypercore_usdc_amount:
        USDC amount in HyperCore wei (uint64) for CoreWriter actions.

    :param vault_address:
        Hypercore native vault address (not the Lagoon vault address).

    :param check_activation:
        If ``True``, verifies the Safe is activated on HyperCore using the
        ``coreUserExists`` precompile before building the multicall.
        Set to ``False`` (default) in simulate/Anvil mode where the
        precompile is not available.

    :param chain_id:
        Override the chain ID used to look up the ``CoreDepositWallet``
        address.  When ``None`` (default), derived from
        ``lagoon_vault.spec.chain_id``.  Pass explicitly when using a
        :py:class:`~eth_defi.erc_4626.vault_protocol.lagoon.vault.LagoonSatelliteVault`
        which has no ``.spec`` attribute.

    :param asset_address:
        Override the USDC token address used for the ``approve`` call.
        When ``None`` (default), derived from the vault's underlying
        asset (``lagoon_vault.vault_contract.functions.asset()``).
        Pass explicitly when using a satellite vault which has no
        ``.vault_contract`` attribute.

    :return:
        Bound ``module.functions.multicall(data)`` ready to ``.transact()``.

    :raises RuntimeError:
        If ``check_activation`` is True and the Safe is not activated on HyperCore.
    """
    if check_activation:
        from eth_defi.hyperliquid.evm_escrow import is_account_activated

        safe_address = lagoon_vault.safe_address
        if not is_account_activated(lagoon_vault.web3, user=safe_address):
            raise RuntimeError(f"Safe {safe_address} is not activated on HyperCore. Call activate_account() before depositing, or bridge actions will get permanently stuck in EVM escrow. See eth_defi.hyperliquid.evm_escrow for details.")

    web3 = lagoon_vault.web3
    module = lagoon_vault.trading_strategy_module

    # Allow overriding chain_id and asset_address for satellite vaults
    # (LagoonSatelliteVault has no .spec or .vault_contract)
    if chain_id is None:
        chain_id = lagoon_vault.spec.chain_id
    if asset_address is None:
        asset_address = lagoon_vault.vault_contract.functions.asset().call()
    usdc_contract = get_deployed_contract(web3, "centre/ERC20.json", asset_address)
    cdw_address = CORE_DEPOSIT_WALLET[chain_id]
    core_deposit_wallet = get_core_deposit_wallet_contract(web3, cdw_address)
    core_writer = get_core_writer_contract(web3)

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


def build_activate_account_multicall(
    lagoon_vault: LagoonVault,
    activation_amount: int | None = None,
) -> ContractFunction:
    """Build a multicall to activate a Safe's HyperCore account.

    Smart contracts (like Safe multisigs) must be activated on HyperCore
    before ``CoreDepositWallet.deposit()`` bridge actions will clear the
    EVM escrow.  This multicall performs the activation in a single
    transaction via the Safe's trading strategy module:

    1. ``approve(CoreDepositWallet, activation_amount)``
    2. ``CoreDepositWallet.depositFor(safe, activation_amount, SPOT_DEX)``

    .. note::

        New HyperCore accounts incur a **1 USDC account creation fee**.
        The default ``activation_amount`` of 2 USDC exceeds the fee.

    :param lagoon_vault:
        Lagoon vault instance with ``trading_strategy_module_address`` configured.

    :param activation_amount:
        USDC amount in raw units (6 decimals) for activation.
        Defaults to :py:data:`~eth_defi.hyperliquid.evm_escrow.DEFAULT_ACTIVATION_AMOUNT` (2 USDC).

    :return:
        Bound ``module.functions.multicall(data)`` ready to ``.transact()``.
    """
    from eth_defi.hyperliquid.evm_escrow import DEFAULT_ACTIVATION_AMOUNT

    if activation_amount is None:
        activation_amount = DEFAULT_ACTIVATION_AMOUNT

    module = lagoon_vault.trading_strategy_module
    safe_address = lagoon_vault.safe_address
    usdc_contract, core_deposit_wallet, _core_writer = _get_hypercore_contracts(lagoon_vault)

    calls = [
        # 1. Approve USDC to CoreDepositWallet
        _encode_perform_call(
            module,
            usdc_contract.address,
            usdc_contract.functions.approve(
                Web3.to_checksum_address(core_deposit_wallet.address),
                activation_amount,
            ),
        ),
        # 2. CoreDepositWallet.depositFor(safe, amount, SPOT_DEX)
        _encode_perform_call(
            module,
            core_deposit_wallet.address,
            core_deposit_wallet.functions.depositFor(
                Web3.to_checksum_address(safe_address),
                activation_amount,
                SPOT_DEX,
            ),
        ),
    ]
    return module.functions.multicall(calls)


def build_hypercore_deposit_phase1(
    lagoon_vault: LagoonVault,
    evm_usdc_amount: int,
) -> ContractFunction:
    """Build phase 1 of a two-phase Hypercore deposit: bridge USDC to HyperCore spot.

    This multicall performs:

    1. ``approve(CoreDepositWallet, amount)`` -- approve USDC transfer
    2. ``CoreDepositWallet.deposit(amount, SPOT_DEX)`` -- bridge USDC to HyperCore spot

    After this transaction lands, the USDC enters EVM escrow. Use
    :py:func:`~eth_defi.hyperliquid.evm_escrow.wait_for_evm_escrow_clear`
    to wait for the funds to arrive in the spot account, then call
    :py:func:`build_hypercore_deposit_phase2` for the remaining steps.

    Example::

        from eth_defi.hyperliquid.core_writer import (
            build_hypercore_deposit_phase1,
            build_hypercore_deposit_phase2,
        )
        from eth_defi.hyperliquid.evm_escrow import wait_for_evm_escrow_clear

        # Phase 1: bridge USDC to HyperCore
        fn1 = build_hypercore_deposit_phase1(lagoon_vault, evm_usdc_amount=1_000_000)
        tx_hash = fn1.transact({"from": asset_manager})

        # Wait for escrow to clear
        wait_for_evm_escrow_clear(session, user=safe_address)

        # Phase 2: move to perp and deposit into vault
        fn2 = build_hypercore_deposit_phase2(
            lagoon_vault,
            hypercore_usdc_amount=1_000_000,
            vault_address="0x...",
        )
        tx_hash = fn2.transact({"from": asset_manager})

    :param lagoon_vault:
        Lagoon vault instance with ``trading_strategy_module_address`` configured.

    :param evm_usdc_amount:
        USDC amount in EVM wei (uint256) for approve and CDW deposit.

    :return:
        Bound ``module.functions.multicall(data)`` ready to ``.transact()``.
    """
    module = lagoon_vault.trading_strategy_module
    usdc_contract, core_deposit_wallet, _core_writer = _get_hypercore_contracts(lagoon_vault)

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
    ]
    return module.functions.multicall(calls)


def build_hypercore_deposit_phase2(
    lagoon_vault: LagoonVault,
    hypercore_usdc_amount: int,
    vault_address: HexAddress | str,
) -> ContractFunction:
    """Build phase 2 of a two-phase Hypercore deposit: spot to perp to vault.

    Batches two CoreWriter actions into a single multicall:

    1. ``transferUsdClass`` — move USDC from spot to perp
    2. ``vaultTransfer`` — deposit USDC from perp into vault

    When the EVM block finishes execution, HyperCore processes all queued
    CoreWriter actions from that block sequentially, so the ``transferUsdClass``
    completes before the ``vaultTransfer`` runs.

    Must only be called after phase 1 USDC has cleared the EVM escrow and
    is available in the user's HyperCore spot account. Use
    :py:func:`~eth_defi.hyperliquid.evm_escrow.wait_for_evm_escrow_clear`
    between phase 1 and phase 2.

    :param lagoon_vault:
        Lagoon vault instance with ``trading_strategy_module_address`` configured.

    :param hypercore_usdc_amount:
        USDC amount in HyperCore wei (uint64) for both CoreWriter actions.

    :param vault_address:
        Hypercore native vault address (not the Lagoon vault address).

    :return:
        Bound ``module.functions.multicall(data)`` ready to ``.transact()``.
    """
    module = lagoon_vault.trading_strategy_module
    core_writer = get_core_writer_contract(lagoon_vault.web3)

    calls = [
        # 1. Move USDC from spot to perp
        _encode_perform_call(
            module,
            core_writer.address,
            core_writer.functions.sendRawAction(
                encode_transfer_usd_class(hypercore_usdc_amount, to_perp=True),
            ),
        ),
        # 2. Deposit USDC from perp into vault
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
    lagoon_vault: LagoonVault,
    evm_usdc_amount: int,
    vault_address: HexAddress | str,
) -> ContractFunction:
    """Build a single multicall transaction for the full Hypercore withdrawal flow.

    Batches the 3-step withdrawal into one EVM transaction:

    1. ``CoreWriter.sendRawAction(vaultTransfer)`` — withdraw from vault
    2. ``CoreWriter.sendRawAction(transferUsdClass)`` — move USDC from perp to spot
    3. ``CoreWriter.sendRawAction(sendAsset)`` — bridge USDC back to HyperEVM

    The final bridge leg is subject to Hyperliquid Core -> HyperEVM bridge
    fees paid from spot. A successful withdrawal can therefore leave a small
    residual reduction on HyperCore spot in addition to the requested EVM
    transfer amount. In manual mainnet verification, transaction
    `0x82c7ca18fed4952dfdfdffb4e7565cc768c2ab14fe7533bb42a0734cfdf36b16
    <https://hyperevmscan.io/tx/0x82c7ca18fed4952dfdfdffb4e7565cc768c2ab14fe7533bb42a0734cfdf36b16>`__
    consumed about 0.000783 USDC in bridge fees on spot while returning
    9 USDC to HyperEVM.

    When the EVM block finishes execution, all queued CoreWriter actions
    are processed sequentially on HyperCore (~47k gas per action).

    Derives all contract instances internally from the :py:class:`LagoonVault`:

    - ``module`` from :py:attr:`LagoonVault.trading_strategy_module`
    - ``core_writer`` at the system address :py:data:`CORE_WRITER_ADDRESS`
    :param lagoon_vault:
        Lagoon vault instance with ``trading_strategy_module_address`` configured.

    :param evm_usdc_amount:
        USDC amount in raw HyperEVM decimals (6 decimals). The final
        ``sendAsset`` leg is converted to linked-token wei internally.

    :param vault_address:
        Hypercore native vault address (not the Lagoon vault address).

    :return:
        Bound ``module.functions.multicall(data)`` ready to ``.transact()``.
    """
    module = lagoon_vault.trading_strategy_module
    core_writer = get_core_writer_contract(lagoon_vault.web3)

    calls = [
        # 1. CoreWriter.sendRawAction(vaultTransfer(vault, false, amount))
        _encode_perform_call(
            module,
            core_writer.address,
            core_writer.functions.sendRawAction(
                encode_vault_withdraw(vault_address, evm_usdc_amount),
            ),
        ),
        # 2. CoreWriter.sendRawAction(transferUsdClass(amount, false))
        _encode_perform_call(
            module,
            core_writer.address,
            core_writer.functions.sendRawAction(
                encode_transfer_usd_class(evm_usdc_amount, to_perp=False),
            ),
        ),
        # 3. CoreWriter.sendRawAction(sendAsset(USDC system address, ...))
        _encode_perform_call(
            module,
            core_writer.address,
            core_writer.functions.sendRawAction(
                encode_send_asset_to_evm(USDC_TOKEN_INDEX, evm_usdc_amount),
            ),
        ),
    ]
    return module.functions.multicall(calls)
