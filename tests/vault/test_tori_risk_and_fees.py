"""Verify Tori exposure disclosures and the guarded metadata refresh."""

import copy
import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.erc_4626.vault_protocol.tori.disclosures import TORI_EXPOSURE_NOTES, TORI_EXPOSURES, append_tori_disclosure
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.flag import VAULT_FLAGS_AND_NOTES, VaultFlag, get_notes
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner
from eth_defi.vault_protocols.repairs import tori_risk_and_fees as repair

SCRIPT_NAME = "013-tori-vault-risk-and-fees.py"
EXPECTED_EXPOSURES = 17
NATIVE = VaultSpec(1, "0x280839980a7ed0d7717f64125fe241012e5f5815")
UPSHIFT = VaultSpec(1, "0xcd69123b3fbbfc666e1f6a501da27b564c00de54")
OTHER = VaultSpec(8453, NATIVE.vault_address)


@pytest.fixture
def exposure_database(monkeypatch: pytest.MonkeyPatch) -> tuple[VaultDatabase, dict[int, Mock], Mock]:
    """Create all reviewed identities without requiring live providers.

    Distinct source blocks ensure the repair pins each chain independently.
    The factory mock includes an Accountable instance to exercise its native
    fee-manager representation rather than the generic fee method.

    :param monkeypatch: Factory replacement support.
    :return: Staged catalogue, chain connections and adapter factory mock.
    """
    features = {
        "Tori Finance": {ERC4626Feature.tori_like},
        "Upshift": {ERC4626Feature.upshift_like, ERC4626Feature.upshift_multi_asset_like},
        "Morpho": {ERC4626Feature.morpho_like},
        "Royco": {ERC4626Feature.royco_tranche_like},
        "Term Finance": {ERC4626Feature.term_finance_like},
        "Accountable": {ERC4626Feature.accountable_like},
        "Curvance": {ERC4626Feature.curvance_like},
        "ERC-4626": set(),
    }
    rows = {}
    timestamp = datetime.datetime(2026, 10, 29, tzinfo=datetime.UTC).replace(tzinfo=None)
    adapters = {}
    for (chain, address), exposure in TORI_EXPOSURES.items():
        spec = VaultSpec(chain, address)
        rows[spec] = {
            "Address": address,
            "Name": exposure.name,
            "Symbol": exposure.symbol,
            "Denomination": exposure.asset,
            "Protocol": exposure.protocol,
            "_detection_data": ERC4262VaultDetection(chain, address, 1, timestamp, features[exposure.protocol], timestamp, 4, 2),
            "_risk": VaultTechnicalRisk.dangerous if spec == UPSHIFT else None,
            "_notes": "Existing independent note",
            "_curator_slug": "preserve-curator",
            "_flags": {VaultFlag.review_needed},
            "NAV": 100,
        }
        adapter = Mock(spec=AccountableVault) if exposure.protocol == "Accountable" else Mock()
        fees = FeeData(VaultFeeMode.internalised_skimming, 0.0, None, 0.0, 0.0)
        adapter.get_fee_data.return_value = fees
        adapter.fetch_accountable_fees.return_value.as_generic_fee_data.return_value = fees
        adapters[address] = adapter
    rows[OTHER] = {"Protocol": "Unrelated", "manual": 7}
    providers = {chain: Mock() for chain in (1, 143)}
    for chain, web3 in providers.items():
        web3.eth.chain_id = chain
        web3.eth.block_number = 1000 + chain
    factory = Mock(side_effect=lambda _web3, address, *_args, **_kwargs: adapters[address])
    monkeypatch.setattr(repair, "create_vault_instance", factory)
    return VaultDatabase(rows=rows, last_scanned_block={1: 99, 143: 88}), providers, factory


