"""Manual test: trade Lighter perpetuals through a Lagoon vault.

This script demonstrates the complete Lagoon + Lighter lifecycle on Ethereum
mainnet:

1. Deploy a Lagoon vault with a 1-of-1 Safe owned by the deployer hot key
2. Deposit USDC into the vault
3. Deposit USDC from the Safe into Lighter's L1 contract
4. Wait for Lighter to create the Safe-owned account
5. Generate and register a Lighter API key with ``changePubKey`` via the Safe
6. Open an ETH long position
7. Close the ETH position
8. Request a secure Lighter USDC withdrawal
9. Claim the pending L1 withdrawal back to the Safe
10. Redeem vault shares and return USDC to the deployer hot wallet

Lighter has no production-like testnet for this full custody flow. The default
mode therefore runs against Ethereum mainnet and spends real ETH / USDC. Use a
small prefunded key and verify all addresses before running.

Funding estimate
----------------

The deployer hot wallet should be prefunded with both ETH and Ethereum mainnet
USDC:

- **USDC:** Lighter's Ethereum-mainnet deposit minimum is **1 USDC** and secure
  withdrawals also have a **1 USDC** minimum:
  https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals. The
  script reads the live ETH perpetual market from
  https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails?market_id=0 and
  uses its ``min_quote_amount`` and ``min_base_amount`` fields. At the time of
  writing, ETH has ``min_quote_amount=10 USDC`` and
  ``min_base_amount=0.005 ETH``. Therefore the theoretical minimum deposit to
  open the smallest ETH position is about **10 USDC**. The script defaults to
  the live minimum position size plus **5 USDC** of buffer; **16-20 USDC** is a
  practical minimum for this full deposit/trade/withdraw/redeem lifecycle.
- **ETH:** this script pays Ethereum mainnet gas for Safe/Lagoon deployment,
  guard/module configuration, ERC-7540 deposit/redeem transactions and the
  Lighter ``deposit`` / ``changePubKey`` / ``withdrawPendingBalance`` calls.
  Treat this as a gas-budget calculation, not a fixed ETH amount. A practical
  planning range is about **5-10 million gas** for the full run:
  **0.025-0.05 ETH at 5 gwei**, **0.05-0.10 ETH at 10 gwei** and
  **0.10-0.20 ETH at 20 gwei**. Use **0.10 ETH as a practical minimum when
  gas is quiet** and increase it if mainnet gas is busy or you expect retries.

Example::

    JSON_RPC_ETHEREUM="https://..." \\
    LIGHTER_TEST_PRIVATE_KEY="0x..." \\
    poetry run python scripts/lagoon/lagoon-lighter-example.py

Simulation mode
---------------

Set ``SIMULATE=true`` to run only the L1 custody smoke test against an Anvil
Ethereum-mainnet fork. This covers Lagoon deployment, vault deposit and the
guarded ``ZkLighter.deposit`` call. It cannot create a real Lighter account,
register an API key, trade, or complete a withdrawal proof.

Example::

    SIMULATE=true JSON_RPC_ETHEREUM="https://..." \\
        poetry run python scripts/lagoon/lagoon-lighter-example.py

Resume example::

    JSON_RPC_ETHEREUM="https://..." \\
    LIGHTER_TEST_PRIVATE_KEY="0x..." \\
    LIGHTER_TUTORIAL_DEPLOYMENT_FILE=~/.tradingstrategy/examples/lighter-tutorial-1.json \\
    poetry run python scripts/lagoon/lagoon-lighter-example.py

Environment variables
---------------------

``JSON_RPC_ETHEREUM``
    Ethereum mainnet RPC endpoint. Required.
``LIGHTER_TEST_PRIVATE_KEY``
    Funded deployer, Safe owner, depositor and asset-manager key. Required for
    mainnet mode. Must hold ETH and enough USDC.
``LIGHTER_DEPOSIT_USDC``
    Optional. USDC amount deposited into Lagoon and then Lighter. Defaults to
    an amount derived from Lighter's documented minimum deposit and the ETH
    market's minimum order size.
``LIGHTER_POSITION_USDC``
    Optional. ETH long notional in USDC. Defaults to Lighter's ETH market
    minimum quote amount with a small buffer.
``LIGHTER_API_KEY_INDEX``
    Optional. API-key slot to register. Defaults to 4. Lighter's dedicated
    API-key documentation reserves indices 0..3 for its web/mobile interfaces,
    https://apidocs.lighter.xyz/docs/api-keys,
    although its Get Started page currently says 0..1; the script uses the
    conservative range to avoid overwriting front-end keys.
``LIGHTER_WITHDRAW_TIMEOUT``
    Optional. Seconds to wait for the secure withdrawal to become claimable.
    Defaults to 14400 because Lighter secure withdrawals may take more than one
    hour to become claimable on L1.
``LIGHTER_RECOVERY_WITHDRAW_USDC``
    Optional. Claim/redeem recovery mode for a run that already requested a
    Lighter withdrawal but timed out before the L1 pending balance became
    claimable. Set this to the expected human-readable USDC withdrawal amount
    together with ``LIGHTER_TUTORIAL_DEPLOYMENT_FILE``. The script skips
    deploy/deposit/trade/withdraw and only claims from Lighter, redeems Lagoon
    shares, and sweeps residual Safe USDC.
``LIGHTER_TUTORIAL_DEPLOYMENT_FILE``
    Optional. Path to a previously saved Lagoon deployment JSON file. When set,
    the script reads the Lagoon deployment info back from this file and
    continues with vault deposit / Lighter account / trading lifecycle. Fresh
    mainnet deployments are saved automatically as
    ``~/.tradingstrategy/examples/lighter-tutorial-{counter}.json``.
``LAGOON_VAULT_ADDRESS`` and ``TRADING_STRATEGY_MODULE_ADDRESS``
    Optional. Use both together to resume after a successful Lagoon deployment
    instead of deploying a new vault. The script reconstructs the
    :class:`LagoonVault` from ``LAGOON_VAULT_ADDRESS``, injects the existing
    module address, verifies the module is enabled on the Safe, and continues
    with the vault deposit / Lighter account / trading lifecycle. Prefer
    ``LIGHTER_TUTORIAL_DEPLOYMENT_FILE`` when the automatic JSON file is
    available.
``SIMULATE``
    Optional. ``true`` for Anvil custody-only mode.
``SIMULATE_FORK_BLOCK``
    Optional. Fork block for simulation mode. Default 25000000.
``ETHERSCAN_API_KEY``
    Optional. Used by Lagoon deployment for source verification.
"""

