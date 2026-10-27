"""Funding and restart boundaries of the manual three-chain Core tutorial."""

# ruff: noqa: PLR2004 -- exact monetary, nonce and permission observations are assertions.

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from web3.exceptions import TransactionNotFound

from eth_defi.cctp.testing import craft_cctp_message
from eth_defi.erc_4626.vault_protocol.lagoon import roundtrip as rt
from eth_defi.lighter.execution import LighterAccountIdentity, LighterPoolExecutor, prepare_pool_deposit, prepare_pool_redemption
from eth_defi.testing.lagoon_roundtrip import RoundtripSimulation, SimulatedLighter


@pytest.mark.parametrize("position,subscription,floor", [("10", "25", "23.02"), ("5", "15", "13.02")])
def test_budget(position: str, subscription: str, floor: str) -> None:
    """Default and small eligible allocations include activation and both fee ceilings."""
    settings = rt.RoundtripSettings.from_env({"POSITION_USDC": position, "USDC_AMOUNT": subscription})
    assert settings.funding_floor == Decimal(floor)
    with pytest.raises(AssertionError, match="Subscription needs"):
        rt.RoundtripSettings.from_env({"POSITION_USDC": position, "USDC_AMOUNT": str(Decimal(floor) - Decimal("0.000001"))})


@pytest.mark.parametrize("value,result", [("false", False), ("0", False), ("true", True), ("yes", True), ("", False)])
def test_boolean(value: str, *, result: bool) -> None:
    """An inherited false string cannot accidentally enable fork mutation helpers."""
    assert rt.parse_bool(value) is result


@pytest.mark.parametrize("env", [{"SIMULATE": "banana"}, {"SIMULATE": "true", "ACTION": "deploy"}, {"ACTION": "all"}, {"NETWORK": "testnet"}])
def test_reject_unsupported_modes(env: dict[str, str]) -> None:
    """Invalid actions fail before connections, keys or filesystem mutations."""
    with pytest.raises(AssertionError):
        rt.RoundtripSettings.from_env(env)


def test_status_does_not_apply_deployment_budget() -> None:
    """Read-only status does not require credentials or a newly funded wallet."""
    settings = rt.RoundtripSettings.from_env({"ACTION": "status", "USDC_AMOUNT": "0"})
    assert settings.action == "status"
    assert rt.RoundtripSettings.from_env({"SIMULATE": "true"}).action == "all"


def test_private_state_roundtrip_and_mode_mismatch(tmp_path: Path) -> None:
    """Protected decimal/integer recovery evidence survives replacement and rejects a live/fork mix."""
    path = tmp_path / "run" / "state.json"
    state = rt.RoundtripState(simulate=True, settings={"position": "10"})
    state.lighter["operations"] = {"mint": {"amount": 7, "nonce": 3}}
    rt.save_state(path, state)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    restored = rt.load_state(path, simulate=True)
    assert restored.lighter == state.lighter
    restored.outcome = "invested"
    rt.save_state(path, restored)
    assert rt.load_state(path, simulate=True).outcome == "invested"
    assert not tuple(path.parent.glob(".state-*"))
    with pytest.raises(AssertionError, match="mode mismatch"):
        rt.load_state(path, simulate=False)
    with rt.lock_run(path), pytest.raises(BlockingIOError), rt.lock_run(path):
        pass


