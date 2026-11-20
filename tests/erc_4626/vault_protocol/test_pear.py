"""Regression tests for Pear classification and scanner integration."""

import datetime
import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import eth_abi
import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626 import classification, scan
from eth_defi.erc_4626.classification import create_vault_instance, probe_vaults
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.pear.constants import PEAR_CHAIN_ID, PEAR_VAULT_FACTORY
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

AGENT_PEAR = HexAddress("0x65c9f4665c7d9ce96f53aafc67796dcad46f4096")
LONGEVITY = HexAddress("0x69f2ed780a4cec49e552dbcd35a60f41368bc362")
PEAR_FEATURES = {ERC4626Feature.pear_like}

#: Use a post-launch fork because the shared HyperEVM midnight block is too early.
PEAR_FORK_BLOCK = 51_750_000


def test_pear_classifies_standard_leads_without_historical_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    """A matching factory identifies Pear on HyperEVM despite failed conversion."""

    def classify(factory: str, chain_id: int) -> set[ERC4626Feature]:
        """Classify probe results with failed conversions and one factory word."""
        word = eth_abi.encode(["address"], [factory])

        def read_probes(_chain: int, _factory: object, calls: list[EncodedCall], **_kwargs: object) -> Iterator[EncodedCallResult]:
            """Return only a successful factory observation for this candidate."""
            assert any(call.func_name == "pear_factory" for call in calls) == (chain_id == PEAR_CHAIN_ID)
            for call in calls:
                matched = call.func_name == "pear_factory"
                yield EncodedCallResult(call=call, success=matched, result=word if matched else b"", block_identifier=PEAR_FORK_BLOCK)

        monkeypatch.setattr(classification, "read_multicall_chunked", read_probes)
        (result,) = probe_vaults(chain_id, SimpleNamespace(), [AGENT_PEAR], block_identifier=PEAR_FORK_BLOCK)
        return result.features

    assert classify(PEAR_VAULT_FACTORY, PEAR_CHAIN_ID) == PEAR_FEATURES
    assert classify("0x" + "11" * 20, PEAR_CHAIN_ID) == {ERC4626Feature.broken}
    assert classify(PEAR_VAULT_FACTORY, 1) == {ERC4626Feature.broken}


@pytest.mark.parametrize(("current_state", "expected_block"), [(True, "latest"), (False, PEAR_FORK_BLOCK)])
def test_pear_metadata_keeps_historical_block_pinned(monkeypatch: pytest.MonkeyPatch, *, current_state: bool, expected_block: int | str) -> None:
    """Only live metadata uses the latest tag, so historical requests stay pinned."""
    first_seen = datetime.datetime.fromisoformat("2026-09-14")
    detection = ERC4262VaultDetection(PEAR_CHAIN_ID, AGENT_PEAR, 45_896_885, first_seen, PEAR_FEATURES, first_seen, 6, 0)
    web3 = SimpleNamespace()
    scanner = Mock(return_value={})
    monkeypatch.setattr(scan, "create_vault_scan_record", scanner)
    monkeypatch.setattr(scan, "_metadata_worker_cache", SimpleNamespace(token_cache={}))
    assert scan.fetch_vault_scan_record_in_worker(lambda: web3, detection, PEAR_FORK_BLOCK, current_state=current_state) == {}
    scanner.assert_called_once_with(web3, detection, expected_block, token_cache={})


@pytest.mark.skipif(not os.environ.get("JSON_RPC_HYPERLIQUID"), reason="JSON_RPC_HYPERLIQUID is needed for the Pear fee reads")
@pytest.mark.xdist_group("fork:hyperliquid:51750000")
def test_pear_fee_scale_and_admission(anvil_fork_pool: AnvilForkPool) -> None:
    """Read exact deployed fees and distinguish unsupported routes from account policy."""
    web3 = anvil_fork_pool.get_web3(os.environ["JSON_RPC_HYPERLIQUID"], PEAR_FORK_BLOCK)
    agent = create_vault_instance(web3, AGENT_PEAR, features=PEAR_FEATURES, default_block_identifier=PEAR_FORK_BLOCK)
    longevity = create_vault_instance(web3, LONGEVITY, features=PEAR_FEATURES)
    assert agent.get_protocol_name() == "Pear"
    fees = agent.get_fee_data(PEAR_FORK_BLOCK)
    assert (fees.management, fees.performance, fees.deposit, fees.withdraw) == pytest.approx((0, 0.2, 0, 0))
    assert agent.fetch_scan_record_extra_data() == {
        "_pear": {
            "hypercore_address": "0x2e9d9f8bc560c3eafa336d774c14c196a6af6500",
            "minimum_withdrawal_fee": Decimal("0.001"),
        },
    }
    assert longevity.get_performance_fee(PEAR_FORK_BLOCK) == pytest.approx(0.08)
    assert longevity.get_withdraw_fee(PEAR_FORK_BLOCK) == pytest.approx(0.01)
    assert scan.fetch_deposit_permission(agent).value == "unknown"
    assert agent.get_deposit_manager_capability() is None
    assert not agent.supports_generic_deposit_manager()