import asyncio
import json
import logging
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonAutomatedDeployment,
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
)
from eth_defi.erc_4626.vault_protocol.lagoon.testing import fund_lagoon_vault
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.api import (
    LIGHTER_MIN_MAINNET_USDC,
    LighterTradeAmounts,
    fetch_lighter_account,
    get_lighter_available_balance,
    get_lighter_collateral,
    import_lighter,
    register_lighter_api_key,
    resolve_eth_trade_amounts,
    trade_eth_roundtrip,
    wait_for_lighter_account,
    wait_for_lighter_collateral,
    withdraw_from_lighter,
)
from eth_defi.lighter.constants import LIGHTER_USDC_ETHEREUM
from eth_defi.lighter.deployment import LighterDeployment
from eth_defi.lighter.lagoon import broadcast_tx, claim_lighter_pending_balance, deposit_usdc_from_lagoon_safe_into_lighter, sweep_safe_usdc_to_hot_wallet
from eth_defi.lighter.pubkey import MIN_API_KEY_INDEX
from eth_defi.provider.anvil import fork_network_anvil, fund_erc20_on_anvil, set_balance
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

#: Environment variable for resuming from a saved deployment file.
LIGHTER_TUTORIAL_DEPLOYMENT_FILE_ENV = "LIGHTER_TUTORIAL_DEPLOYMENT_FILE"

#: Directory for automatically saved manual-test deployment files.
LIGHTER_TUTORIAL_DEPLOYMENT_DIR = Path("~/.tradingstrategy/examples").expanduser()

