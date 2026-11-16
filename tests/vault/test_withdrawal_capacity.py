"""Warning decisions exercise units, unknowns and independent pause directions."""

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from types import SimpleNamespace

import pytest

from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault.deposit_redeem import VaultDepositManager
from eth_defi.vault.withdrawal_capacity import assess_withdrawal_capacity, configured_capacity_threshold, enrich_withdrawal_capacity, fetch_withdrawal_capacity_limit

NOW = datetime(2026, 11, 16, tzinfo=UTC)


def observation(capacity="150", **kwargs):
    return dict(status="measured", current_capacity=capacity, current_tvl="1000", observed_at=NOW.isoformat(), denomination_symbol="USDC", **kwargs)


@pytest.mark.parametrize("capacity,expected", [("149.999999", True), ("150", False), ("150.000001", False), ("0", True)])
def test_boundary(capacity, expected):
    result = assess_withdrawal_capacity(observation(capacity), now=NOW)
    assert result.capacity_warning is expected
    assert result.current_capacity == Decimal(capacity)
    assert "150.00 USDC" in result.capacity_reason


def test_threshold_and_precision():
    data = observation("100.000000000000000001")
    assert assess_withdrawal_capacity(data, Decimal("0.1"), now=NOW).capacity_warning is False
    assert assess_withdrawal_capacity(data, Decimal("0.15"), now=NOW).capacity_warning is True
    assert assess_withdrawal_capacity(data, Decimal("0.1"), now=NOW).to_dict()["current_capacity"] == "100.000000000000000001"


@pytest.mark.parametrize("bad", [Decimal("-0.1"), Decimal("1.1"), Decimal("NaN"), Decimal("Infinity")])
def test_invalid_threshold(bad):
    with pytest.raises(ValueError):
        assess_withdrawal_capacity(None, bad)


@pytest.mark.parametrize("bad", [None, True, 1, 0.15, "0.15"])
def test_threshold_requires_decimal(bad):
    """Reject implicit conversion at API and direct-assessment boundaries."""
    with pytest.raises(AssertionError, match="must be Decimal"):
        assess_withdrawal_capacity(None, bad)
    with pytest.raises(AssertionError, match="must be Decimal"):
        enrich_withdrawal_capacity([], SimpleNamespace(rows={}), threshold=bad)
    with pytest.raises(AssertionError, match="must be Decimal"):
        fetch_withdrawal_capacity_limit(SimpleNamespace(web3=None), capacity_threshold=bad)
    with pytest.raises(AssertionError, match="must be Decimal"):
        VaultBase.fetch_withdrawal_capacity_limit(SimpleNamespace(web3=None), capacity_threshold=bad)
    query = partial(VaultBase.fetch_withdrawal_capacity_limit, SimpleNamespace(web3=None))
    manager = SimpleNamespace(vault=SimpleNamespace(fetch_withdrawal_capacity_limit=query))
    with pytest.raises(AssertionError, match="must be Decimal"):
        VaultDepositManager.fetch_withdrawal_capacity_limit(manager, capacity_threshold=bad)
    with pytest.raises(AssertionError, match="must be Decimal"):
        replace(assess_withdrawal_capacity(None), capacity_threshold=bad)


def test_threshold_environment_converts_to_decimal(monkeypatch):
    monkeypatch.setenv("WITHDRAWAL_CAPACITY_THRESHOLD", "0.20")
    assert configured_capacity_threshold() == Decimal("0.20")


def test_unknown_and_stale_do_not_mean_safe():
    assert assess_withdrawal_capacity(None, now=NOW).capacity_warning is None
    result = assess_withdrawal_capacity(observation(), now=NOW + timedelta(hours=27))
    assert result.capacity_warning is None and result.current_capacity is None
    assert result.observation["status"] == "stale"


def test_structural_warning_and_pause_direction():
    data = observation("900", structural_warning=True, deposits_paused=True, withdrawals_paused=False)
    result = assess_withdrawal_capacity(data, now=NOW)
    assert result.capacity_warning and result.pause_warning
    assert result.observation["capacity_warning_source"] == "structural"
    assert result.current_capacity == Decimal(900)
    assert "Deposits are administratively paused" in result.capacity_reason
    assert not result.observation["withdrawals_paused"]
    assert data == observation("900", structural_warning=True, deposits_paused=True, withdrawals_paused=False)


