"""Exercise the real parallel deployment coordinator with offline provider seams."""

# ruff: noqa: PLR2004

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from eth_account import Account
from hexbytes import HexBytes

from eth_defi.cctp.whitelist import CCTPDeployment
from eth_defi.erc_4626.vault_protocol.lagoon import deployment as dep
from eth_defi.erc_4626.vault_protocol.lagoon import lighter as mc
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LighterAccountSetup
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter import lagoon as custody
from eth_defi.lighter.deployment import LighterDeployment
from eth_defi.token import USDC_NATIVE_TOKEN


@pytest.fixture
def topology(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Two checked networks with realistic config objects and minimal RPCs."""
    owner = Account.create()
    monkeypatch.setattr(mc, "is_anvil", Mock(return_value=False))
    monkeypatch.setattr(mc, "is_account_activated", Mock(return_value=True))
    activation = mc.LagoonLighterActivation(report_path=tmp_path / "activation.jsonl")
    connections, configs = {}, {}
    for name, chain_id in [("hyperliquid", 999), ("ethereum", 1)]:
        connections[name] = SimpleNamespace(eth=SimpleNamespace(chain_id=chain_id, get_balance=lambda _: 1, get_transaction_count=lambda *_: 9, get_code=lambda _: b"code"))
        configs[name] = dep.LagoonConfig(parameters=dep.LagoonDeploymentParameters(underlying=USDC_NATIVE_TOKEN[chain_id], name="Test", symbol="T"), asset_managers=[owner.address], safe_owners=[owner.address], safe_threshold=1, safe_salt_nonce=123, satellite_chain=name == "ethereum", assets=[USDC_NATIVE_TOKEN[chain_id]], cctp_deployment=CCTPDeployment.create_for_chain(chain_id, [1 if chain_id == 999 else 999]), lighter_deployment=LighterDeployment.create_ethereum() if name == "ethereum" else None, max_settlement_amount=Decimal(20) if name == "hyperliquid" else None)
    return SimpleNamespace(owner=owner, activation=activation, connections=connections, configs=configs)


def test_unactivated_hypercore_deployer_stops_all_workers(monkeypatch: pytest.MonkeyPatch, topology: SimpleNamespace) -> None:
    """A missing Core account fails before spending gas on the other chains."""
    monkeypatch.setattr(mc, "is_account_activated", Mock(return_value=False))
    worker = Mock()
    monkeypatch.setattr(dep, "deploy_automated_lagoon_vault", worker)
    with pytest.raises(AssertionError, match="Activate the deployer on Hypercore"):
        dep.deploy_multichain_lagoon_vault(chain_web3=topology.connections, deployer=topology.owner, chain_configs=topology.configs, lighter_activation=topology.activation)
    worker.assert_not_called()
    assert not topology.activation.report_path.exists()


@pytest.fixture
def usdc() -> SimpleNamespace:
    """Ethereum native USDC whose funding can change between checks."""
    return SimpleNamespace(address=USDC_NATIVE_TOKEN[1], fetch_balance_of=Mock(return_value=Decimal(1)), convert_to_raw=lambda v: int(v * 10**6), convert_to_decimals=lambda v: Decimal(v) / 10**6)


def test_preflight_rejects_ethereum_shortfall_before_any_workers(monkeypatch: pytest.MonkeyPatch, topology: SimpleNamespace, usdc: SimpleNamespace) -> None:
    """Missing Ethereum prefunding fails before contract deployment or report creation."""
    usdc.fetch_balance_of.return_value = Decimal("0.99")
    monkeypatch.setattr(mc, "fetch_erc20_details", lambda *_: usdc)
    worker = Mock()
    monkeypatch.setattr(dep, "deploy_automated_lagoon_vault", worker)
    with pytest.raises(AssertionError, match=r"observed 0\.99") as error:
        dep.deploy_multichain_lagoon_vault(chain_web3=topology.connections, deployer=topology.owner, chain_configs=topology.configs, lighter_activation=topology.activation)
    worker.assert_not_called()
    assert not topology.activation.report_path.exists()
    assert "docs/README-lighter-core-execution.md#deployment" in str(error.value)
    assert "ETHEREUM_USDC_AMOUNT=5 DRY_RUN=false poetry run python scripts/lifi/feed-cross-chain.py" in str(error.value)
    assert topology.owner.key.hex() not in str(error.value)


@pytest.mark.parametrize("empty_chain", ["ethereum", "hyperliquid"])
def test_prefunding_gas_asserts_before_workers(monkeypatch: pytest.MonkeyPatch, topology: SimpleNamespace, usdc: SimpleNamespace, empty_chain: str) -> None:
    """Missing gas on any participating chain stops deployment with usable instructions."""
    topology.connections[empty_chain].eth.get_balance = lambda _: 0
    monkeypatch.setattr(mc, "fetch_erc20_details", lambda *_: usdc)
    worker = Mock()
    monkeypatch.setattr(dep, "deploy_automated_lagoon_vault", worker)
    with pytest.raises(AssertionError, match=f"native gas on {empty_chain}") as error:
        dep.deploy_multichain_lagoon_vault(chain_web3=topology.connections, deployer=topology.owner, chain_configs=topology.configs, lighter_activation=topology.activation)
    assert topology.owner.address in str(error.value)
    assert "TARGET_CHAINS=hyperliquid,ethereum" in str(error.value)
    assert "docs/README-lighter-core-execution.md#deployment" in str(error.value)
    assert 'PRIVATE_KEY="${LAGOON_MULTCHAIN_TEST_PRIVATE_KEY:?' in str(error.value)
    worker.assert_not_called()
    assert not topology.activation.report_path.exists()


def test_activation_needs_no_hub_usdc_settlement_or_cctp(monkeypatch: pytest.MonkeyPatch, topology: SimpleNamespace, usdc: SimpleNamespace) -> None:
    """Only Ethereum deployer USDC is read; activation has no settlement or bridge dependency."""
    topology.configs["hyperliquid"].max_settlement_amount = Decimal("0.5")
    for config in topology.configs.values():
        config.cctp_deployment = None

    def fetch_token(web3, address):
        assert web3.eth.chain_id == 1
        assert address == USDC_NATIVE_TOKEN[1]
        return usdc

    monkeypatch.setattr(mc, "fetch_erc20_details", fetch_token)
    assert mc.preflight_lighter_multichain(topology.connections, topology.configs, topology.owner, topology.activation) is usdc


@pytest.mark.parametrize("failure", [None, "worker", "activation", "owner"])
def test_parallel_barrier_then_activation_then_owners(monkeypatch: pytest.MonkeyPatch, topology: SimpleNamespace, failure: str | None) -> None:
    """All chains finish before funding; owner changes follow key verification."""
    events = []
    wallets = {}
    safes = {}
    monkeypatch.setattr(mc, "preflight_lighter_multichain", lambda *_: events.append("preflight"))

    def worker(**kwargs: object) -> SimpleNamespace:
        """Return a prepared Safe while retaining the worker's wallet identity."""
        config = kwargs["config"]
        name = "ethereum" if config.satellite_chain else "hyperliquid"
        assert kwargs["_defer_safe_owners"]
        if failure == "worker" and name == "ethereum":
            message = "contract failed"
            raise RuntimeError(message)
        wallets[name] = kwargs["deployer"]
        safe = Mock()
        safe.retrieve_nonce.return_value = 1
        safe.retrieve_owners.return_value = [topology.owner.address]
        safe.retrieve_threshold.return_value = 1
        safes[name] = safe
        events.append(name + "_prepared")
        return SimpleNamespace(safe=safe, is_satellite=config.satellite_chain, safe_address=topology.owner.address, vault=SimpleNamespace(address=topology.owner.address), as_json_friendly_dict=lambda **_: {"safe_address": topology.owner.address})

    def activate(_web3: object, deployments: dict, received_wallets: dict, _config: object, _progress: object) -> tuple[dict, dict]:
        """Validate join ordering and nonce continuity, then emulate verified activation."""
        assert {"ethereum_prepared", "hyperliquid_prepared"} <= set(events)
        assert all(received_wallets[name] is wallets[name] for name in wallets)
        events.append("activation")
        if failure == "activation":
            message = "key not indexed"
            raise TimeoutError(message)
        return deployments, {"subscription_usdc": "0", "contribution_usdc": "1"}

    def owners(_web3: object, _safe: object, _deployer: object, **_kwargs: object) -> None:
        """Finalise the policy only after the full activation returns."""
        assert events.index("activation") > events.index("ethereum_prepared")
        events.append("owner")
        if failure == "owner":
            message = "owner transaction failed"
            raise RuntimeError(message)

    monkeypatch.setattr(dep, "deploy_automated_lagoon_vault", worker)
    monkeypatch.setattr(mc, "fund_and_activate_lighter", activate)
    monkeypatch.setattr(dep, "add_new_safe_owners", owners)
    kwargs = {"chain_web3": topology.connections, "deployer": topology.owner, "chain_configs": topology.configs, "lighter_activation": topology.activation}
    if failure:
        with pytest.raises((RuntimeError, TimeoutError)):
            dep.deploy_multichain_lagoon_vault(**kwargs)
        if failure in {"worker", "activation"}:
            assert "owner" not in events
        assert '"stage": "complete"' not in topology.activation.report_path.read_text()
    else:
        result = dep.deploy_multichain_lagoon_vault(**kwargs)
        assert result.lighter_funding == {"subscription_usdc": "0", "contribution_usdc": "1"}
        assert events.count("owner") == 2
        rows = [json.loads(line) for line in topology.activation.report_path.read_text().splitlines()]
        assert rows[-1]["stage"] == "complete"
    assert events.count("activation") <= 1


def test_legacy_multichain_does_not_defer_owners(monkeypatch: pytest.MonkeyPatch, topology: SimpleNamespace) -> None:
    """Existing calls retain their old deployment behaviour when opt-in is absent."""

    def worker(**kwargs: object) -> SimpleNamespace:
        assert kwargs["_defer_safe_owners"] is False
        return SimpleNamespace(safe_address=topology.owner.address, is_satellite=True)

    monkeypatch.setattr(dep, "deploy_automated_lagoon_vault", worker)
    result = dep.deploy_multichain_lagoon_vault(chain_web3=topology.connections, deployer=topology.owner, chain_configs=topology.configs)
    assert result.lighter_funding is None and result.activation_report_path is None


@pytest.mark.parametrize("failure", [None, "funding", "receipt", "activation", "reconciliation", "balance_changed", "unexpected_safe_balance", "gas_spent"])
def test_funding_and_activation_boundaries(monkeypatch: pytest.MonkeyPatch, topology: SimpleNamespace, failure: str | None) -> None:
    """Contribute on Ethereum exactly once, then activate without any hub transactions."""
    events = []
    safe_address = "0x" + "22" * 20
    deployments = {}
    wallets = {name: HotWallet(topology.owner) for name in topology.connections}
    for wallet in wallets.values():
        wallet.current_nonce = 9
    for name, config in topology.configs.items():
        vault = Mock()
        vault.address = topology.owner.address
        vault.safe.retrieve_owners.return_value = [topology.owner.address]
        vault.safe.retrieve_threshold.return_value = 1
        deployments[name] = dep.LagoonAutomatedDeployment(chain_id=topology.connections[name].eth.chain_id, vault=vault, trading_strategy_module=SimpleNamespace(address=topology.owner.address), asset_managers=(topology.owner.address,), valuation_manager=topology.owner.address, multisig_owners=[topology.owner.address], deployer=topology.owner.address, block_number=1, parameters=config.parameters, vault_abi="lagoon/v0.5.0/Vault.json", safe_address=safe_address)

    def fetch_balance(address):
        if address == topology.owner.address:
            return Decimal(0) if failure == "balance_changed" else Decimal(1)
        assert address == safe_address
        return Decimal(1) if failure == "unexpected_safe_balance" else Decimal(0)

    eth = SimpleNamespace(fetch_balance_of=fetch_balance, transfer=Mock(return_value="transfer_call"))
    hub = SimpleNamespace(fetch_balance_of=lambda _: Decimal(0))
    monkeypatch.setattr(mc, "fetch_erc20_details", lambda web3, _: eth if web3.eth.chain_id == 1 else hub)

    def step(name: str) -> None:
        if failure == name:
            raise TimeoutError(name)
        events.append(name)

    def send(web3, wallet, call, _progress, label):
        assert web3 is topology.connections["ethereum"] and wallet is wallets["ethereum"]
        assert call == "transfer_call" and label == "ethereum_funding"
        step("funding")
        return "0x1234"

    def receipt(*_args):
        step("receipt")
        return Decimal(1)

    def activate(*_args, **_kwargs):
        step("activation")
        return LighterAccountSetup(123, 4, "SECRET", "pubkey", Decimal(1), "deposit", "key", Decimal(2) if failure == "reconciliation" else Decimal(1))

    monkeypatch.setattr(mc, "send_recorded_transaction", send)
    monkeypatch.setattr(mc, "wait_for_safe_usdc", receipt)
    monkeypatch.setattr(mc, "activate_lighter_safe", activate)
    progress = Mock()
    if failure == "gas_spent":
        topology.connections["ethereum"].eth.get_balance = lambda _: 0
    args = (topology.connections, deployments, wallets, topology.activation, progress)
    if failure:
        with pytest.raises((TimeoutError, AssertionError)):
            mc.fund_and_activate_lighter(*args)
    else:
        updated, funding = mc.fund_and_activate_lighter(*args)
        assert funding["funding_source"] == "ethereum_deployer"
        assert funding["subscription_usdc"] == "0"
        assert funding["contribution_usdc"] == "1"
        assert funding["funding_tx_hash"] == "0x1234"
        assert updated["ethereum"].lighter_account_setup.observed_collateral == Decimal(1)
        assert events == ["funding", "receipt", "activation"]
        eth.transfer.assert_called_once_with(safe_address, Decimal(1))
        assert "SECRET" not in repr(progress.call_args_list)
    assert events.count("funding") <= 1
    if failure in {"balance_changed", "unexpected_safe_balance", "gas_spent"}:
        eth.transfer.assert_not_called()


@pytest.mark.parametrize("uncertain", [False, True])
def test_funding_hash_saved_before_broadcast(monkeypatch: pytest.MonkeyPatch, *, uncertain: bool) -> None:
    """A lost send response leaves the original nonce/hash available for recovery."""

    tx = SimpleNamespace(hash=HexBytes("0xabcd"), nonce=7)
    wallet = Mock()
    wallet.sign_bound_call_with_new_nonce.return_value = tx
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, get_transaction_receipt=lambda _: {"status": 1, "gasUsed": 21_000}))
    records = []

    def broadcast(*_args: object) -> None:
        """Require durable identity before the external broadcast boundary."""
        assert records == [("ethereum_funding_signed", {"chain_id": 1, "tx_hash": "0xabcd", "nonce": 7})]
        if uncertain:
            message = "lost response"
            raise TimeoutError(message)

    monkeypatch.setattr(custody, "estimate_gas_price", lambda _: object())
    monkeypatch.setattr(custody, "apply_gas", lambda params, _: params)
    monkeypatch.setattr(custody, "broadcast_and_wait_transactions_to_complete", broadcast)

    def record(stage: str, **details: object) -> None:
        """Represent a durable progress recorder at the broadcast boundary."""
        records.append((stage, details))

    if uncertain:
        with pytest.raises(TimeoutError):
            mc.send_recorded_transaction(web3, wallet, object(), record, "ethereum_funding")
        assert len(records) == 1
    else:
        assert mc.send_recorded_transaction(web3, wallet, object(), record, "ethereum_funding") == "0xabcd"
        assert records[-1][0] == "ethereum_funding_confirmed"


@pytest.mark.parametrize("mismatch", ["rpc", "factory", "contract"])
def test_preflight_rejects_inconsistent_networks(topology: SimpleNamespace, mismatch: str) -> None:
    """Bad RPC identities or deployment prerequisites fail before broadcasting."""
    if mismatch == "rpc":
        topology.connections["hyperliquid"].eth.chain_id = 8453
    if mismatch == "factory":
        topology.configs["ethereum"].safe_proxy_factory_address = "0x" + "33" * 20
    if mismatch == "contract":
        topology.connections["ethereum"].eth.get_code = lambda _: b""
    with pytest.raises(AssertionError):
        mc.preflight_lighter_multichain(topology.connections, topology.configs, topology.owner, topology.activation)
    assert not topology.activation.report_path.exists()