#: File name prefix for automatically saved deployment files.
LIGHTER_TUTORIAL_DEPLOYMENT_FILE_PREFIX = "lighter-tutorial"

#: Default wait for Lighter secure withdrawals to become claimable on L1.
DEFAULT_LIGHTER_WITHDRAW_TIMEOUT = 14_400


def require_env(name: str) -> str:
    """Read a required environment variable.

    :param name:
        Environment variable name.

    :return:
        Environment variable value.
    """
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value


def parse_decimal_env(name: str, default: Decimal | None) -> Decimal | None:
    """Parse an optional decimal environment variable.

    :param name:
        Environment variable name.

    :param default:
        Default value when the environment variable is unset.

    :return:
        Parsed :class:`~decimal.Decimal`, or ``None``.
    """
    raw_value = os.environ.get(name)
    return Decimal(raw_value) if raw_value else default


def setup_simulation_environment(json_rpc_url: str, fork_block: int) -> tuple:
    """Fork Ethereum mainnet and create a funded test wallet.

    :param json_rpc_url:
        Ethereum mainnet RPC to fork from.

    :param fork_block:
        Fixed fork block.

    :return:
        ``(web3, hot_wallet, anvil_launch)``.
    """
    print("\nStarting Anvil fork of Ethereum mainnet...")
    anvil_launch = fork_network_anvil(json_rpc_url, fork_block_number=fork_block)
    web3 = create_multi_provider_web3(anvil_launch.json_rpc_url, default_http_timeout=(3.0, 180.0))
    assert web3.eth.chain_id == 1, f"Expected Ethereum mainnet, got chain {web3.eth.chain_id}"

    hot_wallet = HotWallet.create_for_testing(web3, test_account_n=0, eth_amount=0)
    hot_wallet.sync_nonce(web3)

    set_balance(web3, hot_wallet.address, 10 * 10**18)
    fund_erc20_on_anvil(web3, LIGHTER_USDC_ETHEREUM, hot_wallet.address, 100 * 10**6)

    print(f"  Fork running at: {anvil_launch.json_rpc_url}")
    print(f"  Forked at block: {web3.eth.block_number:,}")
    print(f"  Simulation wallet: {hot_wallet.address} (10 ETH, 100 USDC)")
    return web3, hot_wallet, anvil_launch


def deploy_lighter_vault(
    web3: Web3,
    hot_wallet: HotWallet,
    etherscan_api_key: str | None,
) -> LagoonAutomatedDeployment:
    """Deploy a Lagoon vault with Lighter L1 deposit whitelisting.

    :param web3:
        Web3 connection.

    :param hot_wallet:
        Deployer, Safe owner and asset-manager wallet.

    :param etherscan_api_key:
        Optional Etherscan API key for source verification.

    :return:
        Lagoon automated deployment information.
    """
    parameters = LagoonDeploymentParameters(
        underlying=LIGHTER_USDC_ETHEREUM,
        name="Lighter Trading Vault Manual Test",
        symbol="LIGHTER-TEST",
    )

    print("\nDeploying Lagoon vault with Lighter integration...")
    print(f"  Deployer / Safe owner / asset manager: {hot_wallet.address}")

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=hot_wallet,
        asset_manager=hot_wallet.address,
        parameters=parameters,
        safe_owners=[hot_wallet.address],
        safe_threshold=1,
        any_asset=False,
        lighter_deployment=LighterDeployment.create_ethereum(),
        use_forge=True,
        assets=[LIGHTER_USDC_ETHEREUM],
        etherscan_api_key=etherscan_api_key,
        between_contracts_delay_seconds=0.0,
    )
    vault = deploy_info.vault
    print("  Vault deployed")
    print(f"    Vault:  {vault.address}")
    print(f"    Safe:   {vault.safe_address}")
    print(f"    Module: {vault.trading_strategy_module_address}")
    return deploy_info


