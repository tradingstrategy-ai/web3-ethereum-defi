"""Deploy a Lagoon vault and trade an ETH perpetual using a Lighter API key.

This is a minimal, one-shot Ethereum mainnet tutorial. It:

1. deploys a Lagoon vault and Lighter guard;
2. makes the accounted 1 USDC deposit needed to activate the Lighter account;
3. generates and registers API key slot 4 (or ``LIGHTER_API_KEY_INDEX``)
   during deployment;
4. adds enough accounted collateral for a conservative small ETH position;
5. opens an ETH-USD perpetual long with the API key and closes the filled size;
6. reads the resulting Lighter account NAV.

The official Lighter SDK is intentionally not an ``eth-defi`` package
dependency. Install the GitHub revision used by this tutorial separately into
the same Python environment before running it. This revision is the latest
upstream ``main`` commit at the time of writing. Its setuptools metadata still
pins an old ``urllib3`` version that conflicts with ``eth-defi``, although the
SDK is compatible with the newer runtime. Install its missing transport
helper, then install the SDK itself without applying that stale transitive
pin::

    poetry run pip install aiohttp-retry
    poetry run pip install --force-reinstall --no-deps "lighter-sdk @ git+https://github.com/elliottech/lighter-python.git@fd4ee2530f78940cbed3dd80131d0fa66f74ad2a"

Installing it this way does not add it to ``pyproject.toml`` or Poetry's lock
file. The immutable Git revision keeps the tutorial reproducible; update it
deliberately when adopting a newer upstream SDK. The SDK supplies Lighter's
native order signing and nonce management; Lagoon deployment and account
activation remain implemented by ``eth-defi``. Until upstream fixes
``setup.py``, ``pip check`` will still report its declared urllib3 conflict;
this installation is therefore tutorial-only.

Lighter has no production-like testnet for this custody flow. The script
therefore spends real ETH for Ethereum mainnet gas and real USDC. It does not
withdraw funds, redeem Lagoon shares, resume a failed run, or perform automatic
recovery. If execution stops after opening the position, inspect and close the
position manually before reusing the account. Collateral remains in the
Safe-owned Lighter account after a successful run; recover it with the
governance-controlled two-phase L1 withdrawal flow documented in
``eth_defi/lighter/README-lighter-guard.md``.

Example::

    JSON_RPC_ETHEREUM="https://..." \
    LIGHTER_TEST_PRIVATE_KEY="0x..." \
    poetry run python scripts/lagoon/lagoon-lighter-trade-example.py

Environment variables
---------------------

``JSON_RPC_ETHEREUM``
    Ethereum mainnet RPC endpoint. Required.
``LIGHTER_TEST_PRIVATE_KEY``
    Funded deployer, Safe owner, Lagoon depositor and asset-manager key.
    Required. It needs deployment gas and enough native Ethereum USDC for the
    selected total collateral.
``LIGHTER_DEPOSIT_USDC``
    Optional total Lighter collateral. By default this is the conservatively
    sized ETH position notional plus a 5 USDC buffer.
``LIGHTER_POSITION_USDC``
    Optional target ETH perpetual notional. Defaults to the reported quote
    minimum plus 1 USDC. The reported base minimum may make the effective
    notional larger. Lighter documents these minimum fields for maker orders;
    this tutorial also uses them as a conservative floor for its IOC order.
``LIGHTER_MAX_SLIPPAGE``
    Optional maximum price slippage for both market orders. Defaults to 0.02
    (2%).
``LIGHTER_API_KEY_INDEX``
    Optional API-key slot. Defaults to 4. Slots 0 through 3 are reserved by
    Lighter's user interfaces.
``LIGHTER_DEPLOYMENT_REPORT``
    Optional secret-bearing deployment-report path. The default is a new file
    below ``~/.tradingstrategy/examples``. It is created with mode ``0600``.
``ETHERSCAN_API_KEY``
    Optional contract-verification key.

Authoritative Lighter documentation:

- API keys: https://apidocs.lighter.xyz/docs/api-keys
- Python SDK: https://github.com/elliottech/lighter-python
- Create orders: https://apidocs.lighter.xyz/docs/create-orders
"""

