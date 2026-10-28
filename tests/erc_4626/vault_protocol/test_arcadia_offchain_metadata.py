"""Arcadia metadata, generated copy and scanner export regressions.

Set ``RUN_ARCADIA_OFFCHAIN_METADATA_TEST=1`` for the real public API check.
Other tests do not require a network connection or RPC credentials.
"""

# Expected public website copy deliberately uses typographic apostrophes.
# ruff: noqa: RUF001

import datetime
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

import eth_defi.erc_4626.scan as scan_module
import eth_defi.erc_4626.vault_protocol.arcadia.offchain_metadata as metadata_module
import eth_defi.erc_4626.vault_protocol.arcadia.vault as vault_module
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.arcadia.description import MAX_SHORT_DESCRIPTION_LENGTH, extract_arcadia_short_description, is_arcadia_narrative, resolve_arcadia_descriptions
from eth_defi.erc_4626.vault_protocol.arcadia.offchain_metadata import ArcadiaVaultMetadata, fetch_arcadia_vault_metadata, fetch_arcadia_vaults, parse_arcadia_pools
from eth_defi.erc_4626.vault_protocol.arcadia.vault import ArcadiaVault
from eth_defi.vault.fee import FeeData, VaultFeeMode

USDG_TRANCHE = "0xa5e1e1f92a244f192943899eb5810e2bab372ea4"
USDG_POOL = "0xf37c0c5996503fdd2b5ccce36e659cd30393ae59"
SECOND_TRANCHE = "0x1111111111111111111111111111111111111111"
VAULT_NAME = "Senior ArcadiaV2 Global Dollar"
EXPECTED_SHORT = "Earn interest by supplying USDG to the senior tranche of Arcadia Finance’s lending pool on Robinhood Chain."
ROBINHOOD_CHAIN_ID = 4663
BASE_CHAIN_ID = 8453
EXPECTED_REFRESH_CALLS = 2


@pytest.fixture(autouse=True)
def isolated_memory_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent tests from sharing worker metadata or retry deadlines."""
    monkeypatch.setattr(metadata_module, "_chain_caches", {})


@pytest.fixture
def pool_payload() -> list[dict]:
    """Return the descriptive fields observed in the real Robinhood API."""
    return [{"name": "USDG", "description": "Arcadia USDG Pool", "address": USDG_POOL, "tranches": [{"name": "Senior USDG", "address": USDG_TRANCHE.upper().replace("0X", "0x")}]}]


@pytest.fixture
def pool_metadata(pool_payload: list[dict]) -> ArcadiaVaultMetadata:
    """Parse the USDG tranche independently of HTTP and filesystem caches."""
    return parse_arcadia_pools(4663, pool_payload)[USDG_TRANCHE]


def mock_api(monkeypatch: pytest.MonkeyPatch, payload: object) -> Mock:
    """Install a successful API response and expose its request counter.

    :param monkeypatch: Pytest patch lifetime manager.
    :param payload: JSON-compatible response data.
    :return: Mocked requests function.
    """
    request = Mock(return_value=Mock(json=Mock(return_value=payload)))
    monkeypatch.setattr(metadata_module.requests, "get", request)
    return request


def test_index_uses_all_tranches_and_chain_identity(pool_payload: list[dict], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pool addresses are not vault identities and cross-chain addresses repeat."""
    pool_payload[0]["tranches"].append({"name": "Junior USDG", "address": SECOND_TRANCHE})
    request = mock_api(monkeypatch, pool_payload)
    robinhood = fetch_arcadia_vaults(4663, cache_path=tmp_path)
    base = fetch_arcadia_vaults(8453, cache_path=tmp_path)
    assert set(robinhood) == {USDG_TRANCHE, SECOND_TRANCHE}
    assert USDG_POOL not in robinhood
    assert robinhood[USDG_TRANCHE].pool_address == USDG_POOL
    assert robinhood[USDG_TRANCHE].chain_id == ROBINHOOD_CHAIN_ID
    assert base[USDG_TRANCHE].chain_id == BASE_CHAIN_ID
    assert request.call_count == EXPECTED_REFRESH_CALLS
    request.assert_called_with(metadata_module.POOLS_API_URL, params={"chain_id": 8453}, timeout=30)


