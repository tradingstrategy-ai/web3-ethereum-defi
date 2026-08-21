#!/usr/bin/env python3
"""Summarise persisted Enzyme Blue and Onyx deposit-permission statuses.

The website exports ``VaultDepositPermission`` from the vault metadata
database.  This read-only report shows the exact Blue/Onyx and per-chain
distribution behind that public field, including legacy rows where the value
is missing and therefore means ``unknown``.

An optional Enzyme API comparison is available for Blue.  Enzyme's
``GetVaultConfiguration`` response exposes the enabled deposit-recipient
policy, so it is a useful independent check of the current onchain adapter
result.  The API is authenticated and provides Blue configuration data; it is
not an authoritative Onyx deposit-handler index.

Usage::

    poetry run python scripts/enzyme/report-whitelist.py

    DETAILS=true CHECK_ENZYME_API=true ENZYME_API_TOKEN=... \
        poetry run python scripts/enzyme/report-whitelist.py

Environment variables:

- ``VAULT_DB_PATH``: metadata database path.
- ``DETAILS``: print every Enzyme row after the summary, default ``false``.
- ``CHECK_ENZYME_API``: cross-check all Blue rows, default ``false``.
- ``ENZYME_API_TOKEN``: bearer token created in the Enzyme application.
- ``MAX_WORKERS``: concurrent Enzyme API requests, default ``8``.
- ``API_TIMEOUT``: timeout for each API request in seconds, default ``30``.
- ``LOG_LEVEL``: console log level, default ``warning``.

Official API documentation:
https://sdk.enzyme.finance/api/endpoints/vault/
"""

import logging
import os
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import requests
from joblib import Parallel, delayed
from requests import RequestException
from tabulate import tabulate
from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

ENZYME_API_CONFIGURATION_URL = "https://api.enzyme.finance/enzyme.enzyme.v1.EnzymeService/GetVaultConfiguration"

#: Connect JSON enum names accepted by Enzyme's Blue API.
ENZYME_API_DEPLOYMENTS = {
    1: "DEPLOYMENT_ETHEREUM",
    137: "DEPLOYMENT_POLYGON",
    8453: "DEPLOYMENT_BASE",
    42161: "DEPLOYMENT_ARBITRUM",
}

#: Enzyme API policy oneof fields that require prior account approval.
#:
#: ``allowedDepositRecipientsPolicy`` is the current Sulu/Blue policy read by
#: our onchain adapter.  The other two fields cover the API's pre-Sulu vault
#: records; they must not be confused with asset or adapter allowlists.
ENZYME_API_DEPOSIT_PERMISSION_POLICIES = frozenset(
    {
        "allowedDepositRecipientsPolicy",
        "buySharesCallerWhitelistPolicy",
        "depositorWhitelistPolicy",
    }
)


@dataclass(slots=True, frozen=True)
class EnzymePermissionRecord:
    """One persisted Enzyme vault permission classification.

    This immutable record separates database parsing from tabular rendering and
    optional API comparison.
    """

    #: Chain and canonical share-token address.
    spec: VaultSpec

    #: ``Blue``, ``Onyx``, or ``Unknown``.
    architecture: str

    #: Persisted vault display name.
    name: str

    #: Normalised shared deposit-permission enum.
    permission: VaultDepositPermission

    #: Optional source qualification exported with the status.
    notes: str | None


@dataclass(slots=True, frozen=True)
class EnzymeApiPermissionResult:
    """One optional Enzyme API permission comparison.

    API failures remain distinct from a source-proven permission status so an
    operator cannot mistake missing offchain data for a permissionless vault.
    """

    #: Blue VaultProxy identity.
    spec: VaultSpec

    #: API-derived permission, or ``unknown`` after an error.
    permission: VaultDepositPermission

    #: Enabled account-admission policy fields.
    policy_names: tuple[str, ...] = ()

    #: Request or response error, if the API could not be checked.
    error: str | None = None


def parse_boolean_env(value: str | None, *, default: bool = False) -> bool:
    """Parse a command-line script boolean environment variable.

    :param value: Environment value, or ``None`` when unset.
    :param default: Result when the variable is unset.
    :return: Parsed boolean value.
    :raise ValueError: If the value is not an accepted boolean spelling.
    """

    if value is None:
        return default
    normalised = value.strip().lower()
    if normalised in {"1", "true", "yes"}:
        return True
    if normalised in {"0", "false", "no"}:
        return False
    raise ValueError(f"Unsupported boolean value: {value!r}")


def get_enzyme_architecture(row: VaultRow) -> str:
    """Classify a persisted Enzyme row from its factory-proven feature flag.

    The top-level feature copy is preferred, with detection data retained as a
    compatibility fallback for older pickles.

    :param row: Vault metadata row.
    :return: ``Blue``, ``Onyx``, or ``Unknown``.
    """

    detection = row.get("_detection_data")
    features = row.get("features") or getattr(detection, "features", set())
    values = {feature.value if isinstance(feature, ERC4626Feature) else str(feature) for feature in features}
    if ERC4626Feature.enzyme_blue_like.value in values:
        return "Blue"
    if ERC4626Feature.enzyme_onyx_like.value in values or "enzyme_like" in values:
        return "Onyx"
    return "Unknown"


