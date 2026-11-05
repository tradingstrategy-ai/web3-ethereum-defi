"""Lagoon Safe helpers for Lighter L1 deposits, withdrawals and claims.

The deployment flow transfers the minimum accounted USDC balance from a Lagoon
Safe to Lighter through ``TradingStrategyModuleV0``. A secure withdrawal is
requested either off-chain with a Lighter API key or onchain with the key-free
L1 ``withdraw`` call of :py:func:`request_usdc_withdrawal_from_lighter_account`,
which also works for sub-accounts. Once Lighter makes it claimable, this module
claims the Safe's L1 pending balance back to the same Safe.

The secure-withdrawal delay is dynamic. Read
:py:func:`eth_defi.lighter.api.fetch_lighter_withdrawal_delay` for an operator
estimate; on 2026-10-26 an L1 withdrawal from a mainnet sub-account became
claimable after about 29 minutes. Only a claimable withdrawal, visible as a
``claimable`` withdrawal-history status or as an onchain pending balance,
permits the L1 claim. Secure withdrawals and direct Ethereum deposits have a
1 USDC minimum. Fast withdrawals (4 USDC minimum) require the L1 account's EOA
private key and are not available to a contract-owned Safe.

``withdrawPendingBalance(owner, ...)`` is permissionless and always pays
``owner``, and Lighter runs a relayer that claims in batches for many owners. A
withdrawal can therefore reach the Safe with no claim from us, and never appear
as a pending balance for long. On 2026-11-05 two mainnet withdrawals were paid
this way about 26 and 31 minutes after their requests, while another that day
waited for our claim. When the relayer wins, our own claim reverts with
``ZkLighter_InvalidWithdrawAmount``, although the money has arrived. Do not treat
a missing pending balance or a reverted claim as a lost withdrawal: use
:py:func:`wait_for_lighter_withdrawal` and :py:func:`is_lighter_withdrawal_paid`,
which read the ``WithdrawPending`` event that every claim emits. The Safe's USDC
balance is not evidence, because Lagoon settlement moves it too.

The event names the owner, not the withdrawal, so these functions assume one
withdrawal at a time per owner. The executor runs them sequentially. A sub-account
trader withdrawing with their own key at the same moment could be mistaken for ours.

Authoritative Lighter deposit and withdrawal documentation:
https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals
"""

import logging
import time
from decimal import Decimal
from typing import Callable

from eth_typing import HexAddress
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract
from eth_defi.confirmation import broadcast_and_wait_transactions_to_complete
from eth_defi.erc_4626.vault_protocol.lagoon.vault import AutomatedSafe
from eth_defi.gas import apply_gas, estimate_gas_price
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.api import LIGHTER_MIN_MAINNET_USDC, LIGHTER_STATE_POLL_SECONDS
from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT, LIGHTER_ROUTE_PERP
from eth_defi.token import TokenDetails

logger = logging.getLogger(__name__)

#: Gas limit for an L1 ``withdraw`` priority request wrapped by the Lagoon module.
LIGHTER_L1_WITHDRAW_GAS_LIMIT = 500_000

#: Topic of ``ZkLighter.WithdrawPending(address indexed owner, uint16 assetIndex, uint128 amount)``.
#:
#: Our ABI file omits events, so the log is decoded by hand. Every
#: ``withdrawPendingBalance`` call emits it, whoever sends the transaction.
LIGHTER_WITHDRAW_PENDING_TOPIC = Web3.keccak(text="WithdrawPending(address,uint16,uint128)").to_0x_hex()

#: Blocks in one ``eth_getLogs`` request. A mainnet provider refused 1000 and
#: accepted 500 blocks on 2026-11-05.
LIGHTER_LOG_CHUNK_BLOCKS = 250


