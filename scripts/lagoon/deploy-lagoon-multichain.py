"""Tutorial: Deploying Lagoon vaults across multiple chains with CCTP bridging.

Supports two network modes:

- **mainnet** (default): 5 chains — Arbitrum, Ethereum, Base, HyperEVM, Monad
- **testnet**: 2 chains — Arbitrum Sepolia, Base Sepolia (no vault whitelisting)

All chains share the same deterministic Safe address via CREATE2.
After deployment, bridges 1 USDC from the source vault to each other
chain via Circle's CCTP V2 protocol to verify cross-chain connectivity.

The deployer needs ETH, HYPE and MONAD balance on all chains.

Environment variables
---------------------

``NETWORK``
    ``mainnet`` (default) or ``testnet``. Selects which set of chains
    and RPC variables to use.

``SIMULATE``
    Set to ``true`` to run using Anvil mainnet/testnet forks with forged
    CCTP attestations. No real transactions are sent.

``LAGOON_MULTCHAIN_TEST_PRIVATE_KEY``
    Deployer private key. Required in real (non-simulate) mode.
    Must hold native gas token balance on all chains.

``SALT_NONCE``
    Optional CREATE2 salt for deterministic Safe address.
    Defaults to a random integer.

``CHAINS``
    Comma-separated list of chain names to deploy on.
    The first chain is the source vault (deposit/redeem entry point).
    Defaults to all chains for the selected network:
    mainnet ``arbitrum,ethereum,base,hyperliquid,monad``,
    testnet ``arbitrum_sepolia,base_sepolia``.
    Example: ``CHAINS=arbitrum,base`` to deploy only on two chains.

``USDC_AMOUNT``
    Amount of USDC to deposit into the source vault for bridge testing.
    Defaults to ``2``.

``BRIDGED_USDC_AMOUNT``
    Amount of USDC to bridge per destination chain.
    Defaults to ``0.1``.

``JSON_RPC_ARBITRUM``
    Arbitrum One RPC URL (mainnet mode).

``JSON_RPC_ETHEREUM``
    Ethereum mainnet RPC URL (mainnet mode).

``JSON_RPC_BASE``
    Base mainnet RPC URL (mainnet mode).

``JSON_RPC_HYPERLIQUID``
    HyperEVM RPC URL (mainnet mode).

``JSON_RPC_MONAD``
    Monad RPC URL (mainnet mode).

``JSON_RPC_ARBITRUM_SEPOLIA``
    Arbitrum Sepolia RPC URL (testnet mode).

``JSON_RPC_BASE_SEPOLIA``
    Base Sepolia RPC URL (testnet mode).

Mainnet simulation
------------------

.. code-block:: shell

    SIMULATE=true \\
    JSON_RPC_ETHEREUM="https://..." \\
    JSON_RPC_ARBITRUM="https://..." \\
    JSON_RPC_BASE="https://..." \\
    JSON_RPC_HYPERLIQUID="https://..." \\
    JSON_RPC_MONAD="https://..." \\
    poetry run python scripts/lagoon/deploy-lagoon-multichain.py

Testnet deployment
------------------


.. code-block:: shell

    NETWORK=testnet python scripts/lagoon/deploy-lagoon-multichain.py

.. note::

    Testnet simulation (``NETWORK=testnet SIMULATE=true``) is **not supported**
    because Lagoon factory contracts are not deployed on Sepolia chains.
    From-scratch deployment requires Forge. Use mainnet simulation
    (``SIMULATE=true``) for local testing.


Unlike mainnet deployment, we do not use a factory contracts, but both Gnosis Safe and all Lagoon contracts are deployed from the scratch.

To get testnet ERH funding, use `thirdweb faucet for Base sepolia <https://thirdweb.com/base-sepolia-testnet>`__ and `LearnWeb3 faucet for Arbitrum sepolia <https://learnweb3.io/faucets/arbitrum_sepolia/>`__.
Get testnet USDC from `Circle faucet <https://faucet.circle.com/>`__.

Architecture overview
---------------------

Mainnet::

    Arbitrum (Lagoon vault — deposit/redeem entry)
        │
        ├── CCTP V2 ────► Ethereum Safe (CowSwap + ERC-4626 vaults)
        ├── CCTP V2 ────► Base Safe (ERC-4626 vaults)
        ├── CCTP V2 ────► HyperEVM Safe (Hypercore + ERC-4626 vaults)
        └── CCTP V2 ────► Monad Safe (ERC-4626 vaults)

Testnet::

    Arbitrum Sepolia (Lagoon vault — deposit/redeem entry)
        │
        └── CCTP V2 ────► Base Sepolia Safe (CCTP only, no vaults)

All Safes share the same deterministic address across all chains.
Each chain has its own TradingStrategyModuleV0 guard with
chain-specific whitelisting rules.
"""

