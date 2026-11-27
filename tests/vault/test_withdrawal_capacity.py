"""Warning decisions exercise units, unknowns and independent pause directions."""

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.fluid.vault import FluidVault
from eth_defi.erc_4626.vault_protocol.sky.vault import SkyVault
from eth_defi.vault import withdrawal_capacity_readers
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositManager
from eth_defi.vault.withdrawal_capacity import assess_withdrawal_capacity, configured_capacity_threshold, enrich_withdrawal_capacity, fetch_withdrawal_capacity_limit

NOW = datetime(2026, 11, 16, tzinfo=UTC)


def observation(capacity="150", **kwargs):
    return dict(status="measured", current_capacity=capacity, current_tvl="1000", observed_at=NOW.isoformat(), denomination_symbol="USDC", **kwargs)


def test_configured_threshold_keeps_exact_token_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WITHDRAWAL_CAPACITY_THRESHOLD", "0.10")
    data = observation("100.000000000000000001")
    result = assess_withdrawal_capacity(data, configured_capacity_threshold(), now=NOW)
    assert result.capacity_warning is False
    assert assess_withdrawal_capacity(data, now=NOW).capacity_warning is True
    assert result.to_dict()["current_capacity"] == "100.000000000000000001"


@pytest.mark.parametrize("bad", [-0.1, float("nan")])
def test_invalid_threshold(bad):
    with pytest.raises(ValueError):
        assess_withdrawal_capacity(None, bad)


@pytest.mark.parametrize("bad", [True, "0.15"])
def test_threshold_requires_percent_float(bad):
    """Reject implicit conversion at API and direct-assessment boundaries."""
    with pytest.raises(AssertionError, match="must be a Percent float"):
        assess_withdrawal_capacity(None, bad)
    with pytest.raises(AssertionError, match="must be a Percent float"):
        enrich_withdrawal_capacity([], SimpleNamespace(rows={}), threshold=bad)
    with pytest.raises(AssertionError, match="must be a Percent float"):
        fetch_withdrawal_capacity_limit(SimpleNamespace(web3=None), capacity_threshold=bad)
    with pytest.raises(AssertionError, match="must be a Percent float"):
        VaultBase.fetch_withdrawal_capacity_limit(SimpleNamespace(web3=None), capacity_threshold=bad)
    query = partial(VaultBase.fetch_withdrawal_capacity_limit, SimpleNamespace(web3=None))
    manager = SimpleNamespace(vault=SimpleNamespace(fetch_withdrawal_capacity_limit=query))
    with pytest.raises(AssertionError, match="must be a Percent float"):
        VaultDepositManager.fetch_withdrawal_capacity_limit(manager, capacity_threshold=bad)
    with pytest.raises(AssertionError, match="must be a Percent float"):
        replace(assess_withdrawal_capacity(None), capacity_threshold=bad)


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


