#!/usr/bin/env python3
"""Tabulate Nest routes against local scanner metadata and the vault export.

Nest's TVL and SEC 30-day yield are product-wide snapshots. Export TVL and CAGR
come from the selected entrypoint's price history. The public export lists each
share token once, preferring Plume, Ethereum, then other chains alphabetically;
within that chain it prefers USDC, USDT, then pUSD. This catalogue audit also
shows alternative chain and deposit routes without export rows. Missing values
stay missing.

Set ``VAULT_DB_PATH`` and ``VAULT_EXPORT_PATH`` to audit local copies. Set
``ACTIVE_ONLY=false`` to include hidden and disabled catalogue routes.
"""

import datetime
import json
import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.erc_4626.vault_protocol.nest.offchain_metadata import NEST_CHAIN_NAMES, NEST_CURATOR_SLUG, NestVaultMetadata, fetch_nest_vaults
from eth_defi.types import Percent
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def format_usd(value: float | None) -> str:
    """Format an optional USD amount.

    Preserve the difference between zero TVL and an unavailable observation.

    :param value: USD amount, or ``None``.
    :return: Compact amount or a missing-value marker.
    """
    return f"${value:,.0f}" if value is not None else "-"


def format_percent(value: Percent | None) -> str:
    """Format an optional fractional annualised yield.

    Display negative and zero returns unchanged; missing returns stay missing.

    :param value: Annualised yield as a fraction, or ``None``.
    :return: Percentage or a missing-value marker.
    """
    return f"{value:.2%}" if value is not None else "-"


def read_export(path: Path) -> dict[str, dict]:
    """Index Nest records in a local JSON vault export.

    Missing exports permit a metadata-only audit. Invalid JSON or a malformed
    export raises an error rather than reporting empty coverage.

    :param path: JSON export path.
    :return: Records indexed by chain and address, or empty if absent.
    """
    if not path.exists():
        logger.warning("Vault export is absent: %s", path)
        return {}
    document = json.loads(path.read_text())
    return {f"{entry['chain_id']}:{entry['address'].lower()}": entry for entry in document["vaults"] if entry.get("protocol_slug") == "nest"}


def select_cagr(export: dict, field: str) -> tuple[Percent | None, str]:
    """Select an exported CAGR and label its fee basis.

    Prefer net returns, including zero or negative values. Use gross only when
    the net metric is unavailable; do not substitute a target yield.

    :param export: One vault JSON record, or an empty mapping.
    :param field: Gross export field, such as ``one_month_cagr`` or ``cagr``.
    :return: Annualised fractional return and ``net``, ``gross`` or ``-``.
    """
    for key, basis in ((f"{field}_net", "net"), (field, "gross")):
        if (value := export.get(key)) is not None:
            return value, basis
    return None, "-"


def build_rows(routes: dict[str, NestVaultMetadata], vault_db: VaultDatabase, exported: dict[str, dict], *, active_only: bool = True) -> list[dict[str, str]]:
    """Join the first-party catalogue, scanner rows and optional export.

    Names are checked against the local scanner row. Chain and curator checks
    use the first-party route and reviewed curator identity respectively.

    :param routes: Current Nest routes keyed by chain and address.
    :param vault_db: Locally migrated scanner database.
    :param exported: Local JSON export indexed by chain and address.
    :param active_only: Exclude inactive products when true.
    :return: Product, chain and denomination sorted audit rows.
    """
    result: list[dict[str, str]] = []
    for key, route in routes.items():
        if active_only and route["status"] != "active":
            continue
        spec = VaultSpec(route["chain_id"], route["vault_address"])
        saved = vault_db.rows.get(spec)
        export = exported.get(key, {})
        expected_name = saved.get("Name") if saved else route.get("display_name") or route["name"]
        exported_name = export.get("name")
        one_month_cagr, cagr_basis = select_cagr(export, "one_month_cagr")
        all_time_cagr, all_time_basis = select_cagr(export, "cagr")
        result.append(
            {
                "Name": exported_name or expected_name,
                "Chain": NEST_CHAIN_NAMES.get(route["chain_id"], str(route["chain_id"])),
                "Asset": route["asset_symbol"],
                "Curator": export.get("curator_name") or "-",
                "TVL": format_usd(export.get("current_nav")),
                "1M CAGR": format_percent(one_month_cagr),
                "CAGR basis": cagr_basis,
                "All-time CAGR": format_percent(all_time_cagr),
                "All-time basis": all_time_basis,
                "History since": str(export.get("first_updated_at") or "-")[:10],
                "Share price": f"{export['last_share_price']:.6f}" if export.get("last_share_price") is not None else "-",
                "Price as-of": str(export.get("last_updated_at") or "-")[:10],
                "Name match": "yes" if exported_name == expected_name else "NO" if export else "-",
                "Chain match": "yes" if export.get("chain_id") == route["chain_id"] else "NO" if export else "-",
                "Curator match": "yes" if export.get("curator_slug") == NEST_CURATOR_SLUG else "NO" if export else "-",
                "DB curator": saved.get("_curator_slug") or "-" if saved else "-",
                "Nest product TVL": format_usd(route.get("tvl_usd")),
                "Nest SEC30d": format_percent(route.get("reported_apy")),
                "DB": "yes" if saved else "NO",
                "Export": "yes" if export else "NO",
                "Address": route["vault_address"],
            }
        )
    return sorted(result, key=lambda row: (row["Name"], row["Chain"], row["Asset"], row["Address"]))


def main() -> None:
    """Print the Nest route audit and coverage counts.

    Fetch the current first-party EVM catalogue and compare it with existing
    local files without modifying scanner metadata or price data.

    :return: ``None`` after printing the comparison.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))
    db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    export_path = Path(os.environ.get("VAULT_EXPORT_PATH", str(db_path.parent / "top_vaults_by_chain.json"))).expanduser()
    routes = fetch_nest_vaults(max_cache_duration=datetime.timedelta(0), allow_stale=False)
    rows = build_rows(routes, VaultDatabase.read(db_path), read_export(export_path), active_only=os.environ.get("ACTIVE_ONLY", "true").lower() != "false")
    print(tabulate(rows, headers="keys", tablefmt="github"))
    print(f"Routes: {len(rows)}; metadata rows: {sum(row['DB'] == 'yes' for row in rows)}; export rows: {sum(row['Export'] == 'yes' for row in rows)}")
    print(f"Export mismatches: names {sum(row['Name match'] == 'NO' for row in rows)}, chains {sum(row['Chain match'] == 'NO' for row in rows)}, curators {sum(row['Curator match'] == 'NO' for row in rows)}")
    print(f"1M CAGR coverage: {sum(row['CAGR basis'] != '-' for row in rows)} routes; net {sum(row['CAGR basis'] == 'net' for row in rows)}, gross {sum(row['CAGR basis'] == 'gross' for row in rows)}")
    print(f"All-time CAGR coverage: {sum(row['All-time basis'] != '-' for row in rows)} routes")
    print("All-time means the available collected price history, starting at History since; it may be shorter than the vault lifetime.")
    print("Coverage counts refer to EVM entrypoints. Nest also publishes Solana deployments, which this scanner does not collect.")
    print("Nest TVL and SEC30d yield are product-wide API snapshots; SEC30d can use a month-end window and differ from the website rolling NAV yield. The export lists each share token once, preferring Plume, Ethereum, then chains alphabetically; within that chain it prefers USDC, USDT, then pUSD. TVL and CAGR belong to the selected chain; omitted chain balances are excluded. Alternative chain and deposit routes remain in this audit without export rows; do not sum repeated Nest API TVL.")


if __name__ == "__main__":
    main()
