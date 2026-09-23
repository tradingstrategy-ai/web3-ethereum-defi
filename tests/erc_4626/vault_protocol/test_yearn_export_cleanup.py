"""Tests for the best-effort Yearn export cleanup hook."""

import datetime

import pytest
from requests.exceptions import Timeout

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.yearn import export_cleanup
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.export_post_processing import run_vault_export_post_processors
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.top_vaults_json import _find_stale_post_processed_vault_ids  # noqa: PLC2701
from eth_defi.vault.vaultdb import VaultDatabase


def _make_database(*, protocol: str = "Yearn", address: str = "0x0000000000000000000000000000000000000001") -> VaultDatabase:
    """Create one small metadata database for hook tests.

    :param protocol:
        Initial public protocol attribution.
    :param address:
        Vault address used for the row and detection record.
    :return:
        In-memory database containing one Yearn-compatible vault.
    """

    detection = ERC4262VaultDetection(
        chain=1,
        address=address,
        first_seen_at_block=1,
        first_seen_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None),
        features={ERC4626Feature.yearn_v3_like},
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None),
        deposit_count=0,
        redeem_count=0,
    )
    row = {
        "Protocol": protocol,
        "Link": "https://yearn.fi/vaults/1/0x0000000000000000000000000000000000000001",
        "Features": "yearn_v3_like",
        "features": {ERC4626Feature.yearn_v3_like},
        "_detection_data": detection,
        "_flags": set(),
    }
    return VaultDatabase(rows={VaultSpec(1, address): row})


def test_parse_yearn_export_catalogue_requires_a_plausible_response() -> None:
    """Reject malformed or undersized responses before using absence as evidence."""

    with pytest.raises(ValueError, match="JSON array"):
        export_cleanup.parse_yearn_export_catalogue({})

    with pytest.raises(ValueError, match="only 0 positive entries"):
        export_cleanup.parse_yearn_export_catalogue([])

    payload = [
        {
            "chainId": 1,
            "address": f"0x{index:040x}",
            "inclusion": {"isYearn": True},
        }
        for index in range(export_cleanup.MINIMUM_YEARN_EXPORT_CATALOGUE_ENTRIES)
    ]
    parsed = export_cleanup.parse_yearn_export_catalogue(payload)
    assert len(parsed) == export_cleanup.MINIMUM_YEARN_EXPORT_CATALOGUE_ENTRIES


def test_clean_yearn_vault_metadata_reclassifies_catalogue_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    """An absent Yearn row becomes generic while technical features survive."""

    database = _make_database()
    original_row = next(iter(database.values()))
    original_row["_curator_slug"] = "yearn"
    original_row["_flags"] = {VaultFlag.trading}

    def fetch_empty_catalogue() -> frozenset:
        return frozenset()

    monkeypatch.setattr(export_cleanup, "fetch_yearn_export_catalogue", fetch_empty_catalogue)

    changed = export_cleanup.clean_yearn_vault_metadata(database)

    row = next(iter(database.values()))
    assert changed == {"1-0x0000000000000000000000000000000000000001"}
    assert row["Protocol"] == "ERC-4626"
    assert row["protocol_slug"] == "erc-4626"
    assert row["Link"] == "https://routescan.io/address/0x0000000000000000000000000000000000000001"
    assert ERC4626Feature.yearn_v3_like in row["features"]
    assert ERC4626Feature.yearn_registry_excluded in row["features"]
    assert ERC4626Feature.yearn_registry_excluded in row["_detection_data"].features
    assert row["_curator_slug"] is None
    assert row["_flags"] == {VaultFlag.trading}


def test_clean_yearn_vault_metadata_preserves_more_specific_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    """A retained non-Yearn feature may provide a more specific protocol."""

    database = _make_database()
    row = next(iter(database.values()))
    row["features"].add(ERC4626Feature.brink_like)
    monkeypatch.setattr(export_cleanup, "fetch_yearn_export_catalogue", frozenset)

    export_cleanup.clean_yearn_vault_metadata(database)

    assert row["Protocol"] == "Brink"
    assert row["protocol_slug"] == "brink"


