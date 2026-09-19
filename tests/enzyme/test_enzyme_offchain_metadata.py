"""Test Enzyme app-profile collection without inferred fallback copy."""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import flaky
import pytest
from eth_typing import HexAddress

from eth_defi.enzyme import offchain_metadata
from eth_defi.enzyme.offchain_metadata import (
    ENZYME_APP_GRAPHQL_URL,
    EnzymeVaultMetadata,
    create_enzyme_app_session,
    create_enzyme_vault_link,
    fetch_enzyme_app_vault_metadata_batch,
    load_enzyme_blue_vault_metadata,
    load_enzyme_vault_metadata_cache,
    parse_enzyme_app_vault_metadata,
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

#: Blue VaultProxy with public app-profile contact metadata. The live test
#: validates the undocumented, unauthenticated application endpoint end-to-end.
LIVE_ENZYME_BLUE_VAULT = "0x4134d87C87df974577c580561b4776c2Ab70CB43"


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


def test_load_enzyme_blue_metadata_reads_cached_profile_fields(monkeypatch) -> None:
    """Read the exact successful app-profile response from the cache."""

    monkeypatch.setattr(
        offchain_metadata,
        "_cached_enzyme_vault_metadata",
        {BLUE_METADATA_KEY: EnzymeVaultMetadata(short_description="Official tagline", description="Official strategy narrative.")},
    )

    assert load_enzyme_blue_vault_metadata(*BLUE_METADATA_KEY) == EnzymeVaultMetadata(
        short_description="Official tagline",
        description="Official strategy narrative.",
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


def test_parse_enzyme_app_vault_metadata_preserves_contact_fields() -> None:
    """Keep app manager fields distinct from the vault strategy copy."""

    metadata = parse_enzyme_app_vault_metadata(
        {
            "data": {
                "vaultProfile": {
                    "tagline": "  Delta-neutral ETH strategy  ",
                    "description": "  Trades ETH spot and derivatives.  ",
                    "managerDescription": "A manager biography, not vault product copy.",
                    "contactInfo": "  Contact the team through Discord.  ",
                    "email": "  team@example.com  ",
                    "telegram": " @delta_team ",
                    "twitter": "@delta_finance",
                    "websiteUrl": "https://delta.example.com",
                },
            },
        }
    )

    assert metadata.short_description == "Delta-neutral ETH strategy"
    assert metadata.description == "Trades ETH spot and derivatives."
    assert metadata.manager_description == "A manager biography, not vault product copy."
    assert metadata.contact_info == "Contact the team through Discord."
    assert metadata.contact_email == "team@example.com"
    assert metadata.telegram == "delta_team"
    assert metadata.twitter == "delta_finance"
    assert metadata.website_url == "https://delta.example.com"
    assert metadata.manager_name == "delta_finance"


def test_parse_enzyme_app_vault_metadata_rejects_invalid_response() -> None:
    """Reject a malformed response before it can overwrite cached copy."""

    with pytest.raises(ValueError, match="JSON object"):
        parse_enzyme_app_vault_metadata([])


def test_fetch_enzyme_app_vault_metadata_batch_uses_graphql_aliases() -> None:
    """Collect multiple page-equivalent profile reads in one HTTP request."""

    requests = []

    class Response:
        """Minimal successful multi-profile app response."""

        def raise_for_status(self) -> None:
            """Act as a successful HTTP response."""

        def json(self) -> dict[str, object]:  # noqa: PLR6301 - Matches the requests response interface.
            """Return two independent app profile values."""

            return {"data": {"profile0": {"twitter": "first_manager"}, "profile1": {"websiteUrl": "https://second.example"}}}

    class Session:
        """Record the request made by the batch collector."""

        def post(self, *args, **kwargs) -> Response:  # noqa: PLR6301 - Matches the requests session interface.
            """Capture request details and return fixture response."""

            requests.append((args, kwargs))
            return Response()

    second_address = "0x000000000000000000000000000000000000c0Fe"
    metadata = fetch_enzyme_app_vault_metadata_batch(
        Session(),
        shares_addresses=[VAULT_ADDRESS, second_address],
        timeout=20,
    )

    assert metadata == {
        HexAddress(VAULT_ADDRESS.lower()): EnzymeVaultMetadata(twitter="first_manager", manager_name="first_manager"),
        HexAddress(second_address.lower()): EnzymeVaultMetadata(website_url="https://second.example", manager_name="second.example"),
    }
    args, kwargs = requests[0]
    assert args == (ENZYME_APP_GRAPHQL_URL,)
    assert kwargs["json"]["operationName"] == "VaultProfiles"
    assert kwargs["json"]["variables"] == {
        "vaultAddress0": "0x000000000000000000000000000000000000bEEF",
        "vaultAddress1": "0x000000000000000000000000000000000000c0Fe",
    }
    assert "profile0: vaultProfile(vaultAddress: $vaultAddress0)" in kwargs["json"]["query"]
    assert "profile1: vaultProfile(vaultAddress: $vaultAddress1)" in kwargs["json"]["query"]


# 2026-09-19: the undocumented Enzyme app returned transient 429 and 502
# responses during the catalogue migration; this read passed locally on retry.
@flaky.flaky(max_runs=2, min_passes=1)
def test_fetch_enzyme_app_vault_metadata_batch_live() -> None:
    """Read a real Blue manager profile from the app backend.

    The test intentionally checks only the stable public response shape. The
    profile owner can edit all values through the Enzyme app at any time.

    :return: None after the live unauthenticated read succeeds.
    """

    session = create_enzyme_app_session()
    try:
        metadata = fetch_enzyme_app_vault_metadata_batch(
            session,
            shares_addresses=[LIVE_ENZYME_BLUE_VAULT],
            timeout=30,
        )[HexAddress(LIVE_ENZYME_BLUE_VAULT.lower())]
    finally:
        session.close()

    assert isinstance(metadata, EnzymeVaultMetadata)
    assert all(
        value is None or isinstance(value, str)
        for value in (
            metadata.short_description,
            metadata.description,
            metadata.manager_description,
            metadata.contact_info,
            metadata.contact_email,
            metadata.telegram,
            metadata.twitter,
            metadata.website_url,
            metadata.manager_name,
        )
    )


@pytest.mark.parametrize(
    ("profile", "expected_manager_name"),
    [
        ({"twitter": "alpha_manager", "telegram": "alpha_telegram", "websiteUrl": "https://alpha.example"}, "alpha_manager"),
        ({"telegram": "alpha_telegram", "websiteUrl": "https://alpha.example"}, "alpha_telegram"),
        ({"email": "alpha-team@example.com", "websiteUrl": "https://alpha.example"}, "alpha-team"),
        ({"email": "info@example.com", "websiteUrl": "https://alpha.example"}, "alpha.example"),
        ({"websiteUrl": "https://alpha.example/path"}, "alpha.example"),
        ({"twitter": "https://x.com/alpha_manager", "websiteUrl": "https://wrong.example"}, "alpha_manager"),
        ({"telegram": "t.me/alpha_telegram", "websiteUrl": "https://wrong.example"}, "alpha_telegram"),
        ({"websiteUrl": "www.alpha.example/path"}, "alpha.example"),
    ],
)
def test_parse_enzyme_app_vault_metadata_derives_manager_identifier(profile: dict[str, str], expected_manager_name: str) -> None:
    """Prefer a public identifier and fall back to the configured website domain."""

    metadata = parse_enzyme_app_vault_metadata({"data": {"vaultProfile": profile}})

    assert metadata.manager_name == expected_manager_name


def test_fetch_enzyme_app_vault_metadata_batch_rejects_graphql_errors() -> None:
    """Stop a migration batch before an app error can clear cached metadata."""

    class Response:
        """Minimal failed GraphQL response."""

        def raise_for_status(self) -> None:
            """Act as a successful HTTP transport response."""

        def json(self) -> dict[str, object]:  # noqa: PLR6301 - Matches the requests response interface.
            """Return one GraphQL execution error."""

            return {"errors": [{"message": "too many aliases"}]}

    class Session:
        """Return the failed GraphQL response."""

        def post(self, *_args, **_kwargs) -> Response:  # noqa: PLR6301 - Matches the requests session interface.
            """Return the test response."""

            return Response()

    with pytest.raises(ValueError, match="too many aliases"):
        fetch_enzyme_app_vault_metadata_batch(Session(), shares_addresses=[VAULT_ADDRESS], timeout=20)


def test_fetch_enzyme_app_vault_metadata_batch_rejects_missing_aliases() -> None:
    """Treat an incomplete GraphQL reply as a failed batch, not empty metadata."""

    class Response:
        """Minimal incomplete GraphQL response."""

        def raise_for_status(self) -> None:
            """Act as a successful HTTP transport response."""

        def json(self) -> dict[str, object]:  # noqa: PLR6301 - Matches the requests response interface.
            """Return no alias values and no execution errors."""

            return {"data": {}}

    class Session:
        """Return the incomplete GraphQL response."""

        def post(self, *_args, **_kwargs) -> Response:  # noqa: PLR6301 - Matches the requests session interface.
            """Return the test response."""

            return Response()

    with pytest.raises(ValueError, match="omitted aliases: profile0"):
        fetch_enzyme_app_vault_metadata_batch(Session(), shares_addresses=[VAULT_ADDRESS], timeout=20)


def test_fetch_enzyme_app_vault_metadata_batch_rejects_more_than_five_aliases() -> None:
    """Reject a request the Enzyme app would refuse before opening a connection."""

    with pytest.raises(ValueError, match="must not exceed 5"):
        fetch_enzyme_app_vault_metadata_batch(
            None,  # type: ignore[arg-type]  # Query-size validation happens before the session is used.
            shares_addresses=[VAULT_ADDRESS] * 6,
            timeout=20,
        )


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


def test_enzyme_metadata_cache_keeps_version_one_descriptions_until_refresh(tmp_path) -> None:
    """Keep existing descriptions readable while the contact migration catches up."""

    cache_path = tmp_path / "enzyme-vault-metadata.json"
    cache_path.write_text('{"version": 1, "vaults": [{"chain_id": 1, "address": "0x000000000000000000000000000000000000bEEF", "short_description": "Existing tagline", "description": "Existing description", "manager_name": null}]}')

    assert load_enzyme_vault_metadata_cache(cache_path) == {BLUE_METADATA_KEY: EnzymeVaultMetadata(short_description="Existing tagline", description="Existing description")}
    assert load_enzyme_vault_metadata_cache(cache_path, minimum_version=2) == {}


def test_parse_enzyme_app_vault_metadata_discards_invalid_social_urls() -> None:
    """Do not publish an unrelated link as a manager social identifier."""

    metadata = parse_enzyme_app_vault_metadata(
        {
            "data": {
                "vaultProfile": {
                    "twitter": "https://linkedin.com/in/manager",
                    "telegram": "https://t.me/manager",
                    "websiteUrl": "www.manager.example/path",
                },
            },
        }
    )

    assert metadata.twitter is None
    assert metadata.telegram == "manager"
    assert metadata.website_url == "https://www.manager.example/path"
    assert metadata.manager_name == "manager"


def test_parse_enzyme_app_vault_metadata_discards_x_internal_routes() -> None:
    """Do not mistake an X login route for a public manager handle."""

    metadata = parse_enzyme_app_vault_metadata({"data": {"vaultProfile": {"twitter": "https://x.com/i/flow/login", "websiteUrl": "https://manager.example"}}})

    assert metadata.twitter is None
    assert metadata.manager_name == "manager.example"


def test_offchain_metadata_migration_refetches_version_one_cache(monkeypatch, tmp_path) -> None:
    """Ensure old cache records cannot suppress the contact-profile migration."""

    module = load_offchain_metadata_migration_module()
    cache_path = tmp_path / "enzyme-vault-metadata.json"
    cache_path.write_text('{"version": 1, "vaults": [{"chain_id": 1, "address": "0x000000000000000000000000000000000000bEEF", "short_description": "Existing tagline", "description": "Existing description", "manager_name": null}]}')

    class Progress:
        """Minimal quiet progress-bar context manager."""

        def __enter__(self) -> "Progress":
            """Enter the progress context."""

            return self

        def __exit__(self, *_args: object) -> None:
            """Exit the progress context."""

        def update(self, _count: int) -> None:
            """Accept a progress update."""

    class Session:
        """Minimal migration session."""

        def close(self) -> None:
            """Close the no-op test session."""

    calls: list[tuple[list[VaultSpec], float, Session]] = []

    def fetch(
        vault_specs: list[VaultSpec],
        *,
        timeout: float,
        session: Session,
    ) -> dict[VaultSpec, EnzymeVaultMetadata]:
        """Record the missing vault and provide its refreshed profile."""

        calls.append((vault_specs, timeout, session))
        return {BLUE_SPEC: EnzymeVaultMetadata(twitter="refreshed_manager", manager_name="refreshed_manager")}

    monkeypatch.setattr(module, "create_enzyme_app_session", Session)
    monkeypatch.setattr(module, "fetch_enzyme_metadata_batch", fetch)
    monkeypatch.setattr(module, "tqdm", lambda **_kwargs: Progress())

    metadata, request_count, cached_metadata = module.fetch_enzyme_metadata(
        [(BLUE_SPEC, {})],
        state_path=tmp_path / "enzyme-offchain-metadata-state.json",
        cache_path=cache_path,
        dry_run=True,
        profile_batch_size=5,
        request_interval_seconds=0,
        timeout=20,
    )

    assert cached_metadata == {}
    assert request_count == 1
    assert calls[0][0] == [BLUE_SPEC]
    assert metadata == {BLUE_SPEC: EnzymeVaultMetadata(twitter="refreshed_manager", manager_name="refreshed_manager")}


def test_offchain_metadata_refresh_resumes_only_refresh_state(monkeypatch, tmp_path) -> None:
    """Resume forced refreshes without reusing entries from an ordinary run."""

    module = load_offchain_metadata_migration_module()
    state_path = tmp_path / "enzyme-offchain-metadata-state.json"
    refreshed_metadata = EnzymeVaultMetadata(twitter="refreshed_manager", manager_name="refreshed_manager")
    module.write_metadata_state(state_path, {BLUE_SPEC: refreshed_metadata}, refresh=True)
    monkeypatch.setenv("ENZYME_METADATA_REFRESH", "true")

    metadata, request_count, cached_metadata = module.fetch_enzyme_metadata(
        [(BLUE_SPEC, {})],
        state_path=state_path,
        cache_path=tmp_path / "enzyme-vault-metadata.json",
        dry_run=True,
        profile_batch_size=5,
        request_interval_seconds=0,
        timeout=20,
    )

    assert metadata == {BLUE_SPEC: refreshed_metadata}
    assert request_count == 0
    assert cached_metadata == {}


def test_offchain_metadata_state_resumes_successful_app_profile_replies(tmp_path) -> None:
    """Keep completed public app-profile replies when the migration is interrupted."""

    module = load_offchain_metadata_migration_module()
    state_path = tmp_path / "enzyme-offchain-metadata-state.json"
    state = {
        BLUE_SPEC: EnzymeVaultMetadata(short_description="Official tagline", twitter="manager", manager_name="manager"),
        ONYX_SPEC: EnzymeVaultMetadata(description="Not selected"),
    }

    module.write_metadata_state(state_path, state)
    loaded = module.load_metadata_state(state_path, {BLUE_SPEC})

    assert loaded == {BLUE_SPEC: EnzymeVaultMetadata(short_description="Official tagline", twitter="manager", manager_name="manager")}


def test_offchain_metadata_migration_changes_only_existing_enzyme_blue_copy() -> None:
    """Apply app-profile fields to Blue without changing Onyx or unrelated metadata."""

    module = load_offchain_metadata_migration_module()
    vault_db = VaultDatabase(
        rows={
            BLUE_SPEC: {
                "Protocol": "Enzyme",
                "Name": "Official Blue vault",
                "_detection_data": SimpleNamespace(features={ERC4626Feature.enzyme_blue_like}),
                "_short_description": "Old short copy",
                "_description": "Old long copy",
                "Denomination": "USDC",
                "NAV": 1_001,
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

    selected_rows = list(module.iter_all_enzyme_blue_rows(vault_db))
    assert selected_rows == [(BLUE_SPEC, vault_db.rows[BLUE_SPEC])]

    update = module.create_metadata_update(
        BLUE_SPEC,
        vault_db.rows[BLUE_SPEC],
        EnzymeVaultMetadata(short_description="Official tagline", description="Official strategy description", manager_name="Official manager"),
    )
    assert update.changed_fields == ("_short_description", "_description", "_manager_name")

    module.apply_metadata_updates(vault_db, [update])

    assert vault_db.rows[BLUE_SPEC]["_short_description"] == "Official tagline"
    assert vault_db.rows[BLUE_SPEC]["_description"] == "Official strategy description"
    assert vault_db.rows[BLUE_SPEC]["_manager_name"] == "Official manager"
    assert vault_db.rows[BLUE_SPEC]["unrelated_field"] == "preserved"
    assert vault_db.rows[ONYX_SPEC]["_short_description"] == "Keep Onyx fallback"


def test_offchain_metadata_migration_clears_empty_profile_copy() -> None:
    """Clear obsolete text after a successful empty app-profile reply."""

    module = load_offchain_metadata_migration_module()
    row = {
        "Protocol": "Enzyme",
        "Name": "No public manager copy",
        "_detection_data": SimpleNamespace(features={ERC4626Feature.enzyme_blue_like}),
        "_short_description": "Obsolete profile tagline",
        "_description": "Obsolete profile description",
    }

    update = module.create_metadata_update(BLUE_SPEC, row, EnzymeVaultMetadata())

    assert update.short_description is None
    assert update.description is None


def test_offchain_metadata_migration_clears_legacy_fields_independently() -> None:
    """Clear each retired generated field without discarding profile copy."""

    module = load_offchain_metadata_migration_module()
    legacy_row = {
        "Name": "Renamed Blue vault",
        "_short_description": module.LEGACY_BLUE_SHORT_DESCRIPTION,
        "_description": f"Previous Blue vault{module.LEGACY_BLUE_DESCRIPTION_SUFFIX}",
    }
    profile_description_row = {
        "Name": "Manager Blue vault",
        "_short_description": module.LEGACY_BLUE_SHORT_DESCRIPTION,
        "_description": "Manager Blue vault is an Enzyme Blue investment vehicle with a manager-authored strategy.",
    }

    update = module.create_legacy_fallback_clear_update(BLUE_SPEC, legacy_row)

    assert update is not None
    assert update.short_description is None
    assert update.description is None
    partial_update = module.create_legacy_fallback_clear_update(BLUE_SPEC, profile_description_row)
    assert partial_update is not None
    assert partial_update.short_description is None
    assert partial_update.description == profile_description_row["_description"]
    assert partial_update.changed_fields == ("_short_description",)
