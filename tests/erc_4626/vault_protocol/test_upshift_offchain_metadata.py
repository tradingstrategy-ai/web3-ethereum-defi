"""Unit tests for Upshift public vault metadata."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from web3 import Web3

import eth_defi.erc_4626.vault_protocol.upshift.offchain_metadata as upshift_metadata_module
from eth_defi.erc_4626.vault_protocol.upshift.offchain_metadata import UpshiftVaultAPIResponse, fetch_upshift_vault_metadata
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault

UPSHIFT_NEMO_VAULT = "0x955256B31097dDf47a9E47A95aDfDFB4460D8522"

OBSERVED_UPSHIFT_API_RESPONSE_FIELDS = frozenset(
    {
        "address",
        "apy_override",
        "cached_at",
        "campaign_apy",
        "chain",
        "chain_type",
        "composability_integrations",
        "daily_pnl_per_share",
        "default_apy_horizon",
        "description",
        "enable_external_assets_update",
        "enabled_historical_price_horizons",
        "eoa_operators",
        "hardcoded_strategists",
        "historical_apy",
        "historical_compound_apy",
        "historical_snapshots",
        "id",
        "instant_redeem_config",
        "internal_type",
        "is_charge_fees_manual",
        "is_distributor_fee_wrapper",
        "is_featured",
        "is_spotlighted",
        "is_visible",
        "latest_reported_tvl",
        "management_fee_waived_until_date",
        "management_fee_waived_until_tvl",
        "max_daily_drawdown",
        "max_drawdown",
        "metrics_last_updated",
        "nav_base_asset_token_id",
        "nav_pricing_overrides",
        "operators",
        "performance_fee_waived_until_date",
        "performance_fee_waived_until_tvl",
        "platform_fee_override",
        "pnl_per_share",
        "public_type",
        "receipt_token_integrations",
        "receipt_token_symbol",
        "reported_apy",
        "reserve_target",
        "reserve_tolerance",
        "rewards",
        "risk",
        "show_cap_filled",
        "solana_vault_metadata",
        "start_datetime",
        "status",
        "stellar_vault_metadata",
        "subaccounts",
        "tvl",
        "vault_logo_url",
        "vault_name",
        "view_type",
        "weekly_performance_fee_bps",
        "withdrawal_alert_channels",
        "withdrawal_alert_threshold",
        "withdrawal_only",
        "yield_distributor",
    }
)

NEMO_API_RESPONSE = {
    "address": UPSHIFT_NEMO_VAULT,
    "chain": 1,
    "description": "NEMO USDC Prime is an automated, multi-strategy, market-neutral vault.",
    "eoa_operators": [
        {
            "address": "0xfb1898bB5955FdD11704e397104c6a0e0725EB17",
            "name": "NEMO USDC Yield Sub 1",
            "wallet_role": None,
        }
    ],
    "hardcoded_strategists": [
        {
            "strategist_name": "NEMO",
            "strategist_logo": "https://example.invalid/nemo.png",
            "website_url": None,
        }
    ],
    "internal_type": "multiAssetVault",
    "is_visible": True,
    "status": "active",
    "vault_name": "NEMO USDC Yield",
}


class _FakeResponse:
    """Minimal successful ``requests`` response for metadata tests."""

    def __init__(self, payload: dict) -> None:
        """Create a JSON response wrapper.

        :param payload:
            Response object to serialise as JSON.
        """

        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        """Model a successful HTTP response."""


def test_upshift_api_response_typed_dict_documents_every_observed_field() -> None:
    """Keep the complete TypedDict aligned with the live Upshift response.

    The reference field set was collected from the NEMO USDC Prime API response
    on 2026-07-23. It covers fields that are irrelevant to the current vault
    adapter as well as the description and strategist fields it consumes.
    """

    assert set(UpshiftVaultAPIResponse.__required_keys__) == OBSERVED_UPSHIFT_API_RESPONSE_FIELDS


def test_fetch_upshift_vault_metadata_caches_strategist_and_operator_names(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Parse NEMO strategist data without inventing a curator field.

    Upshift's API has no curator property. Its ``hardcoded_strategists`` is
    retained as a strategist identity while operator wallets remain distinct.

    :param monkeypatch:
        Pytest request patch helper.

    :param tmp_path:
        Isolated cache directory.
    """

    requested_urls: list[str] = []

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        requested_urls.append(url)
        assert timeout == upshift_metadata_module.DEFAULT_TIMEOUT
        return _FakeResponse(NEMO_API_RESPONSE)

    monkeypatch.setattr(upshift_metadata_module.requests, "get", fake_get)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))

    metadata = fetch_upshift_vault_metadata(web3, UPSHIFT_NEMO_VAULT, cache_path=tmp_path)

    assert metadata is not None
    assert metadata["vault_address"] == Web3.to_checksum_address(UPSHIFT_NEMO_VAULT)
    assert metadata["name"] == "NEMO USDC Yield"
    assert metadata["description"] == NEMO_API_RESPONSE["description"]
    assert metadata["strategist_names"] == ("NEMO",)
    assert metadata["operator_names"] == ("NEMO USDC Yield Sub 1",)
    assert "curator_names" not in metadata
    assert requested_urls == [f"{upshift_metadata_module.DEFAULT_API_BASE_URL}/v1/tokenized_vaults/{UPSHIFT_NEMO_VAULT}"]

    cache_file = tmp_path / f"upshift_vault_1_{UPSHIFT_NEMO_VAULT.lower()}.json"
    assert cache_file.exists()

    def fail_get(*_args: object, **_kwargs: object) -> None:
        message = "Fresh cache must not call the Upshift API"
        raise AssertionError(message)

    monkeypatch.setattr(upshift_metadata_module.requests, "get", fail_get)
    cached_metadata = fetch_upshift_vault_metadata(web3, UPSHIFT_NEMO_VAULT, cache_path=tmp_path)

    assert cached_metadata == metadata


