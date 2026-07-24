"""Test Symbiotic vault metadata fetched from its official public API."""

import base64
import datetime
import json
from pathlib import Path

import pytest

import eth_defi.erc_4626.vault_protocol.symbiotic.offchain_metadata as symbiotic_metadata
from eth_defi.erc_4626.vault_protocol.symbiotic.offchain_metadata import fetch_symbiotic_vault_metadata

SYMBIOTIC_VAULT_ADDRESS = "0x007e0b8e99c6134e81a1eaae754460e3202cb671"
HTTP_CLIENT_ERROR_STATUS = 400
EXPECTED_VAULT_AND_CURATOR_REQUESTS = 2
TEST_NOW = datetime.datetime(2026, 7, 24, tzinfo=datetime.UTC).replace(tzinfo=None)


class _FakeResponse:
    """Minimal successful GitHub contents API response."""

    def __init__(self, payload: dict | None = None, status_code: int = 200) -> None:
        """Create an API response carrying JSON payload data.

        :param payload:
            JSON data returned by the contents API.
        :param status_code:
            HTTP status code returned by the contents API.
        """
        self.payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        """Raise if this fake response represents an error."""
        if self.status_code >= HTTP_CLIENT_ERROR_STATUS:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        """Return the supplied JSON payload."""
        return self.payload


def _contents_response(data: dict) -> _FakeResponse:
    """Create a GitHub contents response for an ``info.json`` dictionary."""
    encoded = base64.b64encode(json.dumps(data).encode("utf-8")).decode("ascii")
    return _FakeResponse({"encoding": "base64", "content": encoded})


def test_fetch_symbiotic_vault_metadata_joins_curator_and_uses_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Fetch a vault description, curator name, links and then reuse the disk cache."""
    calls: list[str] = []

    def fake_get(url: str, **_kwargs) -> _FakeResponse:
        """Return deterministic official vault and curator metadata records."""
        calls.append(url)
        if url.endswith("/vaults/0x007e0B8E99c6134E81A1eAAE754460E3202cB671/info.json"):
            return _contents_response(
                {
                    "name": "Keyrock Flagship USDC",
                    "description": "A USDC vault using lending strategies. Capital can be allocated to adapters.",
                    "tags": ["usdc", "credit"],
                    "links": [{"type": "website", "name": "Website", "url": "https://keyrock.com/"}],
                    "curatorId": "keyrock",
                }
            )
        if url.endswith("/curators/keyrock/info.json"):
            return _contents_response(
                {
                    "name": "Keyrock",
                    "description": "A digital-asset market maker.",
                    "links": [{"type": "externalLink", "name": "X", "url": "https://x.com/keyrock"}],
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(symbiotic_metadata.requests, "get", fake_get)
    metadata = fetch_symbiotic_vault_metadata(
        SYMBIOTIC_VAULT_ADDRESS,
        cache_path=tmp_path,
        api_base_url="https://api.example/contents",
        now_=TEST_NOW,
    )

    assert metadata is not None
    assert metadata["name"] == "Keyrock Flagship USDC"
    assert metadata["description"] == "A USDC vault using lending strategies. Capital can be allocated to adapters."
    assert metadata["tags"] == ["usdc", "credit"]
    assert metadata["links"][0]["url"] == "https://keyrock.com/"
    assert metadata["curator_id"] == "keyrock"
    assert metadata["curator_name"] == "Keyrock"
    assert metadata["curator_links"][0]["name"] == "X"
    assert len(calls) == EXPECTED_VAULT_AND_CURATOR_REQUESTS

    def fail_get(url: str, **_kwargs) -> _FakeResponse:
        """Ensure the second request reads cache rather than the network."""
        raise AssertionError(f"Unexpected cached request: {url}")

    monkeypatch.setattr(symbiotic_metadata.requests, "get", fail_get)
    assert (
        fetch_symbiotic_vault_metadata(
            SYMBIOTIC_VAULT_ADDRESS,
            cache_path=tmp_path,
            api_base_url="https://api.example/contents",
            now_=TEST_NOW,
        )
        == metadata
    )


def test_fetch_symbiotic_vault_metadata_caches_missing_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cache a known missing vault so scans do not repeatedly request it."""
    calls = 0

    def fake_get(_url: str, **_kwargs) -> _FakeResponse:
        """Return an API 404 for an unregistered vault."""
        nonlocal calls
        calls += 1
        return _FakeResponse(status_code=404)

    monkeypatch.setattr(symbiotic_metadata.requests, "get", fake_get)
    assert fetch_symbiotic_vault_metadata(SYMBIOTIC_VAULT_ADDRESS, cache_path=tmp_path, api_base_url="https://api.example/contents", now_=TEST_NOW) is None
    assert fetch_symbiotic_vault_metadata(SYMBIOTIC_VAULT_ADDRESS, cache_path=tmp_path, api_base_url="https://api.example/contents", now_=TEST_NOW) is None
    assert calls == 1
