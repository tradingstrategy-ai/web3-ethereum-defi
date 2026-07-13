"""Test T3tris offchain metadata parsing."""

import requests

from eth_defi.erc_4626.vault_protocol.t3tris import offchain_metadata


class _ResponseStub:
    """Minimal ``requests`` response stub."""

    def __init__(self, payload: dict | None = None, *, should_fail: bool = False):
        self.payload = payload or {}
        self.should_fail = should_fail

    def raise_for_status(self) -> None:
        """Raise a request error when configured."""
        if self.should_fail:
            msg = "404 Client Error"
            raise requests.HTTPError(msg)

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


def test_t3tris_metadata_falls_back_to_vault_list(monkeypatch) -> None:
    """Broken T3tris detail endpoint falls back to the vault list endpoint."""
    calls: list[str] = []

    def fake_get(url: str, **_kwargs) -> _ResponseStub:
        calls.append(url)
        if url.endswith("/pages/vault/42161/0x98e43a491a464f0886bc5e57207c340bbed0d01f"):
            return _ResponseStub(should_fail=True)
        if url.endswith("/vaults"):
            return _ResponseStub(
                {
                    "vaults": [
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
        "https://api.t3tris.finance/api/v1/pages/vault/42161/0x98e43a491a464f0886bc5e57207c340bbed0d01f",
        "https://api.t3tris.finance/api/v1/vaults",
    ]
