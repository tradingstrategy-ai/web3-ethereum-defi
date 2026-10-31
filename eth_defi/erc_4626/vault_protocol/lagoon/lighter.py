"""Activate a Lighter satellite using the deployer's prefunded Ethereum USDC.

All contracts are prepared first. The deployer then contributes the minimum
activation amount directly to the Ethereum Safe; no hub subscription or CCTP
transfer is performed. Final ownership follows verified key registration.
The contribution remains vault-owned collateral and mints no Lagoon shares.

See https://apidocs.lighter.xyz/docs/api-keys.
"""

import logging
import time
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.cctp.constants import CHAIN_ID_TO_CCTP_DOMAIN
from eth_defi.cctp.whitelist import CCTPDeployment
from eth_defi.chain import CHAIN_NAMES
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonAutomatedDeployment, LagoonConfig
from eth_defi.hotwallet import HotWallet
from eth_defi.hyperliquid.evm_escrow import is_account_activated
from eth_defi.lighter.activation import activate_lighter_safe, default_activation_report_path
from eth_defi.lighter.api import LIGHTER_MIN_MAINNET_USDC
from eth_defi.lighter.constants import LIGHTER_USDC_ETHEREUM
from eth_defi.lighter.deployment import LighterDeployment
from eth_defi.lighter.execution import reconcile_bootstrap
from eth_defi.lighter.lagoon import broadcast_tx
from eth_defi.lighter.pubkey import MAX_API_KEY_INDEX, MIN_API_KEY_INDEX
from eth_defi.provider.anvil import is_anvil
from eth_defi.safe.deployment import SAFE_PROXY_FACTORY_ADDRESS
from eth_defi.token import USDC_NATIVE_TOKEN, TokenDetails, fetch_erc20_details

logger = logging.getLogger(__name__)

#: Recovery instructions for missing prefunding; contains an environment reference, never a key.
LIGHTER_PREFUNDING_HELP = 'See docs/README-lighter-core-execution.md#deployment. Load LAGOON_MULTCHAIN_TEST_PRIVATE_KEY for this deployer from your private environment, then run: PRIVATE_KEY="${LAGOON_MULTCHAIN_TEST_PRIVATE_KEY:?Load the deployment key first}" SOURCE_CHAIN=arbitrum TARGET_CHAINS={chains} SOURCE_TOKEN=usdc MIN_GAS_USD=5 TOP_UP_GAS_USD=10 ETHEREUM_USDC_AMOUNT=5 DRY_RUN=false poetry run python scripts/lifi/feed-cross-chain.py\nUse DRY_RUN=true to inspect quotes first. Arbitrum needs source ETH and USDC; set ETHEREUM_USDC_AMOUNT=0 if Ethereum already holds the activation USDC.'


@dataclass(slots=True)
class LagoonLighterActivation:
    """Small optional bootstrap configuration with sensible defaults."""

    #: Hub containing the primary Lagoon vault; no hub funds are spent by activation.
    hub_chain: str = "hyperliquid"
    #: Ethereum satellite name in the multichain configuration.
    ethereum_chain: str = "ethereum"
    #: User API-key slot.
    api_key_index: int = MIN_API_KEY_INDEX
    #: Protected progress/key file, chosen before any broadcasts.
    report_path: Path = field(default_factory=default_activation_report_path)


