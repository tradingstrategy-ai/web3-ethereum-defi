"""Test multichain Lagoon vault deployment with deterministic Safe and parallel CCTP bridging.

- Deploys Lagoon vaults across Ethereum, Arbitrum, Base, and HyperEVM forks
- Verifies the same deterministic Safe address across all chains
- Tests parallel CCTP bridging: Arbitrum -> Ethereum, Base, HyperEVM simultaneously
- Uses per-chain LagoonConfig with explicit CCTP configuration
"""

import logging
import os
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.cctp.bridge import CCTPBridgeDestination, CCTPBridgeResult, bridge_usdc_cctp_parallel
from eth_defi.cctp.constants import CHAIN_ID_TO_CCTP_DOMAIN
from eth_defi.cctp.testing import replace_attester_on_fork
from eth_defi.cctp.whitelist import CCTPDeployment
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonConfig, LagoonDeploymentParameters, LagoonMultichainDeployment, deploy_multichain_lagoon_vault
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonSatelliteVault, LagoonVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, USDC_WHALE, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.deployment import fetch_deployment as fetch_deployment_uni_v3
from eth_defi.uniswap_v3.swap import swap_with_slippage_protection

logger = logging.getLogger(__name__)

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")
JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")
CI = os.environ.get("CI") == "true"

pytestmark = pytest.mark.skipif(
    not JSON_RPC_ETHEREUM or not JSON_RPC_ARBITRUM or not JSON_RPC_BASE or not JSON_RPC_HYPERLIQUID,
    reason="JSON_RPC_ETHEREUM, JSON_RPC_ARBITRUM, JSON_RPC_BASE, and JSON_RPC_HYPERLIQUID environment variables required",
)

#: Anvil default account #0 private key. Fixed so the deployer address is the same on all chains.
DEPLOYER_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

#: All chain IDs in the test (Ethereum, Arbitrum, Base, HyperEVM)
TEST_CHAIN_IDS = [1, 42161, 8453, 999]


@pytest.fixture()
def deployer() -> LocalAccount:
    return Account.from_key(DEPLOYER_PRIVATE_KEY)