def test_export_keeps_existing_fields_and_old_rows():
    address = "0x" + "a" * 40
    row = {"chain_id": 1, "address": address, "available_liquidity": 123, "deposit_closed_reason": "cap reached"}
    original = copy.deepcopy(row)
    database = SimpleNamespace(rows={VaultSpec(1, address): {"_withdrawal_capacity_observation": observation()}})
    enrich_withdrawal_capacity([row], database, now=NOW)
    assert {k: row[k] for k in original} == original
    assert row["withdrawal_capacity_limit"]["capacity_warning"] is False
    database.rows.clear()
    enrich_withdrawal_capacity([row], database, now=NOW)
    assert row["withdrawal_capacity_limit"]["capacity_warning"] is None


def test_corrupt_numeric_observation_stays_unknown():
    result = assess_withdrawal_capacity(observation("not-an-amount"), now=NOW)
    assert result.capacity_warning is None and result.current_capacity is None


@pytest.mark.parametrize("field", ["features", "_detection_data"])
@pytest.mark.parametrize("old", [None, {"status": "failed"}, {"status": "measured", "observed_at": "2020-01-01T00:00:00+00:00", "current_capacity": "1000", "current_tvl": "1000"}])
def test_legacy_40acres_structural_warning_survives_metadata_failure(field, old):
    """A retained legacy row needs no successful RPC observation to warn."""
    features = {ERC4626Feature.forty_acres_like}
    source = {field: features if field == "features" else SimpleNamespace(features=features)}
    if old is not None:
        source["_withdrawal_capacity_observation"] = old
    before = copy.deepcopy(old)
    spec = VaultSpec(1, "0x" + "a" * 40)
    rows = [{"chain_id": 1, "address": spec.vault_address}]
    enrich_withdrawal_capacity(rows, SimpleNamespace(rows={spec: source}), now=NOW)
    assert rows[0]["withdrawal_capacity_limit"]["capacity_warning"] is True
    assert rows[0]["withdrawal_capacity_limit"]["current_capacity"] is None
    assert "40acres" in rows[0]["withdrawal_capacity_limit"]["capacity_reason"]
    assert old == before


def test_zero_tvl_and_partial_cannot_be_safe():
    data = observation("0")
    data["current_tvl"] = "0"
    assert assess_withdrawal_capacity(data, now=NOW).capacity_warning is None
    data = observation("500")
    data["status"] = "partial"
    assert assess_withdrawal_capacity(data, now=NOW).capacity_warning is None


@pytest.mark.parametrize(
    "native,wrapped,token_cash,native_cash,status",
    [(True, True, 0, 10**18, "partial"), (True, True, 10**18, 0, "measured"), (False, False, 0, 10**18, "measured"), (False, False, 10**18, 0, "partial"), (False, True, 0, 10**18, "partial")],
)
def test_fluid_selects_actual_liquidity_asset(monkeypatch, native, wrapped, token_cash, native_cash, status):
    """Native reserves and wrapped-token donations must not be confused."""
    from web3.exceptions import ContractLogicError

    from eth_defi.token import USDC_NATIVE_TOKEN, WRAPPED_NATIVE_TOKEN
    from eth_defi.vault import withdrawal_capacity_readers as readers

    address, liquidity = "0x" + "a" * 40, "0x" + "b" * 40
    sentinel = "0x" + "e" * 40
    calls = []

    class Call:
        def __init__(self, name, target=None):
            self.name, self.target = name, target

        def call(self, *, block_identifier):
            assert block_identifier == 123
            calls.append((self.name, self.target))
            if self.name == "getData":
                return [liquidity, address, address, address, address, False, 0, 0, 0]
            if self.name == "NATIVE_TOKEN_ADDRESS":
                if not native:
                    raise ContractLogicError("Selector unavailable")
                return sentinel
            if self.name == "totalAssets":
                return 100 * 10**18
            return token_cash if self.target.lower() == liquidity else 0

    functions = SimpleNamespace(totalAssets=lambda: Call("totalAssets"), balanceOf=lambda target: Call("balanceOf", target), getData=lambda: Call("getData"), NATIVE_TOKEN_ADDRESS=lambda: Call("NATIVE_TOKEN_ADDRESS"))
    contract = SimpleNamespace(functions=functions)
    token = SimpleNamespace(address=WRAPPED_NATIVE_TOKEN[1] if wrapped else USDC_NATIVE_TOKEN[1], chain_id=1, symbol="WETH" if wrapped else "USDC", contract=contract, convert_to_decimals=lambda raw: Decimal(raw) / 10**18)

    def balance(target, *, block_identifier):
        assert target.lower() == liquidity and block_identifier == 123
        calls.append(("native_balance", target))
        return native_cash

    vault = SimpleNamespace(address=address, chain_id=1, vault_contract=contract, denomination_token=token, web3=SimpleNamespace(eth=SimpleNamespace(get_balance=balance)))
    monkeypatch.setattr(readers, "get_deployed_contract", lambda *args: contract)
    data = readers.fluid_observation(vault, 123)
    assert data["status"] == status
    assert data["current_capacity"] == ("0" if status == "measured" else None)
    assert any(name == "native_balance" for name, _ in calls) is (native or wrapped)
    if native:
        assert not any(name == "balanceOf" and target.lower() == liquidity for name, target in calls)
    data["observed_at"] = NOW.isoformat()
    assert assess_withdrawal_capacity(data, now=NOW).capacity_warning is (True if status == "measured" else None)


