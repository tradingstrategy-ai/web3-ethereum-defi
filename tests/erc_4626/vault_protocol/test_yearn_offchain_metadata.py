"""Yearn yDaemon offchain metadata integration tests.

Set ``RUN_YEARN_OFFCHAIN_METADATA_TEST=1`` to run the optional live yDaemon
check. The remaining tests use synthetic yDaemon documents so they exercise
the classification and cache semantics without a network dependency.
"""

import datetime
import os
from unittest.mock import MagicMock

import pytest

import eth_defi.erc_4626.vault_protocol.yearn.offchain_metadata as yearn_metadata
import eth_defi.erc_4626.vault_protocol.yearn.vault as yearn_vault_module
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.cap.vault import CAPVault
from eth_defi.erc_4626.vault_protocol.yearn.compounder import YearnCompounderVault
from eth_defi.erc_4626.vault_protocol.yearn.morpho_compounder import YearnMorphoCompounderStrategy
from eth_defi.erc_4626.vault_protocol.yearn.offchain_metadata import (
    CachedYearnVaultIndex,
    YearnDetectedVaultCache,
    YearnDetectedVaultCatalogue,
    YearnDetectedVaultMetadata,
    extract_yearn_short_description,
    fetch_yearn_vault_endorsement,
    fetch_yearn_vaults_file_for_chain,
)
from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault
from eth_defi.research.vault_metrics import apply_bad_flag_check
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import NOT_IN_YEARN_FRONTEND, VaultFlag
from eth_defi.vault.risk import VaultTechnicalRisk

COINFLAKES_VAULT = "0x254bd33e2f62713f893f0842c99e68f855cda315"
OFFICIAL_YEARN_VAULT = "0x1111111111111111111111111111111111111111"
ENDORSED_YEARN_PARTNER_VAULT = "0x2222222222222222222222222222222222222222"
LIVE_OFFICIAL_YEARN_VAULT = "0x00c8a649c9837523ebb406ceb17a6378ab5c74cf"
EXPECTED_CACHE_REFRESH_CALLS = 2
FLEX_USDC_VAULT = "0x863687e4e9751b57f38b4b0eba04744c72d0f7b8"


def create_detected_catalogue(
    vaults: dict[tuple[int, str], YearnDetectedVaultMetadata] | None = None,
    *,
    is_complete: bool = True,
) -> YearnDetectedVaultCatalogue:
    """Create synthetic public Yearn catalogue metadata.

    :param vaults:
        Synthetic public vault-page metadata.
    :param is_complete:
        Whether a catalogue miss is reliable negative evidence.
    :return:
        Public Yearn catalogue used by adapter tests.
    """

    return YearnDetectedVaultCatalogue(vaults=vaults or {}, is_complete=is_complete)


def _make_ydaemon_document() -> dict[str, object]:
    """Build the relevant subset of one yDaemon chain metadata document."""

    return {
        "vaults": {
            COINFLAKES_VAULT: {
                "address": COINFLAKES_VAULT,
                "endorsed": False,
                "metadata": {
                    "isHidden": True,
                    "inclusion": {
                        "isYearn": False,
                    },
                },
            },
            OFFICIAL_YEARN_VAULT: {
                "address": OFFICIAL_YEARN_VAULT,
                "endorsed": True,
                "metadata": {
                    "isHidden": False,
                    "inclusion": {
                        "isYearn": True,
                    },
                },
            },
            ENDORSED_YEARN_PARTNER_VAULT: {
                "address": ENDORSED_YEARN_PARTNER_VAULT,
                "endorsed": True,
                "metadata": {
                    "isHidden": False,
                    "inclusion": {
                        "isYearn": False,
                        "isYearnJuiced": True,
                    },
                },
            },
        },
    }


