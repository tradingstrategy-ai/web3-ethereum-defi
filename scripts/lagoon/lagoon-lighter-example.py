"""Deploy and activate a Lighter-enabled Lagoon vault on Ethereum mainnet.

This is a one-shot manual provider check for the package deployment path. It
creates a fresh Lagoon vault, makes the fixed 1 USDC accounted activation
deposit, registers a Lighter API key before the requested Safe owners and
threshold are configured, writes the secret-bearing report with mode ``0600``,
and prints only a redacted summary. It does not trade, withdraw, resume a
previous run, or attempt recovery.

Environment variables
---------------------

``JSON_RPC_ETHEREUM``
    Ethereum mainnet RPC endpoint. Required.
``LIGHTER_TEST_PRIVATE_KEY``
    Funded deployer, Safe owner, depositor and asset-manager key. Required. It
    needs ETH for deployment gas and at least 1 native Ethereum USDC for
    Lighter activation.
``LIGHTER_API_KEY_INDEX``
    Optional Lighter API-key slot. Defaults to 4.
``LIGHTER_DEPLOYMENT_REPORT``
    Optional file for the secret-bearing deployment report. Defaults below
    ``~/.tradingstrategy/examples``.
``ETHERSCAN_API_KEY``
    Optional contract-verification key.
"""

import logging
import os
from pathlib import Path

from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonAutomatedDeployment, LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.constants import LIGHTER_ETHEREUM_DEPLOYMENT_CHAIN_ID, LIGHTER_USDC_ETHEREUM
from eth_defi.lighter.deployment import LighterDeployment
from eth_defi.lighter.pubkey import MIN_API_KEY_INDEX
from eth_defi.lighter.session import create_lighter_session
from eth_defi.lighter.valuation import fetch_lighter_total_equity
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


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


def resolve_report_path() -> Path:
    """Resolve the private deployment-report path.

    :return:
        Not-yet-existing report path below the configured or default directory.
    """
    configured = os.environ.get("LIGHTER_DEPLOYMENT_REPORT")
    if configured:
        return Path(configured).expanduser()
    directory = Path("~/.tradingstrategy/examples").expanduser()
    timestamp = native_datetime_utc_now().strftime("%Y%m%dT%H%M%SZ")
    return directory / f"lighter-lagoon-deployment-{timestamp}.json"


def deploy_lighter_vault(web3: Web3, hot_wallet: HotWallet, etherscan_api_key: str | None, api_key_index: int) -> LagoonAutomatedDeployment:
    """Deploy and activate a fresh Lighter-enabled Lagoon vault.

    :param web3:
        Ethereum mainnet Web3 connection.
    :param hot_wallet:
        Deployer, activation depositor and initial Safe owner.
    :param etherscan_api_key:
        Optional source-verification key.
    :param api_key_index:
        Lighter API-key slot to register.
    :return:
        Deployment report containing the registered Lighter key.
    """
    parameters = LagoonDeploymentParameters(
        underlying=LIGHTER_USDC_ETHEREUM,
        name="Lighter Trading Vault Manual Test",
        symbol="LIGHTER-TEST",
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
        etherscan_api_key=etherscan_api_key,
        between_contracts_delay_seconds=0.0,
    )


def main() -> None:
    """Run the one-shot Ethereum mainnet deployment check.

    :return:
        ``None`` after the deployment report and public NAV check succeed.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "INFO"))
    web3 = create_multi_provider_web3(require_env("JSON_RPC_ETHEREUM"), default_http_timeout=(3.0, 180.0))
    assert web3.eth.chain_id == LIGHTER_ETHEREUM_DEPLOYMENT_CHAIN_ID, "Lighter activation requires Ethereum mainnet"

    hot_wallet = HotWallet.from_private_key(require_env("LIGHTER_TEST_PRIVATE_KEY"))
    hot_wallet.sync_nonce(web3)
    report_path = resolve_report_path()
    if report_path.exists():
        raise FileExistsError(f"Deployment report already exists: {report_path}")
    deployment = deploy_lighter_vault(
        web3,
        hot_wallet,
        os.environ.get("ETHERSCAN_API_KEY"),
        int(os.environ.get("LIGHTER_API_KEY_INDEX", str(MIN_API_KEY_INDEX))),
    )
    assert deployment.lighter_account_setup is not None

    deployment.write_json_file(report_path, include_secrets=True)
    logger.info("Saved secret deployment report: %s", report_path)
    logger.info("Deployment summary:\n%s", deployment.pformat())

    session = create_lighter_session()
    try:
        equity = fetch_lighter_total_equity(session, deployment.lighter_account_setup.account_index)
    finally:
        session.close()
    logger.info("Lighter account %d NAV: %s USDC", equity.account_index, equity.get_total())


if __name__ == "__main__":
    main()
