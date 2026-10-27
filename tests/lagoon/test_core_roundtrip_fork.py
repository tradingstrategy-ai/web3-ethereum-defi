"""Three-chain custody lifecycle with real contracts and explicit Core settlement doubles.

The shared forks use canonical blocks and snapshot isolation. This proves the
Ethereum routes, not either live Core sequencer or a production NAV reader.
"""

import datetime
import os
from contextlib import ExitStack, contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from eth_account import Account

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.vault_protocol.lagoon import roundtrip as rt
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonAutomatedDeployment
from eth_defi.erc_4626.vault_protocol.lagoon.funding import SATELLITE_DEPLOYER_USDC
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fund_erc20_on_anvil, set_balance
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import get_midnight_block
from eth_defi.testing.lagoon_roundtrip import RoundtripSimulation
from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details

RPC_ENV = {"arbitrum": "JSON_RPC_ARBITRUM", "ethereum": "JSON_RPC_ETHEREUM", "hyperliquid": "JSON_RPC_HYPERLIQUID"}
pytestmark = [
    pytest.mark.skipif(not all(os.environ.get(name) for name in RPC_ENV.values()), reason="Three mainnet RPCs required"),
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


def test_three_chain_invest_pending_restart_and_redeem(anvil_fork_pool: AnvilForkPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Locked Core capital delays NAV while the independent Lighter leg exits exactly once."""
    connections = {chain: anvil_fork_pool.get_web3(os.environ[RPC_ENV[chain]], get_midnight_block(chain_id)) for chain, chain_id in rt.CHAINS.items()}
    with ExitStack() as resources:
        for web3 in connections.values():
            resources.enter_context(contextmanager(evm_snapshot_revert)(web3.provider.endpoint_uri, strict=True))
        owner = Account.create()
        settings = rt.RoundtripSettings(action="all", simulate=True, state_path=tmp_path / "private" / "state.json")
        state = rt.RoundtripState(simulate=True, owner=owner.address)
        tokens = {c: fetch_erc20_details(w, USDC_NATIVE_TOKEN[rt.CHAINS[c]]) for c, w in connections.items()}
        for web3 in connections.values():
            set_balance(web3, owner.address, 100 * 10**18)
        for chain, token in tokens.items():
            amount = settings.subscription if chain == "arbitrum" else SATELLITE_DEPLOYER_USDC
            fund_erc20_on_anvil(connections[chain], token.address, owner.address, token.convert_to_raw(amount))
        wallets = {c: HotWallet(owner) for c in connections}
        ctx = rt.RoundtripContext(settings, state, connections, wallets, tokens)
        resources.callback(ctx.hyper_session.close)
        resources.callback(ctx.lighter_session.close)
        simulation = RoundtripSimulation()
        simulation.attach(ctx)
        rt.deploy_and_invest(ctx)
        simulation.advance_investments()
        ctx.state.steps["simulation_core"]["locked_until"] = (native_datetime_utc_now() + datetime.timedelta(days=1)).isoformat()
        ctx.save()
        with pytest.raises(rt.RoundtripPendingError, match="locked until"):
            rt.unwind_and_redeem(ctx)
        assert ctx.state.bridges["return_ethereum"]["complete"]
        assert "redemption_request" not in ctx.state.transactions
        ethereum_nonce = connections["ethereum"].eth.get_transaction_count(owner.address)
        # Release only the fake Core lock; do not advance or alter real-chain time.
        ctx.state.steps["simulation_core"]["locked_until"] = None
        ctx.save()
        restored = rt.load_state(settings.state_path, simulate=True)
        ctx = rt.RoundtripContext(settings, restored, connections, {c: HotWallet(owner) for c in connections}, tokens)
        ctx.deployments = {c: LagoonAutomatedDeployment.from_json_friendly_dict(connections[c], row) for c, row in restored.deployments.items()}
        resources.callback(ctx.hyper_session.close)
        resources.callback(ctx.lighter_session.close)
        simulation.attach(ctx)
        send_transaction = rt.send_transaction

        def send_with_unrelated_credit(ctx: rt.RoundtripContext, name: str, chain: str, call: object) -> str:
            """Model unrelated investor wallet activity during a delayed redemption."""
            if name == "redemption_claim" and name not in ctx.state.transactions:
                token = tokens["arbitrum"]
                fund_erc20_on_anvil(connections["arbitrum"], token.address, owner.address, token.convert_to_raw(Decimal(1)))
            return send_transaction(ctx, name, chain, call)

        monkeypatch.setattr(rt, "send_transaction", send_with_unrelated_credit)
        rt.unwind_and_redeem(ctx)
        assert ctx.state.outcome == "redeemed_with_dust"
        assert ctx.state.results["residual_usdc"] == "0.009"
        assert Decimal(ctx.state.results["claimed_usdc"]) == Decimal("24.185220")
        assert connections["ethereum"].eth.get_transaction_count(owner.address) == ethereum_nonce
        assert tokens["arbitrum"].fetch_balance_of(owner.address) == Decimal("25.185220")
        assert tokens["ethereum"].fetch_balance_of(owner.address) == SATELLITE_DEPLOYER_USDC
        assert tokens["hyperliquid"].fetch_balance_of(owner.address) == SATELLITE_DEPLOYER_USDC
        counts = {c: w.eth.get_transaction_count(owner.address) for c, w in connections.items()}
        rt.unwind_and_redeem(ctx)
        assert counts == {c: w.eth.get_transaction_count(owner.address) for c, w in connections.items()}
