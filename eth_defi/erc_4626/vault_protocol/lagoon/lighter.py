"""Coordinated Lighter satellite funding after multichain contract preparation.

A hub subscription creates accounted capital. CCTP moves only activation
capital to Ethereum, and Safe ownership is finalised by the caller after key
verification. No additional NAV settlement or automatic recovery is performed.

See https://developers.circle.com/cctp and https://apidocs.lighter.xyz/docs/api-keys.
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

from eth_defi.cctp.attestation import fetch_attestation
from eth_defi.cctp.constants import CHAIN_ID_TO_CCTP_DOMAIN
from eth_defi.cctp.fees import CCTPFeeQuote, fetch_cctp_fee_quote
from eth_defi.cctp.receive import fetch_message_received, prepare_receive_message
from eth_defi.cctp.transfer import prepare_approve_for_burn, prepare_deposit_for_burn
from eth_defi.cctp.whitelist import CCTPDeployment
from eth_defi.chain import CHAIN_NAMES
from eth_defi.confirmation import Reverted
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonAutomatedDeployment, LagoonConfig
from eth_defi.erc_4626.vault_protocol.lagoon.funding import fund_lagoon_vault
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.activation import activate_lighter_safe, default_activation_report_path
from eth_defi.lighter.api import LIGHTER_MIN_MAINNET_USDC
from eth_defi.lighter.constants import LIGHTER_USDC_ETHEREUM
from eth_defi.lighter.deployment import LighterDeployment
from eth_defi.lighter.execution import reconcile_bootstrap
from eth_defi.lighter.lagoon import broadcast_tx
from eth_defi.lighter.pubkey import MAX_API_KEY_INDEX, MIN_API_KEY_INDEX
from eth_defi.safe.deployment import SAFE_PROXY_FACTORY_ADDRESS
from eth_defi.token import USDC_NATIVE_TOKEN, TokenDetails, fetch_erc20_details

logger = logging.getLogger(__name__)

#: Accounted multichain capital, including activation and its bridge fee allowance.
LIGHTER_MULTICHAIN_BOOTSTRAP_SUBSCRIPTION = Decimal(20)


@dataclass(slots=True)
class LagoonLighterActivation:
    """Small optional bootstrap configuration with sensible defaults."""

    #: Hub containing the primary Lagoon vault and deployer's USDC.
    hub_chain: str = "hyperliquid"
    #: Ethereum satellite name in the multichain configuration.
    ethereum_chain: str = "ethereum"
    #: Gross burn includes this maximum bridge fee within the subscription.
    max_bridge_fee: Decimal = Decimal("0.01")
    #: User API-key slot.
    api_key_index: int = MIN_API_KEY_INDEX
    #: Protected progress/key file, chosen before any broadcasts.
    report_path: Path = field(default_factory=default_activation_report_path)


def preflight_lighter_multichain(
    chain_web3: dict[str, Web3],
    chain_configs: dict[str, LagoonConfig],
    deployer: LocalAccount,
    activation: LagoonLighterActivation,
) -> tuple[TokenDetails, CCTPFeeQuote]:
    """Validate every selected configuration before deploying any contracts.

    No transaction is sent and no report/key file is created by this function.
    Funding and fee availability are checked again before capital moves.

    :param chain_web3: All participating chain connections.
    :param chain_configs: Per-chain deployment configuration.
    :param deployer: Wallet supplying bootstrap capital and temporary ownership.
    :param activation: Hub, satellite, fee allowance and key slot.
    :return: Hub native USDC and a currently valid bridge quote.
    """
    assert activation.hub_chain != activation.ethereum_chain
    assert {activation.hub_chain, activation.ethereum_chain} <= chain_web3.keys()
    assert chain_web3.keys() == chain_configs.keys()
    assert activation.max_bridge_fee.is_finite() and Decimal(0) <= activation.max_bridge_fee <= LIGHTER_MULTICHAIN_BOOTSTRAP_SUBSCRIPTION - LIGHTER_MIN_MAINNET_USDC
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
        assert chain_id in USDC_NATIVE_TOKEN and chain_id in CHAIN_ID_TO_CCTP_DOMAIN
        assert config.satellite_chain == (name != activation.hub_chain), "Exactly one primary Lagoon vault is required"
        assert not (config.guard_only or config.existing_vault_address or config.existing_safe_address), "Activation supports fresh deployments only"
        assert not config.generate_lighter_api_key, "Use coordinated activation, not per-chain activation"
        assert not (config.lighter_sub_account_count or config.lighter_sub_account_labels), "Coordinated activation does not create sub-accounts"
        managers = config.asset_managers or [config.asset_manager]
        assert managers and str(managers[0]).lower() == deployer.address.lower(), "Deployer must be primary asset manager"
        assert not config.any_asset, "Lighter multichain activation requires explicit asset permissions"
        assert config.parameters.underlying is None or config.parameters.underlying.lower() == USDC_NATIVE_TOKEN[chain_id].lower()
        assert any(a.lower() == USDC_NATIVE_TOKEN[chain_id].lower() for a in config.assets or []), "Native USDC must be explicitly allowed"
        assert config.cctp_deployment is not None
        canonical = CCTPDeployment.create_for_chain(chain_id)
        assert config.cctp_deployment.token_messenger.lower() == canonical.token_messenger.lower(), "CCTP burn target differs from the helper deployment"
        assert config.cctp_deployment.message_transmitter.lower() == canonical.message_transmitter.lower()
        for address in (USDC_NATIVE_TOKEN[chain_id], config.safe_proxy_factory_address or SAFE_PROXY_FACTORY_ADDRESS, canonical.token_messenger, canonical.message_transmitter):
            assert chain_web3[name].eth.get_code(Web3.to_checksum_address(address)), f"Missing required contract on {name}: {address}"
        assert all(CHAIN_ID_TO_CCTP_DOMAIN[other_id] in config.cctp_deployment.allowed_destination_domains for other_name, other_id in ids.items() if other_name != name)
        assert config.safe_owners and 1 <= config.safe_threshold <= len({a.lower() for a in config.safe_owners} | {deployer.address.lower()})
        assert chain_web3[name].eth.get_balance(deployer.address) > 0, f"Fund deployer native gas on {name}"
    hub_config = chain_configs[activation.hub_chain]
    assert hub_config.parameters.valuationManager is None or hub_config.parameters.valuationManager.lower() == deployer.address.lower()
    assert hub_config.max_settlement_amount is None or hub_config.max_settlement_amount >= LIGHTER_MULTICHAIN_BOOTSTRAP_SUBSCRIPTION, "Settlement cap is too small for bootstrap"
    ethereum_config = chain_configs[activation.ethereum_chain]
    assert ethereum_config.lighter_deployment == LighterDeployment.create_ethereum()
    assert chain_web3[activation.ethereum_chain].eth.get_code(ethereum_config.lighter_deployment.zk_lighter), "Lighter is not deployed on the Ethereum RPC"
    assert all(config.lighter_deployment is None for name, config in chain_configs.items() if name != activation.ethereum_chain)
    usdc = fetch_erc20_details(chain_web3[activation.hub_chain], USDC_NATIVE_TOKEN[ids[activation.hub_chain]])
    balance = usdc.fetch_balance_of(deployer.address)
    assert balance >= LIGHTER_MULTICHAIN_BOOTSTRAP_SUBSCRIPTION, f"Hub {activation.hub_chain} USDC {usdc.address}: deployer needs {LIGHTER_MULTICHAIN_BOOTSTRAP_SUBSCRIPTION}, observed {balance}"
    quote = fetch_cctp_fee_quote(usdc, ids[activation.hub_chain], 1, LIGHTER_MIN_MAINNET_USDC, activation.max_bridge_fee)
    return usdc, quote


def send_recorded_transaction(web3: Web3, wallet: HotWallet, call: ContractFunction, progress: Callable[..., None], stage: str) -> str:
    """Persist transaction hash/nonce before broadcast and verify its receipt.

    :param web3: Execution chain.
    :param wallet: Per-chain deployer, preserving nonce continuity.
    :param call: Prepared guarded call or permissionless CCTP relay.
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