def test_yearn_static_metadata_is_cached_and_normalised(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache one complete chain catalogue instead of querying each vault.

    1. Return a yDaemon-shaped source document from the raw GitHub request.
    2. Confirm the result is lowercase address-indexed and persisted locally.
    3. Re-read while fresh and confirm no second network request occurs.
    """

    response = MagicMock()
    response.json.return_value = _make_ydaemon_document()
    monkeypatch.setattr(yearn_metadata.requests, "get", MagicMock(return_value=response))
    now_ = native_datetime_utc_now()

    # 1. Fetch and persist the full chain source document.
    first = fetch_yearn_vaults_file_for_chain(
        1,
        cache_path=tmp_path,
        github_base_url="https://example.invalid/yearn",
        now_=now_,
    )

    # 2. Address normalisation makes checksum and scanner forms equivalent.
    assert first is not None
    assert first[COINFLAKES_VAULT].endorsed is False
    assert first[COINFLAKES_VAULT].is_yearn is False
    assert first[ENDORSED_YEARN_PARTNER_VAULT].endorsed is True
    assert first[ENDORSED_YEARN_PARTNER_VAULT].is_yearn is False
    assert (tmp_path / "yearn-vaults-1.json").exists()

    # 3. The fresh persistent cache prevents per-vault repeated HTTP calls.
    second = fetch_yearn_vaults_file_for_chain(
        1,
        cache_path=tmp_path,
        github_base_url="https://example.invalid/yearn",
        now_=now_ + datetime.timedelta(hours=1),
    )
    assert second == first
    yearn_metadata.requests.get.assert_called_once_with("https://example.invalid/yearn/1.json", timeout=30)


def test_corrupt_yearn_cache_is_replaced_from_the_source(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refresh a corrupt local metadata cache instead of aborting vault scanning."""

    cache_file = tmp_path / "yearn-vaults-1.json"
    cache_file.write_text("not valid json")
    response = MagicMock()
    response.json.return_value = _make_ydaemon_document()
    monkeypatch.setattr(yearn_metadata.requests, "get", MagicMock(return_value=response))

    vaults = fetch_yearn_vaults_file_for_chain(
        1,
        cache_path=tmp_path,
        github_base_url="https://example.invalid/yearn",
    )

    assert vaults is not None
    assert vaults[OFFICIAL_YEARN_VAULT].endorsed is True
    assert cache_file.read_text() != "not valid json"


def test_yearn_metadata_schema_without_any_official_vault_is_rejected() -> None:
    """Fail open when a yDaemon schema change removes the inclusion fields."""

    document = _make_ydaemon_document()
    for metadata in document["vaults"].values():
        metadata.pop("endorsed")
        metadata["metadata"].pop("inclusion")

    with pytest.raises(ValueError, match="lacks an endorsed Yearn frontend vault"):
        yearn_metadata._parse_yearn_vault_index(document)


def test_yearn_metadata_endorsement_has_safe_three_way_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a successfully loaded catalogue can classify a vault as unendorsed."""

    now_ = native_datetime_utc_now()
    index = yearn_metadata._parse_yearn_vault_index(_make_ydaemon_document())
    monkeypatch.setattr(yearn_metadata, "_cached_yearn_vaults", {1: CachedYearnVaultIndex(vaults=index, fetched_at=now_)})

    assert fetch_yearn_vault_endorsement(1, COINFLAKES_VAULT) is False
    assert fetch_yearn_vault_endorsement(1, OFFICIAL_YEARN_VAULT) is True
    assert fetch_yearn_vault_endorsement(1, ENDORSED_YEARN_PARTNER_VAULT) is True
    assert fetch_yearn_vault_endorsement(1, "0x000000000000000000000000000000000000dead") is False

    # An unavailable source is intentionally distinct from a known missing address.
    monkeypatch.setattr(yearn_metadata, "_cached_yearn_vaults", {})
    monkeypatch.setattr(yearn_metadata, "_yearn_metadata_retry_after", {})
    monkeypatch.setattr(yearn_metadata, "fetch_yearn_vaults_file_for_chain", lambda _chain_id: None)
    assert fetch_yearn_vault_endorsement(1, COINFLAKES_VAULT) is None


def test_unavailable_yearn_metadata_retries_after_a_short_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid repeated per-vault HTTP failures without memoising an outage forever."""

    fetch_metadata = MagicMock(return_value=None)
    monkeypatch.setattr(yearn_metadata, "_cached_yearn_vaults", {})
    monkeypatch.setattr(yearn_metadata, "_yearn_metadata_retry_after", {})
    monkeypatch.setattr(yearn_metadata, "fetch_yearn_vaults_file_for_chain", fetch_metadata)

    assert fetch_yearn_vault_endorsement(1, COINFLAKES_VAULT) is None
    assert fetch_yearn_vault_endorsement(1, OFFICIAL_YEARN_VAULT) is None
    fetch_metadata.assert_called_once_with(1)


def test_yearn_metadata_process_cache_refreshes_daily(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refresh one worker's cached yDaemon catalogue after its cache duration.

    Persistent scanner processes must observe endorsement changes without a
    container restart, while individual vaults in the same scanner cycle reuse
    the in-memory index.
    """

    first_document = _make_ydaemon_document()
    refreshed_document = _make_ydaemon_document()
    refreshed_document["vaults"][COINFLAKES_VAULT]["endorsed"] = True
    first_index = yearn_metadata._parse_yearn_vault_index(first_document)
    refreshed_index = yearn_metadata._parse_yearn_vault_index(refreshed_document)
    start = native_datetime_utc_now()
    now_ = start
    fetch_metadata = MagicMock(side_effect=[first_index, refreshed_index])

    monkeypatch.setattr(yearn_metadata, "_cached_yearn_vaults", {})
    monkeypatch.setattr(yearn_metadata, "_yearn_metadata_retry_after", {})
    monkeypatch.setattr(yearn_metadata, "fetch_yearn_vaults_file_for_chain", fetch_metadata)
    monkeypatch.setattr(yearn_metadata, "native_datetime_utc_now", lambda: now_)

    assert fetch_yearn_vault_endorsement(1, COINFLAKES_VAULT) is False
    assert fetch_yearn_vault_endorsement(1, COINFLAKES_VAULT) is False
    fetch_metadata.assert_called_once_with(1)

    now_ = start + yearn_metadata.DEFAULT_CACHE_DURATION + datetime.timedelta(seconds=1)
    assert fetch_yearn_vault_endorsement(1, COINFLAKES_VAULT) is True
    assert fetch_metadata.call_count == EXPECTED_CACHE_REFRESH_CALLS


def test_yearn_detected_catalogue_is_cached_and_preserves_stale_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache public-page metadata and retain a successful index on refresh failure."""

    payload = [
        {
            "chainID": 1,
            "address": FLEX_USDC_VAULT,
            "description": "Flex USDC is an allocator vault managed by the Yearn Curation team.",
        }
    ]
    response = MagicMock()
    response.json.return_value = payload
    requests_get = MagicMock(return_value=response)
    monkeypatch.setattr(yearn_metadata.requests, "get", requests_get)
    monkeypatch.setattr(yearn_metadata, "_yearn_detected_vaults_state", YearnDetectedVaultCache())
    start = native_datetime_utc_now()
    now_ = start
    monkeypatch.setattr(yearn_metadata, "native_datetime_utc_now", lambda: now_)

    first = yearn_metadata.fetch_yearn_detected_vaults()
    second = yearn_metadata.fetch_yearn_detected_vaults()
    assert first is not None
    assert second == first
    assert first.get(1, FLEX_USDC_VAULT).description == payload[0]["description"]
    assert first.is_complete is True
    requests_get.assert_called_once_with(yearn_metadata.YEARN_DETECTED_VAULTS_URL, timeout=30)

    now_ = start + yearn_metadata.DEFAULT_CACHE_DURATION + datetime.timedelta(seconds=1)
    requests_get.side_effect = yearn_metadata.RequestException("temporary outage")
    assert yearn_metadata.fetch_yearn_detected_vaults() == first


def test_yearn_detected_catalogue_outage_retries_after_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid repeated public catalogue requests while a cold cache is unavailable."""

    requests_get = MagicMock(side_effect=yearn_metadata.RequestException("temporary outage"))
    monkeypatch.setattr(yearn_metadata.requests, "get", requests_get)
    monkeypatch.setattr(yearn_metadata, "_yearn_detected_vaults_state", YearnDetectedVaultCache())

    assert yearn_metadata.fetch_yearn_detected_vaults() is None
    assert yearn_metadata.fetch_yearn_detected_vaults() is None
    requests_get.assert_called_once_with(yearn_metadata.YEARN_DETECTED_VAULTS_URL, timeout=30)


def test_yearn_detected_catalogue_limit_keeps_misses_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mark an endpoint-limit response incomplete for negative classification."""

    monkeypatch.setattr(yearn_metadata, "YEARN_DETECTED_VAULTS_LIMIT", 1)
    catalogue = yearn_metadata._parse_yearn_detected_vault_index(
        [
            {
                "chainID": 1,
                "address": FLEX_USDC_VAULT,
                "description": "Flex USDC is an allocator vault.",
            }
        ]
    )

    assert catalogue.is_complete is False
    assert yearn_metadata.resolve_yearn_vault_endorsement(1, FLEX_USDC_VAULT, static_endorsement=False, detected_vaults=catalogue) is True
    assert yearn_metadata.resolve_yearn_vault_endorsement(1, COINFLAKES_VAULT, static_endorsement=False, detected_vaults=catalogue) is None


def test_yearn_endorsement_resolver_requires_both_catalogues_for_negative() -> None:
    """Combine public website and static catalogue outcomes conservatively."""

    listed_catalogue = create_detected_catalogue({(1, FLEX_USDC_VAULT): YearnDetectedVaultMetadata(description="Flex USDC is an allocator vault.")})
    complete_empty_catalogue = create_detected_catalogue()

    assert yearn_metadata.resolve_yearn_vault_endorsement(1, FLEX_USDC_VAULT, static_endorsement=False, detected_vaults=listed_catalogue) is True
    assert yearn_metadata.resolve_yearn_vault_endorsement(1, COINFLAKES_VAULT, static_endorsement=True, detected_vaults=complete_empty_catalogue) is True
    assert yearn_metadata.resolve_yearn_vault_endorsement(1, COINFLAKES_VAULT, static_endorsement=False, detected_vaults=complete_empty_catalogue) is False
    assert yearn_metadata.resolve_yearn_vault_endorsement(1, COINFLAKES_VAULT, static_endorsement=None, detected_vaults=complete_empty_catalogue) is None
    assert yearn_metadata.resolve_yearn_vault_endorsement(1, COINFLAKES_VAULT, static_endorsement=False, detected_vaults=None) is None


def test_unlisted_direct_yearn_vaults_are_blacklisted_in_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unendorsed direct Yearn V3 vaults receive a bad scan flag.

    1. Give the direct V3 adapter an authoritative exclusion result.
    2. Confirm it records the dedicated frontend-membership flag.
    3. Confirm the generic metrics bad-flag policy turns that stored flag into
       the hard ``blacklisted`` technical-risk classification.
    """

    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_vault_endorsement", lambda *_args: False)
    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_detected_vaults", create_detected_catalogue)
    monkeypatch.setattr(ERC4626Vault, "get_flags", lambda _self: set())
    v3_vault = object.__new__(YearnV3Vault)
    v3_vault.spec = VaultSpec(chain_id=1, vault_address=COINFLAKES_VAULT)
    v3_vault.features = {ERC4626Feature.yearn_v3_like}

    # 1-2. The direct adapter captures the dynamic frontend decision.
    flags = v3_vault.get_flags()
    assert flags == {VaultFlag.unofficial}
    assert v3_vault.get_notes() == NOT_IN_YEARN_FRONTEND

    # 3. BAD_FLAGS causes the exported technical risk to be hard blacklisted.
    risk, notes, checked_flags = apply_bad_flag_check(
        risk=VaultTechnicalRisk.low,
        notes=v3_vault.get_notes(),
        flags=flags,
    )
    assert risk == VaultTechnicalRisk.blacklisted
    assert notes == NOT_IN_YEARN_FRONTEND
    assert checked_flags == flags


def test_listed_yearn_vaults_and_strategy_adapters_are_not_blacklisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Yearn-endorsed partner products out of the unofficial classification."""

    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_vault_endorsement", lambda *_args: True)
    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_detected_vaults", create_detected_catalogue)
    monkeypatch.setattr(ERC4626Vault, "get_flags", lambda _self: set())
    v3_vault = object.__new__(YearnV3Vault)
    v3_vault.spec = VaultSpec(chain_id=1, vault_address=ENDORSED_YEARN_PARTNER_VAULT)
    v3_vault.features = {ERC4626Feature.yearn_v3_like}
    compounder_vault = object.__new__(YearnCompounderVault)
    compounder_vault.spec = VaultSpec(chain_id=1, vault_address=ENDORSED_YEARN_PARTNER_VAULT)
    compounder_vault.features = {ERC4626Feature.yearn_compounder_like}

    assert v3_vault.get_flags() == set()
    assert v3_vault.get_notes() is None
    assert compounder_vault.get_flags() == set()
    assert compounder_vault.get_notes() is None


def test_unavailable_yearn_metadata_and_cap_vaults_are_not_blacklisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unknown metadata and non-Yearn protocol adapters out of this flag.

    A source outage must not change a Yearn vault's existing classification.
    CAP intentionally reuses the Yearn V3 adapter but has its own frontend and
    protocol classification, so it must not be classified from Yearn metadata.
    """

    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_vault_endorsement", lambda *_args: None)
    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_detected_vaults", lambda: None)
    monkeypatch.setattr(ERC4626Vault, "get_flags", lambda _self: set())
    yearn_vault = object.__new__(YearnV3Vault)
    yearn_vault.spec = VaultSpec(chain_id=1, vault_address=COINFLAKES_VAULT)
    yearn_vault.features = {ERC4626Feature.yearn_v3_like}
    assert yearn_vault.get_flags() == set()

    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_vault_endorsement", lambda *_args: False)
    cap_vault = object.__new__(CAPVault)
    cap_vault.spec = VaultSpec(chain_id=1, vault_address=COINFLAKES_VAULT)
    cap_vault.features = {ERC4626Feature.cap_like}
    assert cap_vault.get_flags() == set()
    assert cap_vault.description is None


def test_yearn_website_listing_overrides_lagging_static_metadata_and_exports_description(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a public Yearn page as positive evidence for Flex USDC.

    A static source omission cannot leave a website-listed direct V3 vault
    unofficial. The concise website summary must be the complete first
    sentence, not a truncation at only a full-stop-plus-space sequence.
    """

    description = "Flex USDC is an allocator vault managed by the Yearn Curation team. It lends USDC across several Flex markets."
    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_vault_endorsement", lambda *_args: False)
    monkeypatch.setattr(
        yearn_vault_module,
        "fetch_yearn_detected_vaults",
        lambda: create_detected_catalogue({(1, FLEX_USDC_VAULT): YearnDetectedVaultMetadata(description=description)}),
    )
    monkeypatch.setattr(ERC4626Vault, "get_flags", lambda _self: set())
    vault = object.__new__(YearnV3Vault)
    vault.spec = VaultSpec(chain_id=1, vault_address=FLEX_USDC_VAULT)
    vault.features = {ERC4626Feature.yearn_v3_like}

    assert vault.get_flags() == set()
    assert vault.get_notes() is None
    assert vault.description == description
    assert vault.short_description == "Flex USDC is an allocator vault managed by the Yearn Curation team."


def test_strategy_adapters_keep_static_catalogue_misses_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid unofficial flags for TokenizedStrategy and Morpho strategy adapters."""

    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_vault_endorsement", lambda *_args: False)
    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_detected_vaults", create_detected_catalogue)
    monkeypatch.setattr(ERC4626Vault, "get_flags", lambda _self: set())
    compounder_vault = object.__new__(YearnCompounderVault)
    compounder_vault.spec = VaultSpec(chain_id=1, vault_address=COINFLAKES_VAULT)
    compounder_vault.features = {ERC4626Feature.yearn_compounder_like}
    morpho_vault = object.__new__(YearnMorphoCompounderStrategy)
    morpho_vault.spec = VaultSpec(chain_id=1, vault_address=COINFLAKES_VAULT)
    morpho_vault.features = {ERC4626Feature.yearn_morpho_compounder_like}

    assert compounder_vault.get_flags() == set()
    assert compounder_vault.get_notes() is None
    assert morpho_vault.get_flags() == set()
    assert morpho_vault.get_notes() is None


def test_yearn_website_catalogue_outage_keeps_static_miss_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid flagging a recently launched website vault during a public API outage."""

    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_vault_endorsement", lambda *_args: False)
    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_detected_vaults", lambda: None)
    monkeypatch.setattr(ERC4626Vault, "get_flags", lambda _self: set())
    vault = object.__new__(YearnV3Vault)
    vault.spec = VaultSpec(chain_id=1, vault_address=FLEX_USDC_VAULT)
    vault.features = {ERC4626Feature.yearn_v3_like}

    assert vault.get_flags() == set()
    assert vault.get_notes() is None


def test_incomplete_yearn_website_catalogue_keeps_static_miss_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid flagging a vault that could lie beyond the endpoint response limit."""

    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_vault_endorsement", lambda *_args: False)
    monkeypatch.setattr(yearn_vault_module, "fetch_yearn_detected_vaults", lambda: create_detected_catalogue(is_complete=False))
    monkeypatch.setattr(ERC4626Vault, "get_flags", lambda _self: set())
    vault = object.__new__(YearnV3Vault)
    vault.spec = VaultSpec(chain_id=1, vault_address=COINFLAKES_VAULT)
    vault.features = {ERC4626Feature.yearn_v3_like}

    assert vault.get_flags() == set()
    assert vault.get_notes() is None


def test_yearn_description_rejects_templates_and_long_sentence() -> None:
    """Avoid malformed or oversized short descriptions from free-form metadata."""

    assert yearn_metadata._normalise_yearn_detected_description(None) is None
    assert yearn_metadata._normalise_yearn_detected_description("") is None
    assert yearn_metadata._normalise_yearn_detected_description("Earn {{token}} rewards.") is None
    assert extract_yearn_short_description("A sentence without punctuation " * 20) is None


@pytest.mark.skipif(os.environ.get("RUN_YEARN_OFFCHAIN_METADATA_TEST") != "1", reason="Set RUN_YEARN_OFFCHAIN_METADATA_TEST=1 to run the live yDaemon GitHub check")
def test_live_yearn_metadata_confirms_endorsement_and_flex_description(tmp_path) -> None:
    """Check live static membership and the public Flex USDC description.

    This deliberately uses an isolated cache so a stale operator cache cannot
    hide an upstream source change.
    """

    vaults = fetch_yearn_vaults_file_for_chain(1, cache_path=tmp_path)

    assert vaults is not None
    metadata = vaults[COINFLAKES_VAULT]
    assert metadata.endorsed is False
    assert metadata.is_yearn is False
    official_metadata = vaults[LIVE_OFFICIAL_YEARN_VAULT]
    assert official_metadata.endorsed is True
    assert official_metadata.is_yearn is True

    detected_vaults = yearn_metadata.fetch_yearn_detected_vaults()
    assert detected_vaults is not None
    flex_metadata = detected_vaults.get(1, FLEX_USDC_VAULT)
    assert flex_metadata is not None
    assert flex_metadata.description is not None
    assert flex_metadata.description.startswith("Flex USDC is an allocator vault")
    assert extract_yearn_short_description(flex_metadata.description) == "Flex USDC is an allocator vault managed by the Yearn Curation team."