def load_existing_lagoon_vault(
    web3: Web3,
    vault_address: HexAddress | str,
    trading_strategy_module_address: HexAddress | str,
) -> LagoonVault:
    """Load an already deployed Lagoon vault and its trading module.

    :param web3:
        Web3 connection.

    :param vault_address:
        Existing Lagoon vault address.

    :param trading_strategy_module_address:
        Existing ``TradingStrategyModuleV0`` address enabled on the vault Safe.

    :return:
        Configured Lagoon vault object.
    """
    vault_address = Web3.to_checksum_address(vault_address)
    trading_strategy_module_address = Web3.to_checksum_address(trading_strategy_module_address)
    vault = create_vault_instance(
        web3,
        vault_address,
        features={ERC4626Feature.lagoon_like, ERC4626Feature.erc_7540_like},
        default_block_identifier="latest",
        require_denomination_token=True,
    )
    if not isinstance(vault, LagoonVault):
        raise TypeError(f"{vault_address} is not a Lagoon vault: {vault}")

    vault.trading_strategy_module_address = trading_strategy_module_address
    if not vault.is_safe_trading_strategy_module_enabled():
        raise RuntimeError(f"TradingStrategyModuleV0 {trading_strategy_module_address} is not enabled on Safe {vault.safe_address}")

    print("\nUsing existing Lagoon vault deployment...")
    print(f"  Vault:  {vault.address}")
    print(f"  Safe:   {vault.safe_address}")
    print(f"  Module: {vault.trading_strategy_module_address}")
    return vault


def resolve_next_lagoon_deployment_file(directory: Path = LIGHTER_TUTORIAL_DEPLOYMENT_DIR) -> Path:
    """Find the next automatic Lagoon deployment JSON file path.

    The file name uses an incrementing counter so that repeated manual-test
    runs do not overwrite earlier mainnet deployment information.

    :param directory:
        Directory where tutorial deployment files are stored.

    :return:
        Next available deployment file path.
    """
    directory.mkdir(parents=True, exist_ok=True)
    counter = 1
    while True:
        path = directory / f"{LIGHTER_TUTORIAL_DEPLOYMENT_FILE_PREFIX}-{counter}.json"
        if not path.exists():
            return path
        counter += 1


def build_lighter_tutorial_deployment_json(deploy_info: LagoonAutomatedDeployment) -> dict[str, Any]:
    """Build the JSON payload for a saved Lighter tutorial deployment.

    The Lagoon deployment data is stored directly at the top level so it can
    be passed back to
    :py:meth:`eth_defi.erc_4626.vault_protocol.lagoon.deployment.LagoonAutomatedDeployment.from_json_friendly_dict`.
    The ``metadata`` entry is informational and does not contain secrets.

    :param deploy_info:
        Lagoon deployment information to serialise.

    :return:
        JSON-serialisable deployment payload.
    """
    data = deploy_info.as_json_friendly_dict()
    data["metadata"] = {
        "script": "scripts/lagoon/lagoon-lighter-example.py",
        "created_at": native_datetime_utc_now().isoformat(),
        "resume_environment_variable": LIGHTER_TUTORIAL_DEPLOYMENT_FILE_ENV,
    }
    return data


def print_lagoon_deployment_json(path: Path, data: dict[str, Any], action: str) -> None:
    """Print saved or loaded Lagoon deployment JSON.

    :param path:
        Deployment JSON file path.

    :param data:
        Deployment JSON data.

    :param action:
        Human-readable action label, e.g. ``"Saved"`` or ``"Loaded"``.
    """
    print(f"\n{action} Lagoon deployment JSON:")
    print(f"  File: {path}")
    print(json.dumps(data, indent=2, sort_keys=True))


def save_lagoon_deployment_file(deploy_info: LagoonAutomatedDeployment) -> Path:
    """Save Lagoon deployment information to an automatic tutorial file.

    :param deploy_info:
        Lagoon deployment information to write.

    :return:
        Written JSON file path.
    """
    path = resolve_next_lagoon_deployment_file()
    data = build_lighter_tutorial_deployment_json(deploy_info)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print_lagoon_deployment_json(path, data, "Saved")
    print(f"\nContinue a failed run with {LIGHTER_TUTORIAL_DEPLOYMENT_FILE_ENV}={path}")
    return path


