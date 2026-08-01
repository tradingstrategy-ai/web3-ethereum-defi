"""Tests for Asseto registry cache and runtime preparation."""

import datetime
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from eth_defi.erc_4626 import classification
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.tokenised_fund.asseto import offchain_metadata, registry
from eth_defi.tokenised_fund.asseto.constants import ASSETO_PRODUCTS, ASSETO_PRODUCTS_BY_TOKEN, install_asseto_runtime_products
from eth_defi.tokenised_fund.asseto.offchain_api import AssetoAPIError, AssetoOffchainProduct
from eth_defi.tokenised_fund.asseto.offchain_metadata import AssetoOffchainRegistryResult
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

NOW = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC).replace(tzinfo=None)


def make_product() -> AssetoOffchainProduct:
    """Create one deterministic public Asseto registry product."""

    return AssetoOffchainProduct(
        product_id=42,
        product_name="BCAP",
        full_name="Asseto test fund",
        symbol="BCAP",
        product_type="uda",
        chain_id=1,
        chain_name="Ethereum",
        contract_address="0x78e80da0616887b46a31f39310c2a8b0fbd6a42d",
        denomination_symbol="USDC",
        denomination_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        tvl=None,
        apy=None,
        introduction="Asseto supplied description",
        protocol=None,
    )


def test_asseto_registry_cache_survives_api_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed refresh preserves the validated cached product identity."""

    product = make_product()
    cache_path = tmp_path / "registry.json"
    now_ = NOW
    monkeypatch.setattr(offchain_metadata, "fetch_asseto_products", lambda: iter([product]))

    fresh = offchain_metadata.fetch_asseto_registry(cache_path=cache_path, now_=now_)

    assert fresh.status == "fresh"
    assert fresh.products == (product,)

    def fail_fetch():
        """Raise a deterministic upstream error."""

        message = "upstream unavailable"
        raise AssetoAPIError(message)

    monkeypatch.setattr(offchain_metadata, "fetch_asseto_products", fail_fetch)
    stale = offchain_metadata.fetch_asseto_registry(cache_path=cache_path, now_=now_ + datetime.timedelta(days=1))

    assert stale.status == "stale"
    assert stale.products == (product,)
    assert "upstream unavailable" in (stale.diagnostics or "")


def test_asseto_registry_rejects_empty_response_without_poisoning_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty API response falls back to the previous complete snapshot."""

    product = make_product()
    cache_path = tmp_path / "registry.json"
    monkeypatch.setattr(offchain_metadata, "fetch_asseto_products", lambda: iter([product]))
    offchain_metadata.fetch_asseto_registry(cache_path=cache_path, now_=NOW)
    monkeypatch.setattr(offchain_metadata, "fetch_asseto_products", lambda: iter(()))

    result = offchain_metadata.fetch_asseto_registry(cache_path=cache_path, now_=NOW + datetime.timedelta(days=1))

    assert result.status == "stale"
    assert result.products == (product,)


def test_asseto_runtime_product_preserves_non_usd_exchange_rates() -> None:
    """A scheduled non-USD product receives the same FX history as a backfill."""

    product = replace(make_product(), denomination_symbol="HKD")
    rates = ((1_700_000_000, Decimal("7.8")),)

    runtime_product = registry.create_asseto_runtime_product(product, 12_345, NOW, rates)

    assert runtime_product.denomination_symbol == "HKD"
    assert runtime_product.usd_exchange_rates == rates


def test_asseto_runtime_product_is_classified_without_stale_derived_mapping() -> None:
    """A newly installed product is immediately recognised on its own chain."""

    product = registry.create_asseto_runtime_product(make_product(), 12_345, NOW)
    key = (product.chain_id, product.token)
    previous = ASSETO_PRODUCTS.get(key)
    previous_by_token = ASSETO_PRODUCTS_BY_TOKEN.get(product.token)
    try:
        install_asseto_runtime_products([product])

        assert classification._get_hardcoded_protocol_features(product.token, product.chain_id) == {ERC4626Feature.asseto_like}
        assert classification._get_hardcoded_protocol_features(product.token, 56) is None
    finally:
        if previous is None:
            ASSETO_PRODUCTS.pop(key, None)
        else:
            ASSETO_PRODUCTS[key] = previous
        if previous_by_token is None:
            ASSETO_PRODUCTS_BY_TOKEN.pop(product.token, None)
        else:
            ASSETO_PRODUCTS_BY_TOKEN[product.token] = previous_by_token