def broadcast_tx(
    web3: Web3,
    hot_wallet: HotWallet,
    bound_func: ContractFunction,
    description: str,
    gas_limit: int = 1_000_000,
    *,
    tx_observer: Callable[[str, int, str], None] | None = None,
) -> str:
    """Sign, broadcast and wait for one transaction.

    :param web3:
        Web3 connection.
    :param hot_wallet:
        Transaction signer.
    :param bound_func:
        Bound contract function to execute.
    :param description:
        Human-readable transaction description for logs.
    :param gas_limit:
        Transaction gas limit.
    :param tx_observer:
        Optional durable recorder called with hash, nonce and description before broadcast.
    :return:
        Confirmed transaction hash in hexadecimal form.
    """
    gas_price_suggestion = estimate_gas_price(web3)
    tx_params = apply_gas({"gas": gas_limit}, gas_price_suggestion)
    tx = hot_wallet.sign_bound_call_with_new_nonce(bound_func, tx_params=tx_params)
    if tx_observer is not None:
        tx_observer(Web3.to_hex(tx.hash), tx.nonce, description)
    logger.info("Broadcasting: %s", description)
    logger.info("Transaction hash: %s", tx.hash.hex())
    broadcast_and_wait_transactions_to_complete(web3, [tx])
    receipt = web3.eth.get_transaction_receipt(tx.hash)
    if receipt["status"] != 1:
        raise RuntimeError(f"Transaction failed: {description} ({tx.hash.hex()})")
    logger.info("Gas used: %s", receipt["gasUsed"])
    return Web3.to_hex(tx.hash)


def deposit_usdc_from_lagoon_safe_into_lighter(
    web3: Web3,
    hot_wallet: HotWallet,
    *,
    vault: AutomatedSafe,
    usdc: TokenDetails,
    deposit_usdc: Decimal,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
    tx_observer: Callable[[str, int, str], None] | None = None,
) -> str:
    """Approve and deposit Safe-owned USDC into Lighter through the guard.

    The caller must fund the Safe through Lagoon settlement, or bridge already
    accounted capital from the hub to a satellite Safe before this call.
    Direct USDC donations are not an accounted Lagoon subscription.

    :param web3:
        Web3 connection.
    :param hot_wallet:
        Whitelisted Lagoon asset-manager wallet.
    :param vault:
        Lagoon vault or satellite whose Safe owns the Lighter account.
    :param usdc:
        Configured native Ethereum USDC token details.
    :param deposit_usdc:
        Human-readable USDC amount to deposit.
    :param zk_lighter:
        Whitelisted ZkLighter L1 contract address.
    :param tx_observer:
        Optional hash/nonce recorder invoked before each broadcast.
    :return:
        Confirmed Lighter deposit transaction hash.
    """
    if deposit_usdc < LIGHTER_MIN_MAINNET_USDC:
        raise ValueError(f"Lighter Ethereum deposits have a {LIGHTER_MIN_MAINNET_USDC} USDC minimum, got {deposit_usdc}")

    zk_lighter = Web3.to_checksum_address(zk_lighter)
    safe = vault.safe_address
    zk = get_deployed_contract(web3, "lighter/ZkLighter.json", zk_lighter)
    asset_index = zk.functions.USDC_ASSET_INDEX().call()
    amount_raw = usdc.convert_to_raw(deposit_usdc)
    module = get_deployed_contract(web3, "safe-integration/TradingStrategyModuleV0.json", vault.trading_strategy_module_address)

    logger.info("Depositing %s USDC from Safe %s into Lighter %s", deposit_usdc, safe, zk_lighter)
    approve_data = usdc.contract.functions.approve(zk_lighter, amount_raw)._encode_transaction_data()
    broadcast_tx(
        web3,
        hot_wallet,
        module.functions.performCall(usdc.address, approve_data, 0),
        "Approve USDC to Lighter from Safe",
        tx_observer=tx_observer,
    )

    deposit_data = zk.functions.deposit(safe, asset_index, LIGHTER_ROUTE_PERP, amount_raw)._encode_transaction_data()
    tx_hash = broadcast_tx(
        web3,
        hot_wallet,
        module.functions.performCall(zk_lighter, deposit_data, 0),
        "Deposit USDC from Safe to Lighter",
        tx_observer=tx_observer,
    )
    logger.info("Safe USDC balance after Lighter deposit: %s", usdc.fetch_balance_of(safe))
    return tx_hash