def test_morpho_v1_ignores_donations_and_deduplicates_queue(monkeypatch):
    """The vault cannot withdraw donated tokens outside Morpho's queue."""
    from types import SimpleNamespace

    import eth_defi.vault.withdrawal_capacity_readers as readers

    def contract(address, values):
        methods = {name: (lambda *args, value=value: SimpleNamespace(call=lambda **kwargs: value)) for name, value in values.items()}
        return SimpleNamespace(address=address, functions=SimpleNamespace(**methods))

    outer = contract("outer", {"MORPHO": "blue", "withdrawQueueLength": 2, "withdrawQueue": b"same-market"})
    blue = contract("blue", {"market": (100, 100000000, 20, 0, 0, 0), "position": (101000000, 0, 0)})
    monkeypatch.setattr(readers, "get_deployed_contract", lambda web3, abi, address: blue if address == "blue" else outer)
    token = SimpleNamespace(contract=SimpleNamespace(functions=SimpleNamespace(balanceOf=lambda address: SimpleNamespace(call=lambda **kwargs: 1000 if address == "blue" else 99999999))))
    assert readers.morpho_v1_capacity(SimpleNamespace(), "outer", 123, token) == 80


def test_stale_observation_removes_current_claims():
    data = observation("0", deposits_paused=True, withdrawals_paused=True)
    data["reason"] = "Withdrawals are administratively paused"
    result = assess_withdrawal_capacity(data, now=NOW + timedelta(hours=27))
    assert result.pause_warning is None
    assert "administratively paused" not in result.capacity_reason
    assert "current_capacity" not in result.observation
    assert result.observation["last_observation"]["current_capacity"] == "0"


@pytest.mark.parametrize("usd_tvl, expected", [("0", True), ("9999.999999", True), ("10000", False), ("10000.000001", False)])
def test_tiny_tvl_uses_strict_usd_boundary(usd_tvl, expected):
    data = observation("1000")
    data["current_tvl_usd"] = usd_tvl
    result = assess_withdrawal_capacity(data, now=NOW)
    assert result.tiny_tvl is expected
    assert result.to_dict()["tiny_tvl"] is expected
    assert result.capacity_warning is False


def test_tiny_tvl_unknown_without_usd_and_after_expiry():
    data = observation("1")
    data["denomination_symbol"] = "ETH"
    data["current_tvl"] = "5"
    assert assess_withdrawal_capacity(data, now=NOW).tiny_tvl is None
    data["current_tvl_usd"] = "15000"
    assert assess_withdrawal_capacity(data, now=NOW).tiny_tvl is False
    expired = assess_withdrawal_capacity(data, now=NOW + timedelta(hours=27))
    assert expired.tiny_tvl is None
    assert "current_tvl_usd" not in expired.observation


def test_tiny_tvl_does_not_treat_non_usd_tokens_as_dollars():
    from eth_defi.vault.withdrawal_capacity_readers import _with_usd_tvl

    data = {"current_tvl": "1"}
    token = SimpleNamespace(chain_id=1, address="0x" + "b" * 40, symbol="EURC")
    assert "current_tvl_usd" not in _with_usd_tvl(data, token)


def test_tiny_tvl_recognised_usdc_uses_face_value():
    from eth_defi.vault.withdrawal_capacity_readers import _with_usd_tvl
    from eth_defi.token import USDC_NATIVE_TOKEN

    data = observation("100")
    data["current_tvl"] = "9999.99"
    token = SimpleNamespace(chain_id=1, address=USDC_NATIVE_TOKEN[1], symbol="USDC")
    data = _with_usd_tvl(data, token)
    assert data["current_tvl_usd"] == "9999.99"
    assert "face-value" in data["usd_valuation_source"]
    assert assess_withdrawal_capacity(data, now=NOW).tiny_tvl is True
