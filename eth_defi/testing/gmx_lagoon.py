"""Reusable Lagoon/Safe deployment baselines for fixed-block GMX tests.

The deployment factories retain the original oracle-first ordering and real
contract interactions. Callers own fork lifetime and EVM snapshot isolation.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from eth_account import Account
from eth_utils import to_checksum_address
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonAutomatedDeployment, LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet
from eth_defi.gmx.testing import setup_mock_oracle
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.whitelist import GMXDeployment
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details

logger = logging.getLogger(__name__)

_GMX_ADDRESSES = get_contract_addresses("arbitrum")
GMX_EXCHANGE_ROUTER = _GMX_ADDRESSES.exchangerouter
GMX_SYNTHETICS_ROUTER = _GMX_ADDRESSES.syntheticsrouter
GMX_ORDER_VAULT = _GMX_ADDRESSES.ordervault

#: Token addresses on Arbitrum.
WETH_ARBITRUM = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
USDC_ARBITRUM = to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

#: Impersonated token holders used to fund the Safe.
USDC_WHALE = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
WETH_WHALE = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")


@dataclass(slots=True)
class LagoonGMXForkEnv:
    """Components for real Lagoon/Safe and GMX fork integration checks.

    Hold deployed addresses and fresh Python adapters together. Callers own
    process lifetime and must isolate EVM mutations between trading checks.
    """

    #: Fresh chain connection.
    web3: Web3
    #: Deployed Lagoon vault adapter.
    vault: LagoonVault
    #: Wallet wrapping Safe transactions.
    lagoon_wallet: LagoonGMXTradingWallet
    #: Signer with a synchronised nonce.
    asset_manager_wallet: HotWallet
    #: Configuration pointing at the Safe.
    gmx_config: GMXConfig
    #: Order construction and execution adapter.
    trading: GMXTrading
    #: Position reader.
    positions: GetOpenPositions
    #: Fork process owned by the caller.
    anvil_launch: AnvilLaunch
    #: Result of the underlying deployment.
    deploy_info: LagoonAutomatedDeployment


def create_lagoon_gmx_fork_env(anvil_launch: AnvilLaunch, *, forward_eth: bool = False) -> LagoonGMXForkEnv:  # noqa: PLR0914 - Wires the complete deployed integration environment.
    """Initialise a Lagoon GMX test environment on a fixed-block fork.

    Configure the mock oracle before deployment, then fund the Safe and create
    fresh Python adapters. Deployment installs the GMX collateral approvals.
    Fee-forwarding variants leave the Safe without native ETH so the manager
    must fund execution fees.

    :param anvil_launch:
        Live fixed-block Anvil process with the token whales unlocked.
    :param forward_eth:
        Enable manager-funded execution fees and leave the Safe at zero ETH.
    :return:
        Deployed environment. Caller must restore EVM state between sharers.

    See `Anvil fork documentation <https://getfoundry.sh/anvil/overview>`__.
    """
    # Connect to the fixed-block Anvil fork.
    web3 = create_multi_provider_web3(
        anvil_launch.json_rpc_url,
        default_http_timeout=(3.0, 180.0),
    )

    logger.info("Forked Arbitrum at block %s", web3.eth.block_number)

    # Setup mock oracle FIRST.
    setup_mock_oracle(web3)
    logger.info("Mock oracle configured")

    # Deploy Lagoon vault.
    # Use Anvil's default private key for deployer
    deployer_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    deployer_account = Account.from_key(deployer_key)
    deployer_wallet = HotWallet(deployer_account)
    deployer_wallet.sync_nonce(web3)

    # Fund deployer with ETH
    deployer_address = deployer_wallet.get_main_address()
    web3.provider.make_request("anvil_setBalance", [deployer_address, hex(100 * 10**18)])

    # Create asset manager wallet (separate from deployer)
    asset_manager_key = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    asset_manager_account = Account.from_key(asset_manager_key)
    asset_manager_wallet = HotWallet(asset_manager_account)
    asset_manager_wallet.sync_nonce(web3)
    asset_manager_address = asset_manager_wallet.get_main_address()

    # Fund asset manager with ETH
    web3.provider.make_request("anvil_setBalance", [asset_manager_address, hex(100 * 10**18)])

    # Safe owners (use Anvil test accounts)
    safe_owners = [web3.eth.accounts[2], web3.eth.accounts[3], web3.eth.accounts[4]]

    parameters = LagoonDeploymentParameters(
        underlying=USDC_ARBITRUM,
        name="Test GMX Vault (Forward ETH)" if forward_eth else "Test GMX Vault",
        symbol="TGMXF" if forward_eth else "TGMX",
    )

    # The GM ETH/USDC market also holds the WETH used for token funding.
    eth_usdc_market = WETH_WHALE

    gmx_deployment = GMXDeployment(
        exchange_router=GMX_EXCHANGE_ROUTER,
        synthetics_router=GMX_SYNTHETICS_ROUTER,
        order_vault=GMX_ORDER_VAULT,
        markets=[eth_usdc_market],
        tokens=[WETH_ARBITRUM, USDC_ARBITRUM],
    )

    logger.info("Deploying Lagoon vault with GMX support...")
    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer_wallet,
        asset_manager=asset_manager_address,
        parameters=parameters,
        safe_owners=safe_owners,
        safe_threshold=2,
        uniswap_v2=None,
        uniswap_v3=None,
        any_asset=True,  # Allow any asset for GMX trading
        cowswap=False,
        use_forge=True,
        from_the_scratch=False,
        gmx_deployment=gmx_deployment,
    )

    vault = deploy_info.vault
    safe_address = vault.safe_address
    module = deploy_info.trading_strategy_module
    logger.info("Lagoon vault deployed. Safe address: %s", safe_address)

    # Verify GMX whitelisting succeeded (done automatically during deployment)
    is_exchange_router_allowed = module.functions.isAllowedTarget(GMX_EXCHANGE_ROUTER).call()
    is_synthetics_router_approved = module.functions.isAllowedApprovalDestination(GMX_SYNTHETICS_ROUTER).call()
    logger.info(
        "Whitelisting verification - ExchangeRouter allowed: %s, SyntheticsRouter approved: %s",
        is_exchange_router_allowed,
        is_synthetics_router_approved,
    )
    assert is_exchange_router_allowed, f"ExchangeRouter {GMX_EXCHANGE_ROUTER} should be allowed"
    assert is_synthetics_router_approved, f"SyntheticsRouter {GMX_SYNTHETICS_ROUTER} should be approved"

    logger.info("GMX contracts whitelisted via deployment")

    # Fund vault's Safe with tokens.
    # Fund whales with gas
    web3.provider.make_request("anvil_setBalance", [USDC_WHALE, hex(10 * 10**18)])
    web3.provider.make_request("anvil_setBalance", [WETH_WHALE, hex(10 * 10**18)])

    # Transfer USDC to Safe
    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)
    usdc.transfer(safe_address, Decimal(100_000)).transact({"from": USDC_WHALE})

    # Transfer WETH to Safe
    weth = fetch_erc20_details(web3, WETH_ARBITRUM)
    weth.transfer(safe_address, Decimal(50)).transact({"from": WETH_WHALE})

    # Fee-forwarding tests require the manager to supply the native execution fee.
    safe_eth = 0 if forward_eth else 100 * 10**18
    web3.provider.make_request("anvil_setBalance", [safe_address, hex(safe_eth)])
    logger.info("Safe funded with 100000 USDC, 50 WETH; forward_eth=%s", forward_eth)

    # Create LagoonGMXTradingWallet.
    lagoon_wallet = LagoonGMXTradingWallet(
        vault=vault,
        asset_manager=asset_manager_wallet,
        gas_buffer=500_000,  # Extra gas for performCall overhead
        forward_eth=forward_eth,
    )

    # Create GMXConfig pointing to Safe address.
    gmx_config = GMXConfig(web3, user_wallet_address=safe_address)

    # GMX collateral token approvals are now handled automatically by
    # deploy_automated_lagoon_vault() — no manual approval needed here.

    # Sync nonce after deployment
    asset_manager_wallet.sync_nonce(web3)

    # Create trading and position instances
    trading = GMXTrading(gmx_config)
    positions = GetOpenPositions(gmx_config)

    return LagoonGMXForkEnv(
        web3=web3,
        vault=vault,
        lagoon_wallet=lagoon_wallet,
        asset_manager_wallet=asset_manager_wallet,
        gmx_config=gmx_config,
        trading=trading,
        positions=positions,
        anvil_launch=anvil_launch,
        deploy_info=deploy_info,
    )


def create_cached_lagoon_gmx_fork_env(
    anvil_launch: AnvilLaunch,
    baselines: dict[tuple[int, float], LagoonGMXForkEnv],
) -> LagoonGMXForkEnv:
    """Reuse a deployed baseline only for the exact live fork generation.

    Check deployed code on hits in case another snapshot removed the baseline.
    A recycled fork can reuse its URL and eventually even its PID. Include the
    process creation time so stale contract addresses cannot survive a restart.
    Callers must snapshot EVM state and reset Python-side state for each test.

    :param anvil_launch:
        Liveness-checked pooled fork process.
    :param baselines:
        Per-worker deployment cache, retaining only the current generation.
    :return:
        Existing or newly deployed baseline for this fork.
    """
    generation = (anvil_launch.process.pid, anvil_launch.process.create_time())
    baseline = baselines.get(generation)
    if baseline is not None and not baseline.web3.eth.get_code(baseline.deploy_info.trading_strategy_module.address):
        baseline = None
    if baseline is None:
        baseline = create_lagoon_gmx_fork_env(anvil_launch)
        baselines.clear()
        baselines[generation] = baseline
    return baseline