def normalise_permission(value: object) -> VaultDepositPermission:
    """Normalise a persisted permission value without inventing certainty.

    Missing and invalid legacy values become ``unknown``.  This matches the
    public JSON export's fail-closed behaviour.

    :param value: Persisted enum string or enum member.
    :return: Shared deposit-permission enum.
    """

    if isinstance(value, VaultDepositPermission):
        return value
    try:
        return VaultDepositPermission(str(value))
    except ValueError:
        return VaultDepositPermission.unknown


def iter_enzyme_permission_records(vault_db: VaultDatabase) -> Iterator[EnzymePermissionRecord]:
    """Yield every Enzyme row used by the public permission export.

    :param vault_db: Loaded vault metadata database.
    :return: Iterator of normalised Enzyme permission records.
    """

    rows = ((spec, row) for spec, row in vault_db.rows.items() if row.get("Protocol") == "Enzyme")
    for spec, row in sorted(rows, key=lambda item: (item[0].chain_id, item[0].vault_address.lower())):
        yield EnzymePermissionRecord(
            spec=spec,
            architecture=get_enzyme_architecture(row),
            name=row.get("Name") or "",
            permission=normalise_permission(row.get("_deposit_permission")),
            notes=row.get("_whitelist_notes"),
        )


def create_summary_table(records: Iterable[EnzymePermissionRecord]) -> list[dict[str, str | int]]:
    """Aggregate Enzyme statuses by chain and protocol architecture.

    :param records: Normalised database rows.
    The final rows aggregate each architecture across all chains, allowing an
    operator to see protocol-level totals without losing the chain breakdown.

    :return: Table rows with chain, architecture and all enum counts.
    """

    counts = Counter((record.spec.chain_id, record.architecture, record.permission) for record in records)
    groups = sorted({(chain_id, architecture) for chain_id, architecture, _permission in counts})
    table = []
    for chain_id, architecture in groups:
        whitelisted = counts[chain_id, architecture, VaultDepositPermission.whitelisted]
        permissionless = counts[chain_id, architecture, VaultDepositPermission.permissionless]
        unknown = counts[chain_id, architecture, VaultDepositPermission.unknown]
        table.append(
            {
                "Chain": get_chain_name(chain_id),
                "Chain id": chain_id,
                "Architecture": architecture,
                "Whitelisted": whitelisted,
                "Permissionless": permissionless,
                "Unknown": unknown,
                "Total": whitelisted + permissionless + unknown,
            }
        )
    for architecture in sorted({record_architecture for _chain_id, record_architecture in groups}):
        whitelisted = sum(counts[chain_id, architecture, VaultDepositPermission.whitelisted] for chain_id, candidate_architecture in groups if candidate_architecture == architecture)
        permissionless = sum(counts[chain_id, architecture, VaultDepositPermission.permissionless] for chain_id, candidate_architecture in groups if candidate_architecture == architecture)
        unknown = sum(counts[chain_id, architecture, VaultDepositPermission.unknown] for chain_id, candidate_architecture in groups if candidate_architecture == architecture)
        table.append(
            {
                "Chain": "All chains",
                "Chain id": "—",
                "Architecture": architecture,
                "Whitelisted": whitelisted,
                "Permissionless": permissionless,
                "Unknown": unknown,
                "Total": whitelisted + permissionless + unknown,
            }
        )
    return table


def permission_from_enzyme_api_response(payload: dict) -> tuple[VaultDepositPermission, tuple[str, ...]]:
    """Map an official Blue configuration response to the shared enum.

    Only enabled account-admission policies affect the result.  Asset,
    adapter, transfer-recipient, deposit-size and redemption policies are not
    depositor identity approval and therefore do not make a vault
    ``whitelisted``.

    :param payload: JSON ``GetVaultConfiguration`` response.
    :return: Permission enum and matching enabled API policy field names.
    """

    if not isinstance(payload, dict):
        message = "Enzyme API response must be a JSON object"
        raise ValueError(message)
    policy_configurations = payload.get("policyConfigurations", [])
    if not isinstance(policy_configurations, list):
        message = "Enzyme API policyConfigurations must be a list"
        raise ValueError(message)

    matches = tuple(sorted(policy_name for configuration in policy_configurations if isinstance(configuration, dict) for policy_name, policy_data in configuration.items() if policy_name in ENZYME_API_DEPOSIT_PERMISSION_POLICIES and isinstance(policy_data, dict) and policy_data.get("enabled") is True))
    permission = VaultDepositPermission.whitelisted if matches else VaultDepositPermission.permissionless
    return permission, matches