def claim_usdc_to_lagoon_safe_from_lighter(
    web3: Web3,
    hot_wallet: HotWallet,
    *,
    vault: AutomatedSafe,
    usdc: TokenDetails,
    claimable_usdc: Decimal,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
    tx_observer: Callable[[str, int, str], None] | None = None,
) -> str:
    """Claim an already-claimable secure Lighter USDC withdrawal to the Safe.

    This helper deliberately does not request a withdrawal. The request is an
    L2 API-key operation or :py:func:`request_usdc_withdrawal_from_lighter_account`;
    this is the Safe-gated L1 egress step and can only name
    :attr:`AutomatedSafe.safe_address` as its receiver. Lighter's dynamic
    secure-withdrawal delay is informational only: claim only after the
    withdrawal is claimable, see :py:func:`wait_for_lighter_pending_balance`.
    A fast withdrawal is
    not an alternative for this flow because it needs the L1 account's EOA
    private key, whereas the account owner is a Safe contract.

    :param web3:
        Ethereum mainnet connection.
    :param hot_wallet:
        Asset-manager wallet authorised to call the Lagoon module.
    :param vault:
        Lagoon vault or satellite whose Safe receives the claimed USDC.
    :param usdc:
        Native Ethereum USDC token details.
    :param claimable_usdc:
        Amount from the matching Lighter ``claimable`` withdrawal-history row.
        Do not substitute the originally requested amount: Lighter may report
        a different final raw amount after its own precision/fee handling.
    :param zk_lighter:
        Whitelisted ZkLighter L1 contract address.
    :param tx_observer:
        Optional durable hash/nonce recorder called before broadcasting the claim.
    :return:
        Confirmed claim transaction hash.
    """
    if claimable_usdc <= 0:
        raise ValueError(f"Claimable Lighter USDC must be positive, got {claimable_usdc}")

    zk_lighter = Web3.to_checksum_address(zk_lighter)
    safe = Web3.to_checksum_address(vault.safe_address)
    zk = get_deployed_contract(web3, "lighter/ZkLighter.json", zk_lighter)
    asset_index = zk.functions.USDC_ASSET_INDEX().call()
    amount_raw = usdc.convert_to_raw(claimable_usdc)
    module = get_deployed_contract(
        web3,
        "safe-integration/TradingStrategyModuleV0.json",
        vault.trading_strategy_module_address,
    )
    claim_data = zk.functions.withdrawPendingBalance(
        safe,
        asset_index,
        amount_raw,
    )._encode_transaction_data()
    logger.info(
        "Claiming %s USDC from Lighter %s to Safe %s",
        claimable_usdc,
        zk_lighter,
        safe,
    )
    return broadcast_tx(
        web3,
        hot_wallet,
        module.functions.performCall(zk_lighter, claim_data, 0),
        "Claim Lighter USDC withdrawal to Safe",
        tx_observer=tx_observer,
    )


def fetch_lighter_pending_balance(
    web3: Web3,
    *,
    owner: HexAddress | str,
    usdc: TokenDetails,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
    block_identifier: int | str = "latest",
) -> Decimal:
    """Read the USDC an L1 owner can claim from Lighter.

    Lighter credits secure withdrawals from the master account and every
    sub-account to one pending balance keyed by the owner's master account.
    The claim is ``withdrawPendingBalance(owner, ...)``. See Lighter's
    `deposit, transfer and withdrawal documentation <https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals>`__.

    :param web3:
        Ethereum connection.
    :param owner:
        L1 owner, normally the Lagoon Safe.
    :param usdc:
        Native Ethereum USDC token details.
    :param zk_lighter:
        ZkLighter L1 contract address.
    :param block_identifier:
        Block to read.
    :return:
        Claimable USDC.
    """
    zk = get_deployed_contract(web3, "lighter/ZkLighter.json", Web3.to_checksum_address(zk_lighter))
    asset_index = zk.functions.USDC_ASSET_INDEX().call(block_identifier=block_identifier)
    raw = zk.functions.getPendingBalance(Web3.to_checksum_address(owner), asset_index).call(block_identifier=block_identifier)
    return usdc.convert_to_decimals(raw)


