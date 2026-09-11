"""Production funding helper for a new Lagoon vault.

This is the normal Lagoon subscription lifecycle used by the Lighter account
activation ceremony: request a deposit, post valuation, settle and claim the
resulting shares. It intentionally does not transfer tokens directly to the
Safe because that would bypass Lagoon accounting.

Authoritative Lagoon documentation:

- Deposit lifecycle: https://docs.lagoon.finance/vault/deposit-and-withdraw-flows
- Valuation and settlement:
  https://docs.lagoon.finance/vault/how-to/update-the-vault-valuation-and-settle-requests
"""

import logging
import time
from decimal import Decimal

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.receipt import wait_for_transaction_receipt_robust
from eth_defi.token import TokenDiskCache
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)


def fund_lagoon_vault(  # noqa: PLR0917
    web3: Web3,
    vault_address: HexAddress,
    asset_manager: HexAddress,
    test_account_with_balance: HexAddress,
    trading_strategy_module_address: HexAddress,
    amount: Decimal = Decimal(500),
    nav: Decimal = Decimal(0),
    hot_wallet: HotWallet | None = None,
    token_cache: TokenDiskCache | None = None,
) -> None:
    """Fund a Lagoon Safe through the complete ERC-7540 subscription lifecycle.

    The asset manager posts the valuations and settles the subscription. The
    depositor approves and requests the deposit, then claims its Lagoon shares
    after settlement. ``HotWallet`` mode signs all calls for mainnet deployment;
    the unlocked-account fallback is retained for Anvil tests. A zero ``nav``
    identifies initial funding and causes the required first zero valuation to
    be posted; a non-zero ``nav`` assumes the vault has already been activated.

    :param web3:
        Web3 connection to the vault chain.
    :param vault_address:
        Onchain Lagoon vault address.
    :param asset_manager:
        Address allowed to post valuation and settle the vault.
    :param test_account_with_balance:
        Depositor holding the underlying token.
    :param trading_strategy_module_address:
        TradingStrategyModuleV0 address used for settlement.
    :param amount:
        Human-readable underlying-token subscription amount.
    :param nav:
        Human-readable NAV posted for settlement.
    :param hot_wallet:
        Optional signer for a real deployment.
    :param token_cache:
        Optional ERC-20 metadata cache.
    :return:
        ``None`` after settlement and share claim complete.
    """
    assert vault_address.startswith("0x"), f"Vault address should be an address, got: {vault_address}"
    assert asset_manager.startswith("0x"), f"asset_manager should be an address, got: {asset_manager}"
    assert test_account_with_balance.startswith("0x"), f"test_account_with_balance should be an address, got: {test_account_with_balance}"
    assert trading_strategy_module_address.startswith("0x"), f"trading_strategy_module_address should be an address, got: {trading_strategy_module_address}"

    if hot_wallet is not None:
        signer = Web3.to_checksum_address(hot_wallet.address)
        if any(Web3.to_checksum_address(address) != signer for address in (asset_manager, test_account_with_balance)):
            message = "HotWallet funding requires the signer to be both the Lagoon asset manager and depositor"
            raise ValueError(message)

    vault = create_vault_instance(
        web3,
        vault_address,
        features={ERC4626Feature.lagoon_like},
        default_block_identifier="latest",
        require_denomination_token=True,
        token_cache=token_cache,
    )
    assert isinstance(vault, LagoonVault), f"Vault is not a Lagoon vault: {vault}"
    vault.trading_strategy_module_address = trading_strategy_module_address

    denomination_token = vault.denomination_token
    depositor_balance = denomination_token.fetch_balance_of(test_account_with_balance)
    assert depositor_balance >= amount, f"Depositor {test_account_with_balance} has {depositor_balance} {denomination_token.symbol} but needs {amount}"
    raw_amount = denomination_token.convert_to_raw(amount)

    def send(bound_func: ContractFunction, sender: HexAddress, description: str, gas: int = 1_000_000) -> HexBytes:
        """Send one Lagoon lifecycle transaction.

        Use the configured ``HotWallet`` on live networks and the specified
        unlocked account in test environments.

        :param bound_func: Bound contract function to execute.
        :param sender: Unlocked sender used when no ``HotWallet`` was supplied.
        :param description: Human-readable operation included in logs.
        :param gas: Transaction gas limit.
        :return: Confirmed transaction hash.
        """
        if hot_wallet is not None:
            logger.info("Broadcasting Lagoon funding transaction: %s", description)
            tx_hash = hot_wallet.transact_and_broadcast_with_contract(bound_func, gas_limit=gas)
        else:
            tx_hash = bound_func.transact({"from": sender, "gas": gas})
        assert_transaction_success_with_explanation(web3, tx_hash)
        return tx_hash

    if nav == 0:
        send(vault.post_new_valuation(Decimal(0)), asset_manager, "Post initial valuation")
    approval_tx_hash = send(denomination_token.approve(vault.address, amount), test_account_with_balance, f"Approve {amount} for vault deposit")
    wait_for_transaction_receipt_robust(web3, approval_tx_hash)
    send(vault.request_deposit(test_account_with_balance, raw_amount), test_account_with_balance, f"Request {amount} deposit to vault")
    valuation_tx_hash = send(vault.post_new_valuation(nav), asset_manager, "Post valuation for settlement")
    # :py:meth:`ContractFunction.build_transaction` estimates the dependent
    # settlement through the fallback provider pool. Wait until every read
    # provider sees the valuation to avoid a stale ``WrongNewTotalAssets()``.
    wait_for_transaction_receipt_robust(web3, valuation_tx_hash, confirmation_block_count=0, confirmation_block_time=0)
    settle_tx_hash = send(vault.settle_via_trading_strategy_module(nav), asset_manager, "Settle vault deposits")
    wait_for_transaction_receipt_robust(web3, settle_tx_hash, confirmation_block_count=0, confirmation_block_time=0)

    claim_attempts = 12
    claim_retry_delay = 5
    claimable_raw_amount = 0
    for attempt in range(1, claim_attempts + 1):
        claimable_raw_amount = vault.vault_contract.functions.maxDeposit(test_account_with_balance).call()
        if claimable_raw_amount > 0:
            break
        logger.warning(
            "Lagoon settlement is not visible on the read RPC, retrying %d/%d in %d seconds",
            attempt,
            claim_attempts,
            claim_retry_delay,
        )
        time.sleep(claim_retry_delay)

    if claimable_raw_amount == 0:
        raise RuntimeError(f"Lagoon deposit settlement was mined, but maxDeposit({test_account_with_balance}) stayed 0 after {claim_attempts * claim_retry_delay} seconds")

    send(vault.finalise_deposit(test_account_with_balance, raw_amount=claimable_raw_amount), test_account_with_balance, f"Claim shares for {test_account_with_balance}")
    logger.info(
        "Lagoon funding complete: Safe balance %s %s, depositor shares %s",
        vault.underlying_token.fetch_balance_of(vault.safe_address),
        vault.underlying_token.symbol,
        vault.share_token.fetch_balance_of(test_account_with_balance),
    )