def test_preserves_state_on_atomic_replace_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An interrupted file replacement retains the previous complete financial record."""
    path = tmp_path / "state.json"
    rt.save_state(path, rt.RoundtripState(outcome="invested"))
    monkeypatch.setattr(rt.os, "replace", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError):
        rt.save_state(path, rt.RoundtripState(outcome="completed"))
    assert rt.load_state(path, simulate=False).outcome == "invested"
    assert not tuple(tmp_path.glob(".state-*"))


def test_evm_hash_persisted_before_send_and_never_rebroadcast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A restart uses the original receipt even if the broadcaster died before returning."""
    state = rt.RoundtripState()
    web3 = Mock()
    web3.eth.get_transaction_receipt.return_value = {"status": 1, "gasUsed": 123, "effectiveGasPrice": 5}
    wallet = Mock()
    ctx = rt.RoundtripContext(rt.RoundtripSettings(state_path=tmp_path / "state.json"), state, {"arbitrum": web3}, {"arbitrum": wallet}, {}, lighter_session=Mock(), hyper_session=Mock())

    def broadcast(_w3, _signer, _call, description, **kwargs):
        kwargs["tx_observer"]("0xabc", 7, description)
        saved = rt.load_state(ctx.settings.state_path, simulate=False)
        assert saved.transactions["subscription_request"]["hash"] == "0xabc"
        message = "lost acknowledgement"
        raise ConnectionError(message)

    sender = Mock(side_effect=broadcast)
    monkeypatch.setattr(rt, "broadcast_tx", sender)
    with pytest.raises(ConnectionError):
        rt.send_transaction(ctx, "subscription_request", "arbitrum", Mock())
    ctx.state = rt.load_state(ctx.settings.state_path, simulate=False)
    assert rt.send_transaction(ctx, "subscription_request", "arbitrum", Mock()) == "0xabc"
    sender.assert_called_once()
    assert ctx.state.transactions["subscription_request"]["gas_cost_raw"] == "615"
    web3.eth.get_transaction_receipt.side_effect = TransactionNotFound("not indexed")
    with pytest.raises(rt.UncertainExecutionError):
        rt.send_transaction(ctx, "subscription_request", "arbitrum", Mock())
    sender.assert_called_once()


def test_terminal_unwind_submits_nothing() -> None:
    """Both complete and disclosed-dust terminal outcomes are safe to repeat."""
    ctx = Mock()
    for outcome in rt.TERMINAL:
        ctx.state.outcome = outcome
        rt.unwind_and_redeem(ctx)
    ctx.save.assert_not_called()


def test_unactivated_deployer_fails_before_any_chain_deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Funding HyperEVM gas does not activate the Core account needed by big blocks."""
    ctx = Mock()
    ctx.settings = rt.RoundtripSettings(state_path=tmp_path / "state.json")
    ctx.simulation = None
    token = Mock()
    token.convert_to_raw.return_value = 25_000_000
    token.convert_to_decimals.return_value = Decimal(25)
    ctx.usdc = dict.fromkeys(rt.CHAINS, token)
    ctx.web3 = dict.fromkeys(rt.CHAINS, Mock())
    monkeypatch.setattr(rt, "check_multichain_deployer_funding", Mock())
    monkeypatch.setattr(rt, "is_account_activated", Mock(return_value=False))
    deploy = Mock()
    monkeypatch.setattr(rt, "deploy_automated_lagoon_vault", deploy)
    with pytest.raises(AssertionError, match="Activate the deployer"):
        rt.deploy_and_invest(ctx)
    deploy.assert_not_called()
    ctx.save.assert_not_called()


def test_existing_key_backup_stops_before_funding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A new state filename cannot reuse another run's API-key directory."""
    (tmp_path / "lighter-key.jsonl").write_text("existing recovery evidence")
    ctx = Mock()
    ctx.settings = rt.RoundtripSettings(state_path=tmp_path / "another-state.json")
    funding = Mock()
    monkeypatch.setattr(rt, "check_multichain_deployer_funding", funding)
    with pytest.raises(AssertionError, match="Lighter key backup already exists"):
        rt.deploy_and_invest(ctx)
    funding.assert_not_called()
    ctx.save.assert_not_called()


