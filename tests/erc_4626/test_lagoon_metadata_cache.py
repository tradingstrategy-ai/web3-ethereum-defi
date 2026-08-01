"""Test Lagoon metadata cache freshness without live API or RPC access."""

import datetime
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from eth_typing import HexAddress
from pytest import MonkeyPatch
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.lagoon import offchain_metadata

ETHEREUM_CHAIN_ID = 1
FLINT_ADDRESS = HexAddress("0x7f35dea44a192764aa50d50e5f0ece1d5a8b0e45")
FLINT_CHECKSUM_ADDRESS = HexAddress("0x7f35dEa44a192764aa50d50e5f0eCE1d5a8b0e45")
SECOND_ADDRESS = HexAddress("0x1111111111111111111111111111111111111111")
EXPECTED_DAILY_FETCH_COUNT = 2
EXPECTED_FAILED_REFRESH_COUNT = 2
STARTED_AT = datetime.datetime(2026, 8, 1, 12, 0, 0)  # noqa: DTZ001 - Repository convention is naive UTC.


def _make_metadata(description: str | None, short_description: str | None) -> offchain_metadata.LagoonVaultMetadata:
    """Create complete Lagoon metadata for cache tests.

    :param description:
        Long strategy description.

    :param short_description:
        Short strategy description.

    :return:
        JSON-compatible Lagoon metadata.
    """
    return {
        "name": "Flint USD",
        "description": description,
        "short_description": short_description,
        "logo_url": None,
        "transparency_url": None,
        "average_settlement": None,
        "curators": [],
    }


def _set_utc_mtime(path: Path, timestamp: datetime.datetime) -> float:
    """Set a file modification time from a naive UTC datetime.

    :param path:
        File whose modification time is changed.

    :param timestamp:
        Naive UTC datetime to assign.

    :return:
        Assigned Unix timestamp.
    """
    unix_timestamp = timestamp.replace(tzinfo=datetime.UTC).timestamp()
    os.utime(path, (unix_timestamp, unix_timestamp))
    return unix_timestamp


def _set_utc_mtime_after_write(path: Path, metadata: dict[HexAddress, offchain_metadata.LagoonVaultMetadata], timestamp: datetime.datetime) -> float:
    """Write a Lagoon cache and assign its naive UTC modification time.

    :param path:
        Cache file to write.

    :param metadata:
        Lagoon metadata to serialise.

    :param timestamp:
        Naive UTC modification time to assign.

    :return:
        Assigned Unix timestamp.
    """
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return _set_utc_mtime(path, timestamp)


def _make_web3() -> Web3:
    """Create a Web3-shaped test double with an Ethereum chain id.

    :return:
        Web3-compatible test double that performs no RPC reads.
    """
    return cast(Web3, SimpleNamespace(eth=SimpleNamespace(chain_id=ETHEREUM_CHAIN_ID)))


