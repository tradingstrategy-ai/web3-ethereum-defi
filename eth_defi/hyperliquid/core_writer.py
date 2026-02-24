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

#: Minimum USDC deposit into a Hypercore vault (raw, 6 decimals).
#: Hyperliquid silently rejects vaultTransfer deposits below this amount.
#: Determined by reverse-engineering the Hyperliquid web UI.
MINIMUM_VAULT_DEPOSIT = 5_000_000

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
    lagoon_vault: LagoonVault,
    evm_usdc_amount: int,
    hypercore_usdc_amount: int,
    vault_address: HexAddress | str,
    check_activation: bool = False,
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
    chain_id = lagoon_vault.spec.chain_id

    # Derive contract instances from the vault
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
    web3 = lagoon_vault.web3
    module = lagoon_vault.trading_strategy_module
    chain_id = lagoon_vault.spec.chain_id

    asset_address = lagoon_vault.vault_contract.functions.asset().call()
    usdc_contract = get_deployed_contract(web3, "centre/ERC20.json", asset_address)
    cdw_address = CORE_DEPOSIT_WALLET[chain_id]
    core_deposit_wallet = get_core_deposit_wallet_contract(web3, cdw_address)

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
    hypercore_usdc_amount: int,
    vault_address: HexAddress | str,
) -> ContractFunction:
    """Build a single multicall transaction for the full Hypercore withdrawal flow.

    Batches the 3-step withdrawal into one EVM transaction:

    1. ``CoreWriter.sendRawAction(vaultTransfer)`` — withdraw from vault
    2. ``CoreWriter.sendRawAction(transferUsdClass)`` — move USDC from perp to spot
    3. ``CoreWriter.sendRawAction(spotSend)`` — bridge USDC back to EVM Safe

    When the EVM block finishes execution, all queued CoreWriter actions
    are processed sequentially on HyperCore (~47k gas per action).

    Derives all contract instances internally from the :py:class:`LagoonVault`:

    - ``module`` from :py:attr:`LagoonVault.trading_strategy_module`
    - ``core_writer`` at the system address :py:data:`CORE_WRITER_ADDRESS`
    - ``safe_address`` from :py:attr:`LagoonVault.safe_address`

    :param lagoon_vault:
        Lagoon vault instance with ``trading_strategy_module_address`` configured.

    :param hypercore_usdc_amount:
        USDC amount in HyperCore wei (uint64) for all CoreWriter actions.

    :param vault_address:
        Hypercore native vault address (not the Lagoon vault address).

    :return:
        Bound ``module.functions.multicall(data)`` ready to ``.transact()``.
    """
    module = lagoon_vault.trading_strategy_module
    safe_address = lagoon_vault.safe_address
    core_writer = get_core_writer_contract(lagoon_vault.web3)

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
