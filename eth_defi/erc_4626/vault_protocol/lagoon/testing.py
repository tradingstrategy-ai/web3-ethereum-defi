"""Lagoon unit test helpers."""

import logging
from decimal import Decimal

from web3 import Web3

from eth_typing import HexAddress

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.event_reader.conversion import convert_uint256_string_to_int, convert_uin256_to_bytes
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.hotwallet import HotWallet
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)


def fund_lagoon_vault(
    web3: Web3,
    vault_address: HexAddress,
    asset_manager: HexAddress,
    test_account_with_balance: HexAddress,
    trading_strategy_module_address: HexAddress,
    amount=Decimal(500),
    nav=Decimal(0),
    hot_wallet: HotWallet | None = None,
):
    """Deposit tokens into a Lagoon vault so the Safe holds funds.

    Supports two transaction signing modes:

    - **Anvil mode** (default): uses ``.transact({"from": ...})`` for unlocked
      accounts on Anvil forks.  This is the mode used by pytest fixtures.
    - **HotWallet mode**: when *hot_wallet* is provided, signs and broadcasts
      each transaction via :py:meth:`HotWallet.transact_and_broadcast_with_contract`.
      Use this for real deployments and scripts.

    Example (Anvil mode — pytest)::

        fund_lagoon_vault(
            web3,
            vault.address,
            asset_manager,
            depositor,
            module.address,
            amount=Decimal(500),
        )

    Example (HotWallet mode — deploy script)::

        deployer = HotWallet.from_private_key(os.environ["PRIVATE_KEY"])
        deployer.sync_nonce(web3)
        fund_lagoon_vault(
            web3,
            vault.address,
            deployer.address,
            deployer.address,
            module.address,
            amount=Decimal(2),
            hot_wallet=deployer,
        )

    :param web3:
        Web3 connection to the chain where the vault lives.

    :param vault_address:
        On-chain address of the Lagoon vault.

    :param asset_manager:
        Address that has the ``updateNewTotalAssets`` + ``settleDeposit``
        role on the vault.

    :param test_account_with_balance:
        Address that holds the denomination token and will deposit.

    :param trading_strategy_module_address:
        Address of the ``TradingStrategyModuleV0`` guard contract.

    :param amount:
        Human-readable amount to deposit (e.g. ``Decimal(500)`` for 500 USDC).

    :param nav:
        NAV value to post during settlement (usually ``Decimal(0)`` for
        initial funding).

    :param hot_wallet:
        When provided, all transactions are signed with this wallet
        instead of using Anvil's unlocked-account shortcut.
    """

    assert vault_address.startswith("0x"), f"Vault address should be an address, got: {vault_address}"
    assert asset_manager.startswith("0x"), f"asset_manager should be an address, got: {asset_manager}"
    assert test_account_with_balance.startswith("0x"), f"test_account_with_balance should be an address, got: {test_account_with_balance}"
    assert trading_strategy_module_address.startswith("0x"), f"trading_strategy_module_address should be an address, got: {trading_strategy_module_address}"

    vault = create_vault_instance(
        web3,
        vault_address,
        features={ERC4626Feature.lagoon_like},
        default_block_identifier="latest",
        require_denomination_token=True,
    )
    assert isinstance(vault, LagoonVault), f"Vault is not a Lagoon vault: {vault}"

    vault.trading_strategy_module_address = trading_strategy_module_address

    denomination_token = vault.denomination_token
    depositor_balance = denomination_token.fetch_balance_of(test_account_with_balance)
    assert depositor_balance >= amount, f"Depositor {test_account_with_balance} has {depositor_balance} {denomination_token.symbol} (token {denomination_token.address}) but needs {amount}. Vault denomination token: {denomination_token.symbol} at {denomination_token.address}"
    raw_amount = denomination_token.convert_to_raw(amount)

    def _send(bound_func, description: str, gas: int = 1_000_000):
        """Sign and broadcast a single transaction."""
        if hot_wallet is not None:
            logger.info("Broadcasting (HotWallet): %s", description)
            tx_hash = hot_wallet.transact_and_broadcast_with_contract(bound_func, gas_limit=gas)
        else:
            tx_hash = bound_func.transact({"from": test_account_with_balance, "gas": gas})
        assert_transaction_success_with_explanation(web3, tx_hash)

    def _send_as_manager(bound_func, description: str, gas: int = 1_000_000):
        """Sign and broadcast as asset manager."""
        if hot_wallet is not None:
            logger.info("Broadcasting (HotWallet): %s", description)
            tx_hash = hot_wallet.transact_and_broadcast_with_contract(bound_func, gas_limit=gas)
        else:
            tx_hash = bound_func.transact({"from": asset_manager, "gas": gas})
        assert_transaction_success_with_explanation(web3, tx_hash)

    # 1. Post initial valuation (needed for fresh vaults)
    _send_as_manager(vault.post_new_valuation(Decimal(0)), "Post initial valuation")

    # 2. Approve denomination token for vault deposit
    _send(denomination_token.approve(vault.address, amount), f"Approve {amount} for vault deposit")

    # 3. Put to deposit queue
    deposit_func = vault.request_deposit(test_account_with_balance, raw_amount)
    _send(deposit_func, f"Request {amount} deposit to vault")

    # 4. Update NAV and settle
    _send_as_manager(vault.post_new_valuation(nav), "Post valuation for settlement")
    _send_as_manager(vault.settle_via_trading_strategy_module(nav), "Settle vault deposits")

    balance = vault.underlying_token.fetch_balance_of(vault.safe_address)
    logger.info("Vault funded: Safe balance is %s %s", balance, vault.underlying_token.symbol)


def force_lagoon_settle(
    vault: LagoonVault,
    asset_manager: HexAddress,
    raw_nav: int = None,
    gas_limit: int = 15_000_000,
):
    """Force settling of the Lagoon vault.

    - Used in the testing to move the vault to the next epoch

    :param asset_manager:
        Spoofed account in Anvil
    """

    assert asset_manager.startswith("0x"), f"asset_manager should be an address, got: {asset_manager}"

    web3 = vault.web3
    balance = web3.eth.get_balance(asset_manager)

    # Top up if needed
    if balance < 10**18:
        tx_hash = web3.eth.send_transaction({"to": asset_manager, "from": web3.eth.accounts[0], "value": 5 * 10**18})
        assert_transaction_success_with_explanation(web3, tx_hash)

    if not raw_nav:
        nav = vault.fetch_nav()
        raw_nav = vault.denomination_token.convert_to_raw(nav)

    tx_hash = vault.vault_contract.functions.updateNewTotalAssets(raw_nav).transact({"from": asset_manager, "gas": gas_limit})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Lagoon security fix
    #     function settleDeposit(uint256 _newTotalAssets) public virtual;
    call = EncodedCall.from_keccak_signature(
        address=vault.address,
        function="settleDeposit()",
        signature=Web3.keccak(text="settleDeposit(uint256)")[0:4],
        data=convert_uin256_to_bytes(raw_nav),
        extra_data=None,
    )
    tx_data = call.transact(
        from_=asset_manager,
        gas_limit=gas_limit,
    )
    tx_hash = web3.eth.send_transaction(tx_data)
    assert_transaction_success_with_explanation(web3, tx_hash)