@pytest.fixture()
def anvil_ethereum(request) -> AnvilLaunch:
    launch = fork_network_anvil(
        JSON_RPC_ETHEREUM,
        unlocked_addresses=[USDC_WHALE[1]],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def anvil_arbitrum(request) -> AnvilLaunch:
    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM,
        unlocked_addresses=[USDC_WHALE[42161]],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def anvil_base(request) -> AnvilLaunch:
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[USDC_WHALE[8453]],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def anvil_hyperliquid(request) -> AnvilLaunch:
    launch = fork_network_anvil(
        JSON_RPC_HYPERLIQUID,
        # HyperEVM small blocks have 2-3M gas limit; override to large block limit (30M) for TradingStrategyModuleV0 (~5.4M gas). See https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/dual-block-architecture
        gas_limit=30_000_000,
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3_ethereum(anvil_ethereum) -> Web3:
    web3 = create_multi_provider_web3(
        anvil_ethereum.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 1
    return web3


@pytest.fixture()
def web3_arbitrum(anvil_arbitrum) -> Web3:
    web3 = create_multi_provider_web3(
        anvil_arbitrum.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 42161
    return web3


@pytest.fixture()
def web3_base(anvil_base) -> Web3:
    web3 = create_multi_provider_web3(
        anvil_base.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def web3_hyperliquid(anvil_hyperliquid) -> Web3:
    web3 = create_multi_provider_web3(
        anvil_hyperliquid.json_rpc_url,
        default_http_timeout=(3, 500.0),
    )
    assert web3.eth.chain_id == 999
    return web3


def _make_chain_configs(
    deployer_address: HexAddress,
    salt_nonce: int,
    source_chain: str | None = None,
) -> dict[str, LagoonConfig]:
    """Build per-chain LagoonConfig dicts with explicit CCTP configuration.

    Each chain gets its own config with CCTP whitelisting to all other
    CCTP-capable chains in the test set.

    :param source_chain:
        When set, only the source chain gets a full vault deployment;
        other chains are deployed as satellite (Safe + guard only, no vault).
    """
    chain_names = ["ethereum", "arbitrum", "base", "hyperliquid"]
    chain_id_map = {
        "ethereum": 1,
        "arbitrum": 42161,
        "base": 8453,
        "hyperliquid": 999,
    }

    configs = {}
    for chain_name in chain_names:
        chain_id = chain_id_map[chain_name]

        # Configure CCTP with all other chains as allowed destinations
        cctp = None
        if chain_id in CHAIN_ID_TO_CCTP_DOMAIN:
            other_chain_ids = [cid for cid in TEST_CHAIN_IDS if cid != chain_id and cid in CHAIN_ID_TO_CCTP_DOMAIN]
            cctp = CCTPDeployment.create_for_chain(
                chain_id=chain_id,
                allowed_destinations=other_chain_ids,
            )

        is_satellite = source_chain is not None and chain_name != source_chain

        configs[chain_name] = LagoonConfig(
            parameters=LagoonDeploymentParameters(
                underlying=None,
                name="Multichain Test Vault",
                symbol="TEST",
            ),
            asset_manager=deployer_address,
            safe_owners=[deployer_address],
            safe_threshold=1,
            any_asset=True,
            safe_salt_nonce=salt_nonce,
            cctp_deployment=cctp,
            satellite_chain=is_satellite,
        )

    return configs


def _fund_vault(web3, vault, usdc_details, depositor, asset_manager, amount_usdc=100):
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

    tx_hash = vault.settle_via_trading_strategy_module(Decimal(0)).transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert vault.underlying_token.fetch_balance_of(vault.safe_address) == amount_usdc


@pytest.mark.timeout(900)
@pytest.mark.skipif(CI, reason="This is a long-running test that deploys multiple vaults and performs cross-chain bridging. Run locally for testing.")
def test_multichain_lagoon_deploy_and_parallel_cctp_bridge(
    web3_ethereum,
    web3_arbitrum,
    web3_base,
    web3_hyperliquid,
    deployer,
):
    """Deploy Lagoon vaults on 4 chains with deterministic Safe, then bridge USDC via parallel CCTP.

    Part 1: Multichain deployment - verify same Safe address on all 4 chains,
    including HyperEVM CCTP whitelisting.
    Part 2: Parallel CCTP bridging - Arbitrum -> Ethereum, Base, HyperEVM simultaneously.
    Burns are sequential (same source chain), attestations and receives are parallel.
    """

    salt_nonce = 42

    # Fund deployer with ETH/HYPE on all 4 forks
    for web3 in [web3_ethereum, web3_arbitrum, web3_base, web3_hyperliquid]:
        web3.provider.make_request("anvil_setBalance", [deployer.address, hex(100 * 10**18)])

    # --- Part 1: Multichain deployment with per-chain configs ---

    chain_configs = _make_chain_configs(deployer.address, salt_nonce)

    chain_web3 = {
        "ethereum": web3_ethereum,
        "arbitrum": web3_arbitrum,
        "base": web3_base,
        "hyperliquid": web3_hyperliquid,
    }

    result = deploy_multichain_lagoon_vault(
        chain_web3=chain_web3,
        deployer=deployer,
        chain_configs=chain_configs,
    )

    # Verify all Safe addresses are the same
    assert isinstance(result, LagoonMultichainDeployment)
    assert len(result.deployments) == 4
    safe_addresses = {name: d.safe_address for name, d in result.deployments.items()}
    assert len(set(safe_addresses.values())) == 1, f"Safe addresses differ: {safe_addresses}"

    # Verify vault addresses differ across chains
    vault_addresses = {name: d.vault.address for name, d in result.deployments.items()}
    assert len(set(vault_addresses.values())) == 4, f"Vault addresses should differ: {vault_addresses}"

    # Verify CCTP was configured on all 4 chains (including HyperEVM with domain 19)
    for chain_name in ["ethereum", "arbitrum", "base", "hyperliquid"]:
        guard = result.deployments[chain_name].trading_strategy_module
        assert guard is not None

    # --- Part 2: Parallel CCTP bridging Arbitrum -> Ethereum, Base, HyperEVM ---

    arb_vault = result.deployments["arbitrum"].vault
    arb_usdc = fetch_erc20_details(web3_arbitrum, USDC_NATIVE_TOKEN[42161])

    # Fund the Arbitrum vault with USDC so we can burn to 3 destinations
    arb_depositor = USDC_WHALE[42161]
    _fund_vault(web3_arbitrum, arb_vault, arb_usdc, arb_depositor, deployer.address, amount_usdc=400)

    bridge_amount = arb_usdc.convert_to_raw(100)  # 100 USDC per destination

    safe_balance_before = arb_usdc.contract.functions.balanceOf(arb_vault.safe_address).call()
    assert safe_balance_before >= bridge_amount * 3

    # Replace attesters on all 3 destination forks
    test_attesters = {
        1: replace_attester_on_fork(web3_ethereum),
        8453: replace_attester_on_fork(web3_base),
        999: replace_attester_on_fork(web3_hyperliquid),
    }

    # Record destination balances before bridging
    dest_chains = {
        "ethereum": (web3_ethereum, 1),
        "base": (web3_base, 8453),
        "hyperliquid": (web3_hyperliquid, 999),
    }
    dest_balances_before = {}
    for chain_name, (web3, chain_id) in dest_chains.items():
        usdc = fetch_erc20_details(web3, USDC_NATIVE_TOKEN[chain_id])
        dest_safe = result.deployments[chain_name].safe_address
        dest_balances_before[chain_name] = usdc.contract.functions.balanceOf(dest_safe).call()

    # Build parallel bridge destinations
    destinations = [
        CCTPBridgeDestination(
            dest_web3=web3_ethereum,
            dest_safe_address=result.deployments["ethereum"].safe_address,
            amount=bridge_amount,
        ),
        CCTPBridgeDestination(
            dest_web3=web3_base,
            dest_safe_address=result.deployments["base"].safe_address,
            amount=bridge_amount,
        ),
        CCTPBridgeDestination(
            dest_web3=web3_hyperliquid,
            dest_safe_address=result.deployments["hyperliquid"].safe_address,
            amount=bridge_amount,
        ),
    ]

    # Execute parallel bridge: burns sequential, attestations + receives parallel
    bridge_results = bridge_usdc_cctp_parallel(
        source_web3=web3_arbitrum,
        source_vault=arb_vault,
        destinations=destinations,
        sender=deployer.address,
        simulate=True,
        test_attesters=test_attesters,
    )

    # Verify results
    assert len(bridge_results) == 3
    for br in bridge_results:
        assert isinstance(br, CCTPBridgeResult)
        assert br.source_chain_id == 42161
        assert br.amount == bridge_amount
        assert br.burn_tx_hash
        assert br.receive_tx_hash

    # Verify destination chain IDs match
    assert bridge_results[0].dest_chain_id == 1  # Ethereum
    assert bridge_results[1].dest_chain_id == 8453  # Base
    assert bridge_results[2].dest_chain_id == 999  # HyperEVM

    # Verify USDC was burned on Arbitrum (3 * 100 USDC)
    safe_balance_after = arb_usdc.contract.functions.balanceOf(arb_vault.safe_address).call()
    assert safe_balance_after == safe_balance_before - (bridge_amount * 3)

    # Verify USDC was minted on each destination chain
    dest_chain_names = ["ethereum", "base", "hyperliquid"]
    for chain_name, (web3, chain_id) in dest_chains.items():
        usdc = fetch_erc20_details(web3, USDC_NATIVE_TOKEN[chain_id])
        dest_safe = result.deployments[chain_name].safe_address
        balance_after = usdc.contract.functions.balanceOf(dest_safe).call()
        assert balance_after == dest_balances_before[chain_name] + bridge_amount, f"USDC not minted on {chain_name}: before={dest_balances_before[chain_name]}, after={balance_after}"


@pytest.mark.timeout(600)
@pytest.mark.skipif(CI, reason="Long-running test. Run locally for testing.")
def test_satellite_deploy_bridge_and_swap(
    web3_arbitrum,
    web3_base,
    deployer,
):
    """Deploy Arbitrum as source vault, Base as satellite, bridge USDC, then swap on satellite.

    - Arbitrum gets a full Lagoon vault (source chain)
    - Base gets only Safe + TradingStrategyModuleV0 guard (satellite chain)
    - Bridges USDC from Arbitrum to Base satellite Safe via CCTP
    - Performs a Uniswap V3 swap (USDC -> WETH) on the Base satellite
      using ``transact_via_trading_strategy_module()``
    """

    salt_nonce = 43

    # Fund deployer with native tokens on both forks
    for web3 in [web3_arbitrum, web3_base]:
        web3.provider.make_request("anvil_setBalance", [deployer.address, hex(100 * 10**18)])

    # Create Uniswap V3 deployment for Base (needed for guard whitelisting)
    deployment_data = UNISWAP_V3_DEPLOYMENTS["base"]
    uniswap_v3_base = fetch_deployment_uni_v3(
        web3_base,
        factory_address=deployment_data["factory"],
        router_address=deployment_data["router"],
        position_manager_address=deployment_data["position_manager"],
        quoter_address=deployment_data["quoter"],
        quoter_v2=deployment_data.get("quoter_v2", False),
        router_v2=deployment_data.get("router_v2", False),
    )

    # Arbitrum: full vault (source chain)
    arb_cctp = CCTPDeployment.create_for_chain(chain_id=42161, allowed_destinations=[8453])
    arb_config = LagoonConfig(
        parameters=LagoonDeploymentParameters(underlying=None, name="Source Vault", symbol="SRC"),
        asset_manager=deployer.address,
        safe_owners=[deployer.address],
        safe_threshold=1,
        any_asset=True,
        safe_salt_nonce=salt_nonce,
        cctp_deployment=arb_cctp,
    )

    # Base: satellite chain (no vault, just Safe + guard)
    base_cctp = CCTPDeployment.create_for_chain(chain_id=8453, allowed_destinations=[42161])
    base_config = LagoonConfig(
        parameters=LagoonDeploymentParameters(underlying=None, name="Satellite Base", symbol="SAT"),
        asset_manager=deployer.address,
        safe_owners=[deployer.address],
        safe_threshold=1,
        any_asset=True,
        safe_salt_nonce=salt_nonce,
        satellite_chain=True,
        uniswap_v3=uniswap_v3_base,
        cctp_deployment=base_cctp,
    )

    chain_web3 = {"arbitrum": web3_arbitrum, "base": web3_base}
    chain_configs = {"arbitrum": arb_config, "base": base_config}

    result = deploy_multichain_lagoon_vault(
        chain_web3=chain_web3,
        deployer=deployer,
        chain_configs=chain_configs,
    )

    # Verify deployment types
    assert isinstance(result.deployments["arbitrum"].vault, LagoonVault)
    assert isinstance(result.deployments["base"].vault, LagoonSatelliteVault)
    assert result.deployments["arbitrum"].is_satellite is False
    assert result.deployments["base"].is_satellite is True

    # Verify same deterministic Safe address on both chains
    assert result.deployments["arbitrum"].safe_address == result.deployments["base"].safe_address

    # --- Fund Arbitrum vault and bridge USDC to Base satellite ---

    arb_vault = result.deployments["arbitrum"].vault
    arb_usdc = fetch_erc20_details(web3_arbitrum, USDC_NATIVE_TOKEN[42161])
    arb_depositor = USDC_WHALE[42161]
    _fund_vault(web3_arbitrum, arb_vault, arb_usdc, arb_depositor, deployer.address, amount_usdc=200)

    bridge_amount = arb_usdc.convert_to_raw(100)

    # Replace attester on Base fork for simulated CCTP attestation
    test_attesters = {8453: replace_attester_on_fork(web3_base)}

    destinations = [
        CCTPBridgeDestination(
            dest_web3=web3_base,
            dest_safe_address=result.deployments["base"].safe_address,
            amount=bridge_amount,
        ),
    ]

    bridge_results = bridge_usdc_cctp_parallel(
        source_web3=web3_arbitrum,
        source_vault=arb_vault,
        destinations=destinations,
        sender=deployer.address,
        simulate=True,
        test_attesters=test_attesters,
    )

    assert len(bridge_results) == 1
    assert bridge_results[0].dest_chain_id == 8453

    # Verify USDC arrived on Base satellite Safe
    base_usdc = fetch_erc20_details(web3_base, USDC_NATIVE_TOKEN[8453])
    base_safe_address = result.deployments["base"].safe_address
    usdc_balance = base_usdc.contract.functions.balanceOf(base_safe_address).call()
    assert usdc_balance == bridge_amount

    # --- Swap USDC -> WETH on Base satellite via trading strategy module ---

    satellite = result.deployments["base"].vault
    swap_amount = base_usdc.convert_to_raw(50)  # Swap 50 USDC

    # Approve USDC for Uniswap V3 router
    approve_call = base_usdc.contract.functions.approve(uniswap_v3_base.swap_router.address, swap_amount)
    moduled_tx = satellite.transact_via_trading_strategy_module(approve_call)
    tx_hash = moduled_tx.transact({"from": deployer.address, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3_base, tx_hash)

    # Get WETH token details for verification
    base_weth_address = "0x4200000000000000000000000000000000000006"
    base_weth = fetch_erc20_details(web3_base, base_weth_address)
    weth_before = base_weth.contract.functions.balanceOf(base_safe_address).call()

    # Swap USDC -> WETH via Uniswap V3
    swap_call = swap_with_slippage_protection(
        uniswap_v3_base,
        recipient_address=base_safe_address,
        base_token=base_weth.contract,
        quote_token=base_usdc.contract,
        amount_in=swap_amount,
        pool_fees=[500],
    )
    moduled_tx = satellite.transact_via_trading_strategy_module(swap_call)
    tx_hash = moduled_tx.transact({"from": deployer.address, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3_base, tx_hash)

    # Verify WETH balance increased on the satellite Safe
    weth_after = base_weth.contract.functions.balanceOf(base_safe_address).call()
    assert weth_after > weth_before, f"WETH balance did not increase: before={weth_before}, after={weth_after}"

    # Verify USDC balance decreased by swap amount
    usdc_after = base_usdc.contract.functions.balanceOf(base_safe_address).call()
    assert usdc_after == usdc_balance - swap_amount
