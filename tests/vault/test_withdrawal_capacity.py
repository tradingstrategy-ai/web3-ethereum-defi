"""Warning decisions exercise units, unknowns and independent pause directions."""

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.withdrawal_capacity import assess_withdrawal_capacity, enrich_withdrawal_capacity

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
    assert assess_withdrawal_capacity(data, "0.1", now=NOW).capacity_warning is False
    assert assess_withdrawal_capacity(data, "0.15", now=NOW).capacity_warning is True
    assert assess_withdrawal_capacity(data, "0.1", now=NOW).to_dict()["current_capacity"] == "100.000000000000000001"


@pytest.mark.parametrize("bad", [True, 0.15, "-0.1", "1.1", "NaN", "Infinity"])
def test_invalid_threshold(bad):
    with pytest.raises(ValueError):
        assess_withdrawal_capacity(None, bad)


def test_unknown_and_stale_do_not_mean_safe():
    assert assess_withdrawal_capacity(None, now=NOW).capacity_warning is None
    result = assess_withdrawal_capacity(observation(), now=NOW + timedelta(hours=3))
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


def test_refresh_is_due_independently_of_discovery(tmp_path, monkeypatch):
    """The persisted observation changes without modifying other row fields."""
    import eth_defi.vault.withdrawal_capacity_refresh as refresh
    from eth_defi.vault.vaultdb import VaultDatabase

    address = "0x" + "a" * 40
    spec = VaultSpec(1, address)
    database = VaultDatabase()
    database.rows[spec] = {"Name": "Retained", "features": set(), "NAV": Decimal(1000)}
    path = tmp_path / "vaults.pickle"
    database.write(path)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, get_block=lambda _: {"number": 123, "hash": bytes(32), "timestamp": int(NOW.timestamp())}))
    calls = []

    def construct(*args, **kwargs):
        calls.append(kwargs["default_block_identifier"])
        return SimpleNamespace(fetch_withdrawal_capacity_observation=lambda _: observation())

    monkeypatch.setattr(refresh, "create_vault_instance", construct)
    assert refresh.refresh_withdrawal_capacity(web3, path, now=NOW) == 1
    assert refresh.refresh_withdrawal_capacity(web3, path, now=NOW + timedelta(minutes=30)) == 0
    saved = VaultDatabase.read(path).rows[spec]
    assert saved["Name"] == "Retained" and saved["NAV"] == Decimal(1000)
    assert calls == [123]
    assert refresh.refresh_withdrawal_capacity(web3, path, now=NOW + timedelta(hours=1)) == 1


def test_corrupt_numeric_observation_stays_unknown():
    result = assess_withdrawal_capacity(observation("not-an-amount"), now=NOW)
    assert result.capacity_warning is None and result.current_capacity is None


def test_zero_tvl_and_partial_cannot_be_safe():
    data = observation("0")
    data["current_tvl"] = "0"
    assert assess_withdrawal_capacity(data, now=NOW).capacity_warning is None
    data = observation("500")
    data["status"] = "partial"
    assert assess_withdrawal_capacity(data, now=NOW).capacity_warning is None


@pytest.mark.parametrize("fails", [False, True])
def test_normal_tick_refreshes_configured_chains_before_publication(tmp_path, monkeypatch, fails):
    """A normal scan tick retains optional refresh and its existing publication."""
    import eth_defi.vault.withdrawal_capacity_refresh as refresh
    from eth_defi.vault import scan_all_chains

    observed = []
    published = []

    def read(web3, path, **kwargs):
        observed.append(web3)
        if fails:
            raise RuntimeError("Capacity stage failed")
        return 1

    monkeypatch.setattr(refresh, "refresh_withdrawal_capacity", read)
    monkeypatch.setenv("JSON_RPC_CAPACITY_TEST", "unused-test-provider")
    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda _: "configured-chain")
    monkeypatch.setattr(scan_all_chains, "print_dashboard", lambda *args, **kwargs: None)
    monkeypatch.setattr(scan_all_chains, "run_post_processing", lambda **kwargs: published.append(True) or {})
    results = scan_all_chains.run_scan_tick(
        chains=[],
        capacity_chains=[scan_all_chains.ChainConfig("Configured", "JSON_RPC_CAPACITY_TEST")],
        active_protocols=[],
        scan_prices=False,
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_apex=False,
        scan_core3=False,
        scan_currency_rates=False,
        max_workers=1,
        core3_max_workers=1,
        currency_api_max_workers=1,
        frequency="1h",
        retry_count=0,
        skip_post_processing=False,
        skip_cleaning=True,
        skip_top_vaults=True,
        skip_sparklines=True,
        skip_metadata=True,
        skip_data=True,
        skip_samples=True,
        vault_db_path=tmp_path / "vault-metadata-db.pickle",
        uncleaned_price_path=tmp_path / "vault-prices-1h.parquet",
        reader_state_path=tmp_path / "vault-reader-state-1h.pickle",
        hyperliquid_db_path=tmp_path / "hyperliquid-vaults.duckdb",
        hyperliquid_hf_db_path=tmp_path / "hyperliquid-vaults-hf.duckdb",
        grvt_db_path=tmp_path / "grvt-vaults.duckdb",
        lighter_db_path=tmp_path / "lighter-pools.duckdb",
        hibachi_db_path=tmp_path / "hibachi-vaults.duckdb",
        apex_db_path=tmp_path / "apex-vaults.duckdb",
        bkp_files=[],
        bkp_dir=tmp_path / "backups",
        core3_db_path=tmp_path / "core3.duckdb",
    )
    assert observed == ["configured-chain"]
    assert published == [True]


