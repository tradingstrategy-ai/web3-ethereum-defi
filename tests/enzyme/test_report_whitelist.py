"""Test the Enzyme deposit-permission audit report."""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.vaultdb import VaultDatabase

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "enzyme" / "report-whitelist.py"
EXPECTED_API_RETRIES = 5
TOO_MANY_REQUESTS_STATUS = 429


def load_report_module() -> ModuleType:
    """Load the hyphenated Enzyme report script as a Python module.

    :return: Imported report module.
    """

    spec = importlib.util.spec_from_file_location("enzyme_report_whitelist", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_enzyme_row(feature: ERC4626Feature, permission: str | None, name: str) -> dict:
    """Create a minimal persisted Enzyme report fixture.

    :param feature: Factory-proven Enzyme architecture feature.
    :param permission: Persisted deposit permission, or ``None`` for legacy data.
    :param name: Vault display name.
    :return: Metadata row accepted by the report.
    """

    row = {
        "Protocol": "Enzyme",
        "Name": name,
        "_detection_data": SimpleNamespace(features={feature}),
    }
    if permission is not None:
        row["_deposit_permission"] = permission
    return row


def test_enzyme_permission_summary_separates_blue_and_onyx() -> None:
    """Report all shared enum values independently by architecture and chain."""

    module = load_report_module()
    database = VaultDatabase(
        rows={
            VaultSpec(1, "0x0000000000000000000000000000000000000001"): create_enzyme_row(ERC4626Feature.enzyme_blue_like, "whitelisted", "Blue gated"),
            VaultSpec(1, "0x0000000000000000000000000000000000000002"): create_enzyme_row(ERC4626Feature.enzyme_blue_like, "permissionless", "Blue public"),
            VaultSpec(8453, "0x0000000000000000000000000000000000000003"): create_enzyme_row(ERC4626Feature.enzyme_onyx_like, None, "Onyx unresolved"),
        }
    )

    records = tuple(module.iter_enzyme_permission_records(database))
    summary = module.create_summary_table(records)

    assert [(record.architecture, record.permission) for record in records] == [
        ("Blue", VaultDepositPermission.whitelisted),
        ("Blue", VaultDepositPermission.permissionless),
        ("Onyx", VaultDepositPermission.unknown),
    ]
    assert summary == [
        {"Chain": "Ethereum", "Chain id": 1, "Architecture": "Blue", "Whitelisted": 1, "Permissionless": 1, "Unknown": 0, "Total": 2},
        {"Chain": "Base", "Chain id": 8453, "Architecture": "Onyx", "Whitelisted": 0, "Permissionless": 0, "Unknown": 1, "Total": 1},
        {"Chain": "All chains", "Chain id": "—", "Architecture": "Blue", "Whitelisted": 1, "Permissionless": 1, "Unknown": 0, "Total": 2},
        {"Chain": "All chains", "Chain id": "—", "Architecture": "Onyx", "Whitelisted": 0, "Permissionless": 0, "Unknown": 1, "Total": 1},
    ]


def test_enzyme_api_permission_uses_only_enabled_depositor_policies() -> None:
    """Ignore unrelated policies and disabled depositor policies."""

    module = load_report_module()
    permission, policies = module.permission_from_enzyme_api_response(
        {
            "policyConfigurations": [
                {"allowedAdaptersPolicy": {"enabled": True}},
                {"allowedDepositRecipientsPolicy": {"enabled": False}},
                {"minMaxDepositPolicy": {"enabled": True}},
            ]
        }
    )

    assert permission is VaultDepositPermission.permissionless
    assert policies == ()


def test_enzyme_api_permission_accepts_omitted_empty_policy_list() -> None:
    """Treat protobuf JSON's omitted empty repeated field as no active policy."""

    module = load_report_module()

    permission, policies = module.permission_from_enzyme_api_response({})

    assert permission is VaultDepositPermission.permissionless
    assert policies == ()


def test_enzyme_api_permission_supports_current_and_pre_sulu_allowlists() -> None:
    """Map all official API account-admission policy variants consistently."""

    module = load_report_module()
    permission, policies = module.permission_from_enzyme_api_response(
        {
            "policyConfigurations": [
                {"allowedDepositRecipientsPolicy": {"enabled": True}},
                {"buySharesCallerWhitelistPolicy": {"enabled": True}},
                {"depositorWhitelistPolicy": {"enabled": True}},
            ]
        }
    )

    assert permission is VaultDepositPermission.whitelisted
    assert policies == ("allowedDepositRecipientsPolicy", "buySharesCallerWhitelistPolicy", "depositorWhitelistPolicy")


def test_enzyme_api_session_retries_throttled_post_requests() -> None:
    """Use pooling and Retry-After aware retries for the full Blue audit."""

    module = load_report_module()
    session = module.create_enzyme_api_session(max_workers=4)
    try:
        retry = session.get_adapter(module.ENZYME_API_CONFIGURATION_URL).max_retries
        assert retry.total == EXPECTED_API_RETRIES
        assert retry.allowed_methods == frozenset({"POST"})
        assert retry.respect_retry_after_header is True
        assert TOO_MANY_REQUESTS_STATUS in retry.status_forcelist
    finally:
        session.close()


def test_enzyme_api_permission_uses_checksum_address() -> None:
    """Send the canonical EIP-55 vault identity expected by the API gateway."""

    module = load_report_module()
    response = Mock()
    response.json.return_value = {"policyConfigurations": []}
    session = Mock()
    session.post.return_value = response

    spec = VaultSpec(1, "0x000000000000000000000000000000000000beef")
    record = module.EnzymePermissionRecord(spec, "Blue", "Checksum", VaultDepositPermission.permissionless, None)

    result = module.fetch_enzyme_api_permission(session, record, "token", 30)

    assert result.permission is VaultDepositPermission.permissionless
    assert session.post.call_args.kwargs["json"]["address"] == "0x000000000000000000000000000000000000bEEF"


def test_enzyme_api_comparison_reports_only_mismatch_or_failure() -> None:
    """Keep a clean match out of the operator action table."""

    module = load_report_module()
    first_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    second_spec = VaultSpec(137, "0x0000000000000000000000000000000000000002")
    records = (
        module.EnzymePermissionRecord(first_spec, "Blue", "Match", VaultDepositPermission.permissionless, None),
        module.EnzymePermissionRecord(second_spec, "Blue", "Mismatch", VaultDepositPermission.permissionless, None),
    )
    api_results = (
        module.EnzymeApiPermissionResult(first_spec, VaultDepositPermission.permissionless),
        module.EnzymeApiPermissionResult(second_spec, VaultDepositPermission.whitelisted, ("allowedDepositRecipientsPolicy",)),
    )

    table = module.create_api_comparison_table(records, api_results)

    assert len(table) == 1
    assert table[0]["Name"] == "Mismatch"
    assert table[0]["Database"] == "permissionless"
    assert table[0]["Enzyme API"] == "whitelisted"