import logging
import os
import random
import threading
import time
from copy import deepcopy
from decimal import Decimal
from typing import cast

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.cctp.bridge import CCTPBridgeDestination, bridge_usdc_cctp_parallel
from eth_defi.cctp.constants import CHAIN_ID_TO_CCTP_DOMAIN, TESTNET_CHAIN_ID_TO_CCTP_DOMAIN
from eth_defi.cctp.testing import replace_attester_on_fork
from eth_defi.cctp.whitelist import CCTPDeployment
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonConfig, LagoonDeploymentParameters, LagoonMultichainDeployment, deploy_multichain_lagoon_vault
from eth_defi.erc_4626.vault_protocol.lagoon.testing import fund_lagoon_vault
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil, fund_erc20_on_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, USDC_WHALE, WRAPPED_NATIVE_TOKEN, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.deployment import fetch_deployment as fetch_deployment_uni_v3
from eth_defi.uniswap_v3.swap import swap_with_slippage_protection
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-chain ERC-4626 vault addresses to whitelist (top vaults by TVL)
#
# Data source: https://top-defi-vaults.tradingstrategy.ai/top_vaults_by_chain.json
# ---------------------------------------------------------------------------

#: Arbitrum ERC-4626 vaults
ARBITRUM_VAULTS: list[str] = [
    "0xacb7432a4bb15402ce2afe0a7c9d5b738604f6f9",  # Silo Finance: Borrowable USDC (SiloId 146)
    "0x2ba39e5388ac6c702cb29aea78d52aa66832f1ee",  # Euler: Varlamore USDC Growth
    "0x0b2b2b2076d95dda7817e785989fe353fe955ef9",  # USDai: Staked USDai
    "0x2433d6ac11193b4695d9ca73530de93c538ad18a",  # Silo Finance: Borrowable USDC (SiloId 127)
]

#: Ethereum ERC-4626 vaults
ETHEREUM_VAULTS: list[str] = [
    "0x4880799ee5200fc58da299e965df644fbf46780b",  # Centrifuge: Anemoy AAA CLO Fund
    "0xe9d1f733f406d4bbbdfac6d4cfcd2e13a6ee1d01",  # Centrifuge: Anemoy AAA CLO Fund
    "0xfe7c47895edb12a990b311df33b90cfea1d44c24",  # Euler: bUSD0 Zero Rate Vault
    "0xc5d6a7b61d18afa11435a889557b068bb9f29930",  # Decentralized USD: Savings Usdd
]

#: Base ERC-4626 vaults
BASE_VAULTS: list[str] = [
    "0xee8f4ec5672f09119b96ab6fb59c27e1b7e44b61",  # Morpho: Gauntlet USDC Prime
    "0xbeefe94c8ad530842bfe7d8b397938ffc1cb83b2",  # Morpho: Steakhouse Prime USDC
    "0xbeef010f9cb27031ad51e3333f9af9c6b1228183",  # Morpho: Steakhouse USDC
    "0x944766f715b51967e56afde5f0aa76ceacc9e7f9",  # Avantis USDC Vault
]

#: HyperEVM ERC-4626 vaults (chain 999)
HYPEREVM_VAULTS: list[str] = [
    "0x8a862fd6c12f9ad34c9c2ff45ab2b6712e8cea27",  # Morpho: Felix USDC
    "0x808f72b6ff632fba005c88b49c2a76ab01cab545",  # Morpho: Felix USDC (Frontier)
    "0x274f854b2042db1aa4d6c6e45af73588bed4fc9d",  # Morpho: Felix USDH (Frontier)
    "0xfc5126377f0efc0041c0969ef9ba903ce67d151e",  # Morpho: Felix USDT0
]

#: Hypercore native vaults (chain 9999 in data source, whitelisted on HyperEVM via CoreWriter)
HYPERCORE_VAULT_ADDRESSES: list[str] = [
    "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303",  # Hyperliquidity Provider (HLP)
    "0x31ca8395cf837de08b24da3f660e77761dfb974b",  # HLP Strategy B
    "0x010461c14e146ac35fe42271bdc1134ee31c703a",  # HLP Strategy A
    "0xb0a55f13d22f66e6d495ac98113841b2326e9540",  # HLP Liquidator 2
]

#: Monad ERC-4626 vaults
MONAD_VAULTS: list[str] = [
    "0x8d3f9f9eb2f5e8b48efbb4074440d1e2a34bc365",  # Accountable: RWA Backed Lending by Valos
    "0x7cd231120a60f500887444a9baf5e1bd753a5e59",  # Accountable: Hyperithm Delta Neutral
    "0x6b343f7b797f1488aa48c49d540690f2b2c89751",  # Gearbox: EDGE UltraYield USDC
    "0xad4aa2a713fb86fbb6b60de2af9e32a11db6abf2",  # Curvance: Curvance AUSD
]

# ---------------------------------------------------------------------------
# Network configuration
# ---------------------------------------------------------------------------

#: Mainnet chain names to RPC environment variables
MAINNET_CHAIN_RPC_ENV_VARS: dict[str, str] = {
    "arbitrum": "JSON_RPC_ARBITRUM",
    "ethereum": "JSON_RPC_ETHEREUM",
    "base": "JSON_RPC_BASE",
    "hyperliquid": "JSON_RPC_HYPERLIQUID",
    "monad": "JSON_RPC_MONAD",
}

#: Mainnet chain names to expected chain IDs
MAINNET_CHAIN_ID_MAP: dict[str, int] = {
    "arbitrum": 42161,
    "ethereum": 1,
    "base": 8453,
    "hyperliquid": 999,
    "monad": 143,
}

#: Testnet chain names to RPC environment variables
TESTNET_CHAIN_RPC_ENV_VARS: dict[str, str] = {
    "arbitrum_sepolia": "JSON_RPC_ARBITRUM_SEPOLIA",
    "base_sepolia": "JSON_RPC_BASE_SEPOLIA",
}