import asyncio
import importlib
import logging
import os
import time
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonAutomatedDeployment, LagoonDeploymentParameters, LighterAccountSetup, deploy_automated_lagoon_vault
from eth_defi.erc_4626.vault_protocol.lagoon.funding import fund_lagoon_vault
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.api import LIGHTER_MIN_MAINNET_USDC, wait_for_lighter_collateral
from eth_defi.lighter.constants import LIGHTER_API_URL, LIGHTER_ETHEREUM_DEPLOYMENT_CHAIN_ID, LIGHTER_USDC_ETHEREUM
from eth_defi.lighter.deployment import LighterDeployment
from eth_defi.lighter.lagoon import deposit_usdc_from_lagoon_safe_into_lighter
from eth_defi.lighter.pubkey import MIN_API_KEY_INDEX
from eth_defi.lighter.session import LighterSession, create_lighter_session
from eth_defi.lighter.valuation import fetch_lighter_account_by_index, fetch_lighter_total_equity
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

#: ETH-USD perpetual market index on Lighter mainnet.
LIGHTER_ETH_PERP_MARKET_INDEX = 0

#: Extra collateral above the effective minimum ETH position.
DEFAULT_COLLATERAL_BUFFER = Decimal("5")

#: Default maximum execution slippage for the round trip.
DEFAULT_MAX_SLIPPAGE = Decimal("0.02")

#: Public-account state polling interval.
LIGHTER_POSITION_POLL_SECONDS = 5


@dataclass(slots=True, frozen=True)
class EthTradePlan:
    """Resolved ETH perpetual order and collateral sizes.

    :param deposit_usdc:
        Total target Lighter collateral.
    :param position_usdc:
        Approximate ETH position notional at the reference price.
    :param base_amount:
        Integer base amount passed to the SDK signer.
    :param base_size:
        Human-readable ETH base size.
    :param size_decimals:
        Lighter integer base-amount scale.
    :param min_quote_amount:
        Reported ETH maker-order quote minimum.
    :param min_base_amount:
        Reported ETH maker-order base minimum.
    """

    deposit_usdc: Decimal
    position_usdc: Decimal
    base_amount: int
    base_size: Decimal
    size_decimals: int
    min_quote_amount: Decimal
    min_base_amount: Decimal


def require_env(name: str) -> str:
    """Read a required environment variable.

    :param name:
        Environment variable name.
    :return:
        Non-empty environment variable value.
    """
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value


def parse_decimal_env(name: str) -> Decimal | None:
    """Read an optional decimal environment variable.

    :param name:
        Environment variable name.
    :return:
        Parsed value, or ``None`` when unset.
    """
    value = os.environ.get(name)
    return Decimal(value) if value else None


def import_lighter_sdk() -> ModuleType:
    """Import the separately installed official Lighter SDK.

    :return:
        Imported ``lighter`` module.
    :raises ImportError:
        If the operator has not installed the tutorial-only dependency.
    """
    try:
        return importlib.import_module("lighter")
    except ImportError as error:
        message = "Install the separately pinned Lighter SDK as described in this script's module documentation"
        raise ImportError(message) from error