def fund_and_activate_lighter(  # noqa: PLR0914
    chain_web3: dict[str, Web3],
    deployments: dict[str, LagoonAutomatedDeployment],
    wallets: dict[str, HotWallet],
    activation: LagoonLighterActivation,
    progress: Callable[..., None],
) -> tuple[dict[str, LagoonAutomatedDeployment], dict[str, Any]]:
    """Subscribe on the hub, bridge to Ethereum and activate the satellite.

    All workers must have completed before this function. Owner finalisation
    follows successful return. Timeouts preserve the original transfer evidence.

    :param chain_web3: Checked selected networks.
    :param deployments: Completed contract setup results.
    :param wallets: Original per-chain worker wallets and nonce states.
    :param activation: Funding and protected report configuration.
    :param progress: Recorder called before irreversible steps.
    :return: Updated Ethereum deployment and redacted bootstrap accounting.
    """
    hub, eth = activation.hub_chain, activation.ethereum_chain
    source_web3, eth_web3 = chain_web3[hub], chain_web3[eth]
    source, satellite = deployments[hub], deployments[eth]
    assert {owner.lower() for owner in satellite.vault.safe.retrieve_owners()} == {wallets[eth].address.lower()}, "Ethereum Safe must retain temporary deployer ownership before funding"
    assert satellite.vault.safe.retrieve_threshold() == 1
    safe = satellite.safe_address
    source_usdc = fetch_erc20_details(source_web3, USDC_NATIVE_TOKEN[source_web3.eth.chain_id])
    eth_usdc = fetch_erc20_details(eth_web3, LIGHTER_USDC_ETHEREUM)
    assert source_usdc.fetch_balance_of(wallets[hub].address) >= LIGHTER_MULTICHAIN_BOOTSTRAP_SUBSCRIPTION
    quote = fetch_cctp_fee_quote(source_usdc, source_web3.eth.chain_id, 1, LIGHTER_MIN_MAINNET_USDC, activation.max_bridge_fee)
    # Fresh deployment accounting cannot safely incorporate unsolicited donations.
    assert source_usdc.fetch_balance_of(safe) == 0 and eth_usdc.fetch_balance_of(safe) == 0, "Fresh Safes have unexpected balances; reconcile before subscribing"
    progress("subscription_started", hub_chain=hub, amount=str(LIGHTER_MULTICHAIN_BOOTSTRAP_SUBSCRIPTION), nonce=wallets[hub].current_nonce)
    fund_lagoon_vault(web3=source_web3, vault_address=source.vault.address, asset_manager=wallets[hub].address, test_account_with_balance=wallets[hub].address, trading_strategy_module_address=source.trading_strategy_module.address, amount=LIGHTER_MULTICHAIN_BOOTSTRAP_SUBSCRIPTION, hot_wallet=wallets[hub])
    wait_for_safe_usdc(source_usdc, safe, LIGHTER_MULTICHAIN_BOOTSTRAP_SUBSCRIPTION)
    progress("subscription_complete")
    approve = prepare_approve_for_burn(source_web3, quote.amount_raw)
    send_recorded_transaction(source_web3, wallets[hub], source.vault.transact_via_trading_strategy_module(approve), progress, "cctp_approve")
    quote = fetch_cctp_fee_quote(source_usdc, source_web3.eth.chain_id, 1, LIGHTER_MIN_MAINNET_USDC, activation.max_bridge_fee)
    progress("bridge_started", gross_raw=quote.amount_raw, max_fee_raw=quote.max_fee_raw, quoted_fee_raw=quote.fee_raw, finality=quote.finality)
    burn = prepare_deposit_for_burn(source_web3, amount=quote.amount_raw, destination_chain_id=1, mint_recipient=safe, max_fee=quote.max_fee_raw, min_finality_threshold=quote.finality)
    burn_hash = send_recorded_transaction(source_web3, wallets[hub], source.vault.transact_via_trading_strategy_module(burn), progress, "cctp_burn")
    attestation = fetch_attestation(CHAIN_ID_TO_CCTP_DOMAIN[source_web3.eth.chain_id], burn_hash, timeout=900)
    receive_hash = None
    if not fetch_message_received(eth_web3, attestation.message):
        receive = prepare_receive_message(eth_web3, attestation.message, attestation.attestation)
        try:
            receive_hash = send_recorded_transaction(eth_web3, wallets[eth], receive, progress, "cctp_receive")
        except Reverted:
            if not fetch_message_received(eth_web3, attestation.message):
                raise
            progress("cctp_receive_relayed_externally")
    else:
        progress("cctp_receive_relayed_externally")
    received = wait_for_safe_usdc(eth_usdc, safe, LIGHTER_MIN_MAINNET_USDC)
    gross = source_usdc.convert_to_decimals(quote.amount_raw)
    assert received <= gross, "Unexpected destination balance increase"
    fee = gross - received
    assert Decimal(0) <= fee <= activation.max_bridge_fee
    progress("bridge_complete", received_usdc=str(received), fee_usdc=str(fee))
    setup = activate_lighter_safe(eth_web3, wallets[eth], satellite.vault, api_key_index=activation.api_key_index, progress=progress)
    deployments = dict(deployments)
    deployments[eth] = replace(satellite, lighter_account_setup=setup)
    balances = tuple(fetch_erc20_details(chain_web3[name], USDC_NATIVE_TOKEN[chain_web3[name].eth.chain_id]).fetch_balance_of(safe) for name in chain_web3)
    reconcile_bootstrap(LIGHTER_MULTICHAIN_BOOTSTRAP_SUBSCRIPTION, balances, setup.observed_collateral, Decimal(0), fee)
    funding = {"hub_chain": hub, "subscription_usdc": str(LIGHTER_MULTICHAIN_BOOTSTRAP_SUBSCRIPTION), "burn_tx_hash": burn_hash, "receive_tx_hash": receive_hash, "bridge_received_usdc": str(received), "actual_bridge_fee_usdc": str(fee), "lighter_collateral_usdc": str(setup.observed_collateral)}
    progress("bootstrap_reconciled", **funding)
    return deployments, funding