def test_unchanged_big_blocks_does_not_call_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected activation must not be masked by an unnecessary restore API call."""
    ctx = Mock()
    ctx.simulation = None
    ctx.state = rt.RoundtripState(steps={"big_blocks_before": False})
    ctx.web3 = {"hyperliquid": Mock()}
    monkeypatch.setattr(rt, "fetch_using_big_blocks", Mock(return_value=False))
    modify = Mock()
    monkeypatch.setattr(rt, "set_big_blocks", modify)
    rt.restore_big_blocks(ctx)
    modify.assert_not_called()
    assert ctx.state.steps["big_blocks_restored"] is True


@pytest.mark.parametrize("mode", ["default", "disabled", "unifiedAccount", "portfolioMargin", "dexAbstraction", "unknown"])
def test_core_entry_checks_actual_api_modes(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """Separate-balance accounts proceed; unified and unknown modes stop before investing."""
    ctx = Mock()
    ctx.simulation = None
    ctx.settings.position = Decimal(10)
    ctx.state = rt.RoundtripState(targets={"hypercore": "0x1111111111111111111111111111111111111111"})
    ctx.usdc = {"hyperliquid": Mock()}
    for name in ("send_transaction", "build_hypercore_approve_deposit_wallet_call", "build_hypercore_deposit_for_spot_call", "build_hypercore_deposit_to_spot_call", "build_hypercore_transfer_usd_class_call", "build_hypercore_deposit_to_vault_call"):
        monkeypatch.setattr(rt, name, Mock())
    action = Mock()
    monkeypatch.setattr(rt, "core_action", action)
    monkeypatch.setattr(rt, "fetch_user_abstraction_mode", Mock(return_value=mode))
    monkeypatch.setattr(rt, "HyperliquidVault", Mock())
    monkeypatch.setattr(rt, "classify_hyperliquid_vault_deposit", Mock(return_value=SimpleNamespace(deposits_open=True, max_deposit=None)))
    monkeypatch.setattr(rt, "fetch_core_snapshot", Mock(return_value={"spot": "1"}))
    if mode in {"default", "disabled"}:
        rt.invest_hypercore(ctx)
        assert [call.args[1] for call in action.call_args_list] == ["core_activation", "core_deposit", "core_to_perp", "core_invest"]
    else:
        with pytest.raises(AssertionError, match="default/disabled"):
            rt.invest_hypercore(ctx)
        assert [call.args[1] for call in action.call_args_list] == ["core_activation"]


def test_real_executor_with_local_lighter_transport() -> None:
    """The simulation exercises actual sizing, payload checks, submission and receipt decoding."""
    state = rt.RoundtripState(simulate=True)
    ctx = SimpleNamespace(state=state, save=Mock())
    simulation = RoundtripSimulation(ctx=ctx)

    session = SimulatedLighter(simulation)
    simulation.lighter = session
    session.credit_deposit(Decimal(11))
    persisted = []
    executor = LighterPoolExecutor(session, LighterAccountIdentity(simulation.account_index), session, lambda op: persisted.append(op.as_dict()), submission_session_factory=lambda: session)
    mint = prepare_pool_deposit(executor, simulation.pool_index, Decimal(10))
    assert mint.amount == 4
    executor.submit(mint)
    executor.reconcile(mint)
    assert session.ledger["collateral"] == "1.0"
    session.ledger["price"] = "2.525"
    burn = prepare_pool_redemption(executor, simulation.pool_index)
    executor.submit(burn)
    executor.reconcile(burn)
    assert Decimal(session.ledger["collateral"]) == Decimal("11.1")
    assert session.ledger["shares"] == 0
    assert [row["stage"] for row in persisted] == ["prepared", "unknown", "submitted", "confirmed"] * 2
    assert session.ledger["nonce"] == 2


def test_unobserved_core_entry_cannot_be_retried_or_swept(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful EVM receipt with no Core effect cannot authorise source-fund withdrawal."""
    ctx = Mock()
    ctx.state = rt.RoundtripState()
    ctx.state.transactions["core_deposit"] = {"hash": "0xabc"}
    ctx.state.steps["core_deposit"] = {"amount": "10", "before": {"spot": "1"}}
    monkeypatch.setattr(rt, "fetch_core_snapshot", lambda _: {"spot": "1", "perp": "0", "equity": "0", "escrow": "0"})
    with pytest.raises(rt.UncertainExecutionError, match="Core effect remains uncertain"):
        rt.reconcile_core_entries(ctx)
    assert not ctx.state.steps["core_deposit"].get("complete")
    ctx.save.assert_not_called()
    monkeypatch.setattr(rt, "fetch_core_snapshot", lambda _: {"spot": "11", "perp": "0", "equity": "0", "escrow": "0"})
    rt.reconcile_core_entries(ctx)
    assert ctx.state.steps["core_deposit"]["complete"] is True


