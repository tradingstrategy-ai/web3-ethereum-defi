"""Run normal capacity batches for one chain in an isolated local catalogue.

Credentials come from the usual JSON_RPC_<CHAIN> environment variable. Set
CHAIN_ID (default 143, Monad), and either VAULT_METADATA_JSON to a published
vault export or VAULT_DATABASE to a scanner pickle (default pipeline catalogue).
The published export covers its listed vaults, not every discovered contract.

STATE_DIR defaults to data/withdrawal-capacity/<chain>/<UTC run timestamp>.
Reusing it resumes the saved snapshot without rereading the source and keeps
daily/weekly timestamps. A new selection needs a new STATE_DIR.
MAX_BATCHES defaults to one normal tick, MAX_VAULTS_PER_CHAIN to 100 and
MAX_WORKERS to at most four. Increase MAX_BATCHES for manual catch-up.
COLOUR accepts auto, always or never. Source files are never modified and
there is no discovery, historical-state scan, publication or transaction.

Example::

    source .local-test.env
    CHAIN_ID=143 VAULT_METADATA_JSON=vault-metadata.json MAX_BATCHES=2 \
        poetry run python scripts/erc-4626/refresh-withdrawal-capacity.py
"""

import hashlib
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from time import monotonic

from tabulate import tabulate
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir
from eth_defi.vault.withdrawal_capacity import configured_capacity_threshold, enrich_withdrawal_capacity
from eth_defi.vault.withdrawal_capacity_refresh import refresh_withdrawal_capacity, select_due_capacity_vaults

logger = logging.getLogger(__name__)


def load_snapshot(content: bytes, chain_id: int, *, published_json: bool) -> VaultDatabase:
    """Select one chain, retaining supplied features and denomination TVL.

    :param content: Trusted scanner pickle or published JSON bytes, read once.
    :param chain_id: EVM chain whose listed vaults should be checked.
    :param published_json: Interpret the source as the published JSON schema.
    :return: Isolated chain-only catalogue for the normal refresh function.
    """
    database = VaultDatabase()
    if published_json:
        values = json.loads(content)["vaults"]
        for row in values.values() if isinstance(values, dict) else values:
            if int(row["chain_id"]) != chain_id:
                continue
            address = row["address"]
            assert Web3.is_address(address), f"Not an EVM vault address: {address}"
            spec = VaultSpec(chain_id, address)
            assert spec not in database.rows, f"Duplicate vault: {spec}"
            nav = row.get("current_nav")
            database.rows[spec] = {"Name": row["name"], "Address": address, "Protocol": row.get("protocol", "unknown"), "Denomination": row["denomination"], "_source_denomination": row.get("source_denomination") or row["denomination"], "NAV": Decimal(str(nav)) if nav is not None else None, "features": {ERC4626Feature[name] for name in row["features"]}}
    else:
        database.rows = {spec: row for spec, row in VaultDatabase.read(BytesIO(content)).rows.items() if spec.chain_id == chain_id}
    assert database.rows, f"No vaults for chain {chain_id} in the input catalogue"
    return database


