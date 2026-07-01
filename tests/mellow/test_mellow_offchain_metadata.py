"""Test Mellow offchain metadata API parsing and caching."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

from eth_defi.mellow.offchain_metadata import fetch_mellow_api_vaults

LIDO_EARN_USD_VAULT = "0x014e6DA8F283C4aF65B2AA0f201438680A004452"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def test_fetch_mellow_api_vaults_parses_and_caches(monkeypatch, tmp_path) -> None:
    """Parse Mellow public API metadata and reuse the disk cache.

    Mellow API USD TVL is off-chain metadata. The parser keeps it
    available for diagnostics while the adapter reads comparable TVL from
    on-chain share price and supply.
    """

    raw_vaults = [
        {
            "chain_id": 1,
            "address": LIDO_EARN_USD_VAULT,
            "symbol": "earnUSD",
            "name": "Lido Earn USD",
            "layer": "mellow",
            "tvl_usd": "21897383.50",
            "base_token": {
                "symbol": "USDC",
                "address": USDC,
            },
        }
    ]
    calls = []

    def fake_get(url: str, **kwargs):
        """Return a Mellow API response fixture."""

        calls.append((url, kwargs))
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: raw_vaults,
        )

    monkeypatch.setattr("eth_defi.mellow.offchain_metadata.requests.get", fake_get)

    api_vaults = fetch_mellow_api_vaults(cache_path=tmp_path, api_base_url="https://example.invalid")
    api_vault = api_vaults[1, LIDO_EARN_USD_VAULT.lower()]

    assert api_vault.address == LIDO_EARN_USD_VAULT
    assert api_vault.name == "Lido Earn USD"
    assert api_vault.symbol == "earnUSD"
    assert api_vault.layer == "mellow"
    assert api_vault.tvl_usd == Decimal("21897383.50")
    assert api_vault.base_token_address == USDC
    assert api_vault.base_token_symbol == "USDC"
    assert len(calls) == 1

    cached_vaults = fetch_mellow_api_vaults(
        cache_path=tmp_path,
        api_base_url="https://should-not-be-called.invalid",
        max_cache_duration=datetime.timedelta(days=36500),
    )

    assert cached_vaults[1, LIDO_EARN_USD_VAULT.lower()] == api_vault
    assert len(calls) == 1
