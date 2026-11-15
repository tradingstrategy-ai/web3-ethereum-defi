"""Test T3tris offchain metadata parsing."""

import datetime
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from eth_defi.erc_4626.vault_protocol.t3tris import offchain_metadata
from eth_defi.erc_4626.vault_protocol.t3tris.vault import T3trisVault, create_t3tris_withdrawal_period
from eth_defi.vault.base import VaultSpec, WithdrawalDelayType


class _ResponseStub:
    """Minimal ``requests`` response stub."""

    def __init__(self, payload: dict | None = None, *, status_code: int = 200, headers: dict | None = None):
        self.payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        """Raise a request error for a non-200 status."""
        if self.status_code != 200:
            msg = f"{self.status_code} Client Error"
            raise requests.HTTPError(msg, response=self)

    def json(self) -> dict:
        """Return the configured JSON payload."""
        return self.payload


def test_t3tris_metadata_parses_vault_list_row() -> None:
    """T3tris metadata accepts a raw ``/vaults`` list row."""
    metadata = offchain_metadata._parse_vault_detail(
        {
            "name": "First - USDC",
            "symbol": "1ST-USDC",
            "description": "First Capital vault",
            "curatorName": "First Capital",
            "curatorUrl": "https://example.com",
            "verified": True,
            "depositsDisabled": False,
            "category": "Market neutral",
            "attributes": ["USDC"],
            "rating": "Low",
            "visibility": "public",
            "ipfsHash": "",
        }
    )

    assert metadata["name"] == "First - USDC"
    assert metadata["curator_name"] == "First Capital"
    assert metadata["symbol"] == "1ST-USDC"
    assert metadata["deposits_disabled"] is False


def test_t3tris_metadata_parses_deposit_flag() -> None:
    """The current API reports deposit availability in ``flags.depositEnabled``."""
    metadata = offchain_metadata._parse_vault_detail({"vault": {"name": "Gami USDC", "flags": {"depositEnabled": False}}})

    assert metadata["deposits_disabled"] is True
    assert metadata["rating"] is None


def test_t3tris_metadata_fetches_current_detail_endpoint(monkeypatch) -> None:
    """T3tris metadata uses the detail endpoint used by the live web application."""
    calls: list[str] = []

    def fake_get(url: str, **_kwargs) -> _ResponseStub:
        calls.append(url)
        if url.endswith("/vaults/42161/0x98e43a491a464f0886bc5e57207c340bbed0d01f"):
            return _ResponseStub(
                {
                    "vault": {
                        "name": "First - USDC",
                        "curatorName": "First Capital",
                    }
                }
            )
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr(offchain_metadata.requests, "get", fake_get)

    raw = offchain_metadata._fetch_vault_detail(
        42161,
        "0x98e43a491a464f0886bc5e57207c340bbed0d01f",
    )

    assert raw is not None
    assert raw["vault"]["curatorName"] == "First Capital"
    assert calls == [
        "https://earn.t3tris.finance/api/vaults/42161/0x98e43a491a464f0886bc5e57207c340bbed0d01f",
    ]


def test_t3tris_metadata_falls_back_to_vault_list(monkeypatch) -> None:
    """A failing T3tris detail endpoint falls back to the vault list endpoint."""
    calls: list[str] = []

    def fake_get(url: str, **_kwargs) -> _ResponseStub:
        calls.append(url)
        if url.endswith("/vaults/42161/0x98e43a491a464f0886bc5e57207c340bbed0d01f"):
            return _ResponseStub(status_code=500)
        if url.endswith("/vaults"):
            return _ResponseStub(
                {
                    "items": [
                        {
                            "chainId": 42161,
                            "address": "0x98e43a491a464f0886bc5e57207c340bbed0d01f",
                            "name": "First - USDC",
                            "curatorName": "First Capital",
                        }
                    ]
                }
            )
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr(offchain_metadata.requests, "get", fake_get)

    raw = offchain_metadata._fetch_vault_detail(
        42161,
        "0x98e43a491a464f0886bc5e57207c340bbed0d01f",
    )

    assert raw is not None
    assert raw["curatorName"] == "First Capital"
    assert calls == [
        "https://earn.t3tris.finance/api/vaults/42161/0x98e43a491a464f0886bc5e57207c340bbed0d01f",
        "https://earn.t3tris.finance/api/vaults",
    ]


def test_t3tris_metadata_unlisted_vault_returns_none(monkeypatch, caplog) -> None:
    """A vault the T3tris app does not list returns ``None`` without warnings or a list fetch."""
    calls: list[str] = []

    def fake_get(url: str, **_kwargs) -> _ResponseStub:
        calls.append(url)
        return _ResponseStub({"error": {"message": "vault not found"}}, status_code=404)

    monkeypatch.setattr(offchain_metadata.requests, "get", fake_get)

    with caplog.at_level(logging.WARNING):
        raw = offchain_metadata._fetch_vault_detail(42161, "0x98e43a491a464f0886bc5e57207c340bbed0d01f")

    assert raw is None
    assert calls == ["https://earn.t3tris.finance/api/vaults/42161/0x98e43a491a464f0886bc5e57207c340bbed0d01f"]
    assert not caplog.records


#: ``Date: Sun, 15 Nov 2026 17:59:44 GMT`` as a Unix timestamp.
RESPONSE_TS = 1794765584


