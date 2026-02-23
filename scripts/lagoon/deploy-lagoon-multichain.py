"""Tutorial: Deploying Lagoon vaults across 5 chains with CCTP bridging.

Deploys a multichain Lagoon vault setup where all chains share the same
deterministic Safe address via CREATE2:

- **Arbitrum**: Lagoon vault (deposit/redeem entry point) + Safe
- **Ethereum**: Safe + TradingStrategyModuleV0 + CowSwap
- **Base**: Safe + TradingStrategyModuleV0
- **HyperEVM**: Safe + TradingStrategyModuleV0 + Hypercore vaults
- **Monad**: Safe + TradingStrategyModuleV0

After deployment, bridges 1 USDC from the Arbitrum vault to each other
chain via Circle's CCTP V2 protocol to verify cross-chain connectivity.

Simulation mode
---------------

Set ``SIMULATE=true`` to run using Anvil mainnet forks of all 5 chains.
CCTP bridging uses forged attestations in simulation mode.

.. code-block:: shell

    SIMULATE=true \\
    JSON_RPC_ETHEREUM="https://..." \\
    JSON_RPC_ARBITRUM="https://..." \\
    JSON_RPC_BASE="https://..." \\
    JSON_RPC_HYPERLIQUID="https://..." \\
    JSON_RPC_MONAD="https://..." \\
    poetry run python scripts/lagoon/deploy-lagoon-multichain.py

Architecture overview
---------------------

::

    Arbitrum (Lagoon vault — deposit/redeem entry)
        │
        ├── CCTP V2 ────► Ethereum Safe (CowSwap + ERC-4626 vaults)
        ├── CCTP V2 ────► Base Safe (ERC-4626 vaults)
        ├── CCTP V2 ────► HyperEVM Safe (Hypercore + ERC-4626 vaults)
        └── CCTP V2 ────► Monad Safe (ERC-4626 vaults)

All Safes share the same deterministic address across all chains.
Each chain has its own TradingStrategyModuleV0 guard with
chain-specific whitelisting rules.
"""

import logging
import os
import random
from copy import deepcopy
from decimal import Decimal
from typing import cast

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import Web3

from eth_defi.cctp.bridge import CCTPBridgeDestination, CCTPBridgeResult, bridge_usdc_cctp_parallel
from eth_defi.cctp.constants import CHAIN_ID_TO_CCTP_DOMAIN
from eth_defi.cctp.testing import replace_attester_on_fork
from eth_defi.cctp.whitelist import CCTPDeployment
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonConfig,
    LagoonDeploymentParameters,
    LagoonMultichainDeployment,
    deploy_multichain_lagoon_vault,
)
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, USDC_WHALE, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
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

#: Chain names mapping to environment variable names for RPC URLs
CHAIN_RPC_ENV_VARS: dict[str, str] = {
    "arbitrum": "JSON_RPC_ARBITRUM",
    "ethereum": "JSON_RPC_ETHEREUM",
    "base": "JSON_RPC_BASE",
    "hyperliquid": "JSON_RPC_HYPERLIQUID",
    "monad": "JSON_RPC_MONAD",
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
) -> dict[str, LagoonConfig]:
    """Build per-chain LagoonConfig dicts for multichain deployment.

    Shared across all chains:

    - USDC as underlying (auto-resolved per chain)
    - CCTP whitelisting (configured for all CCTP-capable chains)
    - Safe owners, threshold, salt nonce
    - Asset manager

    Chain-specific:

    - Arbitrum: Lagoon vault entry point + ERC-4626 vaults
    - Ethereum: ERC-4626 vaults + CowSwap
    - Base: ERC-4626 vaults
    - HyperEVM: Hypercore vaults + ERC-4626 vaults
    - Monad: ERC-4626 vaults

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

    :return:
        Per-chain LagoonConfig dict ready for ``deploy_multichain_lagoon_vault()``.
    """
    configs: dict[str, LagoonConfig] = {}

    # Shared base parameters
    base_params = LagoonDeploymentParameters(
        underlying=None,  # auto-resolved per chain from USDC_NATIVE_TOKEN
        name="Multichain Strategy Vault",
        symbol="MSV",
    )

    def make_base_config(**kwargs) -> LagoonConfig:
        """Create a LagoonConfig with shared settings."""
        return LagoonConfig(
            parameters=deepcopy(base_params),
            asset_manager=asset_manager,
            safe_owners=list(safe_owners),
            safe_threshold=safe_threshold,
            safe_salt_nonce=safe_salt_nonce,
            any_asset=True,
            **kwargs,
        )

    # --- Arbitrum: vault entry point ---
    configs["arbitrum"] = make_base_config(
        erc_4626_vaults=resolve_vaults(chain_web3["arbitrum"], ARBITRUM_VAULTS),
    )

    # --- Ethereum: CowSwap + vaults ---
    configs["ethereum"] = make_base_config(
        cowswap=True,
        erc_4626_vaults=resolve_vaults(chain_web3["ethereum"], ETHEREUM_VAULTS),
    )

    # --- Base: vaults ---
    configs["base"] = make_base_config(
        erc_4626_vaults=resolve_vaults(chain_web3["base"], BASE_VAULTS),
    )

    # --- HyperEVM: Hypercore + ERC-4626 vaults ---
    configs["hyperliquid"] = make_base_config(
        hypercore_vaults=HYPERCORE_VAULT_ADDRESSES,
        erc_4626_vaults=resolve_vaults(chain_web3["hyperliquid"], HYPEREVM_VAULTS),
    )

    # --- Monad: vaults ---
    configs["monad"] = make_base_config(
        erc_4626_vaults=resolve_vaults(chain_web3["monad"], MONAD_VAULTS),
    )

    # --- Configure CCTP for all CCTP-capable chains ---
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