@pytest.mark.parametrize("error_type", [rt.RoundtripPendingError, rt.UncertainExecutionError])
def test_unwind_processes_the_other_independent_leg(monkeypatch: pytest.MonkeyPatch, error_type: type[RuntimeError]) -> None:
    """An unavailable Core exit does not prevent Lighter recovery or permit final NAV settlement."""
    ctx = Mock()
    ctx.state = rt.RoundtripState(owner="0x1111111111111111111111111111111111111111")
    ctx.state.transactions["subscription_request"] = {}
    ctx.state.steps["subscribed"] = True
    ctx.wallets = {c: SimpleNamespace(address=ctx.state.owner) for c in rt.CHAINS}
    ctx.web3 = {c: Mock() for c in rt.CHAINS}
    for w in ctx.web3.values():
        w.eth.get_balance.return_value = 1
    ctx.deployments = {c: Mock() for c in rt.CHAINS}
    monkeypatch.setattr(rt, "verify_bindings", lambda _: None)
    monkeypatch.setattr(rt, "restore_big_blocks", lambda _: None)
    monkeypatch.setattr(rt, "unwind_hypercore", Mock(side_effect=error_type("original action pending")))
    lighter = Mock()
    monkeypatch.setattr(rt, "unwind_lighter", lighter)
    redeem = Mock()
    monkeypatch.setattr(rt, "redeem", redeem)
    with pytest.raises(error_type):
        rt.unwind_and_redeem(ctx)
    lighter.assert_called_once_with(ctx)
    redeem.assert_not_called()


@pytest.fixture
def lighter_unwind_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> rt.RoundtripContext:
    """Use the real exit orchestration with a local API and mocked Ethereum custody boundary."""
    settings = rt.RoundtripSettings(simulate=True, state_path=tmp_path / "state.json")
    state = rt.RoundtripState(simulate=True, owner="0x1111111111111111111111111111111111111111", targets={"lighter": 42})
    token = Mock()
    token.convert_to_raw.side_effect = lambda amount: int(amount * Decimal(1_000_000))
    token.convert_to_decimals.side_effect = lambda amount: Decimal(amount) / Decimal(1_000_000)
    token.address = rt.USDC_NATIVE_TOKEN[1]
    token.fetch_raw_balance_of.return_value = 0
    token.fetch_balance_of.return_value = Decimal(0)
    ctx = rt.RoundtripContext(settings, state, {"ethereum": Mock()}, {}, {"ethereum": token}, lighter_session=Mock(), hyper_session=Mock())
    safe = Mock(safe_address=state.owner)
    ctx.deployments["ethereum"] = SimpleNamespace(vault=safe)
    simulation = RoundtripSimulation(ctx=ctx)
    simulation.lighter = SimulatedLighter(simulation)
    ctx.simulation = simulation
    ctx.lighter_session = simulation.lighter
    report = tmp_path / "key.jsonl"
    report.write_text(json.dumps({"stage": "lighter_key_saved", "account_index": simulation.account_index, "api_key_index": 4, "private_key": "local-simulation-only"}) + "\n")
    report.chmod(0o600)
    ctx.state.lighter = {"account_index": simulation.account_index, "api_key_index": 4, "activated": True, "key_report": str(report)}
    monkeypatch.setattr("eth_defi.testing.lagoon_roundtrip.credit_lighter_pending_balance_on_anvil", Mock())
    return ctx