def test_exposure_inventory_and_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover each reviewed exposure without masking warnings or other chains.

    Junior first-loss terms, yield-token maturity and strUSD income retention
    must remain distinct. Existing bad flags still outrank reviewed risk.

    :param monkeypatch: Temporary incident warning support.
    :return: ``None`` after verifying scoped disclosures and precedence.
    """
    assert len(TORI_EXPOSURES) == EXPECTED_EXPOSURES
    assert {key[0] for key in TORI_EXPOSURES} == {1, 143}
    for (chain, address), exposure in TORI_EXPOSURES.items():
        assert address == address.lower()
        assert get_notes(address, chain_id=chain, protocol_name=exposure.protocol, include_defaults=False) is None
        note = append_tori_disclosure(None, address.upper(), chain)
        assert note == TORI_EXPOSURE_NOTES[chain, address]
        assert "collateral" in note if exposure.protocol in {"Morpho", "Term Finance"} else "external custodians" in note
        assert "fee" in note.lower() or "charge" in note.lower()
        assert "\n" not in note
        assert append_tori_disclosure(None, address, 8453) is None
        assert append_tori_disclosure(note, address, chain) == note
    assert get_vault_risk("Tori Finance", NATIVE.vault_address) is VaultTechnicalRisk.high
    assert get_vault_risk("Upshift", UPSHIFT.vault_address) is VaultTechnicalRisk.severe
    assert get_vault_risk("Morpho", "0x3bd9adae6643ddcddd02746b8b60075e56df9478") is VaultTechnicalRisk.negligible
    assert get_vault_risk("Royco", "0x3b0cecd83bb9ee016587cbe61bea89e429da7a09") is None
    assert "Tori's retained income share as a direct fee" in TORI_EXPOSURE_NOTES[1, "0x3bd9adae6643ddcddd02746b8b60075e56df9478"]
    assert "undisclosed share of trading income" not in TORI_EXPOSURE_NOTES[1, "0xcab1d2a2973763bed06e782b65a43fb3cfe27f13"]
    assert "before the senior" in TORI_EXPOSURE_NOTES[1, "0x3b0cecd83bb9ee016587cbe61bea89e429da7a09"]
    assert "not a claim to the underlying principal" in TORI_EXPOSURE_NOTES[1, "0x10b2487173d9f7087c8bdce9e70fd135212a8d7d"]
    assert "5%" in TORI_EXPOSURE_NOTES[UPSHIFT.chain_id, UPSHIFT.vault_address]
    monkeypatch.setitem(VAULT_FLAGS_AND_NOTES, NATIVE.vault_address, (VaultFlag.illiquid, "Existing incident warning."))
    assert get_notes(NATIVE.vault_address, chain_id=1).startswith("Existing incident warning.")
    assert get_vault_risk("Tori Finance", NATIVE.vault_address) is VaultTechnicalRisk.blacklisted


def test_refresh_preview_apply_and_retry(exposure_database: tuple[VaultDatabase, dict[int, Mock], Mock], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Persist only reviewed fees, leaving risk, history and identities intact.

    Preview must be byte-preserving. Apply backs up the old catalogue and
    repeated transformation must produce no changes.

    :param exposure_database: Reviewed rows and mocked fee providers.
    :param tmp_path: Isolated pipeline directory.
    :param monkeypatch: Provider configuration support.
    :return: ``None`` after checking guarded preview, apply and repeatability.
    """
    database, providers, factory = exposure_database
    before = copy.deepcopy(database)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "vault-metadata-db.pickle"
    database.write(path)
    for name in ("reader-state.pickle", "vault-prices-1h.parquet", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    original = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts / SCRIPT_NAME).write_bytes(script.source)
    monkeypatch.setattr(repair, "read_json_rpc_url", lambda chain: chain)
    monkeypatch.setattr(repair, "create_multi_provider_web3", lambda chain: providers[chain])
    for key in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    options = {"data_dir": data_dir, "scripts_dir": scripts, "provenance": {"in_docker": False}}
    assert runner.run_pending_migrations(dry_run=True, **options) == [SCRIPT_NAME]
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == original
    assert runner.run_pending_migrations(dry_run=False, **options) == [SCRIPT_NAME]
    refreshed = VaultDatabase.read(path)
    assert refreshed.rows[OTHER] == before.rows[OTHER]
    assert refreshed.last_scanned_block == before.last_scanned_block
    for spec, row in refreshed.rows.items():
        if spec == OTHER:
            continue
        for key in ("Protocol", "_detection_data", "_curator_slug", "_flags", "_risk", "_notes", "Name", "NAV"):
            assert row[key] == before.rows[spec][key]
        assert row["_fees"].performance is None
        assert row["_fees"].get_net_fees().can_calculate_investor_net_performance()
    assert refreshed.rows[UPSHIFT]["_risk"] is VaultTechnicalRisk.dangerous
    assert repair.refresh_tori_risk_and_fees(refreshed, providers)["changed_rows"] == 0
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    for name in ("reader-state.pickle", "vault-prices-1h.parquet", "timestamps.duckdb"):
        assert (data_dir / name).read_bytes() == original[name]
    for call in factory.call_args_list:
        assert call.kwargs["default_block_identifier"] == providers[call.args[0].eth.chain_id].eth.block_number