def setup_simulate_chains() -> tuple[dict[str, Web3], list[AnvilLaunch]]:
    """Create Anvil forks for all 5 chains.

    :return:
        Tuple of (chain_name→Web3 dict, list of AnvilLaunch handles for cleanup).
    """
    anvil_launches = []
    chain_web3 = {}

    for chain_name, env_var in CHAIN_RPC_ENV_VARS.items():
        rpc_url = os.environ.get(env_var)
        assert rpc_url, f"{env_var} environment variable is required"
        rpc_url = rpc_url.split()[0]  # Take first URL if space-separated fallback format

        # HyperEVM needs higher gas limit for TradingStrategyModuleV0 deployment
        # due to dual-block architecture (small blocks ~2-3M gas, large blocks ~30M)
        extra_args = {}
        if chain_name == "hyperliquid":
            extra_args["gas_limit"] = 30_000_000

        # Unlock USDC whales for funding
        chain_id_map = {
            "arbitrum": 42161,
            "ethereum": 1,
            "base": 8453,
            "hyperliquid": 999,
            "monad": 143,
        }
        chain_id = chain_id_map[chain_name]
        unlocked = []
        if chain_id in USDC_WHALE:
            unlocked.append(USDC_WHALE[chain_id])

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


def setup_real_chains() -> dict[str, Web3]:
    """Create Web3 connections for real networks.

    :return:
        chain_name→Web3 dict.
    """
    chain_web3 = {}
    for chain_name, env_var in CHAIN_RPC_ENV_VARS.items():
        rpc_url = os.environ.get(env_var)
        assert rpc_url, f"{env_var} environment variable is required"
        web3 = create_multi_provider_web3(rpc_url)
        chain_web3[chain_name] = web3
        logger.info("Connected to %s (chain %d)", chain_name, web3.eth.chain_id)
    return chain_web3