@pytest.mark.parametrize(
    ("estimated_redemption_ts", "expected_seconds"),
    [
        # First USDC on 2026-11-15: about 5.5 days.
        (RESPONSE_TS + 479_171, 479_171),
        # Kingfisher: two same-block settlements give a near-zero average.
        (RESPONSE_TS + 20, None),
        (None, None),
    ],
)
def test_t3tris_average_settlement_interval(monkeypatch: pytest.MonkeyPatch, estimated_redemption_ts: int | None, expected_seconds: int | None) -> None:
    """The detail response's estimate becomes an interval measured from the server's ``Date``.

    1. Serve a detail response with a ``Date`` header.
    2. Parse it into metadata and the withdrawal period.
    """
    # 1. Serve a detail response with a Date header.
    payload = {"vault": {"name": "First - USDC"}, "estimatedRedemptionTs": estimated_redemption_ts}
    monkeypatch.setattr(offchain_metadata.requests, "get", lambda url, **_kwargs: _ResponseStub(payload, headers={"Date": "Sun, 15 Nov 2026 17:59:44 GMT"}))

    # 2. Parse it into metadata and the withdrawal period.
    raw = offchain_metadata._fetch_vault_detail(42161, "0x98e43a491a464f0886bc5e57207c340bbed0d01f")
    metadata = offchain_metadata._parse_vault_detail(raw)
    assert metadata["average_settlement_seconds"] == expected_seconds
    period = create_t3tris_withdrawal_period(metadata["average_settlement_seconds"])
    if expected_seconds is None:
        assert period is None
    else:
        assert period.estimated_settlement == datetime.timedelta(seconds=expected_seconds)
        assert period.min_period is None
        assert period.max_period is None
        assert period.delay_type is WithdrawalDelayType.delay


def test_t3tris_vault_list_row_has_no_settlement_interval() -> None:
    """The vault-list fallback carries no estimate."""
    metadata = offchain_metadata._parse_vault_detail({"chainId": 42161, "name": "First - USDC", "lastSettlementTs": "1794719970"})

    assert metadata["average_settlement_seconds"] is None


@pytest.mark.parametrize(("average_settlement_seconds", "expected"), [(479_171, datetime.timedelta(seconds=479_171)), (None, None)])
def test_t3tris_vault_and_manager_publish_settlement_estimate(average_settlement_seconds: int | None, expected: datetime.timedelta | None) -> None:
    """The adapter and its manager expose the parsed interval as the estimate.

    Older cache files without the key also give no estimate.
    """
    vault = T3trisVault(None, VaultSpec(42161, "0x98e43a491a464f0886bc5e57207c340bbed0d01f"))
    metadata = {"name": "First - USDC"}
    if average_settlement_seconds is not None:
        metadata["average_settlement_seconds"] = average_settlement_seconds
    vault.__dict__["t3tris_metadata"] = metadata

    period = vault.get_withdrawal_period()
    assert (period.estimated_settlement if period else None) == expected
    assert vault.get_deposit_manager().get_estimated_lock_up() == expected


FIRST_USDC = "0x98e43a491a464f0886bc5e57207c340bbed0d01f"


def test_t3tris_cache_without_estimate_key_is_refreshed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh cache file written before the estimate existed is fetched again.

    1. Seed a current cache file without ``average_settlement_seconds``.
    2. The fetcher refreshes it and writes the estimate.
    """
    monkeypatch.setattr(offchain_metadata, "_cached_vaults", {})
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))
    file = tmp_path / f"t3tris_vault_42161_{FIRST_USDC}.json"

    # 1. Seed a current cache file without average_settlement_seconds.
    file.write_text(json.dumps({"name": "First - USDC"}))
    payload = {"vault": {"name": "First - USDC"}, "estimatedRedemptionTs": RESPONSE_TS + 479_171}
    monkeypatch.setattr(offchain_metadata.requests, "get", lambda url, **_kwargs: _ResponseStub(payload, headers={"Date": "Sun, 15 Nov 2026 17:59:44 GMT"}))

    # 2. The fetcher refreshes it and writes the estimate.
    metadata = offchain_metadata.fetch_t3tris_vault_metadata(web3, FIRST_USDC, cache_path=tmp_path)
    assert metadata["average_settlement_seconds"] == 479_171
    assert json.loads(file.read_text())["average_settlement_seconds"] == 479_171


def test_t3tris_vault_list_fallback_is_not_cached_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A vault-list fallback row, which has no estimate, is not written to disk."""
    monkeypatch.setattr(offchain_metadata, "_cached_vaults", {})
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))

    def fake_get(url: str, **_kwargs) -> _ResponseStub:
        if url.endswith("/vaults"):
            return _ResponseStub({"items": [{"chainId": 42161, "address": FIRST_USDC, "name": "First - USDC"}]})
        return _ResponseStub(status_code=500)

    monkeypatch.setattr(offchain_metadata.requests, "get", fake_get)

    metadata = offchain_metadata.fetch_t3tris_vault_metadata(web3, FIRST_USDC, cache_path=tmp_path)
    assert metadata["name"] == "First - USDC"
    assert metadata["average_settlement_seconds"] is None
    assert not (tmp_path / f"t3tris_vault_42161_{FIRST_USDC}.json").exists()