def test_cleaned_sticky_attribution_is_recalculated_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a stale sticky record bypasses the metrics freshness cadence."""

    database = _make_database()
    monkeypatch.setattr(export_cleanup, "fetch_yearn_export_catalogue", frozenset)
    changed = export_cleanup.clean_yearn_vault_metadata(database)
    vault_id = next(iter(changed))
    row = next(iter(database.values()))
    sticky_record = {
        "protocol": "Yearn",
        "protocol_slug": "yearn",
        "link": "https://yearn.fi/vaults/1/0x0000000000000000000000000000000000000001",
        "curator_slug": "yearn",
        "features": [ERC4626Feature.yearn_v3_like.name],
    }
    sticky_state = {"vaults": {vault_id: {"last_exported_record": sticky_record}}}

    assert _find_stale_post_processed_vault_ids(database, changed, sticky_state) == changed

    detection = row["_detection_data"]
    sticky_record.update(
        {
            "protocol": row["Protocol"],
            "protocol_slug": row["protocol_slug"],
            "link": row["Link"],
            "curator_slug": "spark",
            "features": sorted(feature.name for feature in detection.features),
        }
    )
    assert _find_stale_post_processed_vault_ids(database, changed, sticky_state) == set()


def test_clean_yearn_vault_metadata_keeps_positive_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """An official positive match remains Yearn."""

    database = _make_database()
    monkeypatch.setattr(
        export_cleanup,
        "fetch_yearn_export_catalogue",
        lambda: frozenset({(1, "0x0000000000000000000000000000000000000001")}),
    )

    assert export_cleanup.clean_yearn_vault_metadata(database) == set()
    row = next(iter(database.values()))
    assert row["Protocol"] == "Yearn"


def test_clean_yearn_vault_metadata_repairs_existing_generic_sticky_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Previously excluded rows lose stale Yearn curator and homepage data."""

    database = _make_database(protocol="ERC-4626")
    row = next(iter(database.values()))
    row["features"].add(ERC4626Feature.yearn_registry_excluded)
    row["Link"] = "https://yearn.fi/vaults/1/0x0000000000000000000000000000000000000001"
    row["_curator_slug"] = "yearn"
    monkeypatch.setattr(
        export_cleanup,
        "fetch_yearn_export_catalogue",
        lambda: frozenset({(1, "0x0000000000000000000000000000000000000001")}),
    )

    changed = export_cleanup.clean_yearn_vault_metadata(database)

    assert changed == {"1-0x0000000000000000000000000000000000000001"}
    assert row["Protocol"] == "ERC-4626"
    assert row["Link"] == "https://routescan.io/address/0x0000000000000000000000000000000000000001"
    assert row["_curator_slug"] is None


def test_clean_yearn_vault_metadata_does_not_touch_other_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-Yearn protocol with Yearn-compatible code is outside this hook."""

    database = _make_database(protocol="Morpho")
    next(iter(database.values()))["features"].add(ERC4626Feature.yearn_registry_excluded)
    monkeypatch.setattr(export_cleanup, "fetch_yearn_export_catalogue", lambda: pytest.fail("must not fetch"))

    assert export_cleanup.clean_yearn_vault_metadata(database) == set()
    row = next(iter(database.values()))
    assert row["Protocol"] == "Morpho"
    assert ERC4626Feature.yearn_registry_excluded in row["features"]


def test_clean_yearn_vault_metadata_skips_registry_outage(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """An unavailable catalogue leaves the current metadata unchanged."""

    database = _make_database()
    monkeypatch.setattr(export_cleanup, "fetch_yearn_export_catalogue", lambda: (_ for _ in ()).throw(Timeout("test timeout")))

    assert export_cleanup.clean_yearn_vault_metadata(database) == set()
    row = next(iter(database.values()))
    assert row["Protocol"] == "Yearn"
    assert row["Link"].startswith("https://yearn.fi/")
    assert ERC4626Feature.yearn_registry_excluded not in row["features"]
    assert "skipped" in caplog.text


def test_export_post_processor_failure_is_contained(caplog: pytest.LogCaptureFixture) -> None:
    """A broken hook cannot prevent later hooks or the export process."""

    calls: list[str] = []

    def broken(_database: VaultDatabase) -> set[str]:
        calls.append("broken")
        message = "test outage"
        raise RuntimeError(message)

    def healthy(_database: VaultDatabase) -> set[str]:
        calls.append("healthy")
        return {"1-0x0000000000000000000000000000000000000001"}

    changed = run_vault_export_post_processors(_make_database(), (broken, healthy))

    assert calls == ["broken", "healthy"]
    assert changed == {"1-0x0000000000000000000000000000000000000001"}
    assert "continuing without its cleanup" in caplog.text