def test_upshift_vault_uses_strategist_as_manager_not_curator() -> None:
    """Expose the API strategist as a generic manager identity.

    The scan pipeline stores ``manager_name`` separately from the curator
    registry, allowing a later verified mapping without falsely asserting that
    Upshift publishes a curator.
    """

    vault = object.__new__(UpshiftVault)
    vault.__dict__["upshift_metadata"] = {
        "chain_id": 1,
        "vault_address": Web3.to_checksum_address(UPSHIFT_NEMO_VAULT),
        "name": "NEMO USDC Yield",
        "description": NEMO_API_RESPONSE["description"],
        "strategist_names": ("NEMO",),
        "operator_names": ("NEMO USDC Yield Sub 1",),
        "status": "active",
        "internal_type": "multiAssetVault",
        "is_visible": True,
    }

    assert vault.description == NEMO_API_RESPONSE["description"]
    assert vault.short_description == NEMO_API_RESPONSE["description"]
    assert vault.fetch_strategist() == "NEMO"
    assert vault.manager_name == "NEMO"


def test_upshift_vault_derives_short_description_from_metadata() -> None:
    """Use the first sentence when Upshift metadata has a long description."""

    vault = object.__new__(UpshiftVault)
    vault.__dict__["upshift_metadata"] = {
        "chain_id": 1,
        "vault_address": Web3.to_checksum_address(UPSHIFT_NEMO_VAULT),
        "name": "NEMO USDC Yield",
        "description": "Automated market-neutral yield strategy. Strategies are actively rebalanced.",
        "strategist_names": ("NEMO",),
        "operator_names": (),
        "status": "active",
        "internal_type": "multiAssetVault",
        "is_visible": True,
    }

    assert vault.description == "Automated market-neutral yield strategy. Strategies are actively rebalanced."
    assert vault.short_description == "Automated market-neutral yield strategy."