def test_generated_usdg_descriptions(pool_metadata: ArcadiaVaultMetadata) -> None:
    """Replace the API label with the approved short and long explanations."""
    copy = resolve_arcadia_descriptions(chain_id=4663, vault_name=VAULT_NAME, asset_symbol="USDG", metadata=pool_metadata)
    assert copy.short_description == EXPECTED_SHORT
    assert copy.description == ("This vault represents the senior tranche of Arcadia Finance’s USDG lending pool on Robinhood Chain. Depositors supply USDG, which borrowers use through collateralised Arcadia accounts, and receive shares that accrue lending interest. Interest rates vary with pool utilisation. Depositors are exposed to smart contract risk and potential losses if borrower liquidations do not cover outstanding debt. See [Arcadia’s lending documentation](https://docs.arcadia.finance/users/lenders).")


@pytest.mark.parametrize("label", [None, "", " \n ", "USDG", "Senior USDG", VAULT_NAME, "  ARCADIA  USDG Pool.  "])
def test_labels_are_not_narrative(label: str | None, pool_metadata: ArcadiaVaultMetadata) -> None:
    """Normalised display names never suppress generated explanations."""
    metadata = replace(pool_metadata, description=label)
    assert not is_arcadia_narrative(label, vault_name=VAULT_NAME, asset_symbol="USDG", metadata=metadata)
    assert resolve_arcadia_descriptions(chain_id=4663, vault_name=VAULT_NAME, asset_symbol="USDG", metadata=metadata).short_description == EXPECTED_SHORT


@pytest.mark.parametrize("narrative", ["Earn lending interest.", "Interest accrues", "Supply USDG to earn interest. Borrowers provide collateral."])
def test_provider_narrative_precedes_templates(narrative: str, pool_metadata: ArcadiaVaultMetadata) -> None:
    """Preserve even brief provider copy and extract its first sentence."""
    copy = resolve_arcadia_descriptions(chain_id=4663, vault_name=VAULT_NAME, asset_symbol="USDG", metadata=replace(pool_metadata, description=narrative))
    assert copy.description == narrative
    assert copy.short_description == extract_arcadia_short_description(narrative)


def test_short_description_is_bounded_at_word_boundary() -> None:
    """Long first sentences stay within the listing budget without partial words."""
    text = "Collateralised lending interest " * 20
    short = extract_arcadia_short_description(text)
    assert len(short) <= MAX_SHORT_DESCRIPTION_LENGTH
    assert short.endswith("…")
    assert (short[:-1] + " ") in text
    assert extract_arcadia_short_description("First sentence!\nSecond sentence.") == "First sentence!"


@pytest.mark.parametrize("separator", ["\n", "\r\n", r"\n", r"\r\n"])
def test_provider_narrative_uses_shared_single_line_format(separator: str, pool_metadata: ArcadiaVaultMetadata) -> None:
    """New and cached narratives follow the shared description formatting contract."""
    metadata = replace(pool_metadata, description=f"Earn interest.{separator}Borrowers provide collateral.")
    copy = resolve_arcadia_descriptions(chain_id=4663, vault_name=VAULT_NAME, asset_symbol="USDG", metadata=metadata)
    assert copy.description == "Earn interest. Borrowers provide collateral."
    assert copy.short_description == "Earn interest."