def test_asseto_runtime_registry_rebuilds_existing_adapter_without_metadata_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale snapshot rebuilds a restarted process without changing vault rows."""

    product = replace(make_product(), denomination_symbol="HKD")
    spec = VaultSpec(product.chain_id, product.contract_address)
    detection = SimpleNamespace(first_seen_at_block=12_345, first_seen_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None))
    vault_db_path = tmp_path / "vaults.pickle"
    vault_db = VaultDatabase(rows={spec: {"_detection_data": detection, "Description": "stored metadata"}})
    vault_db.write(vault_db_path)
    monkeypatch.setattr(
        registry,
        "fetch_asseto_registry",
        lambda **_kwargs: AssetoOffchainRegistryResult("stale", (product,), NOW, "offline"),
    )
    monkeypatch.setattr(registry, "load_usd_exchange_rates", lambda *_args: {"HKD": ((1_700_000_000, Decimal("7.8")),)})
    key = (product.chain_id, product.contract_address)
    previous = ASSETO_PRODUCTS.get(key)
    previous_by_token = ASSETO_PRODUCTS_BY_TOKEN.get(product.contract_address)
    try:
        result = registry.fetch_asseto_registry_preparation(vault_db_path=vault_db_path, enabled_chain_ids=frozenset({1}), cache_path=tmp_path / "registry.json")

        assert result.status == "stale"
        assert result.runtime_product_count == 1
        assert result.registered_product_count == 0
        assert ASSETO_PRODUCTS[key].offchain_product_id == product.product_id
        assert ASSETO_PRODUCTS[key].usd_exchange_rates == ((1_700_000_000, Decimal("7.8")),)
        assert VaultDatabase.read(vault_db_path).rows[spec]["Description"] == "stored metadata"
    finally:
        if previous is None:
            ASSETO_PRODUCTS.pop(key, None)
        else:
            ASSETO_PRODUCTS[key] = previous
        if previous_by_token is None:
            ASSETO_PRODUCTS_BY_TOKEN.pop(product.contract_address, None)
        else:
            ASSETO_PRODUCTS_BY_TOKEN[product.contract_address] = previous_by_token


def test_fresh_asseto_registry_updates_existing_description(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh API descriptions replace only the corresponding existing metadata."""

    product = make_product()
    spec = VaultSpec(product.chain_id, product.contract_address)
    detection = SimpleNamespace(first_seen_at_block=12_345, first_seen_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None))
    vault_db_path = tmp_path / "vaults.pickle"
    VaultDatabase(rows={spec: {"_detection_data": detection, "_description": "old description", "_short_description": "old description"}}).write(vault_db_path)
    monkeypatch.setattr(
        registry,
        "fetch_asseto_registry",
        lambda **_kwargs: AssetoOffchainRegistryResult("fresh", (product,), NOW),
    )
    key = (product.chain_id, product.contract_address)
    previous = ASSETO_PRODUCTS.get(key)
    previous_by_token = ASSETO_PRODUCTS_BY_TOKEN.get(product.contract_address)
    try:
        registry.fetch_asseto_registry_preparation(vault_db_path=vault_db_path, enabled_chain_ids=frozenset({1}), cache_path=tmp_path / "registry.json")

        refreshed_row = VaultDatabase.read(vault_db_path).rows[spec]
        assert refreshed_row["_description"] == "Asseto supplied description"
        assert refreshed_row["_short_description"] == "Asseto supplied description"
    finally:
        if previous is None:
            ASSETO_PRODUCTS.pop(key, None)
        else:
            ASSETO_PRODUCTS[key] = previous
        if previous_by_token is None:
            ASSETO_PRODUCTS_BY_TOKEN.pop(product.contract_address, None)
        else:
            ASSETO_PRODUCTS_BY_TOKEN[product.contract_address] = previous_by_token