def calculate_eth_trade_plan(
    market: Any,
    deposit_usdc: Decimal | None = None,
    position_usdc: Decimal | None = None,
) -> EthTradePlan:
    """Resolve a conservative small ETH position from SDK market details.

    The SDK signs integer base amounts using ``size_decimals``. Order sizes are
    rounded up to ``supported_size_decimals``. Lighter documents the reported
    base and quote minimums for maker orders; the tutorial deliberately applies
    them to its IOC order as a conservative sizing floor as well.

    :param market:
        Lighter SDK ETH perpetual market-detail model.
    :param deposit_usdc:
        Optional total collateral override.
    :param position_usdc:
        Optional target notional override.
    :return:
        Validated order and collateral plan.
    """
    if str(market.symbol).upper() != "ETH" or str(market.market_type).lower() != "perp":
        raise ValueError(f"Market {market.market_id} is not the ETH perpetual market")

    min_quote_amount = Decimal(str(market.min_quote_amount))
    min_base_amount = Decimal(str(market.min_base_amount))
    reference_price = Decimal(str(market.last_trade_price))
    size_decimals = int(market.size_decimals)
    supported_size_decimals = int(market.supported_size_decimals)
    if reference_price <= 0:
        raise ValueError(f"Invalid ETH reference price: {reference_price}")
    if supported_size_decimals > size_decimals:
        raise ValueError(f"ETH supported_size_decimals {supported_size_decimals} exceeds size_decimals {size_decimals}")

    conservative_minimum = max(min_quote_amount, min_base_amount * reference_price)
    if position_usdc is not None and position_usdc < conservative_minimum:
        raise ValueError(f"LIGHTER_POSITION_USDC={position_usdc} is below the tutorial's conservative sizing floor {conservative_minimum}")

    target_notional = position_usdc if position_usdc is not None else min_quote_amount + Decimal("1")
    base_size = max(min_base_amount, target_notional / reference_price)
    size_step = Decimal(1).scaleb(-supported_size_decimals)
    base_size = (base_size / size_step).to_integral_value(rounding=ROUND_CEILING) * size_step
    base_amount = int(base_size * Decimal(10**size_decimals))
    effective_notional = base_size * reference_price
    if effective_notional < min_quote_amount:
        raise ValueError(f"Resolved ETH position {effective_notional} USDC is below the conservative quote floor {min_quote_amount} USDC")

    resolved_deposit = deposit_usdc if deposit_usdc is not None else effective_notional + DEFAULT_COLLATERAL_BUFFER
    if resolved_deposit < LIGHTER_MIN_MAINNET_USDC:
        raise ValueError(f"Lighter collateral must be at least {LIGHTER_MIN_MAINNET_USDC} USDC")
    if resolved_deposit <= effective_notional:
        raise ValueError(f"LIGHTER_DEPOSIT_USDC={resolved_deposit} must exceed the effective ETH notional {effective_notional}")

    return EthTradePlan(
        deposit_usdc=resolved_deposit,
        position_usdc=effective_notional,
        base_amount=base_amount,
        base_size=base_size,
        size_decimals=size_decimals,
        min_quote_amount=min_quote_amount,
        min_base_amount=min_base_amount,
    )


async def fetch_eth_trade_plan(
    lighter: ModuleType,
    deposit_usdc: Decimal | None,
    position_usdc: Decimal | None,
) -> EthTradePlan:
    """Fetch live ETH market details through the SDK and build a trade plan.

    :param lighter:
        Official Lighter SDK module.
    :param deposit_usdc:
        Optional total collateral override.
    :param position_usdc:
        Optional target notional override.
    :return:
        Resolved trade plan.
    """
    api_client = lighter.ApiClient(configuration=lighter.Configuration(host=LIGHTER_API_URL))
    try:
        details = await lighter.OrderApi(api_client).order_book_details(market_id=LIGHTER_ETH_PERP_MARKET_INDEX)
        markets = details.order_book_details
        if len(markets) != 1:
            raise RuntimeError(f"Expected one ETH market detail, received {len(markets)}")
        return calculate_eth_trade_plan(markets[0], deposit_usdc, position_usdc)
    finally:
        await api_client.close()


def resolve_report_path() -> Path:
    """Resolve a new secret deployment-report path.

    :return:
        Configured path or a timestamped default path.
    """
    configured = os.environ.get("LIGHTER_DEPLOYMENT_REPORT")
    if configured:
        return Path(configured).expanduser()
    timestamp = native_datetime_utc_now().strftime("%Y%m%dT%H%M%SZ")
    return Path("~/.tradingstrategy/examples").expanduser() / f"lighter-lagoon-trade-{timestamp}.json"


