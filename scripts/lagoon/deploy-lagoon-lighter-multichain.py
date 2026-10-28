"""Manually deploy a multichain Lagoon test vault and activate Lighter last.

Based on ``deploy-lagoon-multichain.py``. This sends real mainnet transactions.
The deployer is the asset manager, valuation manager and sole owner (1-of-1)
of every Safe. This is a fresh test deployment, not a production ownership
handover or a resumable deployment tool. No pool trades are made.

The default hub is HyperEVM, with an Ethereum satellite for Lighter. Hypercore
HLP and the Felix USDC ERC-4626 vault are whitelisted on HyperEVM. Optional
Base/Arbitrum chains can be selected, with the first chain acting as the hub.
All contracts are deployed before the accounted 20-USDC hub subscription,
CCTP transfer, 1-USDC Lighter deposit and Safe-owner API-key registration.

Environment variables
---------------------

``LAGOON_MULTCHAIN_TEST_PRIVATE_KEY``
    Required deployer key, using the earlier tutorial's variable spelling.
    Needs at least 20 native USDC on the hub BEFORE deployment, plus native
    gas tokens on every selected chain. Never put the key in shell history.
``CHAINS``
    Default ``hyperliquid,ethereum``. Must include both; the first is the hub
    and cannot be Ethereum. Also supports ``base`` and ``arbitrum``.
    Example: ``base,hyperliquid,ethereum`` exercises CCTP Fast Transfer.
``JSON_RPC_HYPERLIQUID``, ``JSON_RPC_ETHEREUM``, ``JSON_RPC_BASE``, ``JSON_RPC_ARBITRUM``
    RPC connections for the selected chains; fallback URL format is supported.
``CCTP_MAX_FEE_USDC``
    Optional maximum fee, default ``0.01``. Bridge 1 USDC plus this
    allowance, within the 20-USDC subscription. Unspent funds stay in Safes.
``SALT_NONCE``
    Optional fresh deterministic Safe salt, random by default.
``LIGHTER_API_KEY_INDEX``
    Optional user key slot, default 4. An occupied slot is never overwritten.
``LIGHTER_DEPLOYMENT_REPORT``
    Optional new JSONL file, default under ``~/.tradingstrategy/examples``.
    Created exclusively with mode 0600. Contains recovery stages, transaction
    identifiers and the generated API PRIVATE KEY. Keep this file protected.
``PREFLIGHT_ONLY``
    Set to ``true`` to check RPCs, funding, configuration and Circle quotes
    without deploying contracts or sending transactions.
``LOG_LEVEL``
    Optional, default ``info``.

Run after loading the RPCs and deployer key from your private environment::

    PREFLIGHT_ONLY=true poetry run python scripts/lagoon/deploy-lagoon-lighter-multichain.py
    poetry run python scripts/lagoon/deploy-lagoon-lighter-multichain.py

Fast Transfer is selected on Base/Arbitrum. HyperEVM uses its already-fast
standard finality, as documented in `Circle's source-chain support table
<https://developers.circle.com/cctp/concepts/supported-chains-and-domains>`_.
There is no slow fallback or automatic second burn after a timeout. Lighter
indexing/key registration still takes time; all waits are bounded and logged.

On interruption, inspect the protected JSONL report and transaction logs before
manual recovery. Re-running creates a NEW deployment; it does not resume the
old one. A partial contract deployment may require recovering addresses from
the deployment logs and the recorded deployer nonces. Do not settle the hub
again until multichain NAV accounting includes the satellite and Lighter assets.
A successful run proves deployment, bridging and activation, not pool execution
or NAV conservation through mint/burn/withdrawal.
"""