def test_pending_secure_withdrawal_reuses_nonce_and_history_id(lighter_unwind_context: rt.RoundtripContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restarting a pending withdrawal neither resets its baseline nor requests funds twice."""
    ctx = lighter_unwind_context
    session = ctx.simulation.lighter
    session.credit_deposit(Decimal(11))
    submit = session.post

    def pending_post(*args, **kwargs):
        response = submit(*args, **kwargs)
        session.ledger["withdraws"][0]["status"] = "pending"
        return response

    monkeypatch.setattr(session, "post", pending_post)
    bridge = Mock()
    claim = Mock()
    monkeypatch.setattr(rt, "bridge", bridge)
    monkeypatch.setattr(rt, "claim_lighter", claim)
    with pytest.raises(rt.RoundtripPendingError, match="pending"):
        rt.unwind_lighter(ctx)
    saved = rt.load_state(ctx.settings.state_path, simulate=True)
    assert saved.lighter["withdrawal_baseline"] == []
    assert saved.lighter["withdrawal_id"] == "simulation-0"
    assert session.ledger["nonce"] == 1
    ctx.state = saved
    session.ledger["withdraws"][0]["status"] = "claimable"
    rt.unwind_lighter(ctx)
    assert session.ledger["nonce"] == 1
    claim.assert_called_once_with(ctx, Decimal(11))
    bridge.assert_called_once()


def test_external_claim_requires_original_safe_credit(lighter_unwind_context: rt.RoundtripContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """An auto-claimed withdrawal must match its exact saved ID and Safe balance delta."""
    ctx = lighter_unwind_context
    ctx.state.lighter.update(withdrawal_baseline=[], withdrawal_id="simulation-0", withdrawal_amount="11", withdrawal_safe_before=0, operations={"withdraw": {"stage": "confirmed"}})
    ctx.simulation.lighter.ledger["withdraws"] = [{"id": "simulation-0", "status": "completed", "type": "secure", "asset_id": 3, "amount": "11"}]
    ctx.usdc["ethereum"].fetch_raw_balance_of.return_value = 11_000_000
    monkeypatch.setattr(rt, "fetch_lighter_pending_balance", lambda *_args, **_kwargs: Decimal(0))
    claim = Mock()
    bridge = Mock()
    monkeypatch.setattr(rt, "claim_lighter", claim)
    monkeypatch.setattr(rt, "bridge", bridge)
    rt.unwind_lighter(ctx)
    claim.assert_not_called()
    bridge.assert_called_once()
    assert ctx.state.steps["lighter_external_claim"]["withdrawal_id"] == "simulation-0"
    ctx.usdc["ethereum"].fetch_raw_balance_of.return_value = 12_000_000
    with pytest.raises(AssertionError, match="balance evidence"):
        del ctx.state.steps["lighter_external_claim"]
        rt.unwind_lighter(ctx)
    ctx.usdc["ethereum"].fetch_raw_balance_of.return_value = 11_000_000
    rt.unwind_lighter(ctx)
    ctx.state = rt.load_state(ctx.settings.state_path, simulate=True)
    # The original return burn drained the Safe before its relay timed out.
    ctx.usdc["ethereum"].fetch_raw_balance_of.return_value = 0
    bridge.reset_mock()
    rt.unwind_lighter(ctx)
    bridge.assert_called_once_with(ctx, "return_ethereum", "ethereum", "arbitrum")


def test_bridge_reconciliation_ignores_other_hub_credit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A delayed return uses its own consumed message, not both returns' wallet delta."""
    address = "0x1111111111111111111111111111111111111111"
    message = bytearray(craft_cctp_message(source_domain=0, destination_domain=3, nonce=1, mint_recipient=address, amount=11_000_000, burn_token=address))
    message[312:344] = (1_100).to_bytes(32, "big")
    row = {"source": "ethereum", "destination": "arbitrum", "amount": 11_000_000, "max_fee": 10_000, "finality": 1000, "destination_before": 1_000_000, "message": message.hex(), "attestation": "00"}
    ctx = Mock()
    ctx.state = rt.RoundtripState(bridges={"return_ethereum": row}, transactions={"return_ethereum_burn": {"hash": "0xabc"}})
    ctx.safe.return_value.safe_address = address
    ctx.usdc = {name: Mock() for name in ("arbitrum", "ethereum")}
    ctx.usdc["arbitrum"].fetch_raw_balance_of.return_value = 23_997_800
    ctx.web3 = {name: Mock() for name in ctx.usdc}
    for name in ("prepare_approve_for_burn", "prepare_deposit_for_burn"):
        monkeypatch.setattr(rt, name, Mock())
    monkeypatch.setattr(rt, "send_transaction", Mock(return_value="0xabc"))
    monkeypatch.setattr(rt, "fetch_message_received", Mock(return_value=True))
    rt.bridge(ctx, "return_ethereum", "ethereum", "arbitrum")
    assert row["complete"]
    assert row["received_raw"] == 10_998_900
    assert row["actual_fee_raw"] == 1_100


def test_unwind_cancels_prepared_but_unsigned_outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    """An approval without a signed burn must not start another transfer during unwind."""
    ctx = Mock()
    ctx.settings = rt.RoundtripSettings()
    ctx.state = rt.RoundtripState(owner="owner", steps={"subscribed": True}, transactions={"subscription_request": {}, "out_ethereum_approve": {}}, bridges={"out_ethereum": {"source": "arbitrum", "destination": "ethereum", "amount": 11_010_000}})
    ctx.wallets = {c: SimpleNamespace(address="owner") for c in rt.CHAINS}
    ctx.web3 = {c: Mock() for c in rt.CHAINS}
    ctx.deployments = {c: Mock() for c in rt.CHAINS}
    for web3 in ctx.web3.values():
        web3.eth.get_balance.return_value = 1
    for name in ("verify_bindings", "restore_big_blocks"):
        monkeypatch.setattr(rt, name, Mock())
    for name in ("unwind_hypercore", "unwind_lighter"):
        monkeypatch.setattr(rt, name, Mock(side_effect=rt.RoundtripPendingError("exit boundary")))
    bridge = Mock()
    monkeypatch.setattr(rt, "bridge", bridge)
    with pytest.raises(rt.RoundtripPendingError, match="exit boundary"):
        rt.unwind_and_redeem(ctx)
    assert ctx.state.bridges["out_ethereum"]["cancelled"]
    bridge.assert_not_called()


def test_confirmed_partial_core_exit_allows_distinct_followup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Moving equity leaves a remainder without replaying a confirmed withdrawal."""
    ctx = Mock()
    ctx.settings = rt.RoundtripSettings()
    ctx.simulation = None
    ctx.state = rt.RoundtripState(targets={"hypercore": "vault"})
    token = SimpleNamespace(convert_to_raw=lambda amount: int(amount * 10**6), convert_to_decimals=lambda amount: Decimal(amount) / 10**6)
    ctx.usdc = {"hyperliquid": token}
    ledger = {"spot": "1", "perp": "0", "equity": "10", "escrow": "0", "locked_until": None}
    monkeypatch.setattr(rt, "fetch_core_snapshot", lambda _: dict(ledger))
    monkeypatch.setattr(rt, "reconcile_core_entries", Mock())
    metadata = Mock()
    metadata.fetch_info.side_effect = [SimpleNamespace(max_withdrawable=Decimal(9)), SimpleNamespace(max_withdrawable=Decimal("1.1"))]
    monkeypatch.setattr(rt, "HyperliquidVault", Mock(return_value=metadata))
    monkeypatch.setattr(rt, "build_hypercore_withdraw_from_vault_call", Mock())
    monkeypatch.setattr(rt, "build_hypercore_transfer_usd_class_call", Mock())
    sent = []

    def send(_ctx: object, name: str, _chain: str, _call: object) -> str:
        """Credit one completed withdrawal while allowing its equity to move."""
        sent.append(name)
        if name == "core_exit":
            ledger.update(equity="1.1", perp="9")
        else:
            assert name == "core_exit_1"
            ledger.update(equity="0", perp="10.1")
        return "0xabc"

    monkeypatch.setattr(rt, "send_transaction", send)
    action = rt.core_action

    def exit_only(ctx: rt.RoundtripContext, name: str, call: object, ready: object, amount: Decimal) -> None:
        """Exercise real confirmation and stop before the unrelated return steps."""
        if not name.startswith("core_exit"):
            message = "perp step reached"
            raise rt.RoundtripPendingError(message)
        action(ctx, name, call, ready, amount)

    monkeypatch.setattr(rt, "core_action", exit_only)
    with pytest.raises(rt.RoundtripPendingError, match="confirmed remainder"):
        rt.unwind_hypercore(ctx)
    assert ctx.state.steps["core_exit"]["complete"]
    with pytest.raises(rt.RoundtripPendingError, match="perp step reached"):
        rt.unwind_hypercore(ctx)
    assert sent == ["core_exit", "core_exit_1"]
    assert ctx.state.steps["core_exit_1"]["complete"]
    assert ctx.state.steps["core_exit_1"]["amount"] == "1.1"


@pytest.mark.parametrize("spot,returned", [("0.5", "0.49"), ("0.009", None), ("0.0100005", None)])
def test_dust_override_does_not_skip_recoverable_core_cash(monkeypatch: pytest.MonkeyPatch, spot: str, returned: str | None) -> None:
    """A larger reporting budget cannot turn recoverable activation cash into dust."""
    ctx = Mock()
    ctx.settings = rt.RoundtripSettings(action="unwind", max_residual=Decimal(1))
    ctx.state = rt.RoundtripState()
    ctx.usdc = {"hyperliquid": SimpleNamespace(convert_to_raw=lambda amount: int(amount * 10**6), convert_to_decimals=lambda amount: Decimal(amount) / 10**6, fetch_balance_of=lambda _: Decimal(0))}
    monkeypatch.setattr(rt, "reconcile_core_entries", Mock())
    monkeypatch.setattr(rt, "fetch_core_snapshot", lambda _: {"spot": spot, "perp": "0", "equity": "0", "escrow": "0"})
    monkeypatch.setattr(rt, "build_hypercore_send_asset_to_evm_call", Mock())
    monkeypatch.setattr(rt, "bridge", Mock())
    action = Mock()
    monkeypatch.setattr(rt, "core_action", action)
    rt.unwind_hypercore(ctx)
    if returned:
        assert action.call_args.args[1] == "core_return"
        assert action.call_args.args[-1] == Decimal(returned)
    else:
        action.assert_not_called()


def test_simulated_gain_follows_selected_share_quantity() -> None:
    """Small position overrides conserve cash using their actual raw-share gain."""
    state = rt.RoundtripState(simulate=True)
    state.steps["simulation_core"] = {"equity": "5"}
    ctx = SimpleNamespace(state=state, save=Mock())
    simulation = RoundtripSimulation(ctx=ctx)
    simulation.lighter = SimulatedLighter(simulation)
    simulation.lighter.ledger["shares"] = 2
    simulation.advance_investments()
    assert Decimal(state.results["simulated_profit_usdc"]) == Decimal("0.15")
    assert Decimal(state.steps["simulation_core"]["equity"]) == Decimal("5.1")