def fund_vault(web3: Web3, vault, usdc_details, depositor: str, asset_manager: str, amount_usdc: int = 200):
    """Deposit USDC into the vault and settle so the Safe holds funds."""
    raw_amount = usdc_details.convert_to_raw(amount_usdc)

    tx_hash = vault.post_new_valuation(Decimal(0)).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = usdc_details.contract.functions.approve(
        vault.address,
        raw_amount,
    ).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = vault.request_deposit(depositor, raw_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = vault.post_new_valuation(Decimal(0)).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = vault.settle_via_trading_strategy_module(Decimal(0)).transact(
        {"from": asset_manager, "gas": 1_000_000},
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    balance = vault.underlying_token.fetch_balance_of(vault.safe_address)
    logger.info("Vault funded with %d USDC (Safe balance: %s)", amount_usdc, balance)


def main():
    setup_console_logging(logging.INFO)

    simulate = os.environ.get("SIMULATE", "").lower() in ("true", "1", "yes")
    salt_nonce = int(os.environ.get("SALT_NONCE", str(random.randint(1, 2**32))))

    print("=" * 70)
    print("Lagoon multichain deployment tutorial")
    print("=" * 70)
    print(f"  Mode: {'SIMULATE (Anvil forks)' if simulate else 'REAL (live networks)'}")
    print(f"  Salt nonce: {salt_nonce}")
    print(f"  Chains: Arbitrum, Ethereum, Base, HyperEVM, Monad")
    print()

    anvil_launches: list[AnvilLaunch] = []

    try:
        # --- Step 1: Set up chain connections ---
        if simulate:
            chain_web3, anvil_launches = setup_simulate_chains()
        else:
            chain_web3 = setup_real_chains()

        # --- Step 2: Set up deployer wallet ---
        if simulate:
            deployer = Account.create()
            # Fund deployer with ETH/native on all chains
            for chain_name, web3 in chain_web3.items():
                web3.provider.make_request("anvil_setBalance", [deployer.address, hex(100 * 10**18)])
            print(f"  Deployer: {deployer.address} (simulated, funded with 100 ETH)")
        else:
            private_key = os.environ.get("PRIVATE_KEY")
            assert private_key, "PRIVATE_KEY environment variable is required in real mode"
            deployer = Account.from_key(private_key)
            print(f"  Deployer: {deployer.address}")

        # Use deployer as both asset manager and single Safe owner for tutorial
        asset_manager = deployer.address
        safe_owners = [deployer.address]
        safe_threshold = 1

        # --- Step 3: Build per-chain configurations ---
        print("\nBuilding per-chain whitelisting configurations...")
        chain_configs = create_multichain_whitelisting_configuration(
            chain_web3=chain_web3,
            asset_manager=asset_manager,
            safe_owners=safe_owners,
            safe_threshold=safe_threshold,
            safe_salt_nonce=salt_nonce,
        )

        for chain_name, config in chain_configs.items():
            n_vaults = len(config.erc_4626_vaults) if config.erc_4626_vaults else 0
            n_hypercore = len(config.hypercore_vaults) if config.hypercore_vaults else 0
            cctp = "yes" if config.cctp_deployment else "no"
            print(f"  {chain_name}: {n_vaults} ERC-4626 vaults, {n_hypercore} Hypercore vaults, CCTP: {cctp}")

        # --- Step 4: Deploy across all chains ---
        print("\nDeploying Lagoon vaults across all chains (parallel)...")
        result = deploy_multichain_lagoon_vault(
            chain_web3=chain_web3,
            deployer=deployer,
            chain_configs=chain_configs,
        )

        # --- Step 5: Print deployment summary ---
        print("\n" + "=" * 70)
        print("Deployment summary")
        print("=" * 70)
        print(f"  Deterministic Safe address: {result.safe_address}")
        print(f"  Salt nonce: {result.safe_salt_nonce}")
        print()
        for chain_name, deployment in sorted(result.deployments.items()):
            print(f"  {chain_name}:")
            print(f"    Vault:  {deployment.vault.address}")
            print(f"    Safe:   {deployment.vault.safe_address}")
            print(f"    Module: {deployment.trading_strategy_module.address if deployment.trading_strategy_module else 'N/A'}")

        # --- Step 6: Fund Arbitrum vault for bridging ---
        if simulate:
            print("\nFunding Arbitrum vault with 10 USDC for bridge testing...")
            arb_web3 = chain_web3["arbitrum"]
            arb_vault = result.deployments["arbitrum"].vault
            arb_usdc = fetch_erc20_details(arb_web3, USDC_NATIVE_TOKEN[42161])
            depositor = USDC_WHALE[42161]
            fund_vault(arb_web3, arb_vault, arb_usdc, depositor, asset_manager, amount_usdc=10)
        else:
            print("\nSkipping vault funding in real mode (vault must be pre-funded).")

        # --- Step 7: Bridge 1 USDC from Arbitrum to each CCTP-enabled chain (parallel) ---
        print("\nBridging 1 USDC from Arbitrum to each destination chain (parallel)...")
        arb_vault = result.deployments["arbitrum"].vault
        arb_usdc = fetch_erc20_details(chain_web3["arbitrum"], USDC_NATIVE_TOKEN[42161])
        bridge_amount = arb_usdc.convert_to_raw(1)  # 1 USDC

        # Prepare test attesters on destination forks (simulate mode only)
        # Keyed by chain ID for bridge_usdc_cctp_parallel()
        test_attesters: dict[int, LocalAccount] | None = None
        if simulate:
            test_attesters = {}
            for chain_name in ["ethereum", "base", "hyperliquid", "monad"]:
                dest_chain_id = chain_web3[chain_name].eth.chain_id
                test_attesters[dest_chain_id] = replace_attester_on_fork(chain_web3[chain_name])

        # Build destination list for parallel bridging
        dest_chain_names = ["ethereum", "base", "hyperliquid", "monad"]
        destinations = []
        for dest_chain_name in dest_chain_names:
            dest_safe = result.deployments[dest_chain_name].vault.safe_address
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
            source_web3=chain_web3["arbitrum"],
            source_vault=arb_vault,
            destinations=destinations,
            sender=asset_manager,
            simulate=simulate,
            test_attesters=test_attesters,
        )

        for dest_name, br in zip(dest_chain_names, bridge_results):
            print(f"\n  {dest_name}:")
            print(f"    Burn TX:    {br.burn_tx_hash}")
            print(f"    Receive TX: {br.receive_tx_hash}")

        # --- Step 8: Print final summary ---
        print("\n" + "=" * 70)
        print("Bridge summary")
        print("=" * 70)
        for br in bridge_results:
            print(f"  Chain {br.source_chain_id} -> {br.dest_chain_id}: {arb_usdc.convert_to_decimals(br.amount):.2f} USDC")

        print("\nDone!")

    finally:
        for launch in anvil_launches:
            launch.close(log_level=logging.ERROR)


if __name__ == "__main__":
    main()