def deploy_lighter_vault(web3: Web3, hot_wallet: HotWallet, api_key_index: int) -> LagoonAutomatedDeployment:
    """Deploy and activate a Lagoon vault with a registered Lighter API key.

    :param web3:
        Ethereum mainnet connection.
    :param hot_wallet:
        Deployer, Safe owner and asset manager.
    :param api_key_index:
        Lighter API-key slot registered during deployment.
    :return:
        Deployment report containing the in-memory API private key.
    """
    parameters = LagoonDeploymentParameters(
        underlying=LIGHTER_USDC_ETHEREUM,
        name="Lighter ETH Perpetual Trading Vault",
        symbol="LIGHTER-ETH",
    )
    return deploy_automated_lagoon_vault(
        web3=web3,
        deployer=hot_wallet,
        asset_manager=hot_wallet.address,
        parameters=parameters,
        safe_owners=[hot_wallet.address],
        safe_threshold=1,
        lighter_deployment=LighterDeployment.create_ethereum(),
        generate_lighter_api_key=True,
        lighter_api_key_index=api_key_index,
        use_forge=True,
        assets=[LIGHTER_USDC_ETHEREUM],
        etherscan_api_key=os.environ.get("ETHERSCAN_API_KEY"),
        between_contracts_delay_seconds=0.0,
    )


def add_lighter_collateral(
    web3: Web3,
    hot_wallet: HotWallet,
    deployment: LagoonAutomatedDeployment,
    target_collateral: Decimal,
) -> Decimal:
    """Subscribe more USDC to Lagoon and deposit it into Lighter.

    The deployer already deposited one accounted USDC during activation. This
    function performs one further Lagoon subscription only when the tutorial's
    conservative trade sizing needs more collateral.

    :param web3:
        Ethereum mainnet connection.
    :param hot_wallet:
        Lagoon depositor and asset manager.
    :param deployment:
        Activated Lagoon and Lighter deployment.
    :param target_collateral:
        Desired total Lighter collateral.
    :return:
        Collateral observed after the additional L1 deposit.
    """
    setup = deployment.lighter_account_setup
    assert setup is not None
    vault = deployment.vault
    if not isinstance(vault, LagoonVault):
        message = "Lighter trading requires a primary-chain Lagoon vault"
        raise TypeError(message)

    additional_deposit = target_collateral - setup.observed_collateral
    if additional_deposit <= 0:
        return setup.observed_collateral
    if additional_deposit < LIGHTER_MIN_MAINNET_USDC:
        raise ValueError(f"Additional Lighter deposit {additional_deposit} USDC is below the {LIGHTER_MIN_MAINNET_USDC} USDC L1 minimum")

    logger.info("Adding %s USDC through the Lagoon subscription lifecycle", additional_deposit)
    fund_lagoon_vault(
        web3=web3,
        vault_address=vault.address,
        asset_manager=hot_wallet.address,
        test_account_with_balance=hot_wallet.address,
        trading_strategy_module_address=vault.trading_strategy_module_address,
        amount=additional_deposit,
        nav=setup.observed_collateral,
        hot_wallet=hot_wallet,
    )
    usdc = fetch_erc20_details(web3, LIGHTER_USDC_ETHEREUM)
    deposit_usdc_from_lagoon_safe_into_lighter(
        web3=web3,
        hot_wallet=hot_wallet,
        vault=vault,
        usdc=usdc,
        deposit_usdc=additional_deposit,
    )

    session = create_lighter_session()
    try:
        return wait_for_lighter_collateral(session, setup.account_index, target_collateral)
    finally:
        session.close()


def get_signed_eth_position(account: dict[str, Any]) -> Decimal:
    """Read the signed ETH perpetual size from a public account response.

    :param account:
        Raw Lighter ``/api/v1/account`` item.
    :return:
        Positive long size, negative short size, or zero.
    """
    for position in account.get("positions") or ():
        if int(position["market_id"]) == LIGHTER_ETH_PERP_MARKET_INDEX:
            size = Decimal(str(position["position"]))
            return size if int(position["sign"]) >= 0 else -size
    return Decimal(0)