@pytest.mark.parametrize(
    ("address", "specific_tags"),
    [
        (AGENT_PEAR, {StrategyTag.algorithmic_trading, StrategyTag.arbitrage, StrategyTag.delta_neutral, StrategyTag.mean_reversion, StrategyTag.pair_trading, StrategyTag.statistical_arbitrage}),
        ("0xd38a67a4f54a9b4286d72f08440485a395ddfa70", {StrategyTag.algorithmic_trading, StrategyTag.delta_neutral, StrategyTag.index}),
        ("0xdf6917ee9a2d9b34075bb099a0a1e955bcfcdf84", {StrategyTag.delta_neutral, StrategyTag.directional_trading, StrategyTag.discretionary_trading, StrategyTag.pair_trading}),
        (LONGEVITY, {StrategyTag.discretionary_trading, StrategyTag.pair_trading}),
    ],
)
def test_pear_reviewed_strategy_tags(address: HexAddress, specific_tags: set[StrategyTag]) -> None:
    """Resolve each published Pear mandate through the normal adapter.

    The shared tags capture the portfolio and instrument, while the remaining
    tags describe each deployment's trading decisions.

    :param address: Reviewed HyperEVM vault contract.
    :param specific_tags: Expected distinctions beyond the shared tags.
    """
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=PEAR_CHAIN_ID))
    vault = create_vault_instance(web3, address, features=PEAR_FEATURES)
    assert vault.get_strategy_tags() == specific_tags | {StrategyTag.long_short_portfolio, StrategyTag.perpetual_futures}


@pytest.mark.parametrize(("chain_id", "address"), [(1, AGENT_PEAR), (PEAR_CHAIN_ID, "0xd9c45133b7506735bfc242f7a7940c23a16f7132")])
def test_pear_unreviewed_strategy_tags(chain_id: int, address: HexAddress) -> None:
    """Leave unreviewed chain and address combinations untagged.

    :param chain_id: EVM chain of the unrelated deployment.
    :param address: Address that is unreviewed on this chain.
    """
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=chain_id))
    vault = create_vault_instance(web3, address, features=PEAR_FEATURES)
    assert vault.get_strategy_tags() is None


def test_pear_strategy_tag_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Preview and apply the Pear tag refresh to an isolated catalogue.

    The existing Pear deployment gains its new category. A same-address
    row on another chain retains the full original metadata.

    :param tmp_path: Temporary metadata database, scripts and journal.
    :param monkeypatch: Fixture used to remove metadata path overrides.
    """
    for variable in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(variable, raising=False)
    now = datetime.datetime.fromisoformat("2026-11-20")
    pear = VaultSpec(PEAR_CHAIN_ID, LONGEVITY)
    unrelated = VaultSpec(1, LONGEVITY)
    old_tags = {StrategyTag.discretionary_trading, StrategyTag.long_short_portfolio, StrategyTag.perpetual_futures}
    rows = {
        spec: {
            "Protocol": "Pear",
            "_detection_data": ERC4262VaultDetection(spec.chain_id, spec.vault_address, 50_802_226, now, PEAR_FEATURES, now, 6, 0),
            "_strategy_tags": old_tags.copy(),
            "NAV": 42,
        }
        for spec in (pear, unrelated)
    }
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows).write(path)
    before = path.read_bytes()
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == "100-pear-strategy-tags.py")
    (scripts_dir / script.name).write_bytes(script.source)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}

    runner.run_pending_migrations(dry_run=True, **options)
    assert path.read_bytes() == before
    runner.run_pending_migrations(dry_run=False, **options)
    updated = VaultDatabase.read(path).rows
    assert updated[pear] == rows[pear] | {"_strategy_tags": old_tags | {StrategyTag.pair_trading}}
    assert updated[unrelated] == rows[unrelated]
