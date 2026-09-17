"""Fixed-Ethereum-fork Lighter bootstrap coverage for Lagoon settlement budgets."""

import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    DEFAULT_LAGOON_SETTLEMENT_WINDOW,
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
)
from eth_defi.erc_4626.vault_protocol.lagoon.funding import fund_lagoon_vault
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT, LIGHTER_USDC_ETHEREUM
from eth_defi.lighter.deployment import LighterDeployment
from eth_defi.lighter.lagoon import deposit_usdc_from_lagoon_safe_into_lighter
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.token import fetch_erc20_details

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed for Lighter bootstrap test"),
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


@pytest.fixture(scope="module")
def anvil_ethereum_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Reuse the canonical Ethereum midnight fork for this mutating test module."""
    assert JSON_RPC_ETHEREUM
    return anvil_fork_pool.get_launch(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


@pytest.fixture()
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create a Web3 connection to the pooled Ethereum fork."""
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, default_http_timeout=(3, 250.0))
    assert web3.eth.chain_id == 1
    return web3


@pytest.fixture(autouse=True)
def _evm_snapshot(anvil_ethereum_fork: AnvilLaunch):
    """Revert every bootstrap test back to the pooled fork baseline."""
    yield from evm_snapshot_revert(anvil_ethereum_fork)


def test_lighter_bootstrap_uses_the_configured_settlement_budget(web3: Web3) -> None:
    """Leave 19 USDC in Safe and allow the next 20-USDC operator settlement."""
    deployer = HotWallet.create_for_testing(web3, eth_amount=10)
    deployer.sync_nonce(web3)
    usdc = fetch_erc20_details(web3, LIGHTER_USDC_ETHEREUM)
    one_usdc_raw = usdc.convert_to_raw(Decimal(1))
    bootstrap_subscription_raw = usdc.convert_to_raw(Decimal(20))
    bootstrap_reserve_raw = usdc.convert_to_raw(Decimal(19))
    expected_usage_raw = usdc.convert_to_raw(Decimal(40))
    expected_safe_balance_raw = usdc.convert_to_raw(Decimal(39))
    fund_erc20_on_anvil(web3, usdc.address, deployer.address, usdc.convert_to_raw(100))
    lighter = LighterDeployment.create_ethereum()
    deployment = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer,
        asset_manager=deployer.address,
        parameters=LagoonDeploymentParameters(underlying=LIGHTER_USDC_ETHEREUM, name="Lighter bootstrap", symbol="BOOT"),
        safe_owners=[deployer.address],
        safe_threshold=1,
        lighter_deployment=lighter,
        assets=[LIGHTER_USDC_ETHEREUM],
        max_settlement_amount=Decimal(5_000),
        settlement_window=DEFAULT_LAGOON_SETTLEMENT_WINDOW,
        between_contracts_delay_seconds=0,
    )
    vault = deployment.vault
    module = deployment.trading_strategy_module

    fund_lagoon_vault(
        web3=web3,
        vault_address=vault.address,
        asset_manager=deployer.address,
        test_account_with_balance=deployer.address,
        trading_strategy_module_address=module.address,
        amount=Decimal(20),
        hot_wallet=deployer,
    )
    lighter_balance_before = usdc.fetch_raw_balance_of(LIGHTER_L1_CONTRACT)
    deposit_usdc_from_lagoon_safe_into_lighter(web3, deployer, vault=vault, usdc=usdc, deposit_usdc=Decimal(1))
    assert usdc.fetch_raw_balance_of(LIGHTER_L1_CONTRACT) - lighter_balance_before == one_usdc_raw
    assert usdc.fetch_raw_balance_of(vault.safe_address) == bootstrap_reserve_raw

    assert module.functions.getLagoonSettlementCooldownConfig(vault.address).call()[1] == bootstrap_subscription_raw

    fund_lagoon_vault(
        web3=web3,
        vault_address=vault.address,
        asset_manager=deployer.address,
        test_account_with_balance=deployer.address,
        trading_strategy_module_address=module.address,
        amount=Decimal(20),
        nav=Decimal(19),
        hot_wallet=deployer,
    )
    assert module.functions.getLagoonSettlementCooldownConfig(vault.address).call()[1] == expected_usage_raw
    assert usdc.fetch_raw_balance_of(vault.safe_address) == expected_safe_balance_raw
