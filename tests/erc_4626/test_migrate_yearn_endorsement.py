"""Tests for the Yearn public-registry exclusion migration."""

import datetime
import importlib.util
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast

import pytest
from pytest import MonkeyPatch
from requests.exceptions import RequestException
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.yearn import endorsement as yearn_endorsement
from eth_defi.erc_4626.vault_protocol.yearn.compounder import YearnCompounderVault
from eth_defi.erc_4626.vault_protocol.yearn.endorsement import (
    YearnRegistryExclusionCache,
    YearnRegistryExclusions,
    add_yearn_registry_exclusion,
    is_yearn_registry_excluded_vault,
    parse_yearn_registry_exclusions,
)
from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import identify_curator
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

KATANA_USDC_STB_DEPOSITOR = "0x63a028963907f5a0c1ceb7e47100f52dfc611117"
JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")


def create_yearn_registry_exclusions() -> YearnRegistryExclusions:
    """Create representative explicit and empty Yearn inclusion records.

    :return:
        Parsed primary-list exclusion index.
    """
    return parse_yearn_registry_exclusions(
        [
            {
                "address": "0x7b5a0182e400b241b317e781a4e9dedfc1429822",
                "chainId": 1,
                "inclusion": {"isSet": True, "isYearn": False},
            },
            {
                "address": KATANA_USDC_STB_DEPOSITOR,
                "chainId": 1,
                "inclusion": {},
            },
            {
                "address": "0x0000000000000000000000000000000000000001",
                "chainId": 1,
                "inclusion": {"isSet": True, "isYearn": True},
            },
        ]
    )


