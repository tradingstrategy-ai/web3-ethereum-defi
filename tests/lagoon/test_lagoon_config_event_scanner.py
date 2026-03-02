"""Test guard configuration event scanner for multichain Lagoon deployments.

- Deploys Lagoon vault on Arbitrum (source) + Base (satellite) with CCTP
- Reads back all guard configuration events via config_event_scanner
- Verifies both raw events and structured MultichainGuardConfig output
- Tests symbolic address resolution for approval destinations, vaults, and receivers
"""

import logging
import os

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.cctp.constants import CHAIN_ID_TO_CCTP_DOMAIN
from eth_defi.cctp.whitelist import CCTPDeployment
from eth_defi.cow.constants import COWSWAP_SETTLEMENT, COWSWAP_VAULT_RELAYER
from eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner import (
    ChainGuardConfig,
    DecodedGuardEvent,
    MultichainGuardConfig,
    build_multichain_guard_config,
    fetch_guard_config_events,
    format_chain_config_detailed,
    format_guard_config_report,
    resolve_address_label,
    resolve_trading_strategy_module,
)
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonAutomatedDeployment,
    LagoonConfig,
    LagoonDeploymentParameters,
    LagoonMultichainDeployment,
    deploy_automated_lagoon_vault,
    deploy_multichain_lagoon_vault,
)
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonSatelliteVault, LagoonVault
from eth_defi.gmx.whitelist import GMX_ARBITRUM_ADDRESSES, GMX_POPULAR_MARKETS, GMXDeployment
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, USDC_WHALE, WRAPPED_NATIVE_TOKEN
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.deployment import fetch_deployment as fetch_deployment_uni_v3

logger = logging.getLogger(__name__)

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")
CI = os.environ.get("CI") == "true"


def _first_rpc_url(multi_rpc: str | None) -> str | None:
    """Extract the first non-mev RPC URL from a space-separated multi-RPC string."""
    if not multi_rpc:
        return None
    non_mev = [u for u in multi_rpc.split() if u and not u.startswith("mev+")]
    return non_mev[0] if non_mev else multi_rpc.split()[0].replace("mev+", "", 1)


pytestmark = pytest.mark.skipif(
    not JSON_RPC_ARBITRUM or not JSON_RPC_BASE,
    reason="JSON_RPC_ARBITRUM and JSON_RPC_BASE environment variables required",
)

#: Anvil default account #0 private key. Fixed so the deployer address is the same on all chains.
DEPLOYER_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


@pytest.fixture(scope="module")
def deployer() -> LocalAccount:
    return Account.from_key(DEPLOYER_PRIVATE_KEY)