@pytest.mark.parametrize("name,expected", [(VAULT_NAME, "senior tranche"), ("Junior ArcadiaV2 USDC", "junior tranche"), ("ArcadiaV2 USDC", None), ("Super Seniority ArcadiaV2 USDC", None), (None, None)])
def test_offline_copy_uses_only_explicit_tranche_names(name: str | None, expected: str | None) -> None:
    """An API miss still yields copy, with neutral wording for unknown tranches."""
    copy = resolve_arcadia_descriptions(chain_id=8453, vault_name=name, asset_symbol="USDC")
    assert "USDC" in copy.short_description
    assert "on Base" in copy.description
    if expected:
        assert expected in copy.description
    else:
        assert "tranche" not in copy.description


def test_missing_optional_fields_do_not_become_invented_facts() -> None:
    """Unknown denominations and chains produce a generic lending explanation."""
    copy = resolve_arcadia_descriptions(chain_id=123456789, vault_name=None, asset_symbol=None)
    assert "supplying assets" in copy.short_description
    assert "underlying asset" in copy.description
    assert "None" not in copy.description
    assert "Unknown chain" not in copy.description
    assert "senior" not in copy.description


def test_offline_label_detection_uses_known_tranche_and_asset() -> None:
    """Persisted tranche labels also remain labels when the API is unavailable."""
    assert not is_arcadia_narrative("Senior USDG", vault_name=VAULT_NAME, asset_symbol="USDG")


def test_missing_api_text_fields_are_optional(pool_payload: list[dict]) -> None:
    """Identity alone is sufficient for a usable provider record."""
    pool_payload[0].pop("description")
    pool_payload[0].pop("name")
    pool_payload[0]["tranches"][0].pop("name")
    metadata = parse_arcadia_pools(4663, pool_payload)[USDG_TRANCHE]
    assert metadata.description is None
    assert metadata.pool_name is None
    assert metadata.tranche_name is None


