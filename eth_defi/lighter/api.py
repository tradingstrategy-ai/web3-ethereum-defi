"""Lighter API helpers for manual trading workflows.

This module follows the same ``api.py`` / ``session.py`` split as other
exchange-style integrations in :mod:`eth_defi`. It wraps the optional
``lighter-python`` SDK for scripts and manual integration tests. It covers
account polling, API-key registration, small trade sizing and simple
market-order round trips.

Authoritative documentation:

- Lighter API keys: https://apidocs.lighter.xyz/docs/api-keys
- Deposits and withdrawals:
  https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any, Callable

from safe_eth.safe import Safe
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.constants import LIGHTER_API_URL
from eth_defi.lighter.pubkey import MIN_API_KEY_INDEX, build_change_pubkey_safe_tx, validate_lighter_pubkey
from eth_defi.safe.execute import execute_safe_tx

logger = logging.getLogger(__name__)

#: Ethereum deposits and secure withdrawals have a documented 1 USDC minimum.
LIGHTER_MIN_MAINNET_USDC = Decimal("1")

#: ETH perpetual market index in the official Lighter SDK examples.
LIGHTER_ETH_MARKET_INDEX = 0

#: Seconds to wait after an L2 transaction before polling account state again.
LIGHTER_STATE_POLL_SECONDS = 15


@dataclass(slots=True)
class LighterTradeAmounts:
    """USDC and ETH sizes used by a Lighter manual trade.

    :param deposit_usdc:
        Suggested USDC deposit amount.
    :param position_usdc:
        Effective ETH long notional in USDC.
    :param base_amount:
        Lighter integer base amount for the ETH market order.
    :param max_buy_price:
        Worst acceptable buy price in Lighter integer price units.
    :param min_quote_amount:
        ETH market minimum quote amount, as reported by Lighter.
    :param min_base_amount:
        ETH market minimum base amount, as reported by Lighter.
    """

    deposit_usdc: Decimal
    position_usdc: Decimal
    base_amount: int
    max_buy_price: int
    min_quote_amount: Decimal
    min_base_amount: Decimal


def import_lighter() -> Any:
    """Import the optional Lighter SDK with an actionable error.

    :return:
        Imported ``lighter`` module.
    """
    try:
        import lighter  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as e:
        msg = "The full mainnet Lighter manual test needs the optional Lighter SDK. Install it with `poetry install -E lighter`, or use the full extras install command from pyproject.toml."
        raise ImportError(msg) from e
    return lighter


def ceil_decimal(value: Decimal, step: Decimal) -> Decimal:
    """Round a decimal up to the next step.

    :param value:
        Value to round.
    :param step:
        Rounding step.
    :return:
        Rounded value.
    """
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


async def wait_for_lighter_account(lighter: Any, safe_address: str, timeout: int = 900) -> int:
    """Wait until Lighter API exposes an account for an L1 owner.

    :param lighter:
        Imported Lighter SDK module.
    :param safe_address:
        L1 owner address, usually the Lagoon Safe.
    :param timeout:
        Maximum wait in seconds.
    :return:
        Lighter account index.
    """
    api_client = lighter.ApiClient(configuration=lighter.Configuration(host=LIGHTER_API_URL))
    account_api = lighter.AccountApi(api_client)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                response = await account_api.accounts_by_l1_address(l1_address=safe_address)
                if response.sub_accounts:
                    account = min(response.sub_accounts, key=lambda item: int(item.index))
                    logger.info(f"  Lighter account index: {account.index}")
                    return int(account.index)
            except lighter.ApiException as e:
                message = getattr(getattr(e, "data", None), "message", str(e))
                logger.info("Lighter account not visible yet for %s: %s", safe_address, message)

            if time.monotonic() >= deadline:
                raise TimeoutError(f"Lighter did not expose an account for {safe_address} within {timeout} seconds")
            logger.info(f"  Waiting for Lighter account creation ({LIGHTER_STATE_POLL_SECONDS}s)...")
            await asyncio.sleep(LIGHTER_STATE_POLL_SECONDS)
    finally:
        await api_client.close()


async def fetch_lighter_account(lighter: Any, account_index: int) -> Any:
    """Fetch a Lighter account by index.

    :param lighter:
        Imported Lighter SDK module.
    :param account_index:
        Lighter account index.
    :return:
        Detailed account model.
    """
    api_client = lighter.ApiClient(configuration=lighter.Configuration(host=LIGHTER_API_URL))
    try:
        return await lighter.AccountApi(api_client).account(by="index", value=str(account_index))
    finally:
        await api_client.close()


def unwrap_lighter_account(account: Any) -> Any | None:
    """Unwrap a Lighter account API response.

    :param account:
        Lighter account response. The SDK returns either a ``DetailedAccounts``
        wrapper with an ``accounts`` list or the account object itself.
    :return:
        The first account object, or ``None`` if the response is empty.
    """
    if hasattr(account, "accounts"):
        return account.accounts[0] if account.accounts else None
    return account


def get_eth_position(account: Any, market_index: int = LIGHTER_ETH_MARKET_INDEX) -> Decimal:
    """Read a signed ETH position size from a Lighter account response.

    :param account:
        Lighter detailed account model.
    :param market_index:
        Lighter market index.
    :return:
        Signed ETH position amount.
    """
    account = unwrap_lighter_account(account)
    if account is None:
        return Decimal(0)

    for position in account.positions:
        if int(position.market_id) == market_index:
            size = Decimal(str(position.position))
            return size if int(position.sign) >= 0 else -size
    return Decimal(0)


def get_lighter_available_balance(account: Any) -> Decimal:
    """Read the available USDC balance from a Lighter account response.

    :param account:
        Lighter account response.
    :return:
        Available USDC collateral.
    """
    account = unwrap_lighter_account(account)
    if account is None:
        return Decimal(0)
    return Decimal(str(account.available_balance))


def get_lighter_collateral(account: Any) -> Decimal:
    """Read the total USDC collateral from a Lighter account response.

    :param account:
        Lighter account response.
    :return:
        Total USDC collateral.
    """
    account = unwrap_lighter_account(account)
    if account is None:
        return Decimal(0)
    return Decimal(str(account.collateral))


async def wait_for_lighter_collateral(
    lighter: Any,
    account_index: int,
    expected_usdc: Decimal,
    timeout: int = 900,
) -> Decimal:
    """Wait until the Lighter account shows deposited collateral.

    L1 deposits are asynchronous. The Ethereum transaction can be mined before
    Lighter's API reflects the credited USDC balance, so callers must wait
    before registering a trading key and opening a position.

    :param lighter:
        Imported Lighter SDK module.
    :param account_index:
        Lighter account index.
    :param expected_usdc:
        Expected deposited USDC amount.
    :param timeout:
        Maximum wait in seconds.
    :return:
        Observed collateral.
    """
    deadline = time.monotonic() + timeout
    acceptable_shortfall = Decimal("0.000010")
    while True:
        account = await fetch_lighter_account(lighter, account_index)
        collateral = get_lighter_collateral(account)
        available = get_lighter_available_balance(account)
        if collateral >= expected_usdc - acceptable_shortfall:
            logger.info(f"  Lighter collateral credited: {collateral} USDC, available {available} USDC")
            return collateral
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Lighter collateral did not reach {expected_usdc} USDC within {timeout} seconds; current collateral {collateral}, available {available}")
        logger.info(f"  Waiting for Lighter collateral credit; collateral {collateral} USDC, available {available} USDC")
        await asyncio.sleep(LIGHTER_STATE_POLL_SECONDS)


def sdk_pubkey_to_bytes(public_key: str) -> bytes:
    """Convert a Lighter SDK public-key string to on-chain bytes.

    :param public_key:
        SDK public-key string, ``0x`` plus 40 bytes.
    :return:
        Raw public-key bytes for ``changePubKey``.
    """
    pubkey = bytes.fromhex(public_key.removeprefix("0x"))
    validate_lighter_pubkey(pubkey)
    return pubkey


async def register_lighter_api_key(  # noqa: PLR0917
    lighter: Any,
    web3: Web3,
    safe: Safe,
    hot_wallet: HotWallet,
    account_index: int,
    api_key_index: int,
) -> str:
    """Generate and register a Lighter API key through a Safe.

    :param lighter:
        Imported Lighter SDK module.
    :param web3:
        Web3 connection.
    :param safe:
        Safe that owns the Lighter account.
    :param hot_wallet:
        1-of-1 Safe owner.
    :param account_index:
        Lighter account index.
    :param api_key_index:
        Lighter API-key slot.
    :return:
        SDK API private key.
    """
    if api_key_index < MIN_API_KEY_INDEX:
        raise ValueError(f"Use API key index {MIN_API_KEY_INDEX} or higher; lower indices are reserved by Lighter")

    private_key, public_key, err = lighter.create_api_key()
    if err is not None:
        raise RuntimeError(f"Could not create Lighter API key: {err}")

    pubkey = sdk_pubkey_to_bytes(public_key)
    logger.info(f"\nRegistering Lighter API key index {api_key_index} via Safe changePubKey...")
    gas_price = max(web3.eth.gas_price * 3, 2_000_000_000)
    safe_tx = build_change_pubkey_safe_tx(web3, safe, account_index, api_key_index, pubkey)
    safe_tx.sign(hot_wallet.private_key.hex())
    tx_hash, tx = execute_safe_tx(
        safe_tx,
        tx_sender_private_key=hot_wallet.private_key.hex(),
        tx_gas_price=gas_price,
        hot_wallet=hot_wallet,
    )
    logger.info(f"  changePubKey tx: {tx_hash.hex()} (nonce {tx['nonce']}, gas price {tx['gasPrice']})")
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if receipt["status"] != 1:
        raise RuntimeError(f"Lighter changePubKey transaction failed: {tx_hash.hex()}")

    client = lighter.SignerClient(
        url=LIGHTER_API_URL,
        account_index=account_index,
        api_private_keys={api_key_index: private_key},
    )
    try:
        deadline = time.monotonic() + 300
        while True:
            err = client.check_client()
            if err is None:
                logger.info("  API key is active on Lighter")
                return private_key
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Registered API key was not accepted by Lighter within 300 seconds: {err}")
            logger.info(f"  Waiting for API key activation ({LIGHTER_STATE_POLL_SECONDS}s): {err}")
            await asyncio.sleep(LIGHTER_STATE_POLL_SECONDS)
    finally:
        await client.close()


async def resolve_eth_trade_amounts(
    lighter: Any,
    deposit_usdc: Decimal | None = None,
    position_usdc: Decimal | None = None,
) -> LighterTradeAmounts:
    """Resolve Lighter ETH market minimums and choose manual-test sizes.

    :param lighter:
        Imported Lighter SDK module.
    :param deposit_usdc:
        Optional explicit deposit amount.
    :param position_usdc:
        Optional explicit position notional.
    :return:
        Trade amount configuration.
    """
    api_client = lighter.ApiClient(configuration=lighter.Configuration(host=LIGHTER_API_URL))
    try:
        details = await lighter.OrderApi(api_client).order_book_details(market_id=LIGHTER_ETH_MARKET_INDEX)
        eth_market = details.order_book_details[0]
        min_quote_amount = Decimal(str(eth_market.min_quote_amount))
        min_base_amount = Decimal(str(eth_market.min_base_amount))
        last_price = Decimal(str(eth_market.last_trade_price))
        size_decimals = int(eth_market.size_decimals)

        default_position_usdc = max(LIGHTER_MIN_MAINNET_USDC, min_quote_amount) + Decimal("1")
        resolved_position_usdc = position_usdc if position_usdc is not None else default_position_usdc
        if resolved_position_usdc < min_quote_amount:
            raise ValueError(f"position_usdc={resolved_position_usdc} is below Lighter ETH market min_quote_amount={min_quote_amount}")

        eth_size = max(min_base_amount, resolved_position_usdc / last_price)
        eth_size = ceil_decimal(eth_size, Decimal(1) / Decimal(10**size_decimals))
        base_amount = int(eth_size * Decimal(10**size_decimals))
        max_buy_price = int((last_price * Decimal("1.05") * Decimal(100)).to_integral_value(rounding=ROUND_CEILING))
        effective_position_usdc = eth_size * last_price

        default_deposit_usdc = max(LIGHTER_MIN_MAINNET_USDC, effective_position_usdc + Decimal("5"))
        resolved_deposit_usdc = deposit_usdc if deposit_usdc is not None else default_deposit_usdc
        if resolved_deposit_usdc < LIGHTER_MIN_MAINNET_USDC:
            raise ValueError(f"deposit_usdc={resolved_deposit_usdc} is below Lighter's {LIGHTER_MIN_MAINNET_USDC} USDC deposit minimum")
        if resolved_deposit_usdc <= effective_position_usdc:
            raise ValueError(f"deposit_usdc={resolved_deposit_usdc} must be larger than the test position notional {effective_position_usdc}")

        logger.info("\nLighter ETH market minimums:")
        logger.info(f"  min_quote_amount: {min_quote_amount} USDC")
        logger.info(f"  min_base_amount:  {min_base_amount} ETH")
        logger.info(f"  last price:       {last_price} USDC/ETH")
        logger.info(f"  chosen deposit:   {resolved_deposit_usdc} USDC")
        logger.info(f"  chosen position:  {effective_position_usdc:.6f} USDC ({eth_size} ETH)")

        return LighterTradeAmounts(
            deposit_usdc=resolved_deposit_usdc,
            position_usdc=effective_position_usdc,
            base_amount=base_amount,
            max_buy_price=max_buy_price,
            min_quote_amount=min_quote_amount,
            min_base_amount=min_base_amount,
        )
    finally:
        await api_client.close()


async def wait_for_eth_position(
    lighter: Any,
    account_index: int,
    predicate: Callable[[Decimal], bool],
    description: str,
    timeout: int = 300,
) -> Decimal:
    """Wait until the ETH position matches a predicate.

    :param lighter:
        Imported Lighter SDK module.
    :param account_index:
        Lighter account index.
    :param predicate:
        Function receiving the signed position.
    :param description:
        Human-readable wait target.
    :param timeout:
        Maximum wait in seconds.
    :return:
        Matching position.
    """
    deadline = time.monotonic() + timeout
    while True:
        account = await fetch_lighter_account(lighter, account_index)
        position = get_eth_position(account)
        if predicate(position):
            logger.info(f"  ETH position {description}: {position}")
            return position
        if time.monotonic() >= deadline:
            raise TimeoutError(f"ETH position did not become {description} within {timeout} seconds; current position {position}")
        logger.info(f"  Waiting for ETH position to become {description}; current {position}")
        await asyncio.sleep(LIGHTER_STATE_POLL_SECONDS)


async def trade_eth_roundtrip(
    lighter: Any,
    account_index: int,
    api_private_key: str,
    api_key_index: int,
    amounts: LighterTradeAmounts,
) -> None:
    """Open and close an ETH long on Lighter.

    :param lighter:
        Imported Lighter SDK module.
    :param account_index:
        Lighter account index.
    :param api_private_key:
        SDK API private key.
    :param api_key_index:
        Lighter API-key slot.
    :param amounts:
        Trade sizing information.
    """
    client = lighter.SignerClient(
        url=LIGHTER_API_URL,
        account_index=account_index,
        api_private_keys={api_key_index: api_private_key},
    )
    try:
        err = client.check_client()
        if err is not None:
            raise RuntimeError(f"Lighter API key check failed: {err}")

        logger.info("\nOpening ETH long on Lighter...")
        open_tx, open_response, err = await client.create_market_order(
            market_index=LIGHTER_ETH_MARKET_INDEX,
            client_order_index=int(time.time()),
            base_amount=amounts.base_amount,
            avg_execution_price=amounts.max_buy_price,
            is_ask=False,
            api_key_index=api_key_index,
        )
        if err is not None:
            raise RuntimeError(f"Opening ETH long failed: {err}")
        logger.info(f"  Open order tx: {open_tx}")
        logger.info(f"  Open response: {open_response}")

        opened_position = await wait_for_eth_position(lighter, account_index, lambda position: position > 0, "open")

        logger.info("\nClosing ETH long on Lighter...")
        close_tx, close_response, err = await client.create_market_order(
            market_index=LIGHTER_ETH_MARKET_INDEX,
            client_order_index=int(time.time()) + 1,
            base_amount=amounts.base_amount,
            avg_execution_price=1,
            is_ask=True,
            reduce_only=True,
            api_key_index=api_key_index,
        )
        if err is not None:
            raise RuntimeError(f"Closing ETH long failed: {err}")
        logger.info(f"  Close order tx: {close_tx}")
        logger.info(f"  Close response: {close_response}")

        await wait_for_eth_position(lighter, account_index, lambda position: abs(position) < max(opened_position / Decimal(1000), Decimal("0.000001")), "closed")
    finally:
        await client.close()


async def withdraw_from_lighter(
    lighter: Any,
    account_index: int,
    api_private_key: str,
    api_key_index: int,
    withdraw_usdc: Decimal,
) -> None:
    """Request a secure USDC withdrawal from Lighter.

    :param lighter:
        Imported Lighter SDK module.
    :param account_index:
        Lighter account index.
    :param api_private_key:
        SDK API private key.
    :param api_key_index:
        Lighter API-key slot.
    :param withdraw_usdc:
        Human-readable USDC withdrawal amount.
    """
    if withdraw_usdc < LIGHTER_MIN_MAINNET_USDC:
        raise ValueError(f"Lighter secure withdrawals have a {LIGHTER_MIN_MAINNET_USDC} USDC minimum, got {withdraw_usdc}")

    client = lighter.SignerClient(
        url=LIGHTER_API_URL,
        account_index=account_index,
        api_private_keys={api_key_index: api_private_key},
    )
    try:
        logger.info(f"\nRequesting Lighter secure withdrawal of {withdraw_usdc} USDC...")
        withdraw_tx, response, err = await client.withdraw(
            asset_id=client.ASSET_ID_USDC,
            route_type=client.ROUTE_PERP,
            amount=float(withdraw_usdc),
            api_key_index=api_key_index,
        )
        if err is not None:
            raise RuntimeError(f"Lighter withdrawal request failed: {err}")
        logger.info(f"  Withdraw tx: {withdraw_tx}")
        logger.info(f"  Withdraw response: {response}")
    finally:
        await client.close()