#: Testnet chain names to expected chain IDs
TESTNET_CHAIN_ID_MAP: dict[str, int] = {
    "arbitrum_sepolia": 421614,
    "base_sepolia": 84532,
}

#: Default chain ordering. First chain is the source vault (deposit/redeem entry point).
MAINNET_DEFAULT_CHAINS: list[str] = ["arbitrum", "ethereum", "base", "hyperliquid", "monad"]
TESTNET_DEFAULT_CHAINS: list[str] = ["arbitrum_sepolia", "base_sepolia"]

#: Testnet chain names to Uniswap V3 deployment keys in ``UNISWAP_V3_DEPLOYMENTS``
TESTNET_UNISWAP_V3_KEYS: dict[str, str] = {
    "arbitrum_sepolia": "arbitrum_sepolia",
    "base_sepolia": "base_sepolia",
}

#: Per-chain vault whitelisting and feature configuration (mainnet only).
#: Keys that are absent get a plain config with no vaults.
MAINNET_CHAIN_FEATURES: dict[str, dict] = {
    "arbitrum": {
        "erc_4626_vaults_list": ARBITRUM_VAULTS,
    },
    "ethereum": {
        "erc_4626_vaults_list": ETHEREUM_VAULTS,
        "cowswap": True,
    },
    "base": {
        "erc_4626_vaults_list": BASE_VAULTS,
    },
    "hyperliquid": {
        "erc_4626_vaults_list": HYPEREVM_VAULTS,
        "hypercore_vaults": HYPERCORE_VAULT_ADDRESSES,
    },
    "monad": {
        "erc_4626_vaults_list": MONAD_VAULTS,
    },
}


def resolve_vaults(web3: Web3, vault_addresses: list[str]) -> list[ERC4626Vault]:
    """Resolve ERC-4626 vault addresses into vault instances.

    Detects vault features and creates proper vault instances
    for whitelisting during deployment.

    :param web3:
        Web3 connection to the chain.

    :param vault_addresses:
        List of ERC-4626 vault smart contract addresses.

    :return:
        List of resolved vault instances.
    """
    vaults = []
    for addr in vault_addresses:
        addr = Web3.to_checksum_address(addr)
        try:
            features = detect_vault_features(web3, addr)
            vault = cast(ERC4626Vault, create_vault_instance(web3, addr, features=features))
            if vault.is_valid():
                logger.info("Resolved vault %s: %s", addr, vault.name)
                vaults.append(vault)
            else:
                logger.warning("Skipping invalid vault at %s", addr)
        except Exception as e:
            logger.warning("Could not resolve vault at %s: %s", addr, e)
    return vaults


def create_multichain_whitelisting_configuration(
    chain_web3: dict[str, Web3],
    asset_manager: HexAddress,
    safe_owners: list[HexAddress],
    safe_threshold: int,
    safe_salt_nonce: int,
    source_chain: str | None = None,
) -> dict[str, LagoonConfig]:
    """Build per-chain LagoonConfig dicts for mainnet deployment.

    Only configures chains present in ``chain_web3`` (controlled by
    the ``CHAINS`` environment variable). Per-chain vault lists and
    features are looked up from :data:`MAINNET_CHAIN_FEATURES`.

    :param chain_web3:
        Mapping of chain names to Web3 instances.

    :param asset_manager:
        Address that manages vault assets and executes trades.

    :param safe_owners:
        Addresses of Safe multisig owners.

    :param safe_threshold:
        Number of owner signatures required.

    :param safe_salt_nonce:
        CREATE2 salt for deterministic Safe address.

    :param source_chain:
        Name of the source chain. Non-source chains are deployed as
        satellites (Safe + guard only, no vault contract).

    :return:
        Per-chain LagoonConfig dict ready for ``deploy_multichain_lagoon_vault()``.
    """
    configs: dict[str, LagoonConfig] = {}

    base_params = LagoonDeploymentParameters(
        underlying=None,  # auto-resolved per chain from USDC_NATIVE_TOKEN
        name="Multichain Strategy Vault",
        symbol="MSV",
    )

    for chain_name, web3 in chain_web3.items():
        features = MAINNET_CHAIN_FEATURES.get(chain_name, {})

        kwargs: dict = {}
        if "cowswap" in features:
            kwargs["cowswap"] = features["cowswap"]
        if "hypercore_vaults" in features:
            kwargs["hypercore_vaults"] = features["hypercore_vaults"]
        if "erc_4626_vaults_list" in features:
            kwargs["erc_4626_vaults"] = resolve_vaults(web3, features["erc_4626_vaults_list"])

        is_satellite = source_chain is not None and chain_name != source_chain
        configs[chain_name] = LagoonConfig(
            parameters=deepcopy(base_params),
            asset_manager=asset_manager,
            safe_owners=list(safe_owners),
            safe_threshold=safe_threshold,
            safe_salt_nonce=safe_salt_nonce,
            any_asset=True,
            satellite_chain=is_satellite,
            **kwargs,
        )

    # Configure CCTP for all CCTP-capable chains
    cctp_chain_ids = []
    for chain_name, web3 in chain_web3.items():
        chain_id = web3.eth.chain_id
        if chain_id in CHAIN_ID_TO_CCTP_DOMAIN:
            cctp_chain_ids.append((chain_name, chain_id))

    for chain_name, chain_id in cctp_chain_ids:
        other_ids = [cid for name, cid in cctp_chain_ids if name != chain_name]
        if other_ids:
            configs[chain_name].cctp_deployment = CCTPDeployment.create_for_chain(
                chain_id=chain_id,
                allowed_destinations=other_ids,
            )
            logger.info("CCTP configured on %s with destinations: %s", chain_name, other_ids)

    return configs


