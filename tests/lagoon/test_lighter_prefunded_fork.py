"""Prefunded Ethereum activation with real custody calls and a mocked Lighter sequencer.

The Arbitrum hub starts with zero USDC. No CCTP configuration or attestation
service is needed. Shared canonical forks isolate all EVM mutations.
"""

import os
import stat
from contextlib import ExitStack, contextmanager
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
from eth_account import Account

from eth_defi.erc_4626.vault_protocol.lagoon import lighter as mc
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonConfig, LagoonDeploymentParameters, deploy_multichain_lagoon_vault
from eth_defi.lighter import activation as ac
from eth_defi.lighter.deployment import LighterDeployment
from eth_defi.lighter.testing import register_lighter_account_on_anvil
from eth_defi.provider.anvil import fund_erc20_on_anvil, set_balance
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import get_midnight_block
from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details

pytestmark = [
    pytest.mark.skipif(not all(os.environ.get(name) for name in ("JSON_RPC_ETHEREUM", "JSON_RPC_ARBITRUM")), reason="Ethereum and Arbitrum RPCs required"),
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


def test_prefunded_ethereum_activates_without_hub_subscription(anvil_fork_pool: AnvilForkPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One Ethereum USDC pays for activation while the hub issues no shares."""
    chains = {"arbitrum": 42161, "ethereum": 1}
    connections = {name: anvil_fork_pool.get_web3(os.environ[f"JSON_RPC_{name.upper()}"], get_midnight_block(chain_id)) for name, chain_id in chains.items()}
    with ExitStack() as resources:
        for web3 in connections.values():
            resources.enter_context(contextmanager(evm_snapshot_revert)(web3.provider.endpoint_uri, strict=True))
        owner = Account.create()
        tokens = {name: fetch_erc20_details(connections[name], USDC_NATIVE_TOKEN[chain_id]) for name, chain_id in chains.items()}
        for name, web3 in connections.items():
            set_balance(web3, owner.address, 100 * 10**18)
            fund_erc20_on_anvil(web3, tokens[name].address, owner.address, tokens[name].convert_to_raw(Decimal(1) if name == "ethereum" else Decimal(0)))
        configs = {
            name: LagoonConfig(
                parameters=LagoonDeploymentParameters(underlying=tokens[name].address, name="Prefunded activation", symbol="PREFUND"),
                safe_salt_nonce=123,
                asset_managers=[owner.address],
                safe_owners=[owner.address],
                safe_threshold=1,
                satellite_chain=name == "ethereum",
                assets=[tokens[name].address],
                any_asset=False,
                lighter_deployment=LighterDeployment.create_ethereum() if name == "ethereum" else None,
            )
            for name in chains
        }

        # Only sequencing/indexing is simulated; funding, guarded deposit and
        # Safe-owner key registration execute against the real fork contracts.
        def activate(web3, wallet, safe, **kwargs):
            assert tokens["ethereum"].fetch_balance_of(owner.address) == Decimal(0)
            assert tokens["ethereum"].fetch_balance_of(safe.safe_address) == Decimal(1)
            register_lighter_account_on_anvil(web3, safe.safe_address, 123456)
            return ac.activate_lighter_safe(web3, wallet, safe, **kwargs)

        monkeypatch.setattr(mc, "activate_lighter_safe", activate)
        monkeypatch.setattr(ac, "create_lighter_session", Mock(return_value=Mock(__enter__=Mock(), __exit__=Mock(return_value=False))))
        monkeypatch.setattr(ac, "wait_for_lighter_account", Mock(return_value=123456))
        monkeypatch.setattr(ac, "wait_for_lighter_collateral", Mock(return_value=Decimal(1)))
        monkeypatch.setattr(ac, "fetch_lighter_api_key", Mock(return_value=None))
        monkeypatch.setattr(ac, "wait_for_lighter_api_key", Mock())
        activation = mc.LagoonLighterActivation(hub_chain="arbitrum", report_path=tmp_path / "activation.jsonl")
        result = deploy_multichain_lagoon_vault(chain_web3=connections, deployer=owner, chain_configs=configs, lighter_activation=activation)
        assert result.lighter_funding["contribution_usdc"] == "1"
        assert result.lighter_funding["subscription_usdc"] == "0"
        assert result.deployments["arbitrum"].vault.vault_contract.functions.totalSupply().call() == 0
        for name in chains:
            assert tokens[name].fetch_balance_of(owner.address) == Decimal(0)
            assert tokens[name].fetch_balance_of(result.safe_address) == Decimal(0)
        assert result.deployments["ethereum"].lighter_account_setup.observed_collateral == Decimal(1)
        receipt = connections["ethereum"].eth.get_transaction_receipt(result.lighter_funding["funding_tx_hash"])
        assert receipt["status"] == 1
        assert receipt["to"] == tokens["ethereum"].address
        assert stat.S_IMODE(activation.report_path.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR
