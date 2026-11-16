"""Manually inspect withdrawal buffers without trading or changing scanner state.

Set ``VAULTS`` to space-separated CHAIN-ADDRESS IDs or ``VAULT_LIST_FILE`` to JSON.
JSON is a list of IDs or objects with ``chain_id``, ``address``, optional ``name``
and optional catalogue ``features`` names. Supplied features select the adapter;
otherwise the factory probes the protocol. Each chain uses one recent pinned
block. Native pool IDs and failed reads remain visible in the output.

Example::

    source .local-test.env
    VAULT_LIST_FILE=vaults.json OUTPUT_JSON=capacity.json \
        poetry run python scripts/erc-4626/check-withdrawal-capacity.py

The first column is green only when capacity, pause and USD TVL checks are all
known and clear. A red cross means a warning OR an incomplete assessment, as
distinguished by the status column. This is a buffer screen, not an execution
guarantee: account permissions, settlement windows and fees need separate checks.
"""

import json
import logging
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from joblib import Parallel, delayed
from tabulate import tabulate
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.withdrawal_capacity import WithdrawalCapacityLimit, assess_withdrawal_capacity, configured_capacity_threshold, validate_threshold


def load_vaults(path: Path | None, ids: list[str]) -> list[dict]:
    """Normalise explicit inputs, rejecting duplicates and malformed identities."""
    values = json.loads(path.read_text()) if path else []
    if not isinstance(values, list):
        message = "Vault list must be a JSON array"
        raise ValueError(message)
    rows, seen = [], set()
    for value in values + ids:
        if isinstance(value, str):
            chain, address = value.split("-", 1)
            value = {"chain_id": int(chain), "address": address}
        if not isinstance(value, dict):
            message = "Vault list entries must be IDs or objects"
            raise ValueError(message)
        chain = int(value.get("chain_id", value.get("chain")))
        address = value.get("address")
        if not isinstance(address, str):
            message = "Vault addresses must be strings"
            raise ValueError(message)
        address = address.lower()
        if Web3.is_address(address) and not address.startswith("0x"):
            address = "0x" + address
        if chain <= 0 or not address:
            message = "Vault identities require a positive chain and address"
            raise ValueError(message)
        if address.startswith("0x") and not Web3.is_address(address):
            message = "Malformed EVM vault address"
            raise ValueError(message)
        if "features" in value:
            features = value["features"]
            if not isinstance(features, list) or any(not isinstance(name, str) or name not in ERC4626Feature.__members__ for name in features):
                message = "features must be a list of known ERC4626Feature names; omit it to probe"
                raise ValueError(message)
        key = (chain, address)
        if key in seen:
            raise ValueError(f"Duplicate vault: {chain}-{address}")
        seen.add(key)
        rows.append({**value, "chain_id": chain, "address": address, "id": f"{chain}-{address}"})
    if not rows:
        message = "Supply VAULTS or VAULT_LIST_FILE"
        raise ValueError(message)
    return rows


def assessment_status(limit: dict) -> str:
    """Require all independent checks to be negative before showing green."""
    checks = [limit[key] for key in ("capacity_warning", "pause_warning", "tiny_tvl")]
    warning = any(value is True for value in checks)
    unknown = any(value is None for value in checks)
    if warning:
        return "warning + unknown" if unknown else "warning"
    return "unknown" if unknown else "clear"


