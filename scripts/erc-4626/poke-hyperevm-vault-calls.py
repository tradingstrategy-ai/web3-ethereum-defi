"""Manually poke HyperEVM vault calls used by the historical scanner.

This operator script reads HyperEVM vaults from the local vault metadata
database and executes every per-vault call produced by the historical
ERC-4626 reader as an isolated ``eth_call``. The purpose is to identify
vault/function pairs that can run out of gas and poison a larger Multicall3
batch.

The script is read-only for pipeline state. It writes only diagnostic output.
Vaults with out-of-gas calls must be blacklisted in ``eth_defi/vault/risk.py``
so they are excluded from reports and future historical scanner multicalls.

Usage:

.. code-block:: shell

    source .local-test.env
    poetry run python scripts/erc-4626/poke-hyperevm-vault-calls.py

Environment variables:

- ``JSON_RPC_URL``: Optional. HyperEVM RPC URL. Defaults to
  ``JSON_RPC_HYPERLIQUID`` via :func:`eth_defi.provider.env.read_json_rpc_url`.
- ``VAULT_DB_PATH``: Optional. Defaults to the pipeline vault metadata pickle.
- ``OUTPUT_CSV``: Optional. Defaults to
  ``logs/hyperevm-vault-call-poke.csv``.
- ``OUTPUT_JSONL``: Optional. Defaults to
  ``logs/hyperevm-vault-call-poke.jsonl``.
- ``BLOCK_NUMBER``: Optional. Decimal, hex, or ``latest``. Defaults to the
  current latest block number resolved once at startup.
- ``CALL_GAS``: Optional. Gas cap for each isolated ``eth_call``. Defaults to
  ``2000000`` to mirror HyperEVM small block constraints.
- ``MAX_ESTIMATED_GAS``: Optional. Above this estimate the call is marked as
  suspected gas poison. Defaults to ``CALL_GAS``.
- ``ESTIMATE_GAS``: Optional. Run ``eth_estimateGas`` before the direct
  ``eth_call``. Defaults to ``true``.
- ``MIN_DEPOSIT_THRESHOLD``: Optional. Production scanner activity filter.
  Defaults to ``5``. Set to ``0`` to inspect every HyperEVM row.
- ``VAULT_ID``: Optional. Comma-separated ``chain_id-address`` filters.
- ``LIMIT``: Optional. Maximum number of vaults to inspect.
- ``LOG_LEVEL``: Optional. Defaults to ``info``.
"""

import csv
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from eth_typing import BlockIdentifier
from tabulate import tabulate
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, get_vault_protocol_name, passes_price_scan_activity_filter
from eth_defi.event_reader.fast_json_rpc import get_last_headers
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

logger = logging.getLogger(__name__)

HYPEREVM_CHAIN_ID = 999
DEFAULT_CALL_GAS = 2_000_000
MAX_PROBLEMATIC_TABLE_ROWS = 50

OUT_OF_GAS_CLUES = (
    "out of gas",
    "outofgas",
    "basicoutofgas",
    "exceeds block gas limit",
    "gas required exceeds",
    "intrinsic gas too low",
    "intrinsic gas too high",
    "gas uint64 overflow",
)


@dataclass(slots=True)
class VaultProbeResult:
    """One diagnostic row for a vault call probe.

    :param chain_id:
        EVM chain id.
    :param block_number:
        Block identifier used for the direct call.
    :param vault_address:
        Vault being inspected.
    :param vault_name:
        Human-readable vault name from the metadata database.
    :param protocol:
        Protocol name inferred from vault features.
    :param function:
        Historical reader function label.
    :param call_index:
        Position of the call in the reader's call list.
    :param target_address:
        Contract address receiving the direct ``eth_call``.
    :param calldata:
        ABI-encoded call data.
    :param gas_limit:
        Gas limit used for the direct call.
    :param gas_estimate:
        Optional ``eth_estimateGas`` result.
    :param status:
        ``success``, ``reverted``, ``skipped``, ``unsupported`` or ``error``.
    :param out_of_gas:
        Whether the result looks like a gas poisoner.
    :param error:
        Short error message, if any.
    :param rpc_headers:
        Last RPC response headers captured by the fast JSON-RPC provider.
    """

    chain_id: int
    block_number: str
    vault_address: str
    vault_name: str
    protocol: str
    function: str
    call_index: int
    target_address: str
    calldata: str
    gas_limit: int
    gas_estimate: int | None
    status: str
    out_of_gas: bool
    error: str
    rpc_headers: str