def preflight_lighter_multichain(
    chain_web3: dict[str, Web3],
    chain_configs: dict[str, LagoonConfig],
    deployer: LocalAccount,
    activation: LagoonLighterActivation,
) -> TokenDetails:
    """Validate every selected configuration before deploying any contracts.

    No transaction is sent and no report/key file is created by this function.
    Ethereum deployer funding is checked again before capital moves.

    :param chain_web3: All participating chain connections.
    :param chain_configs: Per-chain deployment configuration.
    :param deployer: Wallet supplying bootstrap capital and temporary ownership.
    :param activation: Hub, satellite, report and key slot.
    :return: Ethereum native USDC, held by the deployer for activation.
    """
    # Prefund the deployer, not the deterministic Safe; see
    # ``docs/README-lighter-core-execution.md#deployment``. Default topology:
    # PRIVATE_KEY="${LAGOON_MULTCHAIN_TEST_PRIVATE_KEY:?Load the deployment key first}" SOURCE_CHAIN=arbitrum TARGET_CHAINS=ethereum,hyperliquid SOURCE_TOKEN=usdc MIN_GAS_USD=5 TOP_UP_GAS_USD=10 ETHEREUM_USDC_AMOUNT=5 DRY_RUN=false poetry run python scripts/lifi/feed-cross-chain.py
    # The assertion message includes all selected destination chains. Source
    # Arbitrum ETH/USDC must already exist; no bridge runs inside deployment.
    funding_help = LIGHTER_PREFUNDING_HELP.replace("{chains}", ",".join(name for name in chain_web3 if name != "arbitrum"))
    assert activation.hub_chain != activation.ethereum_chain
    assert {activation.hub_chain, activation.ethereum_chain} <= chain_web3.keys()
    assert chain_web3.keys() == chain_configs.keys()
    assert MIN_API_KEY_INDEX <= activation.api_key_index <= MAX_API_KEY_INDEX
    assert not activation.report_path.exists(), "Activation report already exists; reconcile before starting a fresh deployment"
    salts = {config.safe_salt_nonce for config in chain_configs.values()}
    assert len(salts) == 1 and None not in salts, "Fresh deployments need one shared deterministic Safe salt"
    factories = {(config.safe_proxy_factory_address or SAFE_PROXY_FACTORY_ADDRESS).lower() for config in chain_configs.values()}
    assert len(factories) == 1, "Deterministic Safes require the same proxy factory on every chain"
    ids = {name: web3.eth.chain_id for name, web3 in chain_web3.items()}
    assert ids[activation.ethereum_chain] == 1
    assert len(set(ids.values())) == len(ids), "Two chain names resolve to the same chain"
    for name, config in chain_configs.items():
        chain_id = ids[name]
        assert CHAIN_NAMES.get(chain_id, "").lower() == name, f"RPC chain ID mismatch for {name}: {chain_id}"
        assert chain_id in USDC_NATIVE_TOKEN
        assert config.satellite_chain == (name != activation.hub_chain), "Exactly one primary Lagoon vault is required"
        assert not (config.guard_only or config.existing_vault_address or config.existing_safe_address), "Activation supports fresh deployments only"
        assert not config.generate_lighter_api_key, "Use coordinated activation, not per-chain activation"
        assert not (config.lighter_sub_account_count or config.lighter_sub_account_labels), "Coordinated activation does not create sub-accounts"
        managers = config.asset_managers or [config.asset_manager]
        assert managers and str(managers[0]).lower() == deployer.address.lower(), "Deployer must be primary asset manager"
        assert not config.any_asset, "Lighter multichain activation requires explicit asset permissions"
        assert config.parameters.underlying is None or config.parameters.underlying.lower() == USDC_NATIVE_TOKEN[chain_id].lower()
        assert any(a.lower() == USDC_NATIVE_TOKEN[chain_id].lower() for a in config.assets or []), "Native USDC must be explicitly allowed"
        addresses = [USDC_NATIVE_TOKEN[chain_id], config.safe_proxy_factory_address or SAFE_PROXY_FACTORY_ADDRESS]
        if config.cctp_deployment is not None:
            canonical = CCTPDeployment.create_for_chain(chain_id)
            assert config.cctp_deployment.token_messenger.lower() == canonical.token_messenger.lower(), "CCTP burn target differs from the helper deployment"
            assert config.cctp_deployment.message_transmitter.lower() == canonical.message_transmitter.lower()
            addresses.extend((canonical.token_messenger, canonical.message_transmitter))
            assert all(CHAIN_ID_TO_CCTP_DOMAIN[other_id] in config.cctp_deployment.allowed_destination_domains for other_name, other_id in ids.items() if other_name != name)
        for address in addresses:
            assert chain_web3[name].eth.get_code(Web3.to_checksum_address(address)), f"Missing required contract on {name}: {address}"
        assert config.safe_owners and 1 <= config.safe_threshold <= len({a.lower() for a in config.safe_owners} | {deployer.address.lower()})
        native_balance = chain_web3[name].eth.get_balance(deployer.address)
        assert native_balance > 0, f"Deployer {deployer.address} needs prefunded native gas on {name}; observed {native_balance} wei. {funding_help}"
        if name == "hyperliquid" and not is_anvil(chain_web3[name]):
            assert is_account_activated(chain_web3[name], deployer.address), "Activate the deployer on Hypercore before deploying any chain; HyperEVM large-block deployment requires a recognised Core account. See docs/README-lighter-core-execution.md#deployment."
    ethereum_config = chain_configs[activation.ethereum_chain]
    assert ethereum_config.lighter_deployment == LighterDeployment.create_ethereum()
    assert chain_web3[activation.ethereum_chain].eth.get_code(ethereum_config.lighter_deployment.zk_lighter), "Lighter is not deployed on the Ethereum RPC"
    assert all(config.lighter_deployment is None for name, config in chain_configs.items() if name != activation.ethereum_chain)
    usdc = fetch_erc20_details(chain_web3[activation.ethereum_chain], LIGHTER_USDC_ETHEREUM)
    balance = usdc.fetch_balance_of(deployer.address)
    assert balance >= LIGHTER_MIN_MAINNET_USDC, f"Ethereum USDC {usdc.address}: prefund deployer {deployer.address} with {LIGHTER_MIN_MAINNET_USDC}, observed {balance}. {funding_help}"
    return usdc