def request_usdc_withdrawal_from_lighter_account(
    web3: Web3,
    hot_wallet: HotWallet,
    *,
    vault: AutomatedSafe,
    usdc: TokenDetails,
    account_index: int,
    amount: Decimal,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
) -> str:
    """Request a secure withdrawal from any Safe-owned Lighter account onchain.

    ``ZkLighter.withdraw(accountIndex, assetIndex, routeType, baseAmount)``
    queues an L1 priority request. The contract binds the request to the
    caller's master account and the sequencer debits ``account_index`` only
    if it is that master or one of its sub-accounts. The proceeds join the
    owner's pending balance, which :py:func:`claim_usdc_to_lagoon_safe_from_lighter`
    claims to the Safe. No Lighter API key is involved, so this is how a vault
    drains a sub-account whose API key it does not hold.

    The guard already whitelists the ``withdraw`` selector for the configured
    ``ZkLighter`` and USDC asset index.

    :param web3:
        Ethereum connection.
    :param hot_wallet:
        Whitelisted Lagoon asset-manager wallet.
    :param vault:
        Lagoon vault or satellite whose Safe owns the Lighter accounts.
    :param usdc:
        Native Ethereum USDC token details.
    :param account_index:
        Master or sub-account index to debit.
    :param amount:
        USDC amount to withdraw.
    :param zk_lighter:
        Whitelisted ZkLighter L1 contract address.
    :return:
        Confirmed request transaction hash.
    """
    if amount < LIGHTER_MIN_MAINNET_USDC:
        raise ValueError(f"Lighter secure withdrawals have a {LIGHTER_MIN_MAINNET_USDC} USDC minimum, got {amount}")
    zk_lighter = Web3.to_checksum_address(zk_lighter)
    zk = get_deployed_contract(web3, "lighter/ZkLighter.json", zk_lighter)
    asset_index = zk.functions.USDC_ASSET_INDEX().call()
    # USDC has a Lighter tick size of one raw unit, so base amount equals raw USDC.
    amount_raw = usdc.convert_to_raw(amount)
    module = get_deployed_contract(web3, "safe-integration/TradingStrategyModuleV0.json", vault.trading_strategy_module_address)
    withdraw_data = zk.functions.withdraw(account_index, asset_index, LIGHTER_ROUTE_PERP, amount_raw)._encode_transaction_data()
    logger.info("Requesting %s USDC L1 withdrawal from Lighter account %d to Safe %s", amount, account_index, vault.safe_address)
    return broadcast_tx(
        web3,
        hot_wallet,
        module.functions.performCall(zk_lighter, withdraw_data, 0),
        f"Request Lighter withdrawal from account {account_index}",
        gas_limit=LIGHTER_L1_WITHDRAW_GAS_LIMIT,
    )


def fetch_lighter_withdrawal_baseline(
    web3: Web3,
    *,
    owner: HexAddress | str,
    usdc: TokenDetails,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
) -> tuple[int, Decimal]:
    """Record where a withdrawal starts, just before requesting it.

    :py:func:`wait_for_lighter_withdrawal` needs both values from one block.
    Otherwise a relayer claim landing between two reads would go uncounted, and
    the wait could run out.

    :param web3:
        Ethereum connection.
    :param owner:
        L1 owner, normally the Lagoon Safe.
    :param usdc:
        Native Ethereum USDC token details.
    :param zk_lighter:
        ZkLighter L1 contract address.
    :return:
        First block to read claims from, and the pending balance before it.
    """
    block = web3.eth.block_number
    return block + 1, fetch_lighter_pending_balance(web3, owner=owner, usdc=usdc, zk_lighter=zk_lighter, block_identifier=block)