def create_testnet_whitelisting_configuration(
    chain_web3: dict[str, Web3],
    asset_manager: HexAddress,
    safe_owners: list[HexAddress],
    safe_threshold: int,
    safe_salt_nonce: int,
    source_chain: str | None = None,
) -> dict[str, LagoonConfig]:
    """Build per-chain LagoonConfig for testnet deployment.

    No vault whitelisting — only CCTP for cross-chain transfers.
    The source chain deploys the full Lagoon protocol from scratch
    since no factory exists on testnets. Satellite chains deploy
    only Safe + guard.

    :param chain_web3:
        Mapping of chain names to Web3 instances.

    :param asset_manager:
        Address that manages vault assets and executes trades.

    :param safe_owners:
        Addresses of Safe multisig owners.

    :param safe_threshold:
        Number of owner signatures required.

    :param safe_salt_nonce:
        CREATE2 salt for deterministic Safe address.

    :param source_chain:
        Name of the source chain. Non-source chains are deployed as
        satellites (Safe + guard only, no vault contract).

    :return:
        Per-chain LagoonConfig dict ready for ``deploy_multichain_lagoon_vault()``.
    """
    configs: dict[str, LagoonConfig] = {}

    base_params = LagoonDeploymentParameters(
        underlying=None,  # auto-resolved per chain from USDC_NATIVE_TOKEN
        name="Testnet Strategy Vault",
        symbol="TSV",
    )

    for chain_name in chain_web3:
        is_satellite = source_chain is not None and chain_name != source_chain
        if is_satellite:
            # Satellite chains: Safe + guard only, no Lagoon protocol
            configs[chain_name] = LagoonConfig(
                parameters=deepcopy(base_params),
                asset_manager=asset_manager,
                safe_owners=list(safe_owners),
                safe_threshold=safe_threshold,
                safe_salt_nonce=safe_salt_nonce,
                any_asset=True,
                satellite_chain=True,
            )
        else:
            # Source chain: full Lagoon protocol from scratch
            configs[chain_name] = LagoonConfig(
                parameters=deepcopy(base_params),
                asset_manager=asset_manager,
                safe_owners=list(safe_owners),
                safe_threshold=safe_threshold,
                safe_salt_nonce=safe_salt_nonce,
                any_asset=True,
                from_the_scratch=True,
                use_forge=True,
                deploy_retries=3,
            )

    # Configure Uniswap V3 on testnet chains that have deployments
    for chain_name in chain_web3:
        uni_key = TESTNET_UNISWAP_V3_KEYS.get(chain_name)
        if uni_key and uni_key in UNISWAP_V3_DEPLOYMENTS:
            d = UNISWAP_V3_DEPLOYMENTS[uni_key]
            uni_v3 = fetch_deployment_uni_v3(
                chain_web3[chain_name],
                factory_address=d["factory"],
                router_address=d["router"],
                position_manager_address=d["position_manager"],
                quoter_address=d["quoter"],
            )
            configs[chain_name].uniswap_v3 = uni_v3
            logger.info("Uniswap V3 configured on %s (testnet)", chain_name)

    # Configure CCTP between all testnet chains
    cctp_chain_ids = []
    for chain_name, web3 in chain_web3.items():
        chain_id = web3.eth.chain_id
        if chain_id in TESTNET_CHAIN_ID_TO_CCTP_DOMAIN:
            cctp_chain_ids.append((chain_name, chain_id))

    for chain_name, chain_id in cctp_chain_ids:
        other_ids = [cid for name, cid in cctp_chain_ids if name != chain_name]
        if other_ids:
            configs[chain_name].cctp_deployment = CCTPDeployment.create_for_chain(
                chain_id=chain_id,
                allowed_destinations=other_ids,
            )
            logger.info("CCTP configured on %s (testnet) with destinations: %s", chain_name, other_ids)

    return configs


def setup_simulate_chains(
    chain_rpc_env_vars: dict[str, str],
    chain_id_map: dict[str, int],
) -> tuple[dict[str, Web3], list[AnvilLaunch]]:
    """Create Anvil forks for all chains.

    :param chain_rpc_env_vars:
        Mapping of chain names to RPC environment variable names.

    :param chain_id_map:
        Mapping of chain names to expected chain IDs.

    :return:
        Tuple of (chain_name->Web3 dict, list of AnvilLaunch handles for cleanup).
    """
    anvil_launches = []
    chain_web3 = {}

    for chain_name, env_var in chain_rpc_env_vars.items():
        rpc_url = os.environ.get(env_var)
        assert rpc_url, f"{env_var} environment variable is required"

        # HyperEVM needs higher gas limit for TradingStrategyModuleV0 deployment
        # due to dual-block architecture (small blocks ~2-3M gas, large blocks ~30M)
        extra_args = {}

        # Unlock USDC whales for funding (mainnet only)
        chain_id = chain_id_map[chain_name]
        unlocked = []
        if chain_id in USDC_WHALE:
            unlocked.append(USDC_WHALE[chain_id])

        # fork_network_anvil handles space-separated multi-RPC URLs natively
        launch = fork_network_anvil(rpc_url, unlocked_addresses=unlocked, **extra_args)
        anvil_launches.append(launch)

        web3 = create_multi_provider_web3(
            launch.json_rpc_url,
            default_http_timeout=(3, 250.0),
        )
        assert web3.eth.chain_id == chain_id, f"Expected chain {chain_id} for {chain_name}, got {web3.eth.chain_id}"
        chain_web3[chain_name] = web3
        logger.info("Anvil fork for %s (chain %d) started at %s", chain_name, chain_id, launch.json_rpc_url)

    return chain_web3, anvil_launches