async def wait_for_eth_position(
    session: LighterSession,
    account_index: int,
    *,
    open_position: bool,
    timeout: int = 300,
    poll_seconds: float = LIGHTER_POSITION_POLL_SECONDS,
) -> Decimal:
    """Wait for the ETH position to become open or flat.

    :param session:
        Public Lighter REST session.
    :param account_index:
        Lighter account index.
    :param open_position:
        Wait for a positive long when true, or an exactly flat position when
        false.
    :param timeout:
        Maximum wait in seconds.
    :param poll_seconds:
        Delay between observable account reads.
    :return:
        Matching signed ETH position.
    """
    deadline = time.monotonic() + timeout
    while True:
        position = get_signed_eth_position(fetch_lighter_account_by_index(session, account_index))
        if (open_position and position > 0) or (not open_position and position == 0):
            return position
        if time.monotonic() >= deadline:
            state = "open" if open_position else "flat"
            raise TimeoutError(f"ETH position did not become {state} within {timeout} seconds; current position {position}")
        logger.info("Waiting for ETH position update; current position %s ETH", position)
        await asyncio.sleep(poll_seconds)


async def trade_eth_roundtrip(
    lighter: ModuleType,
    session: LighterSession,
    setup: LighterAccountSetup,
    plan: EthTradePlan,
    max_slippage: Decimal,
) -> None:
    """Open and close an ETH perpetual long with the deployment API key.

    The official SDK signs both orders and manages their Lighter nonces. The
    close is ``reduce_only`` and uses the position size observed after the IOC
    open order, so a partial fill cannot accidentally create a short.

    :param lighter:
        Official Lighter SDK module.
    :param session:
        Public REST session used to observe fills.
    :param setup:
        Deployment-created account and API-key material.
    :param plan:
        Resolved ETH order size.
    :param max_slippage:
        Maximum fractional price slippage for each market order.
    :return:
        ``None`` once the public account reports a flat ETH position.
    """
    if setup.private_key is None:
        message = "The in-memory or secret-bearing deployment report does not contain the Lighter API private key"
        raise ValueError(message)
    if not Decimal(0) < max_slippage < Decimal(1):
        raise ValueError(f"LIGHTER_MAX_SLIPPAGE must be between 0 and 1, got {max_slippage}")

    client = lighter.SignerClient(
        url=LIGHTER_API_URL,
        account_index=setup.account_index,
        api_private_keys={setup.api_key_index: setup.private_key},
    )
    try:
        error = client.check_client()
        if error is not None:
            raise RuntimeError(f"Lighter SDK rejected the deployment API key: {error}")

        client_order_index = int(time.time() * 1_000)
        logger.info("Opening %s ETH long on Lighter with API key slot %d", plan.base_size, setup.api_key_index)
        open_order, open_response, error = await client.create_market_order_limited_slippage(
            market_index=LIGHTER_ETH_PERP_MARKET_INDEX,
            client_order_index=client_order_index,
            base_amount=plan.base_amount,
            max_slippage=float(max_slippage),
            is_ask=False,
            api_key_index=setup.api_key_index,
        )
        if error is not None:
            raise RuntimeError(f"Opening the ETH perpetual failed: {error}")
        logger.info("ETH open order accepted: order=%s response=%s", open_order, open_response)

        opened_position = await wait_for_eth_position(session, setup.account_index, open_position=True)
        close_base_amount = int((abs(opened_position) * Decimal(10**plan.size_decimals)).to_integral_value(rounding=ROUND_CEILING))
        logger.info("Closing observed ETH long of %s ETH", opened_position)
        close_order, close_response, error = await client.create_market_order_limited_slippage(
            market_index=LIGHTER_ETH_PERP_MARKET_INDEX,
            client_order_index=client_order_index + 1,
            base_amount=close_base_amount,
            max_slippage=float(max_slippage),
            is_ask=True,
            reduce_only=True,
            api_key_index=setup.api_key_index,
        )
        if error is not None:
            raise RuntimeError(f"Closing the ETH perpetual failed: {error}")
        logger.info("ETH close order accepted: order=%s response=%s", close_order, close_response)
        await wait_for_eth_position(session, setup.account_index, open_position=False)
    finally:
        await client.close()