def fetch_lighter_claimed_usdc(
    web3: Web3,
    *,
    owner: HexAddress | str,
    usdc: TokenDetails,
    from_block: int,
    to_block: int | None = None,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
) -> Decimal:
    """Read how much USDC Lighter has paid to an owner since a block.

    Counts every ``withdrawPendingBalance`` paying ``owner``, sent by us or by
    Lighter's relayer. The log is the only evidence that names the payout: the
    owner's USDC balance also moves with deposits and Lagoon settlement. The range
    is read in chunks, because providers limit ``eth_getLogs``, and stops at the
    chain head, which they refuse to pass.

    This is a bounded live read of one owner's recent blocks, not event discovery,
    so it uses the executor's JSON-RPC connection. Hypersync would add a dependency
    and an API key to a step that real withdrawals wait on.

    :param web3:
        Ethereum connection.
    :param owner:
        L1 owner, normally the Lagoon Safe.
    :param usdc:
        Native Ethereum USDC token details.
    :param from_block:
        First block to include.
    :param to_block:
        Last block to include, capped at the current head. Defaults to the head.
    :param zk_lighter:
        ZkLighter L1 contract address.
    :return:
        USDC paid to ``owner`` in the range.
    """
    zk_lighter = Web3.to_checksum_address(zk_lighter)
    zk = get_deployed_contract(web3, "lighter/ZkLighter.json", zk_lighter)
    asset_index = zk.functions.USDC_ASSET_INDEX().call()
    owner_topic = "0x" + bytes.fromhex(Web3.to_checksum_address(owner)[2:]).rjust(32, b"\0").hex()
    head = web3.eth.block_number
    last_block = head if to_block is None else min(to_block, head)
    paid_raw = 0
    # TODO: Switch to Hypersync.
    for start in range(from_block, last_block + 1, LIGHTER_LOG_CHUNK_BLOCKS):
        logs = web3.eth.get_logs(
            {
                "address": zk_lighter,
                "topics": [LIGHTER_WITHDRAW_PENDING_TOPIC, owner_topic],
                "fromBlock": start,
                "toBlock": min(start + LIGHTER_LOG_CHUNK_BLOCKS - 1, last_block),
            }
        )
        for log in logs:
            data = bytes(log["data"])
            if int.from_bytes(data[:32], "big") == asset_index:
                paid_raw += int.from_bytes(data[32:64], "big")
    return usdc.convert_to_decimals(paid_raw)


def is_lighter_withdrawal_paid(
    web3: Web3,
    *,
    owner: HexAddress | str,
    usdc: TokenDetails,
    amount: Decimal,
    pending_before: Decimal,
    from_block: int,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
) -> bool:
    """Check whether a withdrawal has reached the owner without our claim.

    Use this before claiming, and after a claim reverts, because Lighter's relayer
    may have claimed first. Take both baseline values from
    :py:func:`fetch_lighter_withdrawal_baseline`. Any balance that was already
    pending is paid first, so it has to be covered too.

    :param web3:
        Ethereum connection.
    :param owner:
        L1 owner, normally the Lagoon Safe.
    :param usdc:
        Native Ethereum USDC token details.
    :param amount:
        Requested withdrawal.
    :param pending_before:
        Pending balance read just before the request.
    :param from_block:
        First block after that read.
    :param zk_lighter:
        ZkLighter L1 contract address.
    :return:
        ``True`` if claims since ``from_block`` cover the withdrawal.
    """
    claimed = fetch_lighter_claimed_usdc(web3, owner=owner, usdc=usdc, from_block=from_block, zk_lighter=zk_lighter)
    return claimed >= pending_before + amount