def test_memory_disk_cache_expiry_and_concurrent_vaults(pool_payload: list[dict], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Threads and new worker instances reuse disk data until its daily expiry."""
    request = mock_api(monkeypatch, pool_payload)
    now = native_datetime_utc_now()
    with ThreadPoolExecutor(max_workers=4) as executor:
        indices = list(executor.map(lambda _: fetch_arcadia_vaults(4663, cache_path=tmp_path, now_=now), range(8)))
    assert all(index == indices[0] for index in indices)
    request.assert_called_once()
    metadata_module._chain_caches.clear()
    assert fetch_arcadia_vaults(4663, cache_path=tmp_path, now_=now + datetime.timedelta(hours=23)) == indices[0]
    request.assert_called_once()
    fetch_arcadia_vaults(4663, cache_path=tmp_path, now_=now + datetime.timedelta(hours=25))
    assert request.call_count == EXPECTED_REFRESH_CALLS


@pytest.mark.parametrize("failure", [requests.Timeout("provider timed out"), requests.HTTPError("503 unavailable"), ValueError("invalid JSON")])
def test_api_failures_back_off_and_allow_generation(failure: Exception, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unavailable provider cannot prevent offline description generation."""
    request = Mock(side_effect=failure)
    monkeypatch.setattr(metadata_module.requests, "get", request)
    now = native_datetime_utc_now()
    assert fetch_arcadia_vaults(4663, cache_path=tmp_path, now_=now) is None
    assert fetch_arcadia_vaults(4663, cache_path=tmp_path, now_=now + datetime.timedelta(minutes=4)) is None
    request.assert_called_once()
    assert fetch_arcadia_vaults(4663, cache_path=tmp_path, now_=now + datetime.timedelta(minutes=5)) is None
    assert request.call_count == EXPECTED_REFRESH_CALLS
    monkeypatch.setattr(vault_module, "fetch_arcadia_vault_metadata", lambda *_args: None)
    vault = make_vault()
    assert vault.short_description == EXPECTED_SHORT
    assert "collateralised" in vault.description


@pytest.mark.parametrize("payload", [{}, [None], [{"address": USDG_POOL}], [{"address": "bad", "tranches": []}], [{"address": USDG_POOL, "tranches": [{}]}]])
def test_malformed_response_is_not_cached(payload: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed response shapes follow the same fallback path as HTTP errors."""
    mock_api(monkeypatch, payload)
    assert fetch_arcadia_vaults(4663, cache_path=tmp_path) is None
    assert not (tmp_path / "pools-4663.json").exists()


def test_failed_refresh_preserves_old_disk_and_memory_index(pool_payload: list[dict], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider errors never overwrite an older valid response."""
    request = mock_api(monkeypatch, pool_payload)
    now = native_datetime_utc_now()
    original = fetch_arcadia_vaults(4663, cache_path=tmp_path, now_=now)
    file = tmp_path / "pools-4663.json"
    original_bytes = file.read_bytes()
    metadata_module._chain_caches.clear()
    request.side_effect = requests.Timeout("provider timed out")
    assert fetch_arcadia_vaults(4663, cache_path=tmp_path, now_=now + datetime.timedelta(days=2)) == original
    assert fetch_arcadia_vaults(4663, cache_path=tmp_path, now_=now + datetime.timedelta(days=2, minutes=4)) == original
    assert request.call_count == EXPECTED_REFRESH_CALLS
    assert file.read_bytes() == original_bytes


def test_failed_cache_write_keeps_fetched_narrative(pool_payload: list[dict], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cache filesystem error cannot discard a successful provider response."""
    mock_api(monkeypatch, pool_payload)
    now = native_datetime_utc_now()
    original = fetch_arcadia_vaults(4663, cache_path=tmp_path, now_=now)
    pool_payload[0]["description"] = "Updated provider narrative."
    writer = Mock(side_effect=OSError("disk full"))
    monkeypatch.setattr(metadata_module, "_write_cache", writer)
    updated = fetch_arcadia_vaults(4663, cache_path=tmp_path, now_=now + datetime.timedelta(days=2))
    assert updated[USDG_TRANCHE].description == "Updated provider narrative."
    assert original[USDG_TRANCHE].description == "Arcadia USDG Pool"
    monkeypatch.setattr(metadata_module.requests, "get", Mock(side_effect=requests.Timeout("provider timed out")))
    assert fetch_arcadia_vaults(4663, cache_path=tmp_path, now_=now + datetime.timedelta(days=4)) == updated


def test_metadata_adapter_does_not_certify_transaction_support() -> None:
    """Description support alone does not advertise an untested two-way flow."""
    assert make_vault().get_deposit_manager_capability() is None


def test_corrupt_cache_is_replaced_and_empty_chain_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bad local JSON refreshes safely, while an empty array is a valid index."""
    file = tmp_path / "pools-130.json"
    file.write_text("invalid JSON")
    request = mock_api(monkeypatch, [])
    assert fetch_arcadia_vaults(130, cache_path=tmp_path) == {}
    assert fetch_arcadia_vaults(130, cache_path=tmp_path) == {}
    assert json.loads(file.read_text()) == []
    request.assert_called_once()


def test_lookup_normalises_address_and_caches_catalogue_misses(pool_payload: list[dict], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Mixed-case lookups and missing tranches share a single chain request."""
    request = mock_api(monkeypatch, pool_payload)
    original_fetch = fetch_arcadia_vaults
    monkeypatch.setattr(metadata_module, "fetch_arcadia_vaults", lambda chain: original_fetch(chain, cache_path=tmp_path))
    assert fetch_arcadia_vault_metadata(4663, USDG_TRANCHE.upper()).tranche_address == USDG_TRANCHE
    assert fetch_arcadia_vault_metadata(4663, SECOND_TRANCHE) is None
    assert fetch_arcadia_vault_metadata(4663, SECOND_TRANCHE) is None
    assert "absent from the pool API" in caplog.text
    request.assert_called_once()


def make_vault() -> ArcadiaVault:
    """Construct the real adapter with cached tokens and no RPC activity.

    :return: Arcadia adapter selected by the existing classification feature.
    """
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=4663))
    vault = create_vault_instance(web3, USDG_TRANCHE, features={ERC4626Feature.arcadia_finance_like})
    assert isinstance(vault, ArcadiaVault)
    vault.__dict__["share_token"] = SimpleNamespace(name=VAULT_NAME, symbol="srarcV2USDG", export=Mock(return_value={"symbol": "srarcV2USDG"}))
    vault.__dict__["denomination_token"] = SimpleNamespace(symbol="USDG", export=Mock(return_value={"symbol": "USDG"}))
    return vault


def test_adapter_selection_and_scan_export(pool_metadata: ArcadiaVaultMetadata, monkeypatch: pytest.MonkeyPatch) -> None:
    """The existing scanner exports generated copy from the Arcadia adapter."""
    lookup = Mock(return_value=pool_metadata)
    monkeypatch.setattr(vault_module, "fetch_arcadia_vault_metadata", lookup)
    vault = make_vault()
    monkeypatch.setattr(scan_module, "create_vault_instance", lambda *_args, **_kwargs: vault)
    monkeypatch.setattr(scan_module, "_fetch_activity_status", lambda *_args: {})
    monkeypatch.setattr(scan_module, "_fetch_lending_stats", lambda *_args: {})
    stubbed_methods = {
        "get_fee_data": FeeData(fee_mode=VaultFeeMode.internalised_skimming, management=None, performance=None, deposit=None, withdraw=None),
        "fetch_total_assets": Decimal(100),
        "fetch_total_supply": Decimal(100),
        "get_withdrawal_period": None,
        "get_estimated_lock_up": None,
        "get_flags": set(),
        "get_strategy_tags": None,
        "get_link": "https://arcadia.finance/",
        "get_notes": None,
        "get_deposit_manager_capability": None,
        "get_whitelist_notes": None,
        "fetch_minimum_deposit": None,
        "fetch_minimum_redemption": None,
        "fetch_scan_record_extra_data": {},
    }
    for method, value in stubbed_methods.items():
        monkeypatch.setattr(vault, method, Mock(return_value=value))
    monkeypatch.setattr(scan_module, "fetch_deposit_permission", Mock(return_value=SimpleNamespace(value="unknown")))
    now = native_datetime_utc_now()
    detection = ERC4262VaultDetection(chain=4663, address=USDG_TRANCHE, first_seen_at_block=1, first_seen_at=now, features={ERC4626Feature.arcadia_finance_like}, updated_at=now, deposit_count=1, redeem_count=1)
    record = scan_module.create_vault_scan_record(web3=None, detection=detection, block_identifier=1, token_cache={})
    assert record["Protocol"] == "Arcadia Finance"
    assert record["_short_description"] == EXPECTED_SHORT
    assert record["_description"] == vault.description
    assert "collateralised" in record["_description"]
    lookup.assert_called_once()


@pytest.mark.skipif(os.environ.get("RUN_ARCADIA_OFFCHAIN_METADATA_TEST") != "1", reason="Set RUN_ARCADIA_OFFCHAIN_METADATA_TEST=1 for the live Arcadia API check")
def test_live_robinhood_usdg_metadata(tmp_path: Path) -> None:
    """Verify the real provider's exact USDG pool and tranche relationship."""
    index = fetch_arcadia_vaults(4663, cache_path=tmp_path)
    assert index is not None
    metadata = index[USDG_TRANCHE]
    assert metadata.chain_id == ROBINHOOD_CHAIN_ID
    assert metadata.pool_address == USDG_POOL
    assert metadata.pool_name == "USDG"
    assert metadata.tranche_name == "Senior USDG"
    copy = resolve_arcadia_descriptions(chain_id=4663, vault_name=VAULT_NAME, asset_symbol="USDG", metadata=metadata)
    assert copy.short_description
    assert copy.description