def load_lagoon_deployment_file(web3: Web3, path: Path) -> LagoonAutomatedDeployment:
    """Load Lagoon deployment information from a tutorial JSON file.

    :param web3:
        Web3 connection.

    :param path:
        Deployment JSON file path.

    :return:
        Hydrated Lagoon deployment information.
    """
    path = path.expanduser()
    data = json.loads(path.read_text(encoding="utf-8"))
    deploy_info = LagoonAutomatedDeployment.from_json_friendly_dict(web3, data)
    print_lagoon_deployment_json(path, data, "Loaded")
    return deploy_info


def deposit_to_vault(
    web3: Web3,
    hot_wallet: HotWallet,
    vault: LagoonVault,
    deposit_usdc: Decimal,
) -> None:
    """Deposit USDC into the Lagoon vault.

    :param web3:
        Web3 connection.

    :param hot_wallet:
        Depositor and asset-manager wallet.

    :param vault:
        Lagoon vault.

    :param deposit_usdc:
        Human-readable USDC amount.
    """
    print(f"\nDepositing {deposit_usdc} USDC into Lagoon vault...")
    fund_lagoon_vault(
        web3,
        vault_address=vault.address,
        asset_manager=hot_wallet.address,
        test_account_with_balance=hot_wallet.address,
        trading_strategy_module_address=vault.trading_strategy_module_address,
        amount=deposit_usdc,
        hot_wallet=hot_wallet,
    )
    usdc = fetch_erc20_details(web3, vault.underlying_token.address)
    print(f"  Safe USDC balance: {usdc.fetch_balance_of(vault.safe_address)}")
    print(f"  Hot wallet shares: {vault.share_token.fetch_balance_of(hot_wallet.address)}")


def redeem_back_to_hot_wallet(web3: Web3, hot_wallet: HotWallet, vault: LagoonVault) -> None:
    """Redeem all Lagoon shares back to the deployer hot wallet.

    :param web3:
        Web3 connection.

    :param hot_wallet:
        Deployer and share holder.

    :param vault:
        Lagoon vault.
    """
    print("\nRedeeming Lagoon vault shares back to the hot wallet...")
    usdc = vault.underlying_token
    share_token = vault.share_token
    raw_shares = share_token.fetch_raw_balance_of(hot_wallet.address)
    if raw_shares <= 0:
        raise RuntimeError(f"{hot_wallet.address} has no Lagoon shares to redeem")

    human_shares = share_token.convert_to_decimals(raw_shares)
    broadcast_tx(
        web3,
        hot_wallet,
        share_token.approve(vault.address, human_shares),
        f"Approve {human_shares} shares for redemption",
        gas_limit=100_000,
    )
    broadcast_tx(
        web3,
        hot_wallet,
        vault.request_redeem(hot_wallet.address, raw_shares),
        f"Request redemption of {human_shares} shares",
        gas_limit=250_000,
    )

    safe_usdc_balance = usdc.fetch_balance_of(vault.safe_address)

    broadcast_tx(web3, hot_wallet, vault.post_new_valuation(safe_usdc_balance), "Post vault valuation", gas_limit=200_000)
    broadcast_tx(web3, hot_wallet, vault.settle_via_trading_strategy_module(safe_usdc_balance), "Settle vault redemption", gas_limit=450_000)
    deadline = time.monotonic() + 120
    while True:
        claimable_raw_shares = vault.vault_contract.functions.maxRedeem(hot_wallet.address).call()
        if claimable_raw_shares > 0:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Lagoon redemption settlement was mined, but maxRedeem({hot_wallet.address}) stayed zero for 120 seconds")
        print("  Waiting for Lagoon redemption to become claimable...")
        time.sleep(5)
    broadcast_tx(web3, hot_wallet, vault.finalise_redeem(hot_wallet.address, raw_amount=claimable_raw_shares), "Claim redeemed USDC", gas_limit=150_000)

    print(f"  Hot wallet USDC balance: {usdc.fetch_balance_of(hot_wallet.address)}")
    print(f"  Remaining shares: {vault.share_token.fetch_balance_of(hot_wallet.address)}")