async def main() -> None:
    """Run the deployment, collateral deposit and API-key trade tutorial.

    :return:
        ``None`` after the ETH position is closed and final NAV is logged.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "INFO"))
    lighter = import_lighter_sdk()
    plan = await fetch_eth_trade_plan(
        lighter,
        deposit_usdc=parse_decimal_env("LIGHTER_DEPOSIT_USDC"),
        position_usdc=parse_decimal_env("LIGHTER_POSITION_USDC"),
    )
    configured_slippage = parse_decimal_env("LIGHTER_MAX_SLIPPAGE")
    max_slippage = configured_slippage if configured_slippage is not None else DEFAULT_MAX_SLIPPAGE
    logger.info(
        "ETH perpetual plan: collateral=%s USDC, position=%s USDC (%s ETH), reported maker minimums=%s USDC/%s ETH",
        plan.deposit_usdc,
        plan.position_usdc,
        plan.base_size,
        plan.min_quote_amount,
        plan.min_base_amount,
    )

    web3 = create_multi_provider_web3(require_env("JSON_RPC_ETHEREUM"), default_http_timeout=(3.0, 180.0))
    assert web3.eth.chain_id == LIGHTER_ETHEREUM_DEPLOYMENT_CHAIN_ID, "Lagoon Lighter activation requires Ethereum mainnet"
    hot_wallet = HotWallet.from_private_key(require_env("LIGHTER_TEST_PRIVATE_KEY"))
    hot_wallet.sync_nonce(web3)

    usdc = fetch_erc20_details(web3, LIGHTER_USDC_ETHEREUM)
    usdc_balance = usdc.fetch_balance_of(hot_wallet.address)
    eth_balance = Decimal(web3.eth.get_balance(hot_wallet.address)) / Decimal(10**18)
    if eth_balance <= 0:
        raise RuntimeError(f"{hot_wallet.address} has no ETH for deployment gas")
    if usdc_balance < plan.deposit_usdc:
        raise RuntimeError(f"{hot_wallet.address} has {usdc_balance} USDC but the tutorial needs {plan.deposit_usdc}")
    logger.info("Deployer %s balances: %s ETH, %s USDC", hot_wallet.address, eth_balance, usdc_balance)

    report_path = resolve_report_path()
    if report_path.exists():
        raise FileExistsError(f"Deployment report already exists: {report_path}")
    deployment = deploy_lighter_vault(
        web3,
        hot_wallet,
        int(os.environ.get("LIGHTER_API_KEY_INDEX", str(MIN_API_KEY_INDEX))),
    )
    setup = deployment.lighter_account_setup
    assert setup is not None and setup.private_key is not None

    deployment.write_json_file(report_path, include_secrets=True)
    logger.info("Saved secret deployment report with mode 0600: %s", report_path)
    logger.info("Deployed Lagoon vault %s with Lighter account %d and API key slot %d", deployment.vault.address, setup.account_index, setup.api_key_index)

    collateral = add_lighter_collateral(web3, hot_wallet, deployment, plan.deposit_usdc)
    logger.info("Lighter collateral ready: %s USDC", collateral)

    session = create_lighter_session()
    try:
        await trade_eth_roundtrip(lighter, session, setup, plan, max_slippage)
        equity = fetch_lighter_total_equity(session, setup.account_index)
    finally:
        session.close()
    logger.info("ETH perpetual round trip complete; Lighter account NAV: %s USDC", equity.get_total())


if __name__ == "__main__":
    asyncio.run(main())