@dataclass(slots=True, frozen=True)
class ProbeSettings:
    """Settings that control how individual vault calls are executed.

    :param block_identifier:
        Block at which to test scanner calls.
    :param gas_limit:
        Gas cap for direct ``eth_call``.
    :param max_estimated_gas:
        Gas estimate threshold for marking gas poisoners.
    :param estimate_gas:
        Whether to run ``eth_estimateGas`` before direct calls.
    """

    block_identifier: BlockIdentifier
    gas_limit: int
    max_estimated_gas: int
    estimate_gas: bool


@dataclass(slots=True, frozen=True)
class ScriptConfig:
    """Filesystem and filtering configuration for the manual script.

    :param vault_db_path:
        Vault metadata database path.
    :param output_csv:
        CSV report path.
    :param output_jsonl:
        JSON Lines report path.
    :param min_deposit_threshold:
        Production scanner activity threshold.
    :param limit:
        Optional maximum number of vaults to inspect.
    """

    vault_db_path: Path
    output_csv: Path
    output_jsonl: Path
    min_deposit_threshold: int
    limit: int


def env_bool(name: str, *, default: bool) -> bool:
    """Parse an environment variable as a boolean.

    Environment variables are used for manual scripts in this repository
    instead of command line parsers. Accepted truthy values are common shell
    spellings.

    :param name:
        Environment variable name.
    :param default:
        Value used when the variable is missing.
    :return:
        Parsed boolean value.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def parse_block_identifier(web3: Web3) -> BlockIdentifier:
    """Resolve the block identifier used for poking vault calls.

    The default resolves ``latest`` once so all vaults are tested against the
    same block. ``BLOCK_NUMBER=latest`` keeps Web3's dynamic latest behaviour.

    :param web3:
        Web3 connection used to resolve the latest block number.
    :return:
        Block identifier accepted by Web3.py.
    """
    raw_value = os.environ.get("BLOCK_NUMBER")
    if raw_value is None:
        return web3.eth.block_number
    if raw_value == "latest":
        return "latest"
    return int(raw_value, 0)


def get_error_text(exc: BaseException) -> str:
    """Return a compact error string for CSV output.

    RPC providers often return large structured errors. The report keeps the
    first line only so the CSV remains readable while JSONL still carries
    response headers for deeper diagnostics.

    :param exc:
        Exception raised by Web3.py or requests.
    :return:
        Shortened error text.
    """
    return str(exc).replace("\n", " ")[:500]


def is_out_of_gas_error(text: str) -> bool:
    """Check if an RPC error text looks gas-related.

    :param text:
        Error message from ``eth_call`` or ``eth_estimateGas``.
    :return:
        ``True`` if the message matches known gas exhaustion clues.
    """
    text = text.lower()
    return any(clue in text for clue in OUT_OF_GAS_CLUES)


def get_headers_json() -> str:
    """Return the last captured RPC response headers as JSON.

    :return:
        Compact JSON string, or an empty JSON object when unavailable.
    """
    headers = get_last_headers() or {}
    return json.dumps(headers, sort_keys=True, default=str)


def estimate_call_gas(
    web3: Web3,
    call: EncodedCall,
    block_identifier: BlockIdentifier,
) -> tuple[int | None, str | None]:
    """Estimate gas for one historical scanner call.

    :param web3:
        Web3 connection.
    :param call:
        Encoded historical scanner call.
    :param block_identifier:
        Block at which to estimate gas.
    :return:
        Tuple ``(gas_estimate, error_text)``.
    """
    try:
        estimate = web3.eth.estimate_gas(
            {
                "to": Web3.to_checksum_address(call.address),
                "from": ZERO_ADDRESS_STR,
                "data": call.data,
            },
            block_identifier=block_identifier,
        )
        return estimate, None
    except Exception as exc:
        return None, get_error_text(exc)


def poke_call(
    web3: Web3,
    call: EncodedCall,
    block_identifier: BlockIdentifier,
    gas_limit: int,
) -> tuple[bool, str | None]:
    """Execute one historical scanner call as an isolated ``eth_call``.

    :param web3:
        Web3 connection.
    :param call:
        Encoded historical scanner call.
    :param block_identifier:
        Block at which to call.
    :param gas_limit:
        Gas cap for the call.
    :return:
        Tuple ``(success, error_text)``.
    """
    try:
        call.call(
            web3,
            block_identifier=block_identifier,
            from_=ZERO_ADDRESS_STR,
            gas=gas_limit,
            ignore_error=True,
            silent_error=True,
        )
        return True, None
    except Exception as exc:
        return False, get_error_text(exc)


def get_call_function_name(call: EncodedCall) -> str:
    """Return the scanner function label for an encoded call.

    :param call:
        Encoded historical scanner call.
    :return:
        Human-readable call label.
    """
    if call.extra_data:
        return str(call.extra_data.get("function") or call.func_name)
    return call.func_name


def get_hyperevm_vault_rows(vault_db: VaultDatabase, min_deposit_threshold: int) -> list[tuple[VaultSpec, dict]]:
    """Select HyperEVM vault rows from the metadata database.

    By default this mirrors the production scanner activity filter so the
    report focuses on vaults that can enter historical price scanning. Set
    ``MIN_DEPOSIT_THRESHOLD=0`` to include every row.

    :param vault_db:
        Loaded vault metadata database.
    :param min_deposit_threshold:
        Minimum deposit event count for generic vaults.
    :return:
        Selected ``(VaultSpec, VaultRow)`` pairs.
    """
    selected = []
    for spec, row in vault_db.rows.items():
        detection: ERC4262VaultDetection = row["_detection_data"]
        if detection.chain != HYPEREVM_CHAIN_ID:
            continue

        address = detection.address.lower()
        if min_deposit_threshold > 0:
            if address not in HARDCODED_PROTOCOLS and not passes_price_scan_activity_filter(detection, min_deposit_threshold):
                continue

        selected.append((spec, row))

    return selected


def build_vault(web3: Web3, row: dict, token_cache: TokenDiskCache) -> VaultBase | None:
    """Create the same vault adapter class as the historical scanner.

    :param web3:
        Web3 connection.
    :param row:
        Vault database row.
    :param token_cache:
        Token detail cache.
    :return:
        Vault adapter, or ``None`` if the protocol is unsupported.
    """
    detection: ERC4262VaultDetection = row["_detection_data"]
    vault = create_vault_instance(
        web3,
        detection.address,
        detection.features,
        token_cache=token_cache,
    )
    if vault is not None:
        vault.first_seen_at_block = detection.first_seen_at_block
    return vault


def make_failure_result(
    row: dict,
    block_identifier: BlockIdentifier,
    status: str,
    error: str,
) -> VaultProbeResult:
    """Build a report row for vault-level failures.

    :param row:
        Vault database row.
    :param block_identifier:
        Block being tested.
    :param status:
        Failure status.
    :param error:
        Error message.
    :return:
        Diagnostic row.
    """
    detection: ERC4262VaultDetection = row["_detection_data"]
    return VaultProbeResult(
        chain_id=detection.chain,
        block_number=str(block_identifier),
        vault_address=detection.address,
        vault_name=row.get("Name") or "",
        protocol=get_vault_protocol_name(detection.features),
        function="reader_setup",
        call_index=0,
        target_address="",
        calldata="",
        gas_limit=0,
        gas_estimate=None,
        status=status,
        out_of_gas=is_out_of_gas_error(error),
        error=error,
        rpc_headers=get_headers_json(),
    )


def probe_vault(
    web3: Web3,
    row: dict,
    token_cache: TokenDiskCache,
    settings: ProbeSettings,
) -> list[VaultProbeResult]:
    """Probe all historical scanner calls for one vault.

    :param web3:
        Web3 connection.
    :param row:
        Vault database row.
    :param token_cache:
        Token detail cache.
    :param settings:
        Probe execution settings.
    :return:
        Diagnostic rows for this vault.
    """
    detection: ERC4262VaultDetection = row["_detection_data"]
    protocol = get_vault_protocol_name(detection.features)
    vault_name = row.get("Name") or ""

    try:
        vault = build_vault(web3, row, token_cache)
    except Exception as exc:
        return [make_failure_result(row, settings.block_identifier, "error", f"vault setup failed: {get_error_text(exc)}")]

    if vault is None:
        return [make_failure_result(row, settings.block_identifier, "unsupported", "unsupported or broken vault features")]

    try:
        reader = vault.get_historical_reader(stateful=True)
        calls = list(reader.construct_multicalls())
    except Exception as exc:
        return [make_failure_result(row, settings.block_identifier, "error", f"reader construction failed: {get_error_text(exc)}")]

    results = []
    for call_index, call in enumerate(calls, start=1):
        function_name = get_call_function_name(call)
        if not call.is_valid_for_block(settings.block_identifier):
            results.append(
                VaultProbeResult(
                    chain_id=detection.chain,
                    block_number=str(settings.block_identifier),
                    vault_address=detection.address,
                    vault_name=vault_name,
                    protocol=protocol,
                    function=function_name,
                    call_index=call_index,
                    target_address=call.address,
                    calldata=call.data.hex(),
                    gas_limit=settings.gas_limit,
                    gas_estimate=None,
                    status="skipped",
                    out_of_gas=False,
                    error=f"call first valid at block {call.first_block_number}",
                    rpc_headers="{}",
                )
            )
            continue

        gas_estimate = None
        estimate_error = None
        if settings.estimate_gas:
            gas_estimate, estimate_error = estimate_call_gas(web3, call, settings.block_identifier)

        success, call_error = poke_call(web3, call, settings.block_identifier, settings.gas_limit)
        if call_error:
            error = call_error
        elif estimate_error:
            error = f"estimate failed: {estimate_error}"
        else:
            error = ""
        out_of_gas = False
        if gas_estimate is not None and gas_estimate > settings.max_estimated_gas:
            out_of_gas = True
        if estimate_error and is_out_of_gas_error(estimate_error):
            out_of_gas = True
        if call_error and is_out_of_gas_error(call_error):
            out_of_gas = True

        results.append(
            VaultProbeResult(
                chain_id=detection.chain,
                block_number=str(settings.block_identifier),
                vault_address=detection.address,
                vault_name=vault_name,
                protocol=protocol,
                function=function_name,
                call_index=call_index,
                target_address=call.address,
                calldata=call.data.hex(),
                gas_limit=settings.gas_limit,
                gas_estimate=gas_estimate,
                status="success" if success else "reverted",
                out_of_gas=out_of_gas,
                error=error,
                rpc_headers=get_headers_json(),
            )
        )

    return results


def write_results(csv_path: Path, jsonl_path: Path, results: list[VaultProbeResult]) -> None:
    """Write diagnostic results to CSV and JSONL.

    :param csv_path:
        CSV output path.
    :param jsonl_path:
        JSON Lines output path.
    :param results:
        Rows to write.
    """
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=list(asdict(results[0]).keys()))
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))

    with jsonl_path.open("w", encoding="utf-8") as out:
        for result in results:
            out.write(json.dumps(asdict(result), sort_keys=True, default=str))
            out.write("\n")


def parse_vault_filter() -> set[str] | None:
    """Parse optional ``VAULT_ID`` filters.

    :return:
        Lowercase vault addresses, or ``None`` when no filter is set.
    """
    raw_value = os.environ.get("VAULT_ID")
    if raw_value is None:
        return None
    specs = [VaultSpec.parse_string(part.strip()) for part in raw_value.split(",") if part.strip()]
    return {spec.vault_address.lower() for spec in specs if spec.chain_id == HYPEREVM_CHAIN_ID}


def read_probe_settings(web3: Web3) -> ProbeSettings:
    """Read call probing settings from the environment.

    :param web3:
        Web3 connection used to resolve the default block.
    :return:
        Probe settings.
    """
    block_identifier = parse_block_identifier(web3)
    gas_limit = int(os.environ.get("CALL_GAS", str(DEFAULT_CALL_GAS)))
    max_estimated_gas = int(os.environ.get("MAX_ESTIMATED_GAS", str(gas_limit)))
    estimate_gas = env_bool("ESTIMATE_GAS", default=True)
    return ProbeSettings(
        block_identifier=block_identifier,
        gas_limit=gas_limit,
        max_estimated_gas=max_estimated_gas,
        estimate_gas=estimate_gas,
    )


def read_script_config() -> ScriptConfig:
    """Read script filesystem and filtering settings from the environment.

    :return:
        Script configuration.
    """
    min_deposit_threshold = int(os.environ.get("MIN_DEPOSIT_THRESHOLD", "5"))

    pipeline_data_dir = get_pipeline_data_dir()
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(pipeline_data_dir / "vault-metadata-db.pickle"))).expanduser()
    output_csv = Path(os.environ.get("OUTPUT_CSV", "logs/hyperevm-vault-call-poke.csv")).expanduser()
    output_jsonl = Path(os.environ.get("OUTPUT_JSONL", "logs/hyperevm-vault-call-poke.jsonl")).expanduser()
    limit = int(os.environ.get("LIMIT", "0"))
    return ScriptConfig(
        vault_db_path=vault_db_path,
        output_csv=output_csv,
        output_jsonl=output_jsonl,
        min_deposit_threshold=min_deposit_threshold,
        limit=limit,
    )


def load_selected_vault_rows(config: ScriptConfig) -> list[tuple[VaultSpec, dict]]:
    """Load and filter HyperEVM vault rows.

    :param config:
        Script configuration.
    :return:
        Selected vault rows.
    """
    assert config.vault_db_path.exists(), f"Vault database not found: {config.vault_db_path}"

    vault_db = VaultDatabase.read(config.vault_db_path)
    vault_filter = parse_vault_filter()
    min_deposit_threshold = 0 if vault_filter is not None else config.min_deposit_threshold
    vault_rows = get_hyperevm_vault_rows(vault_db, min_deposit_threshold=min_deposit_threshold)

    if vault_filter is not None:
        vault_rows = [(spec, row) for spec, row in vault_rows if spec.vault_address.lower() in vault_filter]

    if config.limit > 0:
        vault_rows = vault_rows[: config.limit]

    return vault_rows


def probe_vault_rows(
    web3: Web3,
    vault_rows: list[tuple[VaultSpec, dict]],
    settings: ProbeSettings,
) -> list[VaultProbeResult]:
    """Probe a list of vault rows.

    :param web3:
        Web3 connection.
    :param vault_rows:
        Vault metadata rows to inspect.
    :param settings:
        Probe settings.
    :return:
        Combined diagnostic rows.
    """
    token_cache = TokenDiskCache()
    results: list[VaultProbeResult] = []
    for index, (_, row) in enumerate(vault_rows, start=1):
        detection: ERC4262VaultDetection = row["_detection_data"]
        logger.info(
            "Poking vault %d/%d %s %s",
            index,
            len(vault_rows),
            detection.address,
            row.get("Name") or "",
        )
        results.extend(
            probe_vault(
                web3=web3,
                row=row,
                token_cache=token_cache,
                settings=settings,
            )
        )
    return results


def print_summary(results: list[VaultProbeResult], config: ScriptConfig) -> None:
    """Print a human-readable diagnostic summary.

    :param results:
        Probe results.
    :param config:
        Script configuration.
    """
    gas_poisoners = [result for result in results if result.out_of_gas]
    failed = [result for result in results if result.status in {"reverted", "error"}]
    problematic = [result for result in results if result.out_of_gas or result.status in {"reverted", "error"}]
    by_status: dict[str, int] = {}
    for result in results:
        by_status[result.status] = by_status.get(result.status, 0) + 1

    print("")
    print("Summary")
    print(tabulate(sorted(by_status.items()), headers=["Status", "Calls"], tablefmt="simple"))
    print(f"Out-of-gas suspects: {len(gas_poisoners):,}")
    print(f"Failed calls: {len(failed):,}")
    print(f"Problematic calls: {len(problematic):,}")
    print(f"CSV: {config.output_csv}")
    print(f"JSONL: {config.output_jsonl}")

    if problematic:
        table = [
            [
                result.vault_address,
                result.vault_name,
                result.function,
                result.gas_estimate or "-",
                result.status,
                "yes" if result.out_of_gas else "no",
                result.error[:120],
            ]
            for result in problematic[:MAX_PROBLEMATIC_TABLE_ROWS]
        ]
        print("")
        print("Problematic vault calls")
        print(tabulate(table, headers=["Vault", "Name", "Function", "Gas estimate", "Status", "Out of gas", "Error"], tablefmt="simple"))
        if len(problematic) > MAX_PROBLEMATIC_TABLE_ROWS:
            print(f"Showing first {MAX_PROBLEMATIC_TABLE_ROWS} of {len(problematic):,} problematic calls. See CSV/JSONL for the full report.")


def main() -> None:
    """Run the HyperEVM vault call probe."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    json_rpc_url = os.environ.get("JSON_RPC_URL") or read_json_rpc_url(HYPEREVM_CHAIN_ID)
    web3 = create_multi_provider_web3(json_rpc_url)
    chain_id = web3.eth.chain_id
    assert chain_id == HYPEREVM_CHAIN_ID, f"Expected HyperEVM chain id {HYPEREVM_CHAIN_ID}, got {chain_id}"

    config = read_script_config()
    settings = read_probe_settings(web3)
    vault_rows = load_selected_vault_rows(config)

    print(f"Connected to chain {chain_id}: {get_chain_name(chain_id)}")
    print(f"Block: {settings.block_identifier}")
    print(f"Vault database: {config.vault_db_path}")
    print(f"Vaults selected: {len(vault_rows):,}")
    print(f"CALL_GAS={settings.gas_limit:,}, MAX_ESTIMATED_GAS={settings.max_estimated_gas:,}, ESTIMATE_GAS={settings.estimate_gas}")

    results = probe_vault_rows(web3, vault_rows, settings)
    if not results:
        print("No results to write")
        return

    write_results(config.output_csv, config.output_jsonl, results)
    print_summary(results, config)


if __name__ == "__main__":
    main()