@pytest.fixture(scope="module")
def anvil_arbitrum() -> AnvilLaunch:
    launch = fork_network_anvil(
        _first_rpc_url(JSON_RPC_ARBITRUM),
        unlocked_addresses=[USDC_WHALE[42161]],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture(scope="module")
def anvil_base() -> AnvilLaunch:
    launch = fork_network_anvil(
        _first_rpc_url(JSON_RPC_BASE),
        unlocked_addresses=[USDC_WHALE[8453]],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture(scope="module")
def web3_arbitrum(anvil_arbitrum) -> Web3:
    web3 = create_multi_provider_web3(
        anvil_arbitrum.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 42161
    return web3


@pytest.fixture(scope="module")
def web3_base(anvil_base) -> Web3:
    web3 = create_multi_provider_web3(
        anvil_base.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture(scope="module")
def from_blocks(web3_arbitrum, web3_base) -> dict[int, int]:
    """Record block numbers before deployment starts.

    On Anvil forks the chain history extends all the way to the real chain's
    genesis, so scanning from block 0 via ``eth_getLogs`` would time out.
    """
    return {
        42161: web3_arbitrum.eth.block_number,
        8453: web3_base.eth.block_number,
    }


@pytest.fixture(scope="module")
def multichain_deployment(
    web3_arbitrum,
    web3_base,
    deployer,
    from_blocks,
) -> LagoonMultichainDeployment:
    """Deploy Arbitrum (source vault) + Base (satellite) with CCTP."""

    salt_nonce = 44

    for web3 in [web3_arbitrum, web3_base]:
        web3.provider.make_request("anvil_setBalance", [deployer.address, hex(100 * 10**18)])

    # Uniswap V3 deployment for Base (for guard whitelisting)
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

    # Arbitrum: full vault (source chain) with CCTP to Base
    arb_cctp = CCTPDeployment.create_for_chain(chain_id=42161, allowed_destinations=[8453])
    arb_config = LagoonConfig(
        parameters=LagoonDeploymentParameters(underlying=None, name="Scanner Test Vault", symbol="SCAN"),
        asset_manager=deployer.address,
        safe_owners=[deployer.address],
        safe_threshold=1,
        any_asset=True,
        safe_salt_nonce=salt_nonce,
        cctp_deployment=arb_cctp,
    )

    # Base: satellite chain with CCTP back to Arbitrum + Uniswap V3
    base_cctp = CCTPDeployment.create_for_chain(chain_id=8453, allowed_destinations=[42161])
    base_config = LagoonConfig(
        parameters=LagoonDeploymentParameters(underlying=None, name="Scanner Satellite", symbol="SAT"),
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

    assert isinstance(result.deployments["arbitrum"].vault, LagoonVault)
    assert isinstance(result.deployments["base"].vault, LagoonSatelliteVault)

    return result


@pytest.mark.timeout(600)
@pytest.mark.skipif(CI, reason="Long-running test. Run locally.")
def test_resolve_trading_strategy_module(
    multichain_deployment,
    web3_arbitrum,
    web3_base,
):
    """Verify module resolution finds TradingStrategyModuleV0 on both chains."""

    safe_address = multichain_deployment.safe_address

    # Arbitrum
    arb_module = resolve_trading_strategy_module(web3_arbitrum, safe_address)
    assert arb_module is not None
    expected_arb = multichain_deployment.deployments["arbitrum"].trading_strategy_module.address
    assert arb_module == Web3.to_checksum_address(expected_arb)

    # Base
    base_module = resolve_trading_strategy_module(web3_base, safe_address)
    assert base_module is not None
    expected_base = multichain_deployment.deployments["base"].trading_strategy_module.address
    assert base_module == Web3.to_checksum_address(expected_base)


@pytest.mark.timeout(600)
@pytest.mark.skipif(CI, reason="Long-running test. Run locally.")
def test_fetch_guard_config_events_raw(
    multichain_deployment,
    web3_arbitrum,
    web3_base,
    deployer,
    from_blocks,
):
    """Verify raw event scanning returns expected event types from both chains."""

    safe_address = multichain_deployment.safe_address

    # Scan starting from Arbitrum, follow CCTP to Base
    events, module_addresses = fetch_guard_config_events(
        safe_address=safe_address,
        web3=web3_arbitrum,
        chain_web3={42161: web3_arbitrum, 8453: web3_base},
        follow_cctp=True,
        from_block=from_blocks,
    )

    # Should have events from both chains
    assert 42161 in events, f"No events for Arbitrum, got chains: {list(events.keys())}"
    assert 8453 in events, f"No events for Base, got chains: {list(events.keys())}"

    # Module addresses should match deployment
    assert module_addresses[42161] == Web3.to_checksum_address(multichain_deployment.deployments["arbitrum"].trading_strategy_module.address)
    assert module_addresses[8453] == Web3.to_checksum_address(multichain_deployment.deployments["base"].trading_strategy_module.address)

    # Verify expected event types on Arbitrum
    arb_event_names = {e.event_name for e in events[42161]}
    assert "SenderApproved" in arb_event_names
    assert "ReceiverApproved" in arb_event_names
    assert "AnyAssetSet" in arb_event_names
    assert "CCTPMessengerApproved" in arb_event_names
    assert "CCTPDestinationApproved" in arb_event_names
    assert "LagoonVaultApproved" in arb_event_names  # Source chain has vault

    # Verify expected event types on Base
    base_event_names = {e.event_name for e in events[8453]}
    assert "SenderApproved" in base_event_names
    assert "ReceiverApproved" in base_event_names
    assert "CCTPDestinationApproved" in base_event_names

    # All events should have valid block numbers and tx hashes
    for chain_id, chain_events in events.items():
        for event in chain_events:
            assert isinstance(event, DecodedGuardEvent)
            assert event.block_number > 0
            assert event.transaction_hash.startswith("0x")

    # Verify sender is the deployer (asset manager)
    arb_sender_events = [e for e in events[42161] if e.event_name == "SenderApproved"]
    assert len(arb_sender_events) >= 1
    assert arb_sender_events[0].args["sender"] == Web3.to_checksum_address(deployer.address)


@pytest.mark.timeout(600)
@pytest.mark.skipif(CI, reason="Long-running test. Run locally.")
def test_build_multichain_guard_config(
    multichain_deployment,
    web3_arbitrum,
    web3_base,
    deployer,
    from_blocks,
):
    """Verify structured config matches deployment parameters."""

    safe_address = multichain_deployment.safe_address

    events, module_addresses = fetch_guard_config_events(
        safe_address=safe_address,
        web3=web3_arbitrum,
        chain_web3={42161: web3_arbitrum, 8453: web3_base},
        follow_cctp=True,
        from_block=from_blocks,
    )

    config = build_multichain_guard_config(events, safe_address, module_addresses)

    assert isinstance(config, MultichainGuardConfig)
    assert config.safe_address == Web3.to_checksum_address(safe_address)
    assert len(config.chains) == 2
    assert 42161 in config.chains
    assert 8453 in config.chains

    # --- Verify Arbitrum (source chain) config ---
    arb_cfg = config.chains[42161]
    assert isinstance(arb_cfg, ChainGuardConfig)
    assert arb_cfg.chain_id == 42161
    assert arb_cfg.chain_name == "Arbitrum"

    # Sender is the deployer/asset manager
    assert Web3.to_checksum_address(deployer.address) in arb_cfg.senders

    # Receiver is the Safe
    assert Web3.to_checksum_address(safe_address) in arb_cfg.receivers

    # any_asset enabled
    assert arb_cfg.any_asset is True

    # CCTP: destination is Base (domain 6)
    assert CHAIN_ID_TO_CCTP_DOMAIN[8453] in arb_cfg.cctp_destinations
    assert len(arb_cfg.cctp_messengers) >= 1

    # Lagoon vault whitelisted on source chain
    assert len(arb_cfg.lagoon_vaults) >= 1

    # Call sites should be populated
    assert len(arb_cfg.call_sites) > 0

    # --- Verify Base (satellite chain) config ---
    base_cfg = config.chains[8453]
    assert base_cfg.chain_id == 8453
    assert base_cfg.chain_name == "Base"

    # Sender is the deployer/asset manager
    assert Web3.to_checksum_address(deployer.address) in base_cfg.senders

    # Receiver is the Safe
    assert Web3.to_checksum_address(safe_address) in base_cfg.receivers

    # any_asset enabled
    assert base_cfg.any_asset is True

    # CCTP: destination is Arbitrum (domain 3)
    assert CHAIN_ID_TO_CCTP_DOMAIN[42161] in base_cfg.cctp_destinations

    # Base has Uniswap V3 whitelisted (approval destinations)
    assert len(base_cfg.approval_destinations) > 0

    # --- Verify human-readable output ---
    human_output = config.format_human_readable()
    assert "Arbitrum" in human_output
    assert "Base" in human_output
    assert safe_address in human_output
    assert "CCTP destinations:" in human_output


# ---------------------------------------------------------------------------
# Full-protocol deployment for symbolic resolution tests
# ---------------------------------------------------------------------------

#: Known ERC-4626 vault on Arbitrum: Umami gmUSDC (non-standard but has name())
UMAMI_GM_USDC = "0x959f3807f0aa7921e18c78b00b2819ba91e52fef"

#: D2 Finance vault on Arbitrum (standard ERC-4626 with name())
D2_FINANCE_VAULT = "0x75288264FDFEA8ce68e6D852696aB1cE2f3E5004"


@pytest.fixture(scope="module")
def full_protocol_from_block(web3_arbitrum) -> int:
    """Record block number before full-protocol deployment.

    Must be requested BEFORE the full_protocol_deployment fixture runs.
    """
    return web3_arbitrum.eth.block_number


@pytest.fixture(scope="module")
def full_protocol_deployment(
    web3_arbitrum,
    deployer,
    full_protocol_from_block,
) -> LagoonAutomatedDeployment:
    """Deploy a Lagoon vault on Arbitrum with all major protocols whitelisted.

    Deploys with explicit token whitelist (no any_asset) and all supported
    protocols: Uniswap V3, Velora, CowSwap, GMX.  After deployment,
    impersonates the Safe to add ERC-4626 vault whitelisting.
    """
    web3_arbitrum.provider.make_request("anvil_setBalance", [deployer.address, hex(200 * 10**18)])

    # Uniswap V3 on Arbitrum
    deployment_data = UNISWAP_V3_DEPLOYMENTS["arbitrum"]
    uniswap_v3_arb = fetch_deployment_uni_v3(
        web3_arbitrum,
        factory_address=deployment_data["factory"],
        router_address=deployment_data["router"],
        position_manager_address=deployment_data["position_manager"],
        quoter_address=deployment_data["quoter"],
        quoter_v2=deployment_data.get("quoter_v2", False),
        router_v2=deployment_data.get("router_v2", False),
    )

    # GMX with ETH/USD and BTC/USD markets (use hardcoded addresses to avoid GitHub fetch)
    gmx = GMXDeployment(
        exchange_router=GMX_ARBITRUM_ADDRESSES["exchange_router"],
        synthetics_router=GMX_ARBITRUM_ADDRESSES["synthetics_router"],
        order_vault=GMX_ARBITRUM_ADDRESSES["order_vault"],
        markets=[
            GMX_POPULAR_MARKETS["ETH/USD"],
            GMX_POPULAR_MARKETS["BTC/USD"],
        ],
    )

    wallet = HotWallet(deployer)
    wallet.sync_nonce(web3_arbitrum)

    config = LagoonConfig(
        parameters=LagoonDeploymentParameters(
            underlying=USDC_NATIVE_TOKEN[42161],
            name="Full Protocol Test Vault",
            symbol="FPTV",
        ),
        asset_manager=deployer.address,
        safe_owners=[deployer.address],
        safe_threshold=1,
        any_asset=False,
        uniswap_v3=uniswap_v3_arb,
        velora=True,
        cowswap=True,
        gmx_deployment=gmx,
        assets=[
            USDC_NATIVE_TOKEN[42161],
            WRAPPED_NATIVE_TOKEN[42161],
        ],
        safe_salt_nonce=99,
    )

    result = deploy_automated_lagoon_vault(
        web3=web3_arbitrum,
        deployer=wallet,
        config=config,
    )
    assert isinstance(result.vault, LagoonVault)

    # Impersonate the Safe to add ERC-4626 vault whitelisting
    safe_address = result.safe_address
    module = result.trading_strategy_module

    web3_arbitrum.provider.make_request("anvil_impersonateAccount", [safe_address])
    web3_arbitrum.provider.make_request("anvil_setBalance", [safe_address, hex(10 * 10**18)])

    module.functions.whitelistERC4626(
        Web3.to_checksum_address(UMAMI_GM_USDC),
        "Umami gmUSDC vault",
    ).transact({"from": safe_address})

    module.functions.whitelistERC4626(
        Web3.to_checksum_address(D2_FINANCE_VAULT),
        "D2 Finance vault",
    ).transact({"from": safe_address})

    web3_arbitrum.provider.make_request("anvil_stopImpersonatingAccount", [safe_address])

    return result


@pytest.mark.timeout(600)
@pytest.mark.skipif(CI, reason="Long-running test. Run locally.")
def test_symbolic_address_resolution(
    full_protocol_deployment,
    web3_arbitrum,
    deployer,
    full_protocol_from_block,
):
    """Verify that approval destinations, vaults, and receivers are symbolically resolved.

    Deploys a guard with all major protocols (Uniswap V3, Velora, CowSwap, GMX,
    ERC-4626 vaults), reads back the configuration, and checks that
    :func:`format_chain_config_detailed` resolves addresses to human-readable
    names including ``<our multisig>`` for the Safe address.
    """
    safe_address = full_protocol_deployment.safe_address

    # Read back events
    events, module_addresses = fetch_guard_config_events(
        safe_address=safe_address,
        web3=web3_arbitrum,
        follow_cctp=False,
        from_block={42161: full_protocol_from_block},
    )

    assert 42161 in events
    config = build_multichain_guard_config(events, safe_address, module_addresses)
    arb_cfg = config.chains[42161]

    # --- Verify structured config has expected whitelisting ---

    # No any_asset — explicit token whitelist
    assert arb_cfg.any_asset is False

    # WETH and USDC should be whitelisted
    usdc = Web3.to_checksum_address(USDC_NATIVE_TOKEN[42161])
    weth = Web3.to_checksum_address(WRAPPED_NATIVE_TOKEN[42161])
    assert usdc in arb_cfg.assets
    assert weth in arb_cfg.assets

    # Uniswap V3 router should be in approval destinations
    uni_router = Web3.to_checksum_address(UNISWAP_V3_DEPLOYMENTS["arbitrum"]["router"])
    assert uni_router in arb_cfg.approval_destinations

    # CowSwap settlement and vault relayer whitelisted
    assert len(arb_cfg.cowswap_settlements) >= 1

    # Velora swapper whitelisted
    assert len(arb_cfg.velora_swappers) >= 1

    # GMX routers and markets whitelisted
    assert len(arb_cfg.gmx_routers) >= 1
    gmx_eth_usd = Web3.to_checksum_address(GMX_POPULAR_MARKETS["ETH/USD"])
    gmx_btc_usd = Web3.to_checksum_address(GMX_POPULAR_MARKETS["BTC/USD"])
    assert gmx_eth_usd in arb_cfg.gmx_markets
    assert gmx_btc_usd in arb_cfg.gmx_markets

    # ERC-4626 vaults should be in approval destinations (whitelistERC4626 adds them)
    umami = Web3.to_checksum_address(UMAMI_GM_USDC)
    d2 = Web3.to_checksum_address(D2_FINANCE_VAULT)
    assert umami in arb_cfg.approval_destinations
    assert d2 in arb_cfg.approval_destinations

    # Receiver should include the Safe
    assert Web3.to_checksum_address(safe_address) in arb_cfg.receivers

    # --- Verify symbolic address resolution ---

    # resolve_address_label should resolve the Safe as <our multisig>
    safe_label = resolve_address_label(
        web3_arbitrum,
        safe_address,
        known_labels={Web3.to_checksum_address(safe_address): "<our multisig>"},
    )
    assert "<our multisig>" in safe_label
    assert safe_address in safe_label

    # resolve_address_label should resolve ERC-4626 vaults by name()
    umami_label = resolve_address_label(web3_arbitrum, umami)
    assert "<unknown>" not in umami_label, f"Umami vault should resolve to a name, got: {umami_label}"
    assert umami in umami_label

    d2_label = resolve_address_label(web3_arbitrum, d2)
    assert "<unknown>" not in d2_label, f"D2 vault should resolve to a name, got: {d2_label}"
    assert d2 in d2_label

    # --- Verify formatted output resolves addresses ---

    report = format_chain_config_detailed(arb_cfg, web3=web3_arbitrum)

    # Safe address in receivers should be labelled as <our multisig>
    assert "<our multisig>" in report

    # ERC-4626 vaults in approval destinations should have their names resolved
    # (Umami and D2 vaults have name(), but protocol routers like Uniswap V3,
    # Velora TokenTransferProxy, CowSwap Settlement don't — those are <unknown>)
    assert "gmUSDC" in report, "Umami gmUSDC vault should be resolved in approval destinations"

    # Full report should contain all protocol sections
    assert "Senders (trade executors):" in report
    assert "Receivers:" in report
    assert "Approval destinations (routers):" in report
    assert "CowSwap settlements:" in report
    assert "Velora (ParaSwap) swappers:" in report
    assert "GMX routers:" in report
    assert "GMX markets:" in report
    assert "Whitelisted assets:" in report

    # Lagoon vault should have its name resolved
    assert "Full Protocol Test Vault" in report

    # Full multichain report should also work
    full_report = format_guard_config_report(
        config=config,
        events=events,
        chain_web3={42161: web3_arbitrum},
    )
    assert "Arbitrum" in full_report
    assert "<our multisig>" in full_report