def send_recorded_transaction(web3: Web3, wallet: HotWallet, call: ContractFunction, progress: Callable[..., None], stage: str) -> str:
    """Persist transaction hash/nonce before broadcast and verify its receipt.

    :param web3: Execution chain.
    :param wallet: Per-chain deployer, preserving nonce continuity.
    :param call: Prepared ERC-20 funding or guarded custody call.
    :param progress: Protected deployment recorder.
    :param stage: Human-readable operation identifier.
    :return: Confirmed transaction hash.
    """
    tx_hash = broadcast_tx(web3, wallet, call, stage, tx_observer=lambda tx_hash, nonce, _description: progress(stage + "_signed", chain_id=web3.eth.chain_id, tx_hash=tx_hash, nonce=nonce))
    progress(stage + "_confirmed", tx_hash=tx_hash)
    return tx_hash


def wait_for_safe_usdc(usdc: TokenDetails, address: HexAddress, minimum: Decimal, timeout: float = 180) -> Decimal:
    """Wait for settled native USDC rather than assuming requested amounts arrived.

    :param usdc: Destination native token metadata.
    :param address: Safe recipient address.
    :param minimum: Minimum observed balance.
    :param timeout: Bounded polling interval in seconds.
    :return: Actual balance after receipt becomes visible.
    """
    deadline = time.monotonic() + timeout
    while True:
        balance = usdc.fetch_balance_of(address)
        if balance >= minimum:
            return balance
        if time.monotonic() >= deadline:
            raise TimeoutError(f"USDC receipt for Safe {address} is not visible; reconcile before replaying")
        logger.info("Waiting for Safe %s USDC: %s, need %s", address, balance, minimum)
        time.sleep(5)