@pytest.mark.parametrize(
    "borrowed,cash,capacity,warning",
    [(920, 1000, "0", True), (700, 1000, "229", False), (700, 30, "40", True)],
)
def test_staked_usds_export_measures_withdrawal_buffer(monkeypatch: pytest.MonkeyPatch, borrowed: int, cash: int, capacity: str, *, warning: bool) -> None:
    """Export the amount left after interest and auctions, bounded by payable USDS."""
    wad, ray = 10**18, 10**27
    block = 123
    ilk = b"LSEV2-SKY-A".ljust(32, b"\0")
    address = "0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9"
    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            get_block=Mock(return_value={"number": block, "timestamp": int(NOW.timestamp()), "hash": bytes(32)}),
            get_storage_at=Mock(return_value=bytes(12) + bytes.fromhex("7a61b7adcfd493f7cf0f86dfcecb94b72c227f22")),
        ),
    )
    vault = SkyVault(web3, VaultSpec(1, address))

    def read(value: int | bytes | tuple[int, ...]) -> Mock:
        """Serve the fixture state only at the requested observation block."""

        def call(*, block_identifier: int) -> int | bytes | tuple[int, ...]:
            assert block_identifier == block
            return value

        return Mock(return_value=SimpleNamespace(call=call))

    stusds = SimpleNamespace(totalSupply=read(1000 * wad), convertToAssets=read(101 * ray // 100), chi=read(ray), ilk=read(ilk))
    vat = SimpleNamespace(ilks=read((borrowed * wad, ray, 0, 0, 0)))
    jug = SimpleNamespace(drip=read(11 * ray // 10))
    clipper = SimpleNamespace(Due=read(11 * wad * ray))
    usds = SimpleNamespace(balanceOf=read(cash * wad))
    contracts = {"sky/StUsds.json": stusds, "sky/Vat.json": vat, "sky/Jug.json": jug, "sky/LockstakeClipper.json": clipper}
    monkeypatch.setattr(withdrawal_capacity_readers, "get_deployed_contract", lambda _web3, abi, _address: SimpleNamespace(functions=contracts[abi]))
    vault.denomination_token = SimpleNamespace(
        symbol="USDS",
        address="0xdc035d45d973e3ec169d2276ddab16f1e407384f",
        contract=SimpleNamespace(functions=usds),
        convert_to_decimals=lambda raw: Decimal(raw) / wad,
    )

    database = SimpleNamespace(rows={vault.spec: {"_withdrawal_capacity_observation": vault.fetch_withdrawal_capacity_limit(block).observation}})
    row = {"chain_id": 1, "address": address}
    enrich_withdrawal_capacity([row], database, now=NOW)
    assert row["withdrawal_capacity_limit"]["current_capacity"] == capacity
    assert row["withdrawal_capacity_limit"]["current_tvl"] == "1010"
    assert row["withdrawal_capacity_limit"]["capacity_warning"] is warning
    assert row["withdrawal_capacity_observation"]["capacity_warning_source"] == ("measured" if warning else None)
    enrich_withdrawal_capacity([row], database, now=NOW + timedelta(hours=27))
    assert row["withdrawal_capacity_limit"]["capacity_warning"] is None


def test_staked_usds_unreviewed_implementation_is_unknown() -> None:
    """An implementation change prevents the reader from using old accounting rules."""
    web3 = SimpleNamespace(eth=SimpleNamespace(get_storage_at=Mock(return_value=bytes(32))))
    vault = SkyVault(web3, VaultSpec(1, "0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9"))
    assert vault.fetch_withdrawal_capacity_observation(123)["status"] == "unsupported"

    savings = SkyVault(web3, VaultSpec(1, "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"))
    assert savings.fetch_withdrawal_capacity_observation(123)["status"] == "unsupported"
    web3.eth.get_storage_at.assert_called_once()


@pytest.mark.parametrize("capacity", [None, "200", "0"])
def test_export_morpho_low_liquidity_sets_capacity_warning(capacity: str | None) -> None:
    """Provider warnings supplement measured capacity and clear on refresh."""
    spec = VaultSpec(137, "0x" + "a" * 40)
    row = {"id": spec.as_string_id()}
    morpho_data = {"vault_warnings": [{"type": "low_liquidity", "level": "YELLOW"}]}
    source = {"_morpho_offchain_data": morpho_data}
    if capacity is not None:
        source["_withdrawal_capacity_observation"] = observation(capacity)
    database = SimpleNamespace(rows={spec: source})

    enrich_withdrawal_capacity([row], database, now=NOW)
    limit = row["withdrawal_capacity_limit"]
    assert limit["capacity_warning"] is True
    assert limit["current_capacity"] == capacity
    assert "Morpho reports low vault liquidity" in limit["capacity_reason"]
    assert row["withdrawal_capacity_observation"]["capacity_warning_source"] == ("measured" if capacity == "0" else "morpho")
    assert row["withdrawal_capacity_observation"]["morpho_low_liquidity"] is True

    morpho_data["vault_warnings"] = [{"type": "not_whitelisted", "level": "YELLOW"}]
    enrich_withdrawal_capacity([row], database, now=NOW)
    assert row["withdrawal_capacity_limit"]["capacity_warning"] is (None if capacity is None else capacity == "0")
    assert "morpho_low_liquidity" not in row["withdrawal_capacity_observation"]


@pytest.mark.parametrize("missing_field,uppercase", [("chain_id", False), ("address", False), ("both", True)])
def test_export_resolves_legacy_id(missing_field: str, uppercase: bool) -> None:
    """Sticky records with only a combined identity retain measured warnings."""
    spec = VaultSpec(1, "0x" + "a" * 40)
    row = {"id": spec.as_string_id(), "chain_id": 1, "address": spec.vault_address}
    if uppercase:
        row["id"] = row["id"].upper()
    for field in ("chain_id", "address") if missing_field == "both" else (missing_field,):
        row.pop(field)
    original = dict(row)
    database = SimpleNamespace(rows={spec: {"_withdrawal_capacity_observation": observation("100")}})
    enrich_withdrawal_capacity([row], database, now=NOW)
    assert {key: row[key] for key in original} == original
    assert row["withdrawal_capacity_limit"]["capacity_warning"] is True
    assert row["withdrawal_capacity_limit"]["current_capacity"] == "100"
    assert set(row) == set(original) | {"withdrawal_capacity_limit", "withdrawal_capacity_observation"}


@pytest.mark.parametrize("identity", [{}, {"id": "invalid"}])
def test_export_unresolvable_identity_is_unknown(identity: dict) -> None:
    """Unmatched legacy identities cannot replay an old safe capacity claim."""
    row = {**identity, "withdrawal_capacity_limit": {"capacity_warning": False, "current_capacity": "1000"}, "withdrawal_capacity_observation": observation("1000")}
    enrich_withdrawal_capacity([row], SimpleNamespace(rows={}), now=NOW)
    assert row["withdrawal_capacity_limit"]["capacity_warning"] is None
    assert row["withdrawal_capacity_limit"]["current_capacity"] is None
    assert row["withdrawal_capacity_limit"]["pause_warning"] is None
    assert row["withdrawal_capacity_observation"]["status"] == "unsupported"


def test_export_does_not_override_invalid_explicit_identity() -> None:
    """A conflicting legacy id must not override invalid explicit identity fields."""
    spec = VaultSpec(1, "0x" + "a" * 40)
    row = {"id": spec.as_string_id(), "chain_id": "invalid", "address": spec.vault_address}
    database = SimpleNamespace(rows={spec: {"_withdrawal_capacity_observation": observation("1000")}})
    enrich_withdrawal_capacity([row], database, now=NOW)
    assert row["withdrawal_capacity_limit"]["capacity_warning"] is None


@pytest.mark.parametrize(
    "field,old,id_only",
    [
        ("features", {"status": "failed"}, False),
        ("_detection_data", None, True),
        ("features", {"status": "measured", "observed_at": "2020-01-01T00:00:00+00:00", "current_capacity": "1000", "current_tvl": "1000"}, True),
    ],
)
def test_legacy_40acres_structural_warning_survives_metadata_failure(field: str, old: dict | None, id_only: bool) -> None:
    """A retained legacy row needs no successful RPC observation to warn."""
    features = {ERC4626Feature.forty_acres_like}
    source = {field: features if field == "features" else SimpleNamespace(features=features)}
    if old is not None:
        source["_withdrawal_capacity_observation"] = old
    before = copy.deepcopy(old)
    spec = VaultSpec(1, "0x" + "a" * 40)
    rows = [{"id": spec.as_string_id()} if id_only else {"chain_id": 1, "address": spec.vault_address}]
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
def test_fluid_selects_actual_liquidity_asset(monkeypatch: pytest.MonkeyPatch, native: bool, wrapped: bool, token_cash: int, native_cash: int, status: str) -> None:
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

    web3 = SimpleNamespace(eth=SimpleNamespace(get_balance=balance, get_storage_at=Mock(return_value=bytes(32))))
    vault = FluidVault(web3, VaultSpec(1, address))
    vault.vault_contract = contract
    vault.denomination_token = token
    monkeypatch.setattr(readers, "get_deployed_contract", lambda *args: contract)
    data = vault.fetch_withdrawal_capacity_observation(123)
    web3.eth.get_storage_at.assert_called_once()
    assert web3.eth.get_storage_at.call_args.kwargs["block_identifier"] == 123
    assert ("getData", None) in calls
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


def test_unusable_observations_remove_current_claims() -> None:
    corrupt = assess_withdrawal_capacity(observation("not-an-amount"), now=NOW)
    assert corrupt.capacity_warning is None and corrupt.current_capacity is None

    data = observation("0", deposits_paused=True, withdrawals_paused=True)
    data["reason"] = "Withdrawals are administratively paused"
    result = assess_withdrawal_capacity(data, now=NOW + timedelta(hours=27))
    assert result.pause_warning is None
    assert result.capacity_warning is None
    assert result.current_capacity is None
    assert result.observation["status"] == "stale"
    assert "administratively paused" not in result.capacity_reason
    assert "current_capacity" not in result.observation
    assert result.observation["last_observation"]["current_capacity"] == "0"


@pytest.mark.parametrize("usd_tvl, expected", [("9999.999999", True), ("10000", False)])
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


def test_tiny_tvl_uses_face_value_only_for_recognised_usd_tokens() -> None:
    from eth_defi.token import USDC_NATIVE_TOKEN
    from eth_defi.vault.withdrawal_capacity_readers import _with_usd_tvl

    eurc = SimpleNamespace(chain_id=1, address="0x" + "b" * 40, symbol="EURC")
    assert "current_tvl_usd" not in _with_usd_tvl({"current_tvl": "1"}, eurc)

    data = observation("100")
    data["current_tvl"] = "9999.99"
    token = SimpleNamespace(chain_id=1, address=USDC_NATIVE_TOKEN[1], symbol="USDC")
    data = _with_usd_tvl(data, token)
    assert data["current_tvl_usd"] == "9999.99"
    assert "face-value" in data["usd_valuation_source"]
    assert assess_withdrawal_capacity(data, now=NOW).tiny_tvl is True


@pytest.mark.parametrize("capacity,warning", [("14.999999999999999999", True), ("15", False)])
def test_percent_threshold_preserves_decimal_amount_boundary(capacity: str, warning: bool) -> None:
    """Float percentages must not introduce binary rounding into token comparisons."""
    data = observation(capacity)
    data["current_tvl"] = "100"
    result = assess_withdrawal_capacity(data, 0.15, now=NOW)
    assert result.capacity_warning is warning
    assert result.to_dict()["capacity_threshold"] == 0.15
    assert result.current_capacity == Decimal(capacity)
