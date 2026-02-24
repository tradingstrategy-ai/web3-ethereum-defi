"""EVM escrow lifecycle for HyperEVM-to-HyperCore bridging.

When USDC is bridged from HyperEVM to HyperCore via ``CoreDepositWallet.deposit()``,
the funds do not appear in the user's HyperCore spot account immediately.
Instead they pass through an intermediate **EVM escrow** stage:

1. **EVM transaction lands** -- ``CoreDepositWallet.deposit(amount, SPOT_DEX)``
   burns USDC on HyperEVM and queues a bridge action.

2. **EVM escrow** -- the deposited amount appears in the
   ``evmEscrows`` field of the user's ``spotClearinghouseState``.
   The funds are locked here while HyperCore's L1 processes the bridge.

3. **HyperCore processing** -- within a few seconds (typically 2-4 s on
   mainnet, up to ~10 s on testnet under load) HyperCore picks up the
   queued action. The escrow entry disappears and the amount is credited
   to the user's HyperCore spot balance.

Contract activation prerequisite
---------------------------------

**Smart contract addresses** (such as Safe multisigs) must be **activated**
on HyperCore before ``CoreDepositWallet.deposit()`` bridge actions will
clear the EVM escrow. Without activation, deposited USDC gets **permanently
stuck** in the ``evmEscrows`` field and never reaches the spot account.

Activation is performed via ``CoreDepositWallet.depositFor(safe, amount, dex)``
which bridges USDC to the Safe's HyperCore spot and creates the account.
New HyperCore accounts incur a **1 USDC account creation fee**, so the
minimum activation amount must be **>1 USDC** (deposits ≤1 USDC to new
accounts fail silently). The default activation amount is 2 USDC.

.. note::

    ``CoreWriter.sendRawAction(spotSend)`` does **not** work for activation:
    HyperCore silently drops ``spotSend`` actions targeting non-existent
    addresses (the EVM transaction succeeds but no USDC is transferred).

Use :py:func:`is_account_activated` to check activation status and
:py:func:`activate_account` to perform the activation before depositing.

Why this matters for multi-step deposit flows
---------------------------------------------

The old 4-step single-multicall deposit batched *all* actions into one
EVM block:

1. ``approve`` USDC to CoreDepositWallet
2. ``CoreDepositWallet.deposit()`` -- bridge USDC to HyperCore spot
3. ``CoreWriter.sendRawAction(transferUsdClass)`` -- move from spot to perp
4. ``CoreWriter.sendRawAction(vaultTransfer)`` -- deposit into vault

Steps 3-4 depend on the USDC having cleared the EVM escrow and being
available in the spot account. Because all four steps land in the
**same EVM block**, HyperCore processes them in a single batch. In
practice this works *most* of the time, but under heavy load the
bridge step can take longer, causing steps 3-4 to silently fail on
HyperCore while the EVM transaction succeeds.

The fix is to split the deposit into two phases with an escrow wait:

- **Phase 1**: ``approve`` + ``CoreDepositWallet.deposit()``
- **Wait**: poll ``spotClearinghouseState`` until the ``evmEscrows``
  entry for USDC disappears (meaning funds arrived in spot).
- **Phase 2**: ``transferUsdClass`` + ``vaultTransfer``

Checking escrow status
----------------------

Use :py:func:`fetch_spot_clearinghouse_state` from :py:mod:`eth_defi.hyperliquid.api`::

    from eth_defi.hyperliquid.api import fetch_spot_clearinghouse_state
    from eth_defi.hyperliquid.session import create_hyperliquid_session

    session = create_hyperliquid_session()
    state = fetch_spot_clearinghouse_state(session, user="0xAbc...")

    if state.evm_escrows:
        for e in state.evm_escrows:
            print(f"{e.coin}: {e.total} in escrow")
    else:
        print("No EVM escrows -- funds have cleared")

Expected latencies
------------------

- **Mainnet**: 2-4 seconds typical, up to 10 seconds under load.
- **Testnet**: 2-10 seconds typical, can be slower during congestion.
- **Maximum observed**: ~30 seconds in extreme cases.

The :py:func:`wait_for_evm_escrow_clear` helper uses conservative defaults
(60 s timeout, 2 s poll interval) to handle worst-case scenarios.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from eth_abi import decode, encode
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.hotwallet import HotWallet
from eth_defi.hyperliquid.api import fetch_spot_clearinghouse_state
from eth_defi.hyperliquid.core_writer import CORE_DEPOSIT_WALLET, SPOT_DEX, get_core_deposit_wallet_contract
from eth_defi.hyperliquid.session import HyperliquidSession
from eth_defi.trace import assert_transaction_success_with_explanation

if TYPE_CHECKING:
    from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault

logger = logging.getLogger(__name__)

#: coreUserExists precompile address on HyperEVM.
#: Returns whether an address exists on HyperCore.
#: See PrecompileLib.sol in hyper-evm-lib.
CORE_USER_EXISTS_ADDRESS = "0x0000000000000000000000000000000000000810"

#: Default USDC amount (raw, 6 decimals) for account activation.
#: New HyperCore accounts incur a ~1 USDC fee, so the minimum
#: activation deposit must comfortably exceed that.
DEFAULT_ACTIVATION_AMOUNT = 2_000_000


def is_account_activated(
    web3: Web3,
    user: str,
) -> bool:
    """Check if an address is activated on HyperCore.

    Uses the ``coreUserExists`` precompile at :py:data:`CORE_USER_EXISTS_ADDRESS`
    to definitively check whether the address exists on HyperCore.

    Smart contracts (like Safe multisigs) must be activated before
    ``CoreDepositWallet.deposit()`` bridge actions will clear the
    EVM escrow. See :py:func:`activate_account`.

    Example::

        from eth_defi.hyperliquid.evm_escrow import is_account_activated
        from eth_defi.provider.multi_provider import create_multi_provider_web3

        web3 = create_multi_provider_web3("https://rpc.hyperliquid.xyz/evm")
        if is_account_activated(web3, user="0xAbc..."):
            print("Account is activated")
        else:
            print("Account needs activation")

    :param web3:
        Web3 connection to HyperEVM.

    :param user:
        On-chain address to check.

    :return:
        ``True`` if the address exists on HyperCore.
    """
    data = encode(["address"], [Web3.to_checksum_address(user)])
    result = web3.eth.call(
        {
            "to": Web3.to_checksum_address(CORE_USER_EXISTS_ADDRESS),
            "data": "0x" + data.hex(),
        }
    )
    exists = decode(["bool"], result)[0]
    logger.info("Account %s coreUserExists on HyperCore: %s", user, exists)
    return exists


def activate_account(
    web3: Web3,
    lagoon_vault: LagoonVault,
    deployer: HotWallet,
    session: HyperliquidSession | None = None,
    activation_amount: int = DEFAULT_ACTIVATION_AMOUNT,
    timeout: float = 60.0,
    poll_interval: float = 2.0,
) -> None:
    """Activate a Safe's HyperCore account via ``depositFor``.

    Smart contracts (like Safe multisigs) must be activated on HyperCore
    before ``CoreDepositWallet.deposit()`` bridge actions will work.
    Without activation, deposited USDC gets permanently stuck in the
    ``evmEscrows`` field.

    The activation flow uses ``transact_via_trading_strategy_module``
    to call ``CoreDepositWallet.depositFor(safe, amount, SPOT_DEX)``
    through the Safe's trading strategy module. This bridges USDC from
    the Safe's EVM balance to the Safe's HyperCore spot account,
    creating the account in the process.

    .. note::

        New HyperCore accounts incur a **1 USDC account creation fee**.
        Deposits ≤1 USDC to new accounts fail silently. The default
        ``activation_amount`` of 2 USDC comfortably exceeds the fee.

    .. warning::

        The Safe must hold sufficient EVM USDC for the activation amount.
        The guard must have ``depositFor`` whitelisted via
        ``whitelistCoreWriter()`` (included since guard v0.x).

    Example::

        from eth_defi.hyperliquid.evm_escrow import activate_account

        activate_account(
            web3=web3,
            lagoon_vault=lagoon_vault,
            deployer=deployer_wallet,
        )

    :param web3:
        Web3 connection to HyperEVM.

    :param lagoon_vault:
        Lagoon vault instance with ``trading_strategy_module_address`` configured.
        The Safe associated with this vault will be activated.

    :param deployer:
        Hot wallet for the asset manager / deployer EOA.

    :param session:
        Optional Hyperliquid API session. If provided, the function checks
        that the Safe has no existing EVM escrow entries before attempting
        activation. Stuck escrow entries from prior failed deposits will
        prevent activation from succeeding.

    :param activation_amount:
        USDC amount in raw units (6 decimals) to deposit for activation.
        Defaults to 2 USDC (:py:data:`DEFAULT_ACTIVATION_AMOUNT`).
        Must comfortably exceed the ~1 USDC account creation fee.

    :param timeout:
        Maximum seconds to wait for activation verification.
        Defaults to 60 seconds.

    :param poll_interval:
        Seconds between precompile polls. Defaults to 2 seconds.

    :raises TimeoutError:
        If the activation does not complete within the timeout period.
    """
    safe_address = lagoon_vault.safe_address

    # Already activated?
    if is_account_activated(web3, safe_address):
        logger.info("Account %s is already activated on HyperCore", safe_address)
        return

    # Check for stuck EVM escrow entries from prior failed deposits.
    # If the Safe already has USDC stuck in escrow, the depositFor
    # will succeed on EVM but HyperCore will never process it.
    if session is not None:
        state = fetch_spot_clearinghouse_state(session, user=safe_address)
        assert not state.evm_escrows, f"Account {safe_address} has existing EVM escrow entries that must clear before activation can succeed: {', '.join(f'{e.coin}={e.total}' for e in state.evm_escrows)}. This typically means a prior deposit() was called before the account was activated, and the USDC is permanently stuck."

    logger.info(
        "Activating account %s on HyperCore via depositFor (%d raw USDC)",
        safe_address,
        activation_amount,
    )

    chain_id = web3.eth.chain_id

    # Get contract instances
    asset_address = lagoon_vault.vault_contract.functions.asset().call()
    usdc_contract = get_deployed_contract(web3, "centre/ERC20.json", asset_address)
    cdw_address = CORE_DEPOSIT_WALLET[chain_id]
    core_deposit_wallet = get_core_deposit_wallet_contract(web3, cdw_address)

    # Step 1: Approve USDC to CoreDepositWallet via trading strategy module
    approve_fn = lagoon_vault.transact_via_trading_strategy_module(
        usdc_contract.functions.approve(
            Web3.to_checksum_address(cdw_address),
            activation_amount,
        ),
    )
    tx_hash = deployer.transact_and_broadcast_with_contract(approve_fn, gas_limit=200_000)
    assert_transaction_success_with_explanation(web3, tx_hash)
    logger.info("Activation: USDC approve tx %s", tx_hash.hex())

    # Step 2: depositFor(safe, amount, SPOT_DEX) via trading strategy module
    deployer.sync_nonce(web3)
    deposit_for_fn = lagoon_vault.transact_via_trading_strategy_module(
        core_deposit_wallet.functions.depositFor(
            Web3.to_checksum_address(safe_address),
            activation_amount,
            SPOT_DEX,
        ),
    )
    tx_hash = deployer.transact_and_broadcast_with_contract(deposit_for_fn, gas_limit=200_000)
    assert_transaction_success_with_explanation(web3, tx_hash)
    logger.info("Activation: depositFor tx %s", tx_hash.hex())

    # Poll coreUserExists precompile to verify activation
    deadline = time.time() + timeout
    while True:
        time.sleep(poll_interval)
        if is_account_activated(web3, safe_address):
            logger.info("Account %s successfully activated on HyperCore", safe_address)
            return
        if time.time() >= deadline:
            raise TimeoutError(f"Account {safe_address} was not activated within {timeout}s after depositFor transaction {tx_hash.hex()}")


def wait_for_evm_escrow_clear(
    session: HyperliquidSession,
    user: str,
    timeout: float = 60.0,
    poll_interval: float = 2.0,
) -> None:
    """Wait until the user's EVM escrow is empty (all bridged funds have cleared).

    Waits one ``poll_interval`` before the first check to give HyperCore
    time to register the escrow entry (the API can lag behind the EVM tx).
    Then polls ``spotClearinghouseState`` until ``evmEscrows`` is empty,
    indicating that all ``CoreDepositWallet.deposit()`` actions have been
    processed and funds are available in the user's spot account.

    Example::

        from eth_defi.hyperliquid.evm_escrow import wait_for_evm_escrow_clear
        from eth_defi.hyperliquid.session import create_hyperliquid_session

        session = create_hyperliquid_session()
        wait_for_evm_escrow_clear(session, user="0xAbc...")
        # Now safe to proceed with CoreWriter actions that need spot balance

    :param session:
        Session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`.

    :param user:
        On-chain address whose escrow to monitor
        (the Safe address for Lagoon vaults).

    :param timeout:
        Maximum seconds to wait before raising :py:class:`TimeoutError`.
        Defaults to 60 seconds which is conservative for typical 2-10 s latency.

    :param poll_interval:
        Seconds between API polls. Defaults to 2 seconds.

    :raises TimeoutError:
        If the escrow does not clear within the timeout period.
    """
    deadline = time.time() + timeout
    attempt = 0

    # Initial delay: the HyperCore API needs time to register the
    # escrow entry after the EVM transaction lands. Without this,
    # the first poll may see the pre-existing state (no escrow) and
    # return immediately, causing phase 2 to fire before the USDC
    # has actually arrived in spot.
    time.sleep(poll_interval)

    while True:
        attempt += 1
        state = fetch_spot_clearinghouse_state(session, user=user)

        if not state.evm_escrows:
            logger.info(
                "EVM escrow cleared for %s after %d poll(s)",
                user,
                attempt,
            )
            return

        escrow_summary = ", ".join(f"{e.coin}={e.total}" for e in state.evm_escrows)
        remaining = deadline - time.time()

        if remaining <= 0:
            raise TimeoutError(f"EVM escrow for {user} did not clear within {timeout}s. Remaining escrows: {escrow_summary}")

        logger.info(
            "EVM escrow pending for %s: %s (%.0fs remaining, poll #%d)",
            user,
            escrow_summary,
            remaining,
            attempt,
        )
        time.sleep(min(poll_interval, remaining))