@pytest.mark.parametrize("fails", [False, True])
def test_idle_loop_refreshes_capacity_without_scan_or_publication(tmp_path, monkeypatch, fails):
    """The real loop's idle branch must not enter any scan/publication pipeline."""
    import logging

    import eth_defi.vault.withdrawal_capacity_refresh as refresh
    from eth_defi.vault import scan_all_chains

    monkeypatch.chdir(tmp_path)
    settings = {
        "PIPELINE_DATA_DIR": str(tmp_path),
        "LOOP_INTERVAL_SECONDS": "3600",
        "MAX_CYCLES": "1",
        "TEST_CHAINS": "Ethereum",
        "SKIP_CORE3": "true",
        "SKIP_XERBERUS": "true",
        "SKIP_CURRENCY_RATES": "true",
        "SKIP_POST_PROCESSING": "false",
        "FORCE_LEAD_DISCOVERY": "false",
        "JSON_RPC_CAPACITY_TEST": "test-provider",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    for name in ("SCAN_PRICES", "SCAN_HYPERCORE", "SCAN_GRVT", "SCAN_LIGHTER", "SCAN_HIBACHI", "SCAN_APEX", "SCAN_DERIVE_V3", "SCAN_NEUTRAL_TRADE"):
        monkeypatch.setenv(name, "false")
    observed = []

    def read(web3, path, **kwargs):
        observed.append((web3, path))
        if fails:
            raise RuntimeError("Capacity stage failed")
        return 1

    monkeypatch.setattr(refresh, "refresh_withdrawal_capacity", read)
    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda _: "configured-chain")
    monkeypatch.setattr(scan_all_chains, "build_chain_configs", lambda: [scan_all_chains.ChainConfig("Ethereum", "JSON_RPC_CAPACITY_TEST"), scan_all_chains.ChainConfig("Hyperliquid", "JSON_RPC_UNUSED")])
    monkeypatch.setattr(scan_all_chains, "get_due_items", lambda *args, **kwargs: ([], []))
    monkeypatch.setattr(scan_all_chains, "refresh_stale_proxy_state", lambda: None)
    monkeypatch.setattr(scan_all_chains, "clear_sqlite_rate_limit_databases", lambda: [])
    monkeypatch.setattr(scan_all_chains, "validate_top_vaults_config", lambda **kwargs: None)
    monkeypatch.setattr(scan_all_chains, "run_scan_tick", lambda **kwargs: pytest.fail("Idle loop entered full scan/publication pipeline"))
    monkeypatch.setattr(scan_all_chains, "run_post_processing", lambda **kwargs: pytest.fail("Idle loop started publication"))
    handlers = set(logging.getLogger().handlers)
    original_level = logging.getLogger().level
    try:
        scan_all_chains._main()
    finally:
        for handler in set(logging.getLogger().handlers) - handlers:
            logging.getLogger().removeHandler(handler)
            handler.close()
        logging.getLogger().setLevel(original_level)
    assert observed == [("configured-chain", tmp_path / "vault-metadata-db.pickle")]


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
    result = assess_withdrawal_capacity(data, now=NOW + timedelta(hours=3))
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
    expired = assess_withdrawal_capacity(data, now=NOW + timedelta(hours=3))
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