async def run_mainnet() -> None:  # noqa: PLR0914
    """Run the full mainnet manual test."""
    lighter = import_lighter()
    json_rpc_url = require_env("JSON_RPC_ETHEREUM")
    private_key = require_env("LIGHTER_TEST_PRIVATE_KEY")
    etherscan_api_key = os.environ.get("ETHERSCAN_API_KEY")
    api_key_index = int(os.environ.get("LIGHTER_API_KEY_INDEX", str(MIN_API_KEY_INDEX)))
    withdraw_timeout = int(os.environ.get("LIGHTER_WITHDRAW_TIMEOUT", str(DEFAULT_LIGHTER_WITHDRAW_TIMEOUT)))
    recovery_withdraw_usdc = parse_decimal_env("LIGHTER_RECOVERY_WITHDRAW_USDC", None)
    deployment_file = os.environ.get(LIGHTER_TUTORIAL_DEPLOYMENT_FILE_ENV)
    existing_vault_address = os.environ.get("LAGOON_VAULT_ADDRESS")
    existing_module_address = os.environ.get("TRADING_STRATEGY_MODULE_ADDRESS")

    if deployment_file and (existing_vault_address or existing_module_address):
        msg = f"{LIGHTER_TUTORIAL_DEPLOYMENT_FILE_ENV} cannot be combined with LAGOON_VAULT_ADDRESS or TRADING_STRATEGY_MODULE_ADDRESS"
        raise ValueError(msg)

    if bool(existing_vault_address) != bool(existing_module_address):
        msg = "Set both LAGOON_VAULT_ADDRESS and TRADING_STRATEGY_MODULE_ADDRESS to resume an existing deployment"
        raise ValueError(msg)

    web3 = create_multi_provider_web3(json_rpc_url, default_http_timeout=(3.0, 180.0))
    assert web3.eth.chain_id == 1, "Lighter deposits and withdrawals are on Ethereum mainnet"

    hot_wallet = HotWallet.from_private_key(private_key)
    hot_wallet.sync_nonce(web3)
    usdc = fetch_erc20_details(web3, LIGHTER_USDC_ETHEREUM)

    usdc_balance = usdc.fetch_balance_of(hot_wallet.address)
    eth_balance = Decimal(web3.eth.get_balance(hot_wallet.address)) / Decimal(10**18)
    if eth_balance <= 0:
        raise RuntimeError(f"{hot_wallet.address} has no ETH for mainnet gas")

    print("\nMainnet wallet:")
    print(f"  Address: {hot_wallet.address}")
    print(f"  ETH:     {eth_balance}")
    print(f"  USDC:    {usdc_balance}")

    amounts: LighterTradeAmounts | None = None
    if recovery_withdraw_usdc is None:
        amounts = await resolve_eth_trade_amounts(
            lighter,
            deposit_usdc=parse_decimal_env("LIGHTER_DEPOSIT_USDC", None),
            position_usdc=parse_decimal_env("LIGHTER_POSITION_USDC", None),
        )
        if usdc_balance < amounts.deposit_usdc:
            raise RuntimeError(f"{hot_wallet.address} has {usdc_balance} USDC but needs {amounts.deposit_usdc}")

    if deployment_file:
        deploy_info = load_lagoon_deployment_file(web3, Path(deployment_file))
        vault = deploy_info.vault
        if not isinstance(vault, LagoonVault):
            msg = f"{deployment_file} describes a satellite deployment, but the Lighter manual test needs a Lagoon vault"
            raise TypeError(msg)
    elif existing_vault_address:
        vault = load_existing_lagoon_vault(web3, existing_vault_address, existing_module_address)
    else:
        deploy_info = deploy_lighter_vault(web3, hot_wallet, etherscan_api_key)
        saved_path = save_lagoon_deployment_file(deploy_info)
        deploy_info = load_lagoon_deployment_file(web3, saved_path)
        vault = deploy_info.vault
        if not isinstance(vault, LagoonVault):
            msg = f"{saved_path} describes a satellite deployment, but the Lighter manual test needs a Lagoon vault"
            raise TypeError(msg)

    if recovery_withdraw_usdc is not None:
        if not deployment_file and not existing_vault_address:
            msg = f"LIGHTER_RECOVERY_WITHDRAW_USDC needs {LIGHTER_TUTORIAL_DEPLOYMENT_FILE_ENV} or LAGOON_VAULT_ADDRESS/TRADING_STRATEGY_MODULE_ADDRESS"
            raise ValueError(msg)
        expected_raw_amount = usdc.convert_to_raw(recovery_withdraw_usdc)
        claim_lighter_pending_balance(web3, hot_wallet, vault, usdc, expected_raw_amount, withdraw_timeout)
        redeem_back_to_hot_wallet(web3, hot_wallet, vault)
        sweep_safe_usdc_to_hot_wallet(web3, hot_wallet, vault)
        print("\nLighter + Lagoon recovery completed.")
        return

    assert amounts is not None

    deposit_to_vault(web3, hot_wallet, vault, amounts.deposit_usdc)
    deposit_usdc_from_lagoon_safe_into_lighter(web3, hot_wallet, vault, usdc, amounts.deposit_usdc)

    account_index = await wait_for_lighter_account(lighter, vault.safe_address)
    await wait_for_lighter_collateral(lighter, account_index, amounts.deposit_usdc)
    api_private_key = await register_lighter_api_key(lighter, web3, vault.safe, hot_wallet, account_index, api_key_index)
    await trade_eth_roundtrip(lighter, account_index, api_private_key, api_key_index, amounts)

    account = await fetch_lighter_account(lighter, account_index)
    available_usdc = get_lighter_available_balance(account)
    collateral_usdc = get_lighter_collateral(account)
    withdraw_usdc = available_usdc.quantize(Decimal("0.000001"))
    print("\nLighter balances after ETH round-trip:")
    print(f"  Collateral: {collateral_usdc} USDC")
    print(f"  Available:  {available_usdc} USDC")
    print(f"  Withdrawal: {withdraw_usdc} USDC")
    if withdraw_usdc < LIGHTER_MIN_MAINNET_USDC:
        raise RuntimeError(f"Lighter available balance {withdraw_usdc} USDC is below the secure withdrawal minimum {LIGHTER_MIN_MAINNET_USDC} USDC")
    await withdraw_from_lighter(lighter, account_index, api_private_key, api_key_index, withdraw_usdc)
    claim_lighter_pending_balance(web3, hot_wallet, vault, usdc, usdc.convert_to_raw(withdraw_usdc), withdraw_timeout)
    redeem_back_to_hot_wallet(web3, hot_wallet, vault)
    sweep_safe_usdc_to_hot_wallet(web3, hot_wallet, vault)

    print("\nFull Lighter + Lagoon manual test completed.")


