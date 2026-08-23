"""Test Enzyme API description collection and safe fallback copy."""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from eth_typing import HexAddress

from eth_defi.enzyme import offchain_metadata
from eth_defi.enzyme.offchain_metadata import (
    ENZYME_API_VAULT_URL,
    EnzymeVaultMetadata,
    create_enzyme_blue_fallback_metadata,
    create_enzyme_vault_link,
    fetch_enzyme_api_vault_metadata,
    load_enzyme_blue_vault_metadata,
    load_enzyme_vault_metadata_cache,
    parse_enzyme_api_vault_metadata,
    resolve_enzyme_blue_vault_metadata,
    write_enzyme_vault_metadata_cache,
)
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

VAULT_ADDRESS = "0x000000000000000000000000000000000000bEEF"
MIGRATION_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "enzyme" / "migrate-offchain-metadata.py"
BLUE_SPEC = VaultSpec(1, VAULT_ADDRESS)
BLUE_METADATA_KEY = (BLUE_SPEC.chain_id, HexAddress(BLUE_SPEC.vault_address.lower()))
ONYX_SPEC = VaultSpec(8453, "0x000000000000000000000000000000000000c0Fe")


def load_offchain_metadata_migration_module() -> ModuleType:
    """Load the hyphenated Enzyme migration script as a Python module.

    :return: Imported migration module.
    """

    spec = importlib.util.spec_from_file_location("enzyme_migrate_offchain_metadata", MIGRATION_SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_enzyme_blue_fallback_has_complete_listing_copy() -> None:
    """Provide generic Blue copy when the official API has no manager text."""

    metadata = create_enzyme_blue_fallback_metadata("Example vault")

    assert metadata.short_description
    assert metadata.description
    assert "Example vault" in metadata.description


def test_curated_enzyme_copy_overrides_only_the_provided_fields() -> None:
    """Retain the fallback field when a curator supplies partial metadata."""

    metadata = resolve_enzyme_blue_vault_metadata(
        "Example vault",
        EnzymeVaultMetadata(description="Curator-supplied strategy narrative."),
    )

    assert metadata.description == "Curator-supplied strategy narrative."
    assert metadata.short_description == "Enzyme Blue tokenised digital-asset investment vehicle."


def test_load_enzyme_blue_metadata_merges_curated_and_cached_fields(monkeypatch) -> None:
    """Retain complementary manager copy from both supported metadata sources."""

    monkeypatch.setattr(
        offchain_metadata,
        "ENZYME_BLUE_VAULT_METADATA",
        {BLUE_METADATA_KEY: EnzymeVaultMetadata(description="Curated strategy narrative.")},
    )
    monkeypatch.setattr(
        offchain_metadata,
        "_cached_enzyme_vault_metadata",
        {BLUE_METADATA_KEY: EnzymeVaultMetadata(short_description="Official tagline")},
    )

    assert load_enzyme_blue_vault_metadata(*BLUE_METADATA_KEY) == EnzymeVaultMetadata(
        short_description="Official tagline",
        description="Curated strategy narrative.",
    )


@pytest.mark.parametrize(
    ("chain_id", "network"),
    [(1, "ethereum"), (137, "polygon"), (8453, "base"), (42161, "arbitrum")],
)
def test_enzyme_vault_link_uses_address_and_network(chain_id: int, network: str) -> None:
    """Create one canonical direct URL format for Blue and Onyx adapters."""

    assert create_enzyme_vault_link(chain_id, VAULT_ADDRESS) == f"https://app.enzyme.finance/vault/0x000000000000000000000000000000000000bEEF?network={network}"


def test_enzyme_vault_link_rejects_unsupported_network() -> None:
    """Fail explicitly instead of publishing a malformed unknown-chain URL."""

    with pytest.raises(ValueError, match="Unsupported Enzyme chain"):
        create_enzyme_vault_link(999_999, VAULT_ADDRESS)


def test_parse_enzyme_api_vault_metadata_uses_only_vault_copy() -> None:
    """Map official tagline and description without treating manager bio as strategy text."""

    metadata = parse_enzyme_api_vault_metadata(
        {
            "tagline": "  Delta-neutral ETH strategy  ",
            "description": "  Trades ETH spot and derivatives.  ",
            "managerDescription": "A manager biography, not vault product copy.",
        }
    )

    assert metadata.short_description == "Delta-neutral ETH strategy"
    assert metadata.description == "Trades ETH spot and derivatives."
    assert metadata.manager_name is None


def test_parse_enzyme_api_vault_metadata_rejects_invalid_response() -> None:
    """Reject a malformed response before it can overwrite cached copy."""

    with pytest.raises(ValueError, match="JSON object"):
        parse_enzyme_api_vault_metadata([])


def test_fetch_enzyme_api_vault_metadata_uses_official_connect_endpoint() -> None:
    """Send the documented deployment, address and USD currency request fields."""

    requests = []

    class Response:
        """Minimal successful API response."""

        def raise_for_status(self) -> None:
            """Act as a successful HTTP response."""

        def json(self) -> dict[str, str]:  # noqa: PLR6301 - Matches the requests response interface.
            """Return official API metadata fields."""

            return {"tagline": "Income", "description": "Income strategy"}

    class Session:
        """Record the request made by the collector."""

        def post(self, *args, **kwargs) -> Response:  # noqa: PLR6301 - Matches the requests session interface.
            """Capture request details and return fixture response."""

            requests.append((args, kwargs))
            return Response()

    metadata = fetch_enzyme_api_vault_metadata(
        Session(),
        chain_id=1,
        shares_address=VAULT_ADDRESS,
        api_token="test-token",
        timeout=20,
    )

    assert metadata == EnzymeVaultMetadata(short_description="Income", description="Income strategy")
    args, kwargs = requests[0]
    assert args == (ENZYME_API_VAULT_URL,)
    assert kwargs["json"] == {
        "deployment": "DEPLOYMENT_ETHEREUM",
        "address": "0x000000000000000000000000000000000000bEEF",
        "currency": "CURRENCY_USD",
    }
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert kwargs["headers"]["Connect-Protocol-Version"] == "1"


def test_enzyme_metadata_cache_round_trip_retains_empty_api_reply(tmp_path) -> None:
    """Keep a valid empty reply distinct from an absent or broken cache entry."""

    cache_path = tmp_path / "enzyme-vault-metadata.json"
    metadata = {
        (1, VAULT_ADDRESS.lower()): EnzymeVaultMetadata(short_description="Income", description="Income strategy"),
        (137, "0x000000000000000000000000000000000000c0fe"): EnzymeVaultMetadata(),
    }

    write_enzyme_vault_metadata_cache(metadata, cache_path)
    loaded = load_enzyme_vault_metadata_cache(cache_path)

    assert loaded == metadata


def test_offchain_metadata_migration_changes_only_existing_enzyme_blue_copy() -> None:
    """Apply official fields to Blue without changing Onyx or unrelated metadata."""

    module = load_offchain_metadata_migration_module()
    vault_db = VaultDatabase(
        rows={
            BLUE_SPEC: {
                "Protocol": "Enzyme",
                "Name": "Official Blue vault",
                "_detection_data": SimpleNamespace(features={ERC4626Feature.enzyme_blue_like}),
                "_short_description": "Old short copy",
                "_description": "Old long copy",
                "unrelated_field": "preserved",
            },
            ONYX_SPEC: {
                "Protocol": "Enzyme",
                "Name": "Onyx vault",
                "_detection_data": SimpleNamespace(features={ERC4626Feature.enzyme_onyx_like}),
                "_short_description": "Keep Onyx fallback",
                "_description": "Keep Onyx fallback detail",
            },
        }
    )

    selected_rows = list(module.iter_enzyme_blue_rows(vault_db))
    assert selected_rows == [(BLUE_SPEC, vault_db.rows[BLUE_SPEC])]

    update = module.create_metadata_update(
        BLUE_SPEC,
        vault_db.rows[BLUE_SPEC],
        EnzymeVaultMetadata(short_description="Official tagline", description="Official strategy description"),
    )
    assert update.changed_fields == ("_short_description", "_description")

    module.apply_metadata_updates(vault_db, [update])

    assert vault_db.rows[BLUE_SPEC]["_short_description"] == "Official tagline"
    assert vault_db.rows[BLUE_SPEC]["_description"] == "Official strategy description"
    assert vault_db.rows[BLUE_SPEC]["unrelated_field"] == "preserved"
    assert vault_db.rows[ONYX_SPEC]["_short_description"] == "Keep Onyx fallback"


def test_offchain_metadata_migration_reverts_empty_api_copy_to_safe_fallback() -> None:
    """Replace obsolete text only after a successful authoritative empty reply."""

    module = load_offchain_metadata_migration_module()
    row = {
        "Protocol": "Enzyme",
        "Name": "No public manager copy",
        "_detection_data": SimpleNamespace(features={ERC4626Feature.enzyme_blue_like}),
        "_short_description": "Obsolete API tagline",
        "_description": "Obsolete API description",
    }

    update = module.create_metadata_update(BLUE_SPEC, row, EnzymeVaultMetadata())

    assert update.short_description == "Enzyme Blue tokenised digital-asset investment vehicle."
    assert "No manager-provided strategy description is available" in update.description