def wait_for_lighter_withdrawal(
    web3: Web3,
    *,
    owner: HexAddress | str,
    usdc: TokenDetails,
    amount: Decimal,
    pending_before: Decimal,
    from_block: int,
    timeout: float,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
    poll_seconds: float = LIGHTER_STATE_POLL_SECONDS,
) -> bool:
    """Wait until a withdrawal is claimable or has already been paid.

    Waiting for the pending balance alone can run forever, because Lighter's
    relayer may empty it between two polls. This also counts what was claimed since
    the request. Claims are read before the balance: a claim landing between the
    two reads then delays the result by one poll instead of counting twice.

    :param web3:
        Ethereum connection.
    :param owner:
        L1 owner, normally the Lagoon Safe.
    :param usdc:
        Native Ethereum USDC token details.
    :param amount:
        Requested withdrawal.
    :param pending_before:
        Pending balance read just before the request.
    :param from_block:
        First block after that read.
    :param timeout:
        Maximum seconds to wait.
    :param zk_lighter:
        ZkLighter L1 contract address.
    :param poll_seconds:
        Seconds between reads.
    :return:
        ``True`` if the withdrawal has already been paid and must not be claimed,
        ``False`` if it is waiting for a claim.
    :raises TimeoutError:
        If neither happens in time, which includes a withdrawal left partly claimed.
        The withdrawal may still arrive later.
    """
    deadline = time.monotonic() + timeout
    while True:
        claimed = fetch_lighter_claimed_usdc(web3, owner=owner, usdc=usdc, from_block=from_block, zk_lighter=zk_lighter)
        if claimed >= pending_before + amount:
            logger.info("Lighter withdrawal of %s USDC to %s was paid without our claim", amount, owner)
            return True
        pending = fetch_lighter_pending_balance(web3, owner=owner, usdc=usdc, zk_lighter=zk_lighter)
        # What remains of the older balance, plus all of this withdrawal. A partly
        # claimed withdrawal does not qualify: claiming it in full would revert.
        if pending >= max(pending_before - claimed, Decimal(0)) + amount:
            logger.info("Lighter withdrawal of %s USDC to %s is claimable", amount, owner)
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Lighter withdrawal of {amount} USDC to {owner} did not arrive within {timeout} seconds")
        logger.info("Waiting for Lighter withdrawal of %s USDC, claimed %s, pending %s, %.0f seconds remaining", amount, claimed, pending, remaining)
        time.sleep(min(poll_seconds, remaining))


def wait_for_lighter_pending_balance(
    web3: Web3,
    *,
    owner: HexAddress | str,
    usdc: TokenDetails,
    minimum: Decimal,
    timeout: float,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
    poll_seconds: float = LIGHTER_STATE_POLL_SECONDS,
) -> Decimal:
    """Wait until the owner's claimable Lighter balance reaches ``minimum``.

    An L1 ``withdraw`` request becomes claimable when Lighter verifies the
    batch containing it, after its dynamic secure-withdrawal delay; about 29
    minutes for a mainnet sub-account withdrawal on 2026-10-26. This polls the
    contract only, so it needs no API key.

    Lighter's relayer can claim first, in which case this times out with the money
    already paid. Use it only where nothing else can claim, such as on a fork.
    Otherwise use :py:func:`wait_for_lighter_withdrawal`.

    :param web3:
        Ethereum connection.
    :param owner:
        L1 owner, normally the Lagoon Safe.
    :param usdc:
        Native Ethereum USDC token details.
    :param minimum:
        Claimable balance to wait for.
    :param timeout:
        Maximum seconds to wait.
    :param zk_lighter:
        ZkLighter L1 contract address.
    :param poll_seconds:
        Seconds between reads.
    :return:
        Observed claimable USDC, at least ``minimum``.
    """
    deadline = time.monotonic() + timeout
    while True:
        pending = fetch_lighter_pending_balance(web3, owner=owner, usdc=usdc, zk_lighter=zk_lighter)
        if pending >= minimum:
            logger.info("Lighter pending balance for %s reached %s USDC", owner, pending)
            return pending
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Lighter pending balance for {owner} stayed at {pending} USDC, below {minimum}, for {timeout} seconds")
        logger.info("Waiting for Lighter pending balance %s / %s USDC, %.0f seconds remaining", pending, minimum, remaining)
        time.sleep(min(poll_seconds, remaining))