def setup_real_chains(
    chain_rpc_env_vars: dict[str, str],
) -> dict[str, Web3]:
    """Create Web3 connections for real networks.

    :param chain_rpc_env_vars:
        Mapping of chain names to RPC environment variable names.

    :return:
        chain_name->Web3 dict.
    """
    chain_web3 = {}
    for chain_name, env_var in chain_rpc_env_vars.items():
        rpc_url = os.environ.get(env_var)
        assert rpc_url, f"{env_var} environment variable is required"
        web3 = create_multi_provider_web3(rpc_url)
        chain_web3[chain_name] = web3
        logger.info("Connected to %s (chain %d)", chain_name, web3.eth.chain_id)
    return chain_web3


def bridge_to_destinations(
    chain_web3: dict[str, Web3],
    result: LagoonMultichainDeployment,
    source_chain: str,
    source_usdc,
    asset_manager: HexAddress,
    simulate: bool,
    deployer: "HotWallet | None" = None,
    bridge_usdc_amount: Decimal = Decimal("0.1"),
    attestation_timeout: float = 2400.0,
) -> list:
    """Bridge USDC from the source vault to each destination chain.

    Checks that the source vault has sufficient USDC, prepares test
    attesters in simulate mode, and calls
    :func:`~eth_defi.cctp.bridge.bridge_usdc_cctp_parallel`.

    :param chain_web3:
        Mapping of chain names to Web3 instances.

    :param result:
        Multichain deployment result with vault references.

    :param source_chain:
        Name of the source chain (e.g. ``"arbitrum"``).

    :param source_usdc:
        USDC token details on the source chain.

    :param asset_manager:
        Address that executes trades via the module.

    :param simulate:
        Whether to use forged attestations on Anvil forks.

    :param bridge_usdc_amount:
        Human-readable amount of USDC to bridge per destination.

    :param attestation_timeout:
        Maximum seconds to wait for each attestation.

    :return:
        List of :class:`~eth_defi.cctp.bridge.CCTPBridgeResult`.
    """
    source_vault = result.deployments[source_chain].vault
    dest_chain_names = [name for name in chain_web3 if name != source_chain]

    bridge_amount = source_usdc.convert_to_raw(bridge_usdc_amount)
    total_bridge = bridge_amount * len(dest_chain_names)

    # Check source vault has sufficient USDC for bridging
    safe_balance = source_usdc.contract.functions.balanceOf(source_vault.safe_address).call()
    safe_balance_human = source_usdc.convert_to_decimals(safe_balance)
    total_bridge_human = source_usdc.convert_to_decimals(total_bridge)
    print(f"\nSource vault USDC balance: {safe_balance_human} USDC")
    print(f"  Required for bridging: {total_bridge_human} USDC ({len(dest_chain_names)} destinations x {bridge_usdc_amount} USDC)")
    assert safe_balance >= total_bridge, f"Source vault needs {total_bridge_human} USDC but has {safe_balance_human} USDC. Fund the vault on {source_chain} first."

    print(f"Bridging {bridge_usdc_amount} USDC from {source_chain} to each destination chain...")

    # Prepare test attesters on destination forks (simulate mode only)
    test_attesters: dict[int, LocalAccount] | None = None
    if simulate:
        test_attesters = {}
        for chain_name in dest_chain_names:
            dest_chain_id = chain_web3[chain_name].eth.chain_id
            test_attesters[dest_chain_id] = replace_attester_on_fork(chain_web3[chain_name])

    # Build destination list for parallel bridging
    destinations = []
    for dest_chain_name in dest_chain_names:
        dest_safe = result.deployments[dest_chain_name].safe_address
        destinations.append(
            CCTPBridgeDestination(
                dest_web3=chain_web3[dest_chain_name],
                dest_safe_address=dest_safe,
                amount=bridge_amount,
            )
        )
        print(f"  Destination: {dest_chain_name} (Safe: {dest_safe})")

    # Execute parallel bridge: burns sequentially, attestations + receives in parallel
    bridge_results = bridge_usdc_cctp_parallel(
        source_web3=chain_web3[source_chain],
        source_vault=source_vault,
        destinations=destinations,
        sender=asset_manager,
        hot_wallet=deployer,
        simulate=simulate,
        test_attesters=test_attesters,
        attestation_timeout=attestation_timeout,
    )

    for dest_name, br in zip(dest_chain_names, bridge_results):
        print(f"\n  {dest_name}:")
        print(f"    Burn TX:    {br.burn_tx_hash}")
        print(f"    Receive TX: {br.receive_tx_hash}")

    return bridge_results