def fetch_enzyme_api_permission(record: EnzymePermissionRecord, api_token: str, timeout: float) -> EnzymeApiPermissionResult:
    """Fetch one Blue vault's current permission from Enzyme's official API.

    The endpoint is a Connect/gRPC JSON gateway and requires a bearer token.
    A failed request remains an explicit ``unknown`` comparison result; it
    never overwrites or downgrades the persisted onchain classification.

    :param record: Blue database record to check.
    :param api_token: Enzyme API bearer token.
    :param timeout: HTTP timeout in seconds.
    :return: API comparison result.
    """

    try:
        deployment = ENZYME_API_DEPLOYMENTS[record.spec.chain_id]
    except KeyError:
        return EnzymeApiPermissionResult(record.spec, VaultDepositPermission.unknown, error=f"Unsupported Blue chain {record.spec.chain_id}")

    try:
        response = requests.post(
            ENZYME_API_CONFIGURATION_URL,
            headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
            json={"deployment": deployment, "address": record.spec.vault_address, "currency": "CURRENCY_USD"},
            timeout=timeout,
        )
        response.raise_for_status()
        permission, policies = permission_from_enzyme_api_response(response.json())
        return EnzymeApiPermissionResult(record.spec, permission, policies)
    except (RequestException, ValueError) as error:
        return EnzymeApiPermissionResult(record.spec, VaultDepositPermission.unknown, error=str(error))


def fetch_enzyme_api_permissions(
    records: Iterable[EnzymePermissionRecord],
    api_token: str,
    *,
    max_workers: int,
    timeout: float,
) -> tuple[EnzymeApiPermissionResult, ...]:
    """Fetch official API comparisons for all supplied Blue vaults.

    Calls are independent and use a bounded threaded worker pool.  Onyx rows
    are deliberately excluded because this Blue endpoint does not enumerate
    their active modular deposit handlers.

    :param records: Enzyme records; non-Blue rows are ignored.
    :param api_token: Enzyme API bearer token.
    :param max_workers: Maximum concurrent HTTP requests.
    :param timeout: Per-request HTTP timeout in seconds.
    :return: Immutable API result collection.
    """

    blue_records = [record for record in records if record.architecture == "Blue"]
    results = Parallel(n_jobs=max_workers, backend="threading")(delayed(fetch_enzyme_api_permission)(record, api_token, timeout) for record in tqdm(blue_records, desc="Checking Enzyme Blue API"))
    return tuple(results)


def create_api_comparison_table(
    records: Iterable[EnzymePermissionRecord],
    api_results: Iterable[EnzymeApiPermissionResult],
) -> list[dict[str, str]]:
    """Create API errors and adapter/API mismatches for operator review.

    :param records: Persisted Enzyme permission records.
    :param api_results: Official Blue API results keyed by vault identity.
    :return: Table containing only mismatches and failed checks.
    """

    records_by_spec = {record.spec: record for record in records}
    table = []
    for result in api_results:
        record = records_by_spec[result.spec]
        if result.error is None and result.permission is record.permission:
            continue
        table.append(
            {
                "Chain": get_chain_name(result.spec.chain_id),
                "Address": result.spec.vault_address,
                "Name": record.name,
                "Database": record.permission.value,
                "Enzyme API": result.permission.value,
                "API policies": ", ".join(result.policy_names) or "—",
                "Error": result.error or "—",
            }
        )
    return table


def main() -> None:
    """Load the database and print Enzyme whitelist summary tables.

    :return: None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    records = tuple(iter_enzyme_permission_records(VaultDatabase.read(vault_db_path)))

    print("Enzyme deposit-permission summary")
    print(tabulate(create_summary_table(records), headers="keys", tablefmt="github"))

    if parse_boolean_env(os.environ.get("DETAILS")):
        details = [
            {
                "Chain": get_chain_name(record.spec.chain_id),
                "Architecture": record.architecture,
                "Address": record.spec.vault_address,
                "Name": record.name,
                "Permission": record.permission.value,
                "Notes": record.notes or "—",
            }
            for record in records
        ]
        print("\nEnzyme deposit-permission details")
        print(tabulate(details, headers="keys", tablefmt="github"))

    if parse_boolean_env(os.environ.get("CHECK_ENZYME_API")):
        api_token = os.environ.get("ENZYME_API_TOKEN")
        if not api_token:
            message = "CHECK_ENZYME_API=true requires ENZYME_API_TOKEN"
            raise RuntimeError(message)
        api_results = fetch_enzyme_api_permissions(
            records,
            api_token,
            max_workers=int(os.environ.get("MAX_WORKERS", "8")),
            timeout=float(os.environ.get("API_TIMEOUT", "30")),
        )
        comparisons = create_api_comparison_table(records, api_results)
        permissions_by_spec = {record.spec: record.permission for record in records}
        matched = sum(result.error is None and result.permission is permissions_by_spec[result.spec] for result in api_results)
        print(f"\nEnzyme Blue API comparison: {matched} matched, {len(comparisons)} mismatched or failed")
        print(tabulate(comparisons, headers="keys", tablefmt="github"))


if __name__ == "__main__":
    main()