def load_migration_module() -> ModuleType:
    """Load the Yearn registry-exclusion migration script as a test module.

    :return:
        Loaded migration module.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "migrate-yearn-endorsement.py"
    spec = importlib.util.spec_from_file_location("migrate_yearn_endorsement", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_detection(spec: VaultSpec, features: set[ERC4626Feature]) -> ERC4262VaultDetection:
    """Create persisted detection data for a vault fixture.

    :param spec:
        Fixture chain/address identifier.
    :param features:
        Persisted vault feature markers.
    :return:
        Minimal metadata detection envelope.
    """
    timestamp = datetime.datetime(2026, 9, 14, tzinfo=datetime.UTC).replace(tzinfo=None)
    return ERC4262VaultDetection(
        chain=spec.chain_id,
        address=spec.vault_address,
        first_seen_at_block=1,
        first_seen_at=timestamp,
        features=set(features),
        updated_at=timestamp,
        deposit_count=1,
        redeem_count=1,
    )


def create_row(spec: VaultSpec, features: set[ERC4626Feature]) -> VaultRow:
    """Create a cached Yearn row for the migration fixture.

    :param spec:
        Fixture chain/address identifier.
    :param features:
        Persisted vault feature markers.
    :return:
        Minimal vault metadata row.
    """
    return cast(
        VaultRow,
        {
            "Name": "Neutral vault name",
            "Protocol": "Yearn",
            "Features": ", ".join(sorted(feature.name for feature in features)),
            "features": set(features),
            "protocol_slug": "yearn",
            "_detection_data": create_detection(spec, features),
        },
    )


def test_yearn_registry_exclusions_include_empty_inclusion_object() -> None:
    """An empty registry inclusion object removes the Katana vault from Yearn attribution."""
    exclusions = create_yearn_registry_exclusions()
    assert is_yearn_registry_excluded_vault(1, KATANA_USDC_STB_DEPOSITOR, exclusions=exclusions)
    assert is_yearn_registry_excluded_vault(1, "0x7b5a0182e400b241b317e781a4e9dedfc1429822", exclusions=exclusions)
    assert not is_yearn_registry_excluded_vault(1, "0x0000000000000000000000000000000000000001", exclusions=exclusions)


def test_fetch_yearn_registry_exclusions_caches_empty_inclusion_result(monkeypatch: MonkeyPatch) -> None:
    """A fetched empty inclusion result is shared by subsequent classification calls."""
    exclusions = create_yearn_registry_exclusions()
    calls: list[str] = []

    def fetch_registry(url: str, *, timeout: int) -> SimpleNamespace:
        """Return representative registry data without contacting the live endpoint.

        :param url:
            Requested Yearn registry endpoint.
        :param timeout:
            HTTP request timeout in seconds.
        :return:
            Minimal successful response object.
        """
        assert url == yearn_endorsement.YEARN_VAULT_REGISTRY_URL
        assert timeout == yearn_endorsement.YEARN_REGISTRY_REQUEST_TIMEOUT
        calls.append(url)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: [
                {
                    "address": KATANA_USDC_STB_DEPOSITOR,
                    "chainId": 1,
                    "inclusion": {},
                }
            ],
        )

    monkeypatch.setattr(yearn_endorsement, "_yearn_registry_exclusions_state", YearnRegistryExclusionCache())
    monkeypatch.setattr(yearn_endorsement, "MINIMUM_YEARN_REGISTRY_EXCLUSION_COUNT", 1)
    monkeypatch.setattr(yearn_endorsement.requests, "get", fetch_registry)
    assert yearn_endorsement.fetch_yearn_registry_exclusions() == exclusions - {(1, "0x7b5a0182e400b241b317e781a4e9dedfc1429822")}
    assert yearn_endorsement.fetch_yearn_registry_exclusions() == exclusions - {(1, "0x7b5a0182e400b241b317e781a4e9dedfc1429822")}
    assert calls == [yearn_endorsement.YEARN_VAULT_REGISTRY_URL]


def test_fetch_yearn_registry_exclusions_rejects_short_response(monkeypatch: MonkeyPatch) -> None:
    """A short registry response is unavailable rather than a new authoritative index."""
    calls: list[str] = []

    def fetch_short_registry(url: str, *, timeout: int) -> SimpleNamespace:
        """Return one incomplete exclusion record.

        :param url:
            Requested Yearn registry endpoint.
        :param timeout:
            HTTP request timeout in seconds.
        :return:
            Minimal successful but incomplete response object.
        """
        assert url == yearn_endorsement.YEARN_VAULT_REGISTRY_URL
        assert timeout == yearn_endorsement.YEARN_REGISTRY_REQUEST_TIMEOUT
        calls.append(url)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: [{"address": KATANA_USDC_STB_DEPOSITOR, "chainId": 1, "inclusion": {}}],
        )

    monkeypatch.setattr(yearn_endorsement, "_yearn_registry_exclusions_state", YearnRegistryExclusionCache())
    monkeypatch.setattr(yearn_endorsement.requests, "get", fetch_short_registry)
    assert yearn_endorsement.fetch_yearn_registry_exclusions() is None
    assert yearn_endorsement.fetch_yearn_registry_exclusions() is None
    assert calls == [yearn_endorsement.YEARN_VAULT_REGISTRY_URL]


def test_fetch_yearn_registry_exclusions_uses_stale_cache_after_failure(monkeypatch: MonkeyPatch) -> None:
    """A refresh failure retains the last successful exclusion index."""
    exclusions = create_yearn_registry_exclusions()
    cached_index = yearn_endorsement.CachedYearnRegistryExclusions(
        exclusions=exclusions,
        fetched_at=native_datetime_utc_now() - yearn_endorsement.YEARN_REGISTRY_CACHE_DURATION - datetime.timedelta(seconds=1),
    )

    def fail_fetch(url: str, *, timeout: int) -> None:
        """Simulate a transient Yearn registry connection failure.

        :param url:
            Requested Yearn registry endpoint.
        :param timeout:
            HTTP request timeout in seconds.
        :return:
            This function always raises a connection error.
        """
        assert url == yearn_endorsement.YEARN_VAULT_REGISTRY_URL
        assert timeout == yearn_endorsement.YEARN_REGISTRY_REQUEST_TIMEOUT
        message = "simulated outage"
        raise RequestException(message)

    monkeypatch.setattr(yearn_endorsement, "_yearn_registry_exclusions_state", YearnRegistryExclusionCache(index=cached_index))
    monkeypatch.setattr(yearn_endorsement.requests, "get", fail_fetch)
    assert yearn_endorsement.fetch_yearn_registry_exclusions() == exclusions


def test_parse_yearn_registry_exclusions_rejects_invalid_and_skips_unset_records() -> None:
    """Only explicit exclusion records produce registry keys."""
    with pytest.raises(ValueError, match="JSON array"):
        parse_yearn_registry_exclusions({})
    assert (
        parse_yearn_registry_exclusions(
            [
                {"address": KATANA_USDC_STB_DEPOSITOR, "chainId": 1, "inclusion": {"isSet": False, "isYearn": False}},
                {"address": None, "chainId": 1, "inclusion": {}},
            ]
        )
        == frozenset()
    )


def test_non_yearn_features_skip_registry_fetch(monkeypatch: MonkeyPatch) -> None:
    """Non-Yearn feature sets do not make an offchain registry request."""
    excluded_features = {ERC4626Feature.morpho_like}

    def fail_fetch() -> None:
        """Fail if the Yearn registry lookup is unexpectedly attempted.

        :return:
            This function always raises an assertion failure.
        """
        pytest.fail("Non-Yearn vault classification fetched the Yearn registry")

    monkeypatch.setattr(yearn_endorsement, "fetch_yearn_registry_exclusions", fail_fetch)
    assert add_yearn_registry_exclusion(1, KATANA_USDC_STB_DEPOSITOR, excluded_features) == excluded_features


def test_yearn_features_fetch_registry_and_add_exclusion(monkeypatch: MonkeyPatch) -> None:
    """A Yearn adapter feature receives the exclusion marker from the cached index."""
    exclusions = create_yearn_registry_exclusions()
    monkeypatch.setattr(yearn_endorsement, "fetch_yearn_registry_exclusions", lambda: exclusions)
    assert add_yearn_registry_exclusion(1, KATANA_USDC_STB_DEPOSITOR, {ERC4626Feature.yearn_v3_like}) == {
        ERC4626Feature.yearn_v3_like,
        ERC4626Feature.yearn_registry_excluded,
    }


def test_missing_chain_id_skips_registry_fetch(monkeypatch: MonkeyPatch) -> None:
    """A vault without a chain identifier cannot match the Yearn registry."""
    monkeypatch.setattr(yearn_endorsement, "fetch_yearn_registry_exclusions", lambda: pytest.fail("Unexpected registry fetch"))
    assert not is_yearn_registry_excluded_vault(None, KATANA_USDC_STB_DEPOSITOR)


@pytest.mark.skipif(os.environ.get("RUN_YEARN_REGISTRY_TEST") != "1", reason="Set RUN_YEARN_REGISTRY_TEST=1 to run the live Yearn registry check")
def test_fetch_yearn_registry_exclusions_live() -> None:
    """The live Yearn registry exposes the Katana empty-inclusion classification.

    :return:
        ``None`` after validating the public registry response.
    """
    exclusions = yearn_endorsement.fetch_yearn_registry_exclusions()
    assert exclusions is not None
    assert is_yearn_registry_excluded_vault(1, KATANA_USDC_STB_DEPOSITOR, exclusions=exclusions)


@pytest.mark.skipif(
    os.environ.get("RUN_YEARN_REGISTRY_TEST") != "1" or JSON_RPC_ETHEREUM is None,
    reason="Set RUN_YEARN_REGISTRY_TEST=1 and JSON_RPC_ETHEREUM to run the live Katana pipeline check",
)
def test_katana_empty_inclusion_vault_is_generic_through_live_pipeline() -> None:
    """A live Katana depositor keeps its Yearn adapter but loses Yearn attribution.

    This real integration covers the end-to-end path: Ethereum RPC feature
    detection reads the vault contract, the Yearn registry adds the empty-
    inclusion marker, and protocol attribution becomes generic ERC-4626.

    :return:
        ``None`` after validating the live vault classification.
    """
    web3: Web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    features = detect_vault_features(web3, KATANA_USDC_STB_DEPOSITOR, verbose=False)
    assert features == {ERC4626Feature.yearn_compounder_like, ERC4626Feature.yearn_registry_excluded}
    assert get_vault_protocol_name(features) == "ERC-4626"

    vault = create_vault_instance(web3, KATANA_USDC_STB_DEPOSITOR, features=features)
    assert isinstance(vault, YearnCompounderVault)
    assert vault.get_link().lower() == f"https://routescan.io/address/{KATANA_USDC_STB_DEPOSITOR}".lower()


def test_migrate_yearn_registry_exclusions_remove_protocol_and_curator_attribution() -> None:
    """An excluded V3 vault becomes generic without losing its Yearn adapter feature."""
    migration = load_migration_module()
    exclusions = create_yearn_registry_exclusions()
    excluded_spec = VaultSpec(1, KATANA_USDC_STB_DEPOSITOR)
    endorsed_spec = VaultSpec(747474, "0x93fec6639717b6215a48e5a72a162c50dcc40d68")
    features = {ERC4626Feature.yearn_v3_like}
    excluded_row = create_row(excluded_spec, features)
    excluded_row["protocol_slug"] = "stale-protocol-slug"
    vault_db = VaultDatabase(
        rows={
            excluded_spec: excluded_row,
            endorsed_spec: create_row(endorsed_spec, features),
        }
    )

    updates = migration.collect_yearn_registry_exclusion_updates(vault_db, exclusions=exclusions)
    assert [update.spec for update in updates] == [excluded_spec]
    assert updates[0].new_protocol == "ERC-4626"
    excluded_features = add_yearn_registry_exclusion(excluded_spec.chain_id, excluded_spec.vault_address, features, exclusions=exclusions)
    assert excluded_features == {ERC4626Feature.yearn_v3_like, ERC4626Feature.yearn_registry_excluded}
    assert get_vault_protocol_name(excluded_features) == "ERC-4626"
    assert get_vault_protocol_name(excluded_features | {ERC4626Feature.morpho_like}) == "Morpho"
    adapter = create_vault_instance(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), excluded_spec.vault_address, features=excluded_features)
    assert isinstance(adapter, YearnV3Vault)
    assert adapter.get_link().lower() == f"https://routescan.io/address/{excluded_spec.vault_address}".lower()

    migration.apply_yearn_registry_exclusion_updates(vault_db, updates)

    excluded_row = vault_db.rows[excluded_spec]
    assert excluded_row["Protocol"] == "ERC-4626"
    assert excluded_row["protocol_slug"] == "erc-4626"
    assert excluded_row["Link"] == f"https://routescan.io/address/{excluded_spec.vault_address}"
    assert excluded_row["features"] == excluded_features
    assert excluded_row["_detection_data"].features == excluded_features
    assert identify_curator(excluded_spec.chain_id, "kpdUSDC", "Neutral vault name", excluded_spec.vault_address, excluded_row["protocol_slug"]) is None

    endorsed_row = vault_db.rows[endorsed_spec]
    assert endorsed_row["Protocol"] == "Yearn"
    assert identify_curator(endorsed_spec.chain_id, "yvAUSD", "Neutral vault name", endorsed_spec.vault_address, endorsed_row["protocol_slug"]) == "yearn"


def test_migration_uses_legacy_detection_features_and_skips_empty_write(tmp_path: Path) -> None:
    """Legacy rows are updated from detection data, while empty runs do not write a backup."""
    migration = load_migration_module()
    exclusions = create_yearn_registry_exclusions()
    excluded_spec = VaultSpec(1, KATANA_USDC_STB_DEPOSITOR)
    legacy_row = create_row(excluded_spec, {ERC4626Feature.yearn_v3_like})
    del legacy_row["features"]
    vault_db = VaultDatabase(rows={excluded_spec: legacy_row})
    updates = migration.collect_yearn_registry_exclusion_updates(vault_db, exclusions=exclusions)
    assert updates[0].old_features == frozenset({ERC4626Feature.yearn_v3_like})

    database_path = tmp_path / "vault-db.pickle"
    no_updates_db = VaultDatabase(rows={VaultSpec(1, "0x0000000000000000000000000000000000000001"): {"Protocol": "ERC-4626"}})
    no_updates_db.write(database_path)
    original_data = database_path.read_bytes()

    assert migration.migrate_yearn_registry_exclusions(database_path, dry_run=False, exclusions=exclusions) == []
    assert database_path.read_bytes() == original_data
    assert not list(tmp_path.glob("*.bak-yearn-registry-exclusion*"))


def test_migration_fails_when_live_registry_is_unavailable(monkeypatch: MonkeyPatch) -> None:
    """A registry outage cannot be reported as a successful zero-update migration."""
    migration = load_migration_module()
    vault_db = VaultDatabase(rows={})
    monkeypatch.setattr(migration, "fetch_yearn_registry_exclusions", lambda: None)
    with pytest.raises(RuntimeError, match="live registry is unavailable"):
        migration.collect_yearn_registry_exclusion_updates(vault_db)