def fund_and_activate_lighter(
    chain_web3: dict[str, Web3],
    deployments: dict[str, LagoonAutomatedDeployment],
    wallets: dict[str, HotWallet],
    activation: LagoonLighterActivation,
    progress: Callable[..., None],
) -> tuple[dict[str, LagoonAutomatedDeployment], dict[str, Any]]:
    """Contribute prefunded Ethereum USDC to the satellite and activate Lighter.

    All workers must have completed before this function. Owner finalisation
    follows successful return. Timeouts preserve the original transfer evidence.
    The contribution creates collateral without issuing Lagoon shares; include
    it once in NAV before later investor subscriptions are settled.

    :param chain_web3: Checked selected networks.
    :param deployments: Completed contract setup results.
    :param wallets: Original per-chain worker wallets and nonce states.
    :param activation: Funding and protected report configuration.
    :param progress: Recorder called before irreversible steps.
    :return: Updated Ethereum deployment and redacted bootstrap accounting.
    """
    eth = activation.ethereum_chain
    eth_web3, wallet, satellite = chain_web3[eth], wallets[eth], deployments[eth]
    assert {owner.lower() for owner in satellite.vault.safe.retrieve_owners()} == {wallet.address.lower()}, "Ethereum Safe must retain temporary deployer ownership before funding"
    assert satellite.vault.safe.retrieve_threshold() == 1
    safe = satellite.safe_address
    # Contract deployment consumes gas and another process may spend the USDC.
    # Recheck before the contribution; use the prefunding command documented in
    # ``docs/README-lighter-core-execution.md#deployment``, then recover the saved
    # deployment explicitly instead of rerunning fresh deployment after a failure.
    funding_help = LIGHTER_PREFUNDING_HELP.replace("{chains}", ",".join(name for name in chain_web3 if name != "arbitrum"))
    assert eth_web3.eth.chain_id == 1, "Lighter prefunding must use Ethereum mainnet"
    for name, web3 in chain_web3.items():
        assert web3.eth.get_balance(wallets[name].address) > 0, f"Deployer {wallets[name].address} has no native gas left on {name} for activation/owner finalisation. {funding_help}"
    usdc = fetch_erc20_details(eth_web3, LIGHTER_USDC_ETHEREUM)
    assert usdc.fetch_balance_of(wallet.address) >= LIGHTER_MIN_MAINNET_USDC, f"Prefund deployer {wallet.address} with {LIGHTER_MIN_MAINNET_USDC} USDC on Ethereum before activation. {funding_help}"
    assert all(fetch_erc20_details(web3, USDC_NATIVE_TOKEN[web3.eth.chain_id]).fetch_balance_of(deployments[name].safe_address) == 0 for name, web3 in chain_web3.items()), "Fresh Safes have unexpected balances; reconcile before activation"
    progress("ethereum_funding_started", funder=wallet.address, safe_address=safe, amount_usdc=str(LIGHTER_MIN_MAINNET_USDC))
    funding_hash = send_recorded_transaction(eth_web3, wallet, usdc.transfer(safe, LIGHTER_MIN_MAINNET_USDC), progress, "ethereum_funding")
    received = wait_for_safe_usdc(usdc, safe, LIGHTER_MIN_MAINNET_USDC)
    assert received == LIGHTER_MIN_MAINNET_USDC, "Unexpected Ethereum Safe balance; reconcile before activation"
    setup = activate_lighter_safe(eth_web3, wallet, satellite.vault, api_key_index=activation.api_key_index, progress=progress)
    deployments = dict(deployments)
    deployments[eth] = replace(satellite, lighter_account_setup=setup)
    balances = tuple(fetch_erc20_details(web3, USDC_NATIVE_TOKEN[web3.eth.chain_id]).fetch_balance_of(deployments[name].safe_address) for name, web3 in chain_web3.items())
    reconcile_bootstrap(received, balances, setup.observed_collateral, Decimal(0), Decimal(0))
    funding = {"funding_source": "ethereum_deployer", "funding_chain": eth, "funder": wallet.address, "contribution_usdc": str(received), "subscription_usdc": "0", "funding_tx_hash": funding_hash, "lighter_collateral_usdc": str(setup.observed_collateral)}
    progress("bootstrap_reconciled", **funding)
    return deployments, funding