def fetch_capacity_report(rows: list[dict], threshold: Decimal, max_workers: int) -> dict:
    """Query the universal capacity API with per-chain block pinning.

    Provider and adapter failures are isolated per chain or vault. Error text
    records exception types only, preventing authenticated RPC URLs from being
    written into the report. No catalogue or price data is modified.

    :param rows: Explicit vault identities and optional catalogue adapter features.
    :param threshold: Minimum buffer/TVL fraction, validated before any RPC.
    :param max_workers: Maximum concurrent reads within each chain.
    :return: JSON-ready observations, assessments, source blocks and counts.
    """
    threshold = validate_threshold(threshold)
    started = datetime.now(UTC).isoformat()
    results, blocks = {}, {}

    def record(row: dict, limit: WithdrawalCapacityLimit) -> dict:
        data = limit.to_dict()
        return {**row, "status": assessment_status(data), "withdrawal_capacity_limit": data, "withdrawal_capacity_observation": limit.observation}

    def unknown(row: dict, reason: str, status: str = "unsupported") -> dict:
        structural = "forty_acres_like" in row.get("features", [])
        limit = assess_withdrawal_capacity({"status": status, "reason": reason, "structural_warning": structural}, threshold)
        return record(row, limit)

    for chain in sorted({row["chain_id"] for row in rows}):
        targets = [row for row in rows if row["chain_id"] == chain]
        # Hypercore vaults have Ethereum-shaped addresses but are not EVM contracts.
        evm = [row for row in targets if chain not in {9998, 9999} and Web3.is_address(row["address"]) and not any(name.endswith("_native") for name in row.get("features", []))]
        for row in targets:
            if row not in evm:
                results[row["id"]] = unknown(row, "Native/non-EVM pool has no EVM capacity reader")
        if not evm:
            continue
        try:  # ruff: ignore[too-many-statements-in-try-clause] - Isolate all provider setup failures to this chain.
            web3 = create_multi_provider_web3(read_json_rpc_url(chain), retries=2, request_kwargs={"timeout": (5, 30)})
            if web3.eth.chain_id != chain:
                message = "RPC chain mismatch"
                raise ValueError(message)
            block = web3.eth.get_block(max(0, web3.eth.block_number - 3))
            number = block["number"]
            blocks[str(chain)] = {"number": number, "hash": block["hash"].hex(), "timestamp": block["timestamp"]}
        except Exception as error:
            for row in evm:
                results[row["id"]] = unknown(row, f"Chain provider unavailable ({type(error).__name__})", "failed")
            continue

        def fetch_row(row: dict, web3: Web3 = web3, number: int = number) -> dict:
            try:
                features = row.get("features")
                features = {ERC4626Feature[name] for name in features} if features is not None else detect_vault_features(web3, row["address"], verbose=False)
                vault = create_vault_instance(web3, row["address"], features=features, default_block_identifier=number)
                if vault is None:
                    return unknown(row, "Vault factory has no supported adapter")
                return record(row, vault.fetch_withdrawal_capacity_limit(block_identifier=number, capacity_threshold=threshold))
            except Exception as error:
                return unknown(row, f"Capacity query failed ({type(error).__name__})", "failed")

        print(f"Chain {chain}: reading {len(evm)} vaults at block {number:,}", file=sys.stderr, flush=True)
        with Parallel(n_jobs=max_workers, backend="threading", return_as="generator_unordered") as pool:
            reads = pool(delayed(fetch_row)(row) for row in evm)
            for result in tqdm(reads, total=len(evm), desc=f"Capacity chain {chain}"):
                results[result["id"]] = result
    ordered = [results[row["id"]] for row in rows]
    return {"started_at": started, "completed_at": datetime.now(UTC).isoformat(), "capacity_threshold": str(threshold), "blocks": blocks, "summary": dict(Counter(row["status"] for row in ordered)), "rows": ordered}


def render_table(report: dict, *, colour: bool = True) -> str:
    """Render a leftmost checkmark and separate warning, amount and read states."""
    table = []

    def flag(value: bool | None) -> str:  # ruff: ignore[boolean-type-hint-positional-argument] - A tri-state data value, not an option switch.
        return "?" if value is None else "yes" if value else "no"

    def amount(value: str | None) -> str:
        return "?" if value is None else f"{Decimal(value):,.6f}"

    for row in report["rows"]:
        limit = row["withdrawal_capacity_limit"]
        observation = row["withdrawal_capacity_observation"]
        clear = row["status"] == "clear"
        mark = "✓" if clear else "✗"
        if colour:
            mark = f"\033[{'32' if clear else '31'}m{mark}\033[0m"
        capacity, tvl = limit["current_capacity"], limit["current_tvl"]
        ratio = f"{Decimal(capacity) / Decimal(tvl):.2%}" if capacity is not None and tvl is not None and Decimal(tvl) > 0 else "?"
        table.append([mark, row["id"], row.get("name", ""), row["status"], observation.get("status", "unknown"), *[flag(limit[key]) for key in ("capacity_warning", "pause_warning", "tiny_tvl")], amount(capacity), amount(tvl), observation.get("denomination_symbol", "?"), ratio, limit["capacity_reason"]])
    return tabulate(table, headers=["", "Vault", "Name", "Status", "Read", "Capacity!", "Pause!", "Tiny!", "Capacity", "TVL", "Token", "Buffer", "Reason"], disable_numparse=True)


def main() -> None:
    """Run an explicit read-only scan; optionally preserve machine-readable output."""
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    logging.getLogger("tqdm_loggable").setLevel(logging.INFO)
    try:  # ruff: ignore[too-many-statements-in-try-clause] - Validate all settings before network reads.
        input_path = os.environ.get("VAULT_LIST_FILE")
        rows = load_vaults(Path(input_path) if input_path else None, os.environ.get("VAULTS", "").split())
        threshold = configured_capacity_threshold()
        max_workers = int(os.environ.get("MAX_WORKERS", min(4, os.process_cpu_count() or 1)))
        colour = os.environ.get("COLOUR", "auto")
        if max_workers < 1 or colour not in {"auto", "always", "never"}:
            message = "MAX_WORKERS must be positive; COLOUR must be auto, always or never"
            raise ValueError(message)
    except (ValueError, KeyError, TypeError, InvalidOperation) as error:
        raise SystemExit(f"Invalid scan settings: {error}") from None
    report = fetch_capacity_report(rows, threshold, max_workers)
    output_path = os.environ.get("OUTPUT_JSON")
    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(render_table(report, colour=colour == "always" or (colour == "auto" and sys.stdout.isatty())))
    print("\n✓ green = all three checks known and clear; ✗ red = warning and/or unknown. ? = unknown.")
    print("Summary:", json.dumps(report["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