import logging
import os
import secrets
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.cctp.whitelist import CCTPDeployment
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonConfig, LagoonDeploymentParameters, deploy_multichain_lagoon_vault
from eth_defi.erc_4626.vault_protocol.lagoon.lighter import LIGHTER_MULTICHAIN_BOOTSTRAP_SUBSCRIPTION, LagoonLighterActivation, preflight_lighter_multichain
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.api import LIGHTER_MIN_MAINNET_USDC
from eth_defi.lighter.deployment import LighterDeployment
from eth_defi.lighter.pubkey import MAX_API_KEY_INDEX, MIN_API_KEY_INDEX
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

#: Mainnet chains deliberately supported by this small rehearsal.
CHAIN_IDS = {"hyperliquid": 999, "ethereum": 1, "base": 8453, "arbitrum": 42161}
#: Accounted bootstrap capital, including the bridge fee allowance.
SUBSCRIPTION_USDC = LIGHTER_MULTICHAIN_BOOTSTRAP_SUBSCRIPTION
#: Minimum Lighter activation deposit.
ACTIVATION_USDC = LIGHTER_MIN_MAINNET_USDC
#: Same Hypercore and ERC-4626 examples as the earlier multichain tutorial.
HLP = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"
FELIX_USDC = "0x8a862fd6c12f9ad34c9c2ff45ab2b6712e8cea27"


@dataclass(slots=True)
class Settings:
    """Non-secret inputs for one fresh manual deployment."""

    #: Ordered chain names; first is the hub.
    chains: tuple[str, ...]
    #: Explicit USDC fee ceiling, included in the bridge amount.
    max_fee: Decimal
    #: Shared deterministic Safe salt.
    salt_nonce: int
    #: Lighter user API-key slot.
    api_key_index: int
    #: Protected append-only progress and key backup file.
    report: Path

    @classmethod
    def from_env(cls) -> "Settings":
        """Read and validate the environment before any network mutations.

        :return: Validated, non-secret script settings.
        """
        chains = tuple(c.strip() for c in os.environ.get("CHAINS", "hyperliquid,ethereum").split(","))
        assert len(chains) == len(set(chains)) and set(chains) <= CHAIN_IDS.keys(), "CHAINS contains duplicate or unsupported chains"
        assert {"hyperliquid", "ethereum"} <= set(chains), "CHAINS must include HyperEVM and Ethereum"
        assert chains[0] != "ethereum", "Ethereum must be a Lighter satellite, not the hub"
        fee = Decimal(os.environ.get("CCTP_MAX_FEE_USDC", "0.01"))
        assert fee.is_finite() and Decimal(0) <= fee <= SUBSCRIPTION_USDC - ACTIVATION_USDC, "Fee allowance must fit the 20-USDC subscription"
        salt = int(os.environ.get("SALT_NONCE", str(secrets.randbits(64))))
        assert 0 <= salt < 2**256, "SALT_NONCE must fit uint256"
        key_index = int(os.environ.get("LIGHTER_API_KEY_INDEX", str(MIN_API_KEY_INDEX)))
        assert MIN_API_KEY_INDEX <= key_index <= MAX_API_KEY_INDEX, "Invalid Lighter API-key slot"
        default = Path.home() / ".tradingstrategy/examples" / f"lighter-multichain-{native_datetime_utc_now():%Y%m%dT%H%M%S}-{salt}.jsonl"
        report = Path(os.environ.get("LIGHTER_DEPLOYMENT_REPORT", str(default))).expanduser()
        assert not report.exists(), "Report already exists; inspect it before manual recovery, do not overwrite it"
        return cls(chains, fee, salt, key_index, report)