@pytest.mark.parametrize("field", ["Protocol", "Address", "Symbol", "Denomination", "_detection_data"])
def test_refresh_rejects_identity_before_rpc(exposure_database: tuple[VaultDatabase, dict[int, Mock], Mock], field: str) -> None:
    """Reject stale or conflicting identities before any fee read or mutation.

    A later conflicting target must not leave earlier rows partially changed.

    :param exposure_database: Reviewed rows and mocked providers.
    :param field: Identity component deliberately made incompatible.
    :return: ``None`` after checking atomic rejection.
    """
    database, providers, factory = exposure_database
    database.rows[UPSHIFT][field] = None if field == "_detection_data" else "wrong"
    before = copy.deepcopy(database.rows)
    with pytest.raises(ValueError, match="Conflicting reviewed"):
        repair.refresh_tori_risk_and_fees(database, providers)
    assert database.rows == before
    factory.assert_not_called()


def test_refresh_provider_failure_is_atomic(exposure_database: tuple[VaultDatabase, dict[int, Mock], Mock]) -> None:
    """Propagate a later provider failure without staging earlier proposals.

    Replacing a fee-read error with a guessed value would hide a failed
    refresh and violate the migration's all-target validation boundary.

    :param exposure_database: Reviewed rows and adapter factory.
    :return: ``None`` after checking failure propagation and preservation.
    """
    database, providers, factory = exposure_database
    before = copy.deepcopy(database.rows)
    native_adapter = factory.side_effect(providers[1], NATIVE.vault_address)
    upshift_adapter = factory.side_effect(providers[1], UPSHIFT.vault_address)
    upshift_adapter.get_fee_data.side_effect = ConnectionError("Provider unavailable")
    with pytest.raises(ConnectionError, match="Provider unavailable"):
        repair.refresh_tori_risk_and_fees(database, providers)
    native_adapter.get_fee_data.assert_called_once_with(1001)
    assert database.rows == before


def test_refresh_missing_targets_need_no_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report missing targets without inventing rows or reading a provider.

    A partial catalogue must not silently expand its deployment inventory.

    :param monkeypatch: Provider call guard.
    :return: ``None`` after verifying the explicit missing-target report.
    """
    provider = Mock(side_effect=AssertionError("No RPC expected"))
    monkeypatch.setattr(repair, "create_multi_provider_web3", provider)
    database = VaultDatabase(rows={OTHER: {"manual": 7}})
    result = repair.refresh_tori_risk_and_fees(database)
    assert result["changed_rows"] == result["inspected_rows"] == 0
    assert len(result["missing"]) == EXPECTED_EXPOSURES
    assert database.rows == {OTHER: {"manual": 7}}
    provider.assert_not_called()


def test_refresh_generic_vault_retains_unknown_fees(exposure_database: tuple[VaultDatabase, dict[int, Mock], Mock]) -> None:
    """Handle the real generic class without calling its abstract fee methods.

    Locked yield-token and Stake DAO wrappers have no supported fee reader.
    A disclosure is available, but no numerical fee is invented.

    :param exposure_database: Reviewed rows and mocked fee providers.
    :return: ``None`` after checking the unsupported-fee boundary.
    """
    database, providers, factory = exposure_database
    generic_spec = VaultSpec(1, "0x10b2487173d9f7087c8bdce9e70fd135212a8d7d")
    original_factory = factory.side_effect
    factory.side_effect = lambda web3, address, *args, **kwargs: ERC4626Vault(web3, VaultSpec(1, address)) if address == generic_spec.vault_address else original_factory(web3, address, *args, **kwargs)
    repair.refresh_tori_risk_and_fees(database, providers)
    fees = database.rows[generic_spec]["_fees"]
    assert fees == FeeData(None, None, None, None, None)
    assert not fees.get_net_fees().can_calculate_investor_net_performance()


def test_refresh_wrong_provider_chain_is_atomic(exposure_database: tuple[VaultDatabase, dict[int, Mock], Mock]) -> None:
    """Reject a misconfigured Monad provider before any row is staged.

    Matching symbols do not justify reading a same-address contract on a
    different network.

    :param exposure_database: Reviewed rows and mocked fee providers.
    :return: ``None`` after checking chain validation.
    """
    database, providers, factory = exposure_database
    providers[143].eth.chain_id = 1
    before = copy.deepcopy(database.rows)
    with pytest.raises(ValueError, match="wrong chain"):
        repair.refresh_tori_risk_and_fees(database, providers)
    assert database.rows == before
    factory.assert_not_called()