def swap_on_satellites(
    chain_web3: dict[str, Web3],
    result: LagoonMultichainDeployment,
    source_chain: str,
    deployer: HotWallet | None = None,
    swap_fraction: Decimal = Decimal("0.5"),
):
    """Swap bridged USDC to WETH on satellite chains via Uniswap V3.

    Proves the guard allows trading on satellite chains after bridging.
    Only swaps on satellite chains, not on the source chain.

    :param deployer:
        HotWallet for signing on live networks.
        ``None`` for Anvil simulate mode (uses unlocked account).

    :param swap_fraction:
        Fraction of the satellite Safe's USDC balance to swap.
    """
    for chain_name, deployment in result.deployments.items():
        if chain_name == source_chain or not deployment.is_satellite:
            continue

        web3 = chain_web3[chain_name]
        chain_id = web3.eth.chain_id

        uni_key = TESTNET_UNISWAP_V3_KEYS.get(chain_name)
        if not uni_key or uni_key not in UNISWAP_V3_DEPLOYMENTS:
            print(f"  {chain_name}: no Uniswap V3, skipping swap")
            continue

        d = UNISWAP_V3_DEPLOYMENTS[uni_key]
        uni_v3 = fetch_deployment_uni_v3(
            web3,
            factory_address=d["factory"],
            router_address=d["router"],
            position_manager_address=d["position_manager"],
            quoter_address=d["quoter"],
        )

        usdc = fetch_erc20_details(web3, USDC_NATIVE_TOKEN[chain_id])
        weth_address = WRAPPED_NATIVE_TOKEN.get(chain_id)
        if not weth_address:
            print(f"  {chain_name}: no WETH configured, skipping swap")
            continue
        weth = fetch_erc20_details(web3, weth_address)
        safe_address = deployment.safe_address

        usdc_balance_raw = usdc.contract.functions.balanceOf(safe_address).call()
        swap_amount = int(usdc_balance_raw * swap_fraction)
        if swap_amount == 0:
            print(f"  {chain_name}: no USDC to swap")
            continue

        print(f"  {chain_name}: swapping {usdc.convert_to_decimals(swap_amount)} USDC -> WETH...")

        satellite = deployment.vault

        # Create a per-chain HotWallet so the nonce counter is independent
        # of the source chain (the shared deployer may have nonce 300+ on
        # Arbitrum Sepolia while Base Sepolia is at 200).
        if deployer is not None:
            chain_wallet = HotWallet(deployer.account)
            chain_wallet.sync_nonce(web3)
        else:
            chain_wallet = None

        # Approve USDC for Uniswap V3 router
        approve_call = usdc.contract.functions.approve(uni_v3.swap_router.address, swap_amount)
        moduled_tx = satellite.transact_via_trading_strategy_module(approve_call)
        if chain_wallet is not None:
            tx_hash = chain_wallet.transact_and_broadcast_with_contract(moduled_tx)
        else:
            tx_hash = moduled_tx.transact({"from": result.deployments[source_chain].vault.safe_address, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash)

        # Swap USDC -> WETH
        swap_call = swap_with_slippage_protection(
            uni_v3,
            recipient_address=safe_address,
            base_token=weth.contract,
            quote_token=usdc.contract,
            amount_in=swap_amount,
            pool_fees=[3000],  # 30 bps fee tier
            max_slippage=500,  # 5% — testnet pools have thin liquidity
        )
        moduled_tx = satellite.transact_via_trading_strategy_module(swap_call)
        if chain_wallet is not None:
            tx_hash = chain_wallet.transact_and_broadcast_with_contract(moduled_tx)
        else:
            tx_hash = moduled_tx.transact({"from": result.deployments[source_chain].vault.safe_address, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash)

        weth_balance = weth.fetch_balance_of(safe_address)
        print(f"  {chain_name}: received {weth_balance} WETH")


def main():
    threading.current_thread().name = "main"
    setup_console_logging("info", coloured_threads=True)

    network = os.environ.get("NETWORK", "mainnet").lower()
    simulate = os.environ.get("SIMULATE", "").lower() in ("true", "1", "yes")
    salt_nonce = int(os.environ.get("SALT_NONCE", str(random.randint(1, 2**32))))
    usdc_amount = Decimal(os.environ.get("USDC_AMOUNT", "2"))
    bridged_usdc_amount = Decimal(os.environ.get("BRIDGED_USDC_AMOUNT", "0.1"))

    assert network in ("mainnet", "testnet"), f"NETWORK must be 'mainnet' or 'testnet', got '{network}'"

    is_testnet = network == "testnet"

    # Resolve which chains to deploy on
    all_rpc_env_vars = TESTNET_CHAIN_RPC_ENV_VARS if is_testnet else MAINNET_CHAIN_RPC_ENV_VARS
    all_chain_id_map = TESTNET_CHAIN_ID_MAP if is_testnet else MAINNET_CHAIN_ID_MAP
    default_chains = TESTNET_DEFAULT_CHAINS if is_testnet else MAINNET_DEFAULT_CHAINS

    chains_env = os.environ.get("CHAINS", "")
    if chains_env.strip():
        selected_chains = [c.strip() for c in chains_env.split(",") if c.strip()]
    else:
        selected_chains = list(default_chains)

    for chain_name in selected_chains:
        assert chain_name in all_rpc_env_vars, f"Unknown chain '{chain_name}' for {network} mode. Available: {list(all_rpc_env_vars.keys())}"

    # First chain is the source vault (deposit/redeem entry point)
    source_chain = selected_chains[0]

    # Filter dicts to selected chains only
    chain_rpc_env_vars = {k: all_rpc_env_vars[k] for k in selected_chains}
    chain_id_map = {k: all_chain_id_map[k] for k in selected_chains}

    print("=" * 70)
    print("Lagoon multichain deployment tutorial")
    print("=" * 70)
    print(f"  Network: {network}")
    print(f"  Mode: {'SIMULATE (Anvil forks)' if simulate else 'REAL (live networks)'}")
    print(f"  Salt nonce: {salt_nonce}")
    print(f"  Chains: {', '.join(selected_chains)}")
    print(f"  Vault funding: {usdc_amount} USDC")
    print(f"  Bridge per chain: {bridged_usdc_amount} USDC")
    print()

    anvil_launches: list[AnvilLaunch] = []

    try:
        # --- Step 1: Set up chain connections ---
        if simulate:
            chain_web3, anvil_launches = setup_simulate_chains(chain_rpc_env_vars, chain_id_map)
        else:
            chain_web3 = setup_real_chains(chain_rpc_env_vars)

        # --- Step 2: Set up deployer wallet ---
        if simulate:
            deployer = HotWallet(Account.create())
            # Fund deployer with ETH/native on all chains
            for chain_name, web3 in chain_web3.items():
                web3.provider.make_request("anvil_setBalance", [deployer.address, hex(100 * 10**18)])
            print(f"  Deployer: {deployer.address} (simulated, funded with 100 ETH)")
        else:
            private_key = os.environ.get("LAGOON_MULTCHAIN_TEST_PRIVATE_KEY")
            assert private_key, "LAGOON_MULTCHAIN_TEST_PRIVATE_KEY environment variable is required in real mode"
            deployer = HotWallet.from_private_key(private_key)
            print(f"  Deployer: {deployer.address}")

        # Use deployer as both asset manager and single Safe owner for tutorial
        asset_manager = deployer.address
        safe_owners = [deployer.address]
        safe_threshold = 1

        # --- Step 3: Check deployer balance on all chains ---
        if not simulate:
            print("\nChecking deployer balances...")
            insufficient = []
            for chain_name, web3 in chain_web3.items():
                balance_wei = web3.eth.get_balance(deployer.address)
                balance_eth = balance_wei / 10**18
                status = "OK" if balance_wei > 0 else "EMPTY"
                print(f"  {chain_name}: {balance_eth:.6f} native ({status})")
                if balance_wei == 0:
                    insufficient.append(chain_name)
            if insufficient:
                print(f"\n  WARNING: Deployer has zero balance on: {', '.join(insufficient)}")
                print(f"  Deployment will fail on chains without gas. Fund {deployer.address} first.")
                raise SystemExit(1)

        # --- Step 4: Build per-chain configurations ---
        print("\nBuilding per-chain whitelisting configurations...")
        if is_testnet:
            chain_configs = create_testnet_whitelisting_configuration(
                chain_web3=chain_web3,
                asset_manager=asset_manager,
                safe_owners=safe_owners,
                safe_threshold=safe_threshold,
                safe_salt_nonce=salt_nonce,
                source_chain=source_chain,
            )
        else:
            chain_configs = create_multichain_whitelisting_configuration(
                chain_web3=chain_web3,
                asset_manager=asset_manager,
                safe_owners=safe_owners,
                safe_threshold=safe_threshold,
                safe_salt_nonce=salt_nonce,
                source_chain=source_chain,
            )

        for chain_name, config in chain_configs.items():
            n_vaults = len(config.erc_4626_vaults) if config.erc_4626_vaults else 0
            n_hypercore = len(config.hypercore_vaults) if config.hypercore_vaults else 0
            cctp = "yes" if config.cctp_deployment else "no"
            print(f"  {chain_name}: {n_vaults} ERC-4626 vaults, {n_hypercore} Hypercore vaults, CCTP: {cctp}")

        # --- Step 5: Deploy across all chains ---
        print("\nDeploying Lagoon vaults across all chains (parallel)...")
        result = deploy_multichain_lagoon_vault(
            chain_web3=chain_web3,
            deployer=deployer.account,
            chain_configs=chain_configs,
        )

        # --- Step 6: Print deployment summary ---
        print("\n" + "=" * 70)
        print("Deployment summary")
        print("=" * 70)
        print(f"  Deterministic Safe address: {result.safe_address}")
        print(f"  Salt nonce: {result.safe_salt_nonce}")
        print()
        for chain_name, deployment in sorted(result.deployments.items()):
            print(f"  {chain_name}{'  (satellite)' if deployment.is_satellite else ''}:")
            if deployment.is_satellite:
                print(f"    Vault:  N/A (satellite chain)")
            else:
                print(f"    Vault:  {deployment.vault.address}")
            print(f"    Safe:   {deployment.safe_address}")
            print(f"    Module: {deployment.trading_strategy_module.address if deployment.trading_strategy_module else 'N/A'}")

        # --- Step 7: Fund source vault for bridging ---
        source_chain_id = chain_web3[source_chain].eth.chain_id
        source_usdc_address = USDC_NATIVE_TOKEN[source_chain_id]
        source_usdc = fetch_erc20_details(chain_web3[source_chain], source_usdc_address)
        source_vault = result.deployments[source_chain].vault

        source_web3 = chain_web3[source_chain]

        if simulate:
            if source_chain_id in USDC_WHALE:
                # Mainnet simulate: transfer USDC from whale to deployer
                whale = USDC_WHALE[source_chain_id]
                raw_amount = source_usdc.convert_to_raw(usdc_amount)
                tx_hash = source_usdc.contract.functions.transfer(
                    deployer.address,
                    raw_amount,
                ).transact({"from": whale})
                assert_transaction_success_with_explanation(source_web3, tx_hash)
            else:
                # Testnet simulate: mint USDC to deployer via storage manipulation
                fund_erc20_on_anvil(source_web3, source_usdc.address, deployer.address, source_usdc.convert_to_raw(usdc_amount))

            print(f"\nFunding {source_chain} vault with {usdc_amount} USDC for bridge testing...")
            deployer.sync_nonce(source_web3)
            source_module = result.deployments[source_chain].trading_strategy_module
            fund_lagoon_vault(
                web3=source_web3,
                vault_address=source_vault.address,
                asset_manager=deployer.address,
                test_account_with_balance=deployer.address,
                trading_strategy_module_address=source_module.address,
                amount=usdc_amount,
                hot_wallet=deployer,
            )
        else:
            # Real mode: fund the vault from the deployer's USDC balance
            deployer_balance = source_usdc.fetch_balance_of(deployer.address)
            print(f"\nDeployer USDC balance on {source_chain}: {deployer_balance} USDC")
            assert deployer_balance >= usdc_amount, f"Deployer needs at least {usdc_amount} USDC on {source_chain} but has {deployer_balance} USDC. Get testnet USDC from Circle faucet: https://faucet.circle.com/" if is_testnet else f"Transfer USDC to deployer {deployer.address} on {source_chain}."

            print(f"Funding {source_chain} vault with {usdc_amount} USDC from deployer...")
            deployer.sync_nonce(source_web3)
            source_module = result.deployments[source_chain].trading_strategy_module
            fund_lagoon_vault(
                web3=source_web3,
                vault_address=source_vault.address,
                asset_manager=deployer.address,
                test_account_with_balance=deployer.address,
                trading_strategy_module_address=source_module.address,
                amount=usdc_amount,
                hot_wallet=deployer,
            )

        # --- Step 8: Bridge 0.1 USDC from source to each destination chain ---
        if not simulate:
            deployer.sync_nonce(source_web3)
        bridge_results = bridge_to_destinations(
            chain_web3=chain_web3,
            result=result,
            source_chain=source_chain,
            source_usdc=source_usdc,
            asset_manager=asset_manager,
            simulate=simulate,
            deployer=deployer if not simulate else None,
            bridge_usdc_amount=bridged_usdc_amount,
            attestation_timeout=3600.0 if is_testnet else 2400.0,
        )

        # --- Step 8b: Swap bridged USDC to WETH on satellite chains ---
        if is_testnet:
            print("\nSwapping bridged USDC to WETH on satellite chains...")
            swap_on_satellites(
                chain_web3=chain_web3,
                result=result,
                source_chain=source_chain,
                deployer=deployer if not simulate else None,
            )

        # --- Step 9: Print final summary ---
        print("\n" + "=" * 70)
        print("Bridge summary")
        print("=" * 70)
        for br in bridge_results:
            print(f"  Chain {br.source_chain_id} -> {br.dest_chain_id}: {source_usdc.convert_to_decimals(br.amount):.2f} USDC")

        # --- Step 10: Print vault status across all chains ---
        print("\n" + "=" * 70)
        print("Vault status")
        print("=" * 70)
        for chain_name, deployment in sorted(result.deployments.items()):
            web3 = chain_web3[chain_name]
            chain_id = web3.eth.chain_id
            usdc_address = USDC_NATIVE_TOKEN[chain_id]
            usdc = fetch_erc20_details(web3, usdc_address)
            safe_balance = usdc.fetch_balance_of(deployment.safe_address)
            print(f"  {chain_name}{'  (satellite)' if deployment.is_satellite else ''}:")
            if deployment.is_satellite:
                print(f"    Vault:       N/A (satellite chain)")
            else:
                print(f"    Vault:       {deployment.vault.address}")
                share_price = deployment.vault.fetch_share_price("latest")
                print(f"    Share price: {share_price}")
            print(f"    Safe:        {deployment.safe_address}")
            print(f"    Safe USDC:   {safe_balance} USDC")
            weth_address = WRAPPED_NATIVE_TOKEN.get(chain_id)
            if weth_address:
                weth = fetch_erc20_details(web3, weth_address)
                weth_balance = weth.fetch_balance_of(deployment.safe_address)
                if weth_balance > 0:
                    print(f"    Safe WETH:   {weth_balance} WETH")

        print("\nDone!")

    finally:
        for launch in anvil_launches:
            launch.close(log_level=logging.ERROR)


if __name__ == "__main__":
    main()