def build_configs(settings: Settings, chain_web3: dict[str, Web3], owner: HexAddress) -> dict[str, LagoonConfig]:
    """Build custody permissions using the earlier multichain tutorial's pattern.

    API-key activation is deliberately separate from contract deployment.

    :param settings: Fresh deployment settings.
    :param chain_web3: Checked chain connections.
    :param owner: Test deployer and sole Safe owner.
    :return: Configurations with explicit native USDC permissions.
    """
    hyper_web3 = chain_web3["hyperliquid"]
    felix_address = Web3.to_checksum_address(FELIX_USDC)
    felix = create_vault_instance(hyper_web3, felix_address, features=detect_vault_features(hyper_web3, felix_address))
    configs = {}
    for name in chain_web3:
        chain_id = CHAIN_IDS[name]
        satellite = name != settings.chains[0]
        configs[name] = LagoonConfig(
            parameters=LagoonDeploymentParameters(underlying=USDC_NATIVE_TOKEN[chain_id], name="Lighter Multichain Manual Test", symbol="LIGHTER-MC"),
            asset_managers=[owner],
            safe_owners=[owner],
            safe_threshold=1,
            safe_salt_nonce=settings.salt_nonce,
            satellite_chain=satellite,
            assets=[USDC_NATIVE_TOKEN[chain_id]],
            any_asset=False,
            hypercore_vaults=[HLP] if name == "hyperliquid" else None,
            erc_4626_vaults=[felix] if name == "hyperliquid" else None,
            lighter_deployment=LighterDeployment.create_ethereum() if chain_id == 1 else None,
            generate_lighter_api_key=False,
            cctp_deployment=CCTPDeployment.create_for_chain(chain_id, allowed_destinations=[CHAIN_IDS[c] for c in settings.chains if c != name]),
            max_settlement_amount=None if satellite else SUBSCRIPTION_USDC,
        )
    return configs


def run_deployment(settings: Settings, chain_web3: dict[str, Web3], deployer: HotWallet, *, preflight_only: bool = False) -> None:
    """Run the shared deployment coordinator, or its read-only preflight.

    :param settings: Optional routine overrides resolved from sane defaults.
    :param chain_web3: Configured mainnet connections.
    :param deployer: Funded test deployer and sole Safe owner.
    :param preflight_only: Perform checks without sending transactions.
    """
    configs = build_configs(settings, chain_web3, deployer.address)
    activation = LagoonLighterActivation(hub_chain=settings.chains[0], max_bridge_fee=settings.max_fee, api_key_index=settings.api_key_index, report_path=settings.report)
    if preflight_only:
        preflight_lighter_multichain(chain_web3, configs, deployer.account, activation)
        logger.info("Preflight passed; no transactions sent")
        return
    complete = False
    logger.info("Deployment progress and key backup: %s", settings.report)
    try:
        result = deploy_multichain_lagoon_vault(chain_web3=chain_web3, deployer=deployer.account, chain_configs=configs, lighter_activation=activation)
        setup = result.deployments["ethereum"].lighter_account_setup
        logger.info("Deployment complete: Safe %s, Lighter account %d, API-key slot %d", result.safe_address, setup.account_index, setup.api_key_index)
        complete = True
    finally:
        if not complete:
            logger.error("Deployment incomplete. Inspect %s and transaction logs before manual recovery; do not blindly rerun.", settings.report)


def main() -> None:
    """Load private environment inputs and run the manual mainnet deployment.

    See the module documentation for funding requirements and read-only mode.
    """
    setup_console_logging(os.environ.get("LOG_LEVEL", "info"))
    assert os.environ.get("NETWORK", "mainnet") == "mainnet", "Lighter activation requires mainnet"
    assert os.environ.get("SIMULATE", "false").lower() in {"false", "0", ""}, "Lighter's live sequencer cannot observe Anvil deposits"
    settings = Settings.from_env()
    deployer = HotWallet.from_private_key(os.environ["LAGOON_MULTCHAIN_TEST_PRIVATE_KEY"])
    chain_web3 = {name: create_multi_provider_web3(os.environ[f"JSON_RPC_{name.upper()}"], default_http_timeout=(3, 180)) for name in settings.chains}
    run_deployment(settings, chain_web3, deployer, preflight_only=os.environ.get("PREFLIGHT_ONLY", "false").lower() == "true")


if __name__ == "__main__":
    main()