def run_refresh(source: Path, state_dir: Path, chain_id: int, *, published_json: bool = False, max_batches: int = 1, batch_size: int = 100, max_workers: int = 4) -> dict:  # ruff: ignore[too-many-locals] - Keep the snapshot lifecycle, batches and final report in one coordinator.
    """Run production selection/read/persistence against a diagnostic snapshot.

    The source bytes are read once for hashing and loading on initialisation.
    Resume uses the saved catalogue and input identity without rereading the
    source; start a new STATE_DIR to use a new selection. Each batch uses
    current chain state and persists normal due timestamps.

    :param source: Read-only input on initialisation; ignored when resuming.
    :param state_dir: Local diagnostic state directory, outside the pipeline.
    :param chain_id: Chain to refresh.
    :param published_json: Input uses the published vault export schema.
    :param max_batches: Maximum normal batches in this invocation.
    :param batch_size: Per-batch vault limit.
    :param max_workers: Reader threads, capped at four.
    :return: JSON-ready observations, scheduling state and summary counts.
    """
    assert chain_id > 0 and min(max_batches, batch_size, max_workers) > 0, "Chain and worker/batch limits must be positive"
    assert state_dir.resolve() != get_pipeline_data_dir().resolve(), "Use an isolated STATE_DIR outside the scanner pipeline"
    state_path = state_dir / "vault-metadata-db.pickle"
    manifest = state_dir / "input.json"
    assert source.resolve() not in {state_path.resolve(), manifest.resolve()}, "The source catalogue must remain read-only"
    threshold = configured_capacity_threshold()
    started_at = native_datetime_utc_now()
    started = monotonic()
    state_dir.mkdir(parents=True, exist_ok=True)
    with wait_other_writers(state_dir / "scan-pipeline", timeout=1):
        if state_path.exists():
            assert manifest.exists(), "Incomplete STATE_DIR: input.json is missing; choose a new directory"
            identity = json.loads(manifest.read_text(encoding="utf-8"))
            assert identity["chain_id"] == chain_id, "STATE_DIR belongs to another chain; choose a new directory"
            database = VaultDatabase.read(state_path)
        else:
            assert not manifest.exists(), "Incomplete STATE_DIR; choose a new directory"
            content = source.read_bytes()
            identity = {"chain_id": chain_id, "source_sha256": hashlib.sha256(content).hexdigest(), "published_json": published_json, "source": str(source.resolve())}
            database = load_snapshot(content, chain_id, published_json=published_json)
            database.write(state_path)
            manifest.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
        refreshed, batches, web3 = 0, 0, None
        for _ in range(max_batches):
            now = native_datetime_utc_now()
            targets = select_due_capacity_vaults(database, now=now, max_vaults_per_chain=batch_size).get(chain_id, [])
            if not targets:
                break
            if web3 is None:
                web3 = create_multi_provider_web3(read_json_rpc_url(chain_id))
            refreshed += refresh_withdrawal_capacity(web3, state_path, database=database, targets=targets, now=now, max_workers=min(max_workers, 4))
            batches += 1
        now = native_datetime_utc_now()
        remaining = select_due_capacity_vaults(database, now=now, max_vaults_per_chain=len(database.rows)).get(chain_id, [])
        records = [{"chain_id": spec.chain_id, "address": spec.vault_address, "name": row.get("Name", ""), "protocol": row.get("Protocol", "unknown"), "denomination": row.get("Denomination")} for spec, row in sorted(database.rows.items(), key=lambda item: item[0].vault_address)]
        enrich_withdrawal_capacity(records, database, threshold=threshold, now=now)
        for record in records:
            saved = database.rows[VaultSpec(chain_id, record["address"])].get("_withdrawal_capacity_observation")
            checked_at = saved.get("checked_at") if saved else None
            interval = saved.get("refresh_interval_seconds") if saved else None
            record["next_due_at"] = (datetime.fromisoformat(checked_at) + timedelta(seconds=interval)).isoformat() if checked_at and interval else None
            if not saved:
                record["withdrawal_capacity_observation"]["status"] = "unchecked"
        return {**identity, "source": identity.get("source", str(source.resolve())), "state_path": str(state_path.resolve()), "started_at": started_at.isoformat(), "completed_at": now.isoformat(), "elapsed_seconds": round(monotonic() - started, 3), "refreshed": refreshed, "batches": batches, "remaining_due": len(remaining), "vault_count": len(records), "read_counts": dict(Counter(record["withdrawal_capacity_observation"].get("status", "unknown") for record in records)), "cadence_counts": dict(Counter(str(record["withdrawal_capacity_observation"].get("refresh_interval_seconds", "unknown")) for record in records)), "rows": records}


