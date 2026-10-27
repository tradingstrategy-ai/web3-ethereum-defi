"""Shared Ethereum Safe activation and durable key backup."""

import json
import stat
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eth_defi.lighter import activation as ac
from eth_defi.lighter.api_key import LighterApiKey


@pytest.fixture
def ceremony(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    """Keep external calls observable while executing the actual ceremony."""
    events = []
    report = tmp_path / "activation.jsonl"
    wallet = SimpleNamespace(address="0xowner", private_key=bytes(32), current_nonce=5)
    safe = Mock()
    safe.safe_address = "0xsafe"
    safe.safe.retrieve_owners.return_value = [wallet.address]
    safe.safe.retrieve_threshold.return_value = 1
    safe.safe.retrieve_nonce.return_value = 2
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    monkeypatch.setattr(ac, "fetch_erc20_details", lambda *_: SimpleNamespace(fetch_balance_of=lambda _: Decimal(1)))
    monkeypatch.setattr(ac, "deposit_usdc_from_lagoon_safe_into_lighter", lambda *_, **__: events.append("deposit") or "0x01")
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(ac, "create_lighter_session", lambda: session)
    monkeypatch.setattr(ac, "wait_for_lighter_account", lambda *_: events.append("account") or 123)
    monkeypatch.setattr(ac, "wait_for_lighter_collateral", lambda *_: events.append("collateral") or Decimal(1))
    monkeypatch.setattr(ac, "fetch_lighter_api_key", lambda *_: None)
    monkeypatch.setattr(ac, "generate_lighter_api_key", lambda _: LighterApiKey(4, "API_PRIVATE_SECRET", "0x" + "22" * 40))
    monkeypatch.setattr(ac, "build_change_pubkey_safe_tx", lambda *_: Mock())

    def register(*_args: object, **_kwargs: object) -> str:
        """Require the on-disk key before a registration can be sent."""
        assert "API_PRIVATE_SECRET" in report.read_text()
        events.append("register")
        return "0x02"

    monkeypatch.setattr(ac, "broadcast_tx", register)
    monkeypatch.setattr(ac, "wait_for_lighter_api_key", lambda *_, **__: events.append("verify"))
    return SimpleNamespace(web3=web3, wallet=wallet, safe=safe, report=report, events=events)


def test_key_backup_precedes_registration(ceremony: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Deposit and credit precede registration; private material never reaches logs."""
    result = ac.activate_lighter_safe(ceremony.web3, ceremony.wallet, ceremony.safe, report_path=ceremony.report)
    assert ceremony.events == ["deposit", "account", "collateral", "register", "verify"]
    assert result.private_key == "API_PRIVATE_SECRET" and "API_PRIVATE_SECRET" not in repr(result)
    assert "API_PRIVATE_SECRET" not in caplog.text
    assert stat.S_IMODE(ceremony.report.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR
    records = [json.loads(line) for line in ceremony.report.read_text().splitlines()]
    assert records[-2]["stage"] == "lighter_key_registration_started"
    assert records[-1]["stage"] == "lighter_key_registration_confirmed"


@pytest.mark.parametrize("boundary", ["deposit", "collateral", "registration", "verify"])
def test_activation_failure_keeps_evidence(monkeypatch: pytest.MonkeyPatch, ceremony: SimpleNamespace, boundary: str) -> None:
    """Failures stop the ceremony; a generated secret survives registration failure."""
    target = {"deposit": "deposit_usdc_from_lagoon_safe_into_lighter", "collateral": "wait_for_lighter_collateral", "registration": "broadcast_tx", "verify": "wait_for_lighter_api_key"}[boundary]
    monkeypatch.setattr(ac, target, Mock(side_effect=TimeoutError("interrupted")))
    with pytest.raises(TimeoutError):
        ac.activate_lighter_safe(ceremony.web3, ceremony.wallet, ceremony.safe, report_path=ceremony.report)
    if boundary in {"registration", "verify"}:
        assert "API_PRIVATE_SECRET" in ceremony.report.read_text()
    else:
        assert "API_PRIVATE_SECRET" not in ceremony.report.read_text()
    with pytest.raises(FileExistsError):
        ac.activate_lighter_safe(ceremony.web3, ceremony.wallet, ceremony.safe, report_path=ceremony.report)


def test_conflicting_slot_is_not_rotated(monkeypatch: pytest.MonkeyPatch, ceremony: SimpleNamespace) -> None:
    """An existing nonzero user key is never silently overwritten."""
    monkeypatch.setattr(ac, "fetch_lighter_api_key", lambda *_: {"public_key": "11" * 40})
    with pytest.raises(AssertionError, match="occupied"):
        ac.activate_lighter_safe(ceremony.web3, ceremony.wallet, ceremony.safe, report_path=ceremony.report)
    assert "register" not in ceremony.events