def run_simulation() -> None:
    """Run the Anvil custody-only smoke test."""
    json_rpc_url = require_env("JSON_RPC_ETHEREUM")
    fork_block = int(os.environ.get("SIMULATE_FORK_BLOCK", "25000000"))
    etherscan_api_key = os.environ.get("ETHERSCAN_API_KEY")
    anvil_launch = None
    try:
        web3, hot_wallet, anvil_launch = setup_simulation_environment(json_rpc_url, fork_block)
        usdc = fetch_erc20_details(web3, LIGHTER_USDC_ETHEREUM)
        deploy_info = deploy_lighter_vault(web3, hot_wallet, etherscan_api_key)
        deposit_to_vault(web3, hot_wallet, deploy_info.vault, Decimal(100))
        deposit_usdc_from_lagoon_safe_into_lighter(web3, hot_wallet, deploy_info.vault, usdc, Decimal(100))
        print("\nCustody-only simulation completed. Mainnet mode is required for account creation, API-key registration, trading and withdrawal proofs.")
    finally:
        if anvil_launch is not None:
            anvil_launch.close()


def main() -> None:
    """Script entry point."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "INFO"))
    simulate = os.environ.get("SIMULATE", "").lower() in {"true", "1", "yes"}
    if simulate:
        run_simulation()
    else:
        asyncio.run(run_mainnet())


if __name__ == "__main__":
    main()