def render_table(report: dict, *, colour: bool = False) -> str:
    """Show warnings and persisted scheduling alongside denomination amounts.

    :param report: JSON-ready output of :py:func:`run_refresh`.
    :param colour: Add ANSI green/red checkmarks.
    :return: One tabulated row per vault, preserving unknown checks.
    """
    table = []
    for row in report["rows"]:
        limit, observation = row["withdrawal_capacity_limit"], row["withdrawal_capacity_observation"]
        flags = [limit[key] for key in ("capacity_warning", "pause_warning", "tiny_tvl")]
        clear = all(value is False for value in flags)
        mark = "✓" if clear else "✗"
        if colour:
            mark = f"\033[{'32' if clear else '31'}m{mark}\033[0m"
        amounts = [f"{Decimal(limit[key]):,.6f}" if limit[key] is not None else "?" for key in ("current_capacity", "current_tvl")]
        cadence = observation.get("refresh_interval_seconds")
        table.append([mark, row["address"], row["name"][:50], row["protocol"], observation.get("status"), *["?" if value is None else "yes" if value else "no" for value in flags], *amounts, row["denomination"], f"{cadence // 86400}d" if cadence else "?", row["next_due_at"] or "due"])
    return tabulate(table, headers=["", "Vault", "Name", "Protocol", "Read", "Capacity!", "Pause!", "Tiny!", "Capacity", "TVL", "Token", "Cadence", "Next check UTC"], disable_numparse=True)


def main() -> None:
    """Run the bounded diagnostic from environment settings and print its report.

    Own assertion messages explain configuration/state refusals. Provider error
    text stays redacted because it can contain authenticated RPC URLs.
    """
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    logging.getLogger("eth_defi.vault.withdrawal_capacity_refresh").setLevel(logging.INFO)
    logging.getLogger("tqdm_loggable").setLevel(logging.INFO)
    try:  # ruff: ignore[too-many-statements-in-try-clause] - Keep settings and provider errors behind the URL-redacting CLI boundary.
        chain_id = int(os.environ.get("CHAIN_ID", "143"))
        json_source = os.environ.get("VAULT_METADATA_JSON")
        source = Path(json_source or os.environ.get("VAULT_DATABASE", get_pipeline_data_dir() / "vault-metadata-db.pickle")).expanduser()
        state_dir = Path(os.environ.get("STATE_DIR", f"data/withdrawal-capacity/{chain_id}/{native_datetime_utc_now():%Y%m%dT%H%M%S%f}")).expanduser()
        colour = os.environ.get("COLOUR", "auto")
        assert colour in {"auto", "always", "never"}, "COLOUR must be auto, always or never"
        report = run_refresh(source, state_dir, chain_id, published_json=bool(json_source), max_batches=int(os.environ.get("MAX_BATCHES", "1")), batch_size=int(os.environ.get("MAX_VAULTS_PER_CHAIN", "100")), max_workers=int(os.environ.get("MAX_WORKERS", min(4, os.process_cpu_count() or 1))))
    except AssertionError as error:
        detail = str(error) if "://" not in str(error) else "AssertionError (URL-bearing details redacted)"
        raise SystemExit(f"Invalid capacity diagnostic settings: {detail}") from None
    except Exception as error:
        # Provider exceptions may include authenticated URLs; never print them.
        raise SystemExit(f"Capacity diagnostic failed ({type(error).__name__}); check input paths, settings and RPC configuration") from None
    output = state_dir / f"report-{native_datetime_utc_now():%Y%m%dT%H%M%S%f}.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(render_table(report, colour=colour == "always" or (colour == "auto" and sys.stdout.isatty())))
    print("\n✓ = all three warnings known and clear; ✗ = warning and/or unknown; ? = unknown.")
    print("Summary:", json.dumps({key: value for key, value in report.items() if key not in {"rows", "source_sha256"}}, sort_keys=True))
    print("Report:", output)


if __name__ == "__main__":
    main()