def test_lagoon_metadata_default_cache_refreshes_after_one_day(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Refresh the default Lagoon cache at the exact one-day boundary.

    :param tmp_path:
        Temporary cache directory.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    """
    cache_file = offchain_metadata._get_cache_file(tmp_path, ETHEREUM_CHAIN_ID)
    now_ = STARTED_AT
    cache_file.write_text(json.dumps({FLINT_CHECKSUM_ADDRESS: _make_metadata(None, None)}), encoding="utf-8")
    _set_utc_mtime(cache_file, now_ - datetime.timedelta(days=1))

    def fetch_listing_page(chain_id: int, **_kwargs: object) -> dict[str, object]:
        """Return one Lagoon listing row."""
        return {"vaults": [{"address": FLINT_ADDRESS, "chain": {"id": str(chain_id)}}], "hasNextPage": False}

    def fetch_vault_detail(chain_id: int, address: HexAddress, **_kwargs: object) -> dict[str, object]:
        """Return refreshed Lagoon detail metadata."""
        assert chain_id == ETHEREUM_CHAIN_ID
        assert address == FLINT_ADDRESS
        return {
            "name": "Flint USD",
            "description": "Refreshed long description.",
            "shortDescription": "Refreshed short description.",
            "curators": [],
        }

    monkeypatch.setattr(offchain_metadata, "_fetch_vault_listing_page", fetch_listing_page)
    monkeypatch.setattr(offchain_metadata, "_fetch_vault_detail", fetch_vault_detail)

    vaults = offchain_metadata.fetch_lagoon_vaults_for_chain(ETHEREUM_CHAIN_ID, cache_path=tmp_path, now_=now_)

    assert vaults[FLINT_CHECKSUM_ADDRESS]["description"] == "Refreshed long description."
    assert vaults[FLINT_CHECKSUM_ADDRESS]["short_description"] == "Refreshed short description."


def test_lagoon_metadata_failed_listing_keeps_stale_cache(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Retain descriptions when Lagoon's listing endpoint is unavailable.

    :param tmp_path:
        Temporary cache directory.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    """
    cache_file = offchain_metadata._get_cache_file(tmp_path, ETHEREUM_CHAIN_ID)
    now_ = STARTED_AT
    stale_timestamp = _set_utc_mtime_after_write(
        cache_file,
        {FLINT_CHECKSUM_ADDRESS: _make_metadata("Existing long description.", "Existing short description.")},
        now_ - datetime.timedelta(days=1),
    )

    def fail_listing_page(chain_id: int, **_kwargs: object) -> None:
        """Simulate a transient Lagoon listing request failure."""
        assert chain_id == ETHEREUM_CHAIN_ID

    monkeypatch.setattr(offchain_metadata, "_fetch_vault_listing_page", fail_listing_page)

    vaults = offchain_metadata.fetch_lagoon_vaults_for_chain(ETHEREUM_CHAIN_ID, cache_path=tmp_path, now_=now_)

    assert vaults[FLINT_CHECKSUM_ADDRESS]["description"] == "Existing long description."
    assert cache_file.stat().st_mtime == pytest.approx(stale_timestamp)


def test_lagoon_metadata_corrupt_cache_has_diagnostic(tmp_path: Path) -> None:
    """Raise a diagnostic error for a non-UTF-8 Lagoon cache.

    :param tmp_path:
        Temporary cache directory.
    """
    cache_file = offchain_metadata._get_cache_file(tmp_path, ETHEREUM_CHAIN_ID)
    cache_file.write_bytes(b"\xff")

    with pytest.raises(RuntimeError, match="Could not parse Lagoon vaults file"):
        offchain_metadata._load_cached_vaults(cache_file, ETHEREUM_CHAIN_ID)


def test_lagoon_metadata_failed_detail_keeps_only_matching_stale_entry(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Merge a failed detail request from stale metadata into fresh results.

    :param tmp_path:
        Temporary cache directory.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    """
    cache_file = offchain_metadata._get_cache_file(tmp_path, ETHEREUM_CHAIN_ID)
    now_ = STARTED_AT
    _set_utc_mtime_after_write(
        cache_file,
        {
            FLINT_CHECKSUM_ADDRESS: _make_metadata("Old Flint description.", "Old Flint summary."),
            SECOND_ADDRESS: _make_metadata("Retained description.", "Retained summary."),
        },
        now_ - datetime.timedelta(days=1),
    )

    def fetch_listing_page(_chain_id: int, **_kwargs: object) -> dict[str, object]:
        """Return two Lagoon listing rows."""
        return {
            "vaults": [
                {"address": FLINT_ADDRESS, "chain": {"id": ETHEREUM_CHAIN_ID}},
                {"address": SECOND_ADDRESS, "chain": {"id": ETHEREUM_CHAIN_ID}},
            ],
            "hasNextPage": False,
        }

    def fetch_vault_detail(_chain_id: int, address: HexAddress, **_kwargs: object) -> dict[str, object] | None:
        """Refresh Flint while failing the second detail request."""
        if address == SECOND_ADDRESS:
            return None
        return {"name": "Flint USD", "description": "Fresh description.", "shortDescription": "Fresh summary.", "curators": []}

    monkeypatch.setattr(offchain_metadata, "_fetch_vault_listing_page", fetch_listing_page)
    monkeypatch.setattr(offchain_metadata, "_fetch_vault_detail", fetch_vault_detail)

    vaults = offchain_metadata.fetch_lagoon_vaults_for_chain(ETHEREUM_CHAIN_ID, cache_path=tmp_path, now_=now_)

    assert vaults[FLINT_CHECKSUM_ADDRESS]["description"] == "Fresh description."
    assert vaults[SECOND_ADDRESS]["description"] == "Retained description."
    assert json.loads(cache_file.read_text(encoding="utf-8"))[SECOND_ADDRESS]["description"] == "Retained description."


def test_lagoon_metadata_empty_chain_is_cached(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Cache a successful empty listing instead of retrying it hourly.

    :param tmp_path:
        Temporary cache directory.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    """
    now_ = STARTED_AT
    listing_fetch_count = 0

    def fetch_listing_page(_chain_id: int, **_kwargs: object) -> dict[str, object]:
        """Return a successful empty listing."""
        nonlocal listing_fetch_count
        listing_fetch_count += 1
        return {"vaults": [], "hasNextPage": False}

    monkeypatch.setattr(offchain_metadata, "_fetch_vault_listing_page", fetch_listing_page)

    first = offchain_metadata.fetch_lagoon_vaults_for_chain(ETHEREUM_CHAIN_ID, cache_path=tmp_path, now_=now_)
    cache_file = offchain_metadata._get_cache_file(tmp_path, ETHEREUM_CHAIN_ID)
    _set_utc_mtime(cache_file, now_)
    second = offchain_metadata.fetch_lagoon_vaults_for_chain(ETHEREUM_CHAIN_ID, cache_path=tmp_path, now_=now_ + datetime.timedelta(hours=1))

    assert first == second == {}
    assert json.loads(cache_file.read_text(encoding="utf-8")) == {}
    assert listing_fetch_count == 1


def test_lagoon_metadata_empty_listing_keeps_populated_cache(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Do not erase known descriptions after an unexpectedly empty listing.

    :param tmp_path:
        Temporary cache directory.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    """
    cache_file = offchain_metadata._get_cache_file(tmp_path, ETHEREUM_CHAIN_ID)
    cached_metadata = {FLINT_CHECKSUM_ADDRESS: _make_metadata("Existing description.", "Existing summary.")}
    stale_timestamp = _set_utc_mtime_after_write(cache_file, cached_metadata, STARTED_AT - datetime.timedelta(days=1))

    def fetch_listing_page(_chain_id: int, **_kwargs: object) -> dict[str, object]:
        """Return an unexpectedly empty listing."""
        return {"vaults": [], "hasNextPage": False}

    monkeypatch.setattr(offchain_metadata, "_fetch_vault_listing_page", fetch_listing_page)

    vaults = offchain_metadata.fetch_lagoon_vaults_for_chain(ETHEREUM_CHAIN_ID, cache_path=tmp_path, now_=STARTED_AT)

    assert vaults == cached_metadata
    assert cache_file.stat().st_mtime == pytest.approx(stale_timestamp)


def test_lagoon_metadata_memory_cache_refreshes_after_one_day(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Refresh metadata held by a continuously running scanner after one day.

    :param tmp_path:
        Temporary cache directory.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    """
    started_at = STARTED_AT
    fetch_count = 0

    def fetch_chain_metadata(chain_id: int, cache_path: Path, now_: datetime.datetime, **_kwargs: object) -> dict[HexAddress, offchain_metadata.LagoonVaultMetadata]:
        """Return a different description after each cache refresh."""
        nonlocal fetch_count
        assert chain_id == ETHEREUM_CHAIN_ID
        fetch_count += 1
        result = {FLINT_CHECKSUM_ADDRESS: _make_metadata(f"Long description {fetch_count}.", f"Short description {fetch_count}.")}
        cache_file = offchain_metadata._get_cache_file(cache_path, chain_id)
        _set_utc_mtime_after_write(cache_file, result, now_)
        return result

    monkeypatch.setattr(offchain_metadata, "fetch_lagoon_vaults_for_chain", fetch_chain_metadata)
    monkeypatch.setattr(offchain_metadata, "_cached_vaults", {})
    web3 = _make_web3()

    first = offchain_metadata.fetch_lagoon_vault_metadata(web3, FLINT_ADDRESS, now_=started_at, cache_path=tmp_path)
    cached = offchain_metadata.fetch_lagoon_vault_metadata(web3, FLINT_ADDRESS, now_=started_at + datetime.timedelta(hours=23), cache_path=tmp_path)
    refreshed = offchain_metadata.fetch_lagoon_vault_metadata(web3, FLINT_ADDRESS, now_=started_at + datetime.timedelta(days=1), cache_path=tmp_path)

    assert first is not None and first["description"] == "Long description 1."
    assert cached is not None and cached["description"] == "Long description 1."
    assert refreshed is not None and refreshed["description"] == "Long description 2."
    assert fetch_count == EXPECTED_DAILY_FETCH_COUNT


def test_lagoon_metadata_failed_memory_refresh_retries_after_one_hour(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Retry a failed stale-cache refresh once, not once per vault lookup.

    :param tmp_path:
        Temporary cache directory.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    """
    started_at = STARTED_AT
    stale_metadata = {FLINT_CHECKSUM_ADDRESS: _make_metadata("Stale description.", "Stale summary.")}
    cache_file = offchain_metadata._get_cache_file(tmp_path, ETHEREUM_CHAIN_ID)
    _set_utc_mtime_after_write(cache_file, stale_metadata, started_at - datetime.timedelta(days=1))
    fetch_count = 0

    def fail_refresh(_chain_id: int, **_kwargs: object) -> dict[HexAddress, offchain_metadata.LagoonVaultMetadata]:
        """Return stale metadata without advancing the disk cache time."""
        nonlocal fetch_count
        fetch_count += 1
        return stale_metadata

    monkeypatch.setattr(offchain_metadata, "fetch_lagoon_vaults_for_chain", fail_refresh)
    monkeypatch.setattr(offchain_metadata, "_cached_vaults", {})
    web3 = _make_web3()

    first = offchain_metadata.fetch_lagoon_vault_metadata(web3, FLINT_ADDRESS, now_=started_at, cache_path=tmp_path)
    cached = offchain_metadata.fetch_lagoon_vault_metadata(web3, FLINT_ADDRESS, now_=started_at + datetime.timedelta(minutes=59), cache_path=tmp_path)
    retried = offchain_metadata.fetch_lagoon_vault_metadata(web3, FLINT_ADDRESS, now_=started_at + datetime.timedelta(hours=1), cache_path=tmp_path)

    assert first == cached == retried == stale_metadata[FLINT_CHECKSUM_ADDRESS]
    assert fetch_count == EXPECTED_FAILED_REFRESH_COUNT
