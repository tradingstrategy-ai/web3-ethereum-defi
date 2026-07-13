"""Anvil-only guarded vault-deposit probe.

The probe is intentionally an operator tool: it certifies a manager-generated
deposit request from ``SimpleVaultV0`` through its ``GuardV0``.  It never uses a
private key supplied by an operator and never targets an upstream RPC endpoint.

Large all-protocol runs are intentionally long-lived: each candidate deploys
and exercises contracts on a fresh Anvil fork. Interactive runners
must run bounded protocol batches and resume from the durable status file,
rather than imposing one short wall-clock timeout on the complete sweep.
"""

import json
import logging
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from eth_account import Account
from eth_typing import HexAddress
from requests.exceptions import RequestException
from tabulate import tabulate
from web3 import HTTPProvider, Web3
from web3.contract.contract import ContractFunction
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, TimeExhausted, TransactionNotFound, Web3Exception
from web3.types import TxReceipt

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import native_datetime_utc_now
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil, fund_erc20_on_anvil, is_anvil, set_balance
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.revert_reason import fetch_transaction_revert_reason
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.token import fetch_erc20_details
from eth_defi.utils import wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

STATUS_SCHEMA_VERSION = 1
#: Version-controlled package artefact refreshed by the guarded Anvil probe.
DEFAULT_STATUS_PATH = Path(__file__).parent.parent / "data" / "deposit-status" / "vault-deposit-status.json"
ProbeResult = dict[str, object]

#: Expected Anvil, RPC, and contract errors from an individual probe attempt.
PROBE_EXECUTION_EXCEPTIONS = (
    ConnectionError,
    RuntimeError,
    TimeoutError,
    RequestException,
    ValueError,
    BadFunctionCallOutput,
    ContractLogicError,
    TimeExhausted,
    TransactionNotFound,
    Web3Exception,
)


class AnvilProbeTransactionError(RuntimeError):
    """Anvil mined a probe transaction with a failing status."""


@dataclass(frozen=True, slots=True)
class VaultDepositProbeCandidate:
    """One database vault selected for a guarded deposit attempt."""

    spec: VaultSpec
    row: VaultRow
    denomination_token_address: HexAddress

    @property
    def key(self) -> str:
        """Return canonical persistent-state key.

        The key prevents checksum casing differences from creating duplicate
        status records for the same chain and vault.

        :return:
            ``<chain_id>-<lowercase_address>`` status key.
        """
        return f"{self.spec.chain_id}-{self.spec.vault_address.lower()}"

    @property
    def denomination_token_label(self) -> str:
        """Return a compact human-readable denomination-token identifier.

        :return:
            Scanned denomination symbol followed by its checksummed address.
        """
        symbol = self.row.get("Denomination", "<unknown>")
        return f"{symbol} ({self.denomination_token_address})"


@dataclass(frozen=True, slots=True)
class VaultDepositProbeOutput:
    """One human-readable result row for a completed or skipped probe."""

    #: Scanner protocol name.
    protocol: str
    #: Vault contract address.
    address: HexAddress | str
    #: Scanner vault name.
    name: str
    #: Symbol and address of the denomination token.
    denomination_token: str
    #: Probe outcome used for summary statistics.
    outcome: str
    #: Concise result shown in the detailed table.
    status: str
    #: Informational ``maxDeposit(address(0))`` response, never a decision input.
    max_deposit_guidance: str
    #: Failure detail, absent for successful probes.
    failure_reason: str | None
    #: Raw Anvil exception text for reverted execution.
    revert_reason: str | None


def _timestamp() -> str:
    """Format a naive UTC timestamp at the JSON boundary.

    :return:
        ISO-8601 UTC timestamp ending in ``Z``.
    """
    return native_datetime_utc_now().isoformat(timespec="seconds") + "Z"


def require_simulation() -> None:
    """Require explicit acknowledgement before creating an Anvil fork.

    The flag is an operator-intent gate only; provider identity is separately
    checked immediately before any transaction is signed.

    :raises AnvilProbeTransactionError:
        If ``SIMULATE`` is not exactly ``true``.
    """
    if os.environ.get("SIMULATE", "").lower() != "true":
        raise RuntimeError("Set SIMULATE=true: this script only operates against an Anvil fork")  # noqa: EM101


def _read_decimal_env(name: str) -> Decimal:
    """Read one positive human-readable decimal environment value.

    :param name:
        Name of the required environment variable.
    :return:
        Parsed positive decimal without ERC-20 decimal conversion.
    :raises ValueError:
        If the setting is absent or not positive.
    """
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is required")
    parsed = Decimal(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def _row_token_address(row: VaultRow) -> HexAddress | None:
    """Extract a valid scanned denomination-token address.

    :param row:
        Scanner metadata row.
    :return:
        Checksummed ERC-20 address, or ``None`` for incomplete metadata.
    """
    token = row.get("_denomination_token")
    if not isinstance(token, dict):
        return None
    address = token.get("address")
    if not isinstance(address, str) or not Web3.is_address(address):
        return None
    return Web3.to_checksum_address(address)


def _has_deposit_capability(row: VaultRow) -> bool:
    """Check the scanner's fail-closed public deposit capability.

    :param row:
        Scanner metadata row.
    :return:
        ``True`` only for an explicitly advertised deposit manager.
    """
    capability = row.get("_deposit_manager")
    return isinstance(capability, dict) and capability.get("can_deposit") is True


def select_candidates(
    database: VaultDatabase,
    *,
    selection: str,
    min_tvl: Decimal | None = None,
    denomination_token: HexAddress | None = None,
    protocol: str | None = None,
    vault_ids: str | None = None,
    chain_id: int | None = None,
    include_uncertified: bool = False,
    max_per_protocol: int | None = None,
) -> list[VaultDepositProbeCandidate]:
    """Select database candidates with deterministic order and fail-closed metadata.

    ``min_tvl`` compares the scanner's USD NAV values. A denomination token
    may optionally narrow that selection further.

    :param database:
        Local scanner metadata database.
    :param selection:
        One of ``min_tvl``, ``protocol``, ``all_protocols`` or ``vault_ids``.
    :param min_tvl:
        Minimum USD NAV for ``min_tvl`` selection.
    :param denomination_token:
        Optional denomination-token filter for ``min_tvl`` selection.
    :param protocol:
        Case-insensitive protocol name for ``protocol`` selection.
    :param vault_ids:
        Comma-separated vault identifiers for explicit selection.
    :param chain_id:
        Optional EVM chain filter applied to every selection mode.
    :param include_uncertified:
        Include legacy database rows without scanner capability metadata. Their
        adapter capability is still recomputed and enforced on the Anvil fork.
    :param max_per_protocol:
        Optional highest-N NAV limit applied independently in
        ``all_protocols`` mode.
    :return:
        Deterministically ordered, deposit-capable EVM candidates.
    :raises ValueError:
        If selection mode requirements are not met.
    """
    rows = database.rows
    selected: list[tuple[VaultSpec, VaultRow]] = []
    if selection == "min_tvl":
        if min_tvl is None:
            raise ValueError("min_tvl selection requires MIN_TVL")  # noqa: EM101
        wanted_token = denomination_token.lower() if denomination_token is not None else None
        selected = [(spec, row) for spec, row in rows.items() if isinstance(row.get("NAV"), Decimal) and row["NAV"] >= min_tvl and (wanted_token is None or (_row_token_address(row) is not None and _row_token_address(row).lower() == wanted_token))]
        selected.sort(key=lambda item: (item[0].chain_id, item[0].vault_address.lower()))
    elif selection == "protocol":
        if not protocol:
            raise ValueError("protocol selection requires PROTOCOL")  # noqa: EM101
        selected = [(spec, row) for spec, row in rows.items() if str(row.get("Protocol", "")).lower() == protocol.lower()]
        selected.sort(key=lambda item: (item[0].chain_id, item[0].vault_address.lower()))
    elif selection == "all_protocols":
        selected = list(rows.items())
    elif selection == "vault_ids":
        if not vault_ids:
            raise ValueError("vault_ids selection requires VAULT_IDS")  # noqa: EM101
        seen: set[VaultSpec] = set()
        missing: list[VaultSpec] = []
        for item in vault_ids.split(","):
            spec = VaultSpec.parse_string(item.strip(), separator="-")
            if spec in seen:
                continue
            seen.add(spec)
            if spec not in rows:
                missing.append(spec)
                continue
            selected.append((spec, rows[spec]))
        if missing:
            missing_ids = ", ".join(spec.as_string_id() for spec in missing)
            raise ValueError(f"VAULT_IDS entries are missing from the vault database: {missing_ids}")
    else:
        raise ValueError("VAULT_SELECTION must be min_tvl, protocol, all_protocols, or vault_ids")  # noqa: EM101

    if chain_id is not None:
        selected = [(spec, row) for spec, row in selected if spec.chain_id == chain_id]

    candidates: list[VaultDepositProbeCandidate] = []
    for spec, row in selected:
        token_address = _row_token_address(row)
        has_capability = _has_deposit_capability(row)
        if not Web3.is_address(spec.vault_address) or not (has_capability or include_uncertified):
            continue
        if token_address is None or not isinstance(row.get("NAV"), Decimal) or row["NAV"] <= 0:
            continue
        candidates.append(VaultDepositProbeCandidate(spec, row, token_address))

    if selection == "protocol":
        candidates.sort(key=lambda candidate: (-candidate.row["NAV"], candidate.spec.chain_id, candidate.spec.vault_address.lower()))
    elif selection == "all_protocols":
        by_protocol: dict[str, list[VaultDepositProbeCandidate]] = defaultdict(list)
        for candidate in candidates:
            by_protocol[str(candidate.row.get("Protocol", "<unknown>"))].append(candidate)
        candidates = []
        for protocol_name in sorted(by_protocol, key=str.lower):
            ranked = sorted(by_protocol[protocol_name], key=lambda candidate: (-candidate.row["NAV"], candidate.spec.chain_id, candidate.spec.vault_address.lower()))
            candidates.extend(ranked[:max_per_protocol] if max_per_protocol else ranked)
    return candidates


def _normalise_legacy_status(state: dict[str, object]) -> dict[str, object]:
    """Invalidate legacy successes that do not identify their fork block.

    Older probe versions persisted successful outcomes without enough evidence
    to reproduce the tested chain state. Their complete records remain in the
    bounded history, while the current outcome fails closed until refreshed.

    :param state:
        Valid schema-versioned status state.
    :return:
        The same state with unverifiable current successes invalidated.
    :raises ValueError:
        If an individual vault record is malformed.
    """
    vaults = state["vaults"]
    assert isinstance(vaults, dict)
    for key, current in tuple(vaults.items()):
        if not isinstance(current, dict):
            raise ValueError(f"Malformed vault deposit status record: {key}")
        fork_block_number = current.get("fork_block_number")
        valid_fork_block = isinstance(fork_block_number, int) and not isinstance(fork_block_number, bool) and fork_block_number > 0
        if current.get("outcome") != "success" or valid_fork_block:
            continue
        history = list(current.get("history", []))
        history.append({field: value for field, value in current.items() if field != "history"})
        identity_fields = ("chain_id", "address", "name", "protocol", "last_attempt_at")
        invalidated = {field: current[field] for field in identity_fields if field in current}
        invalidated.update(
            {
                "outcome": "invalid_evidence",
                "message": "Legacy success did not record a valid Anvil fork block; refresh required",
                "attempt_count": int(current.get("attempt_count", 0)),
                "history": history[-10:],
            }
        )
        vaults[key] = invalidated
    return state


def _load_status(path: Path) -> dict[str, object]:
    """Load valid local probe state without silently replacing it.

    :param path:
        Absolute or user-expanded local status path.
    :return:
        Schema-versioned state object, creating an empty one when absent.
    :raises ValueError:
        If existing state is malformed or uses another schema version.
    """
    if not path.exists():
        return {"schema_version": STATUS_SCHEMA_VERSION, "updated_at": _timestamp(), "vaults": {}}
    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict) or state.get("schema_version") != STATUS_SCHEMA_VERSION or not isinstance(state.get("vaults"), dict):
        raise ValueError(f"Unsupported or malformed vault deposit status file: {path}")
    return _normalise_legacy_status(state)


def update_status(path: Path, key: str, result: ProbeResult) -> None:
    """Atomically store one durable, hash-free probe result in local state.

    Re-reading under the lock preserves concurrent writers. Current-attempt
    fields are rebuilt from ``result`` so evidence from an older attempt cannot
    leak into a later failure or success.

    :param path:
        Status-file destination.
    :param key:
        Canonical vault key.
    :param result:
        Current attempt result without fork-local transaction identifiers.
    :raises ValueError:
        If a successful result does not identify a positive integer fork block.
    """
    if result.get("outcome") == "success":
        fork_block_number = result.get("fork_block_number")
        if not isinstance(fork_block_number, int) or isinstance(fork_block_number, bool) or fork_block_number <= 0:
            raise ValueError("Successful probe result must contain a positive integer fork_block_number")  # noqa: EM101
    path = path.expanduser().absolute()
    with wait_other_writers(path):
        state = _load_status(path)
        existing = state["vaults"].get(key, {})
        history = list(existing.get("history", []))
        if existing:
            history.append({k: v for k, v in existing.items() if k != "history"})
        current = {**result, "attempt_count": int(existing.get("attempt_count", 0)) + 1, "history": history[-10:]}
        state["vaults"][key] = current
        state["updated_at"] = _timestamp()
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True, allow_nan=False)
        temporary.replace(path)


def log_probe_tables(rows: list[VaultDepositProbeOutput]) -> None:
    """Log per-vault outcomes and aggregate outcome counts as tables.

    The detailed table is emitted after results are persisted, allowing an
    operator to compare terminal output with the durable local status file.

    :param rows:
        Completed or skipped vault-probe rows.
    :return:
        ``None`` after logging both human-readable tables at info level.
    """
    logger.info(
        "Vault deposit probe results\n%s",
        tabulate(
            [
                (
                    row.protocol,
                    row.address,
                    row.name,
                    row.denomination_token,
                    row.status,
                    row.max_deposit_guidance,
                    "Ok" if row.outcome == "success" else row.failure_reason or row.outcome,
                    row.revert_reason or "",
                )
                for row in sorted(rows, key=lambda row: (row.outcome != "success", row.protocol.lower(), row.address.lower()))
            ],
            headers=["Protocol", "Address", "Name", "Denomination token", "Status", "maxDeposit guidance", "Failure reason", "Revert reason"],
            tablefmt="github",
        ),
    )
    counts = Counter(row.outcome for row in rows)
    total = len(rows)
    logger.info(
        "Vault deposit probe summary\n%s",
        tabulate(
            [(outcome, count, f"{count / total:.1%}") for outcome, count in sorted(counts.items())],
            headers=["Outcome", "Vaults", "Percentage"],
            tablefmt="github",
        ),
    )


def _broadcast(wallet: HotWallet, function: ContractFunction, web3: Web3, gas: int = 750_000) -> TxReceipt:
    """Sign, submit and wait for one control-wallet transaction.

    :param wallet:
        Fresh gas-only Anvil control wallet.
    :param function:
        Bound SimpleVault or Guard contract call.
    :param web3:
        Verified Anvil Web3 instance.
    :param gas:
        Outer transaction gas limit.
    :return:
        Successful mined receipt.
    :raises AnvilProbeTransactionError:
        If Anvil reports a reverted receipt, including its replayed reason.
    """
    signed = wallet.sign_bound_call_with_new_nonce(function, {"gas": gas}, web3=web3, fill_gas_price=True)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt["status"] != 1:
        raise AnvilProbeTransactionError(fetch_transaction_revert_reason(web3, tx_hash))
    return receipt


def fetch_max_deposit_guidance(vault: ERC4626Vault) -> str:
    """Read the ERC-4626 ``maxDeposit`` response for operator guidance only.

    ERC-4626 permits conservative underestimates.  In particular, Morpho V2
    always returns zero because its external gates are address-dependent.  The
    guarded deposit transaction, rather than this advisory call, decides whether
    the vault accepts a ``SimpleVaultV0`` deposit.

    :param vault:
        Constructed ERC-4626 reader whose standard contract accessor is queried.
    :return:
        Raw ``maxDeposit(address(0))`` result as text, or a concise unavailable
        marker when the optional accessor cannot be read.
    """
    try:
        return str(vault.vault_contract.functions.maxDeposit("0x0000000000000000000000000000000000000000").call())
    except (BadFunctionCallOutput, ContractLogicError, ValueError, Web3Exception) as error:
        return f"unavailable: {type(error).__name__}"


def _assert_anvil_target(web3: Web3, launch_url: str, expected_chain_id: int) -> None:
    """Reject provider substitution before any transaction is signed.

    :param web3:
        Candidate transaction provider.
    :param launch_url:
        JSON-RPC endpoint produced by the local Anvil launch.
    :param expected_chain_id:
        Chain ID selected from vault metadata.
    :raises RuntimeError:
        If the endpoint, Anvil identity, or chain ID differs.
    """
    endpoint = getattr(web3.provider, "endpoint_uri", None)
    if endpoint != launch_url or not is_anvil(web3) or web3.eth.chain_id != expected_chain_id:
        raise RuntimeError("Refusing to mutate a provider that is not the expected Anvil fork")  # noqa: EM101


def probe_candidate(  # noqa: PLR0914
    web3: Web3,
    candidate: VaultDepositProbeCandidate,
    amount: Decimal,
    fork_block_number: int | None,
) -> ProbeResult:
    """Run one guarded deposit attempt on an already verified Anvil fork.

    Returned data is safe to persist.  Ephemeral Anvil transaction hashes are
    deliberately excluded.

    :param web3:
        Verified Anvil fork provider.
    :param candidate:
        Selected local database vault.
    :param amount:
        Human-readable denomination-token deposit amount.
    :param fork_block_number:
        Upstream block at which the fork started.
    :return:
        Hash-free status result for persistent local state.
    """
    vault = create_vault_instance(web3, candidate.spec.vault_address, candidate.row["features"])
    if vault is None:
        return {"outcome": "adapter_error", "message": "Could not construct vault adapter"}
    max_deposit_guidance = fetch_max_deposit_guidance(vault) if isinstance(vault, ERC4626Vault) else "not an ERC-4626 vault"
    capability = vault.get_deposit_manager_capability()
    manager_source = "explicit"
    manager = None
    if capability is None and isinstance(vault, ERC4626Vault) and vault.supports_generic_deposit_manager():
        capability = vault.get_synchronous_deposit_manager_capability()
        manager = ERC4626DepositManager(vault)
        manager_source = "generic_erc4626"
    capability_data = capability.as_initial_public_schema() if capability else None
    if capability_data is None or not capability_data["can_deposit"]:
        return {"outcome": "adapter_error", "message": "Live adapter does not advertise deposits", "deposit_manager": capability_data, "max_deposit_guidance": max_deposit_guidance}

    token = fetch_erc20_details(web3, candidate.denomination_token_address)
    raw_amount = token.convert_to_raw(amount)

    control = HotWallet(Account.create())
    set_balance(web3, control.address, Web3.to_wei(10, "ether"))
    control.sync_nonce(web3)
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", control, control.address, libraries=GUARD_LIBRARIES)
    _broadcast(control, simple_vault.functions.initialiseOwnership(control.address), web3)
    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    try:
        _broadcast(control, guard.functions.whitelistERC4626(vault.address, "Vault deposit probe"), web3)
    except (*PROBE_EXECUTION_EXCEPTIONS, AnvilProbeTransactionError) as e:
        return {"outcome": "guard_configuration_error", "message": str(e), "deposit_manager": capability_data, "max_deposit_guidance": max_deposit_guidance}

    try:
        fund_erc20_on_anvil(web3, token.address, simple_vault.address, raw_amount)
        if token.fetch_raw_balance_of(simple_vault.address) < raw_amount:
            return {"outcome": "funding_error", "message": "Anvil token deal did not set the required balance", "deposit_manager": capability_data, "max_deposit_guidance": max_deposit_guidance}
    except RuntimeError as e:
        return {"outcome": "funding_error", "message": str(e), "deposit_manager": capability_data, "max_deposit_guidance": max_deposit_guidance}
    if manager is None:
        manager = vault.get_deposit_manager()
    try:
        if isinstance(manager, ERC4626DepositManager):
            request = manager.create_deposit_request(owner=simple_vault.address, to=simple_vault.address, raw_amount=raw_amount, check_max_deposit=False)
        else:
            request = manager.create_deposit_request(owner=simple_vault.address, to=simple_vault.address, raw_amount=raw_amount)
    except PROBE_EXECUTION_EXCEPTIONS as e:
        return {"outcome": "adapter_error", "message": str(e), "deposit_manager": capability_data, "max_deposit_guidance": max_deposit_guidance}
    if request.value:
        return {"outcome": "guard_value_unsupported", "message": "SimpleVaultV0 cannot forward native value", "deposit_manager": capability_data, "max_deposit_guidance": max_deposit_guidance}

    try:
        approval_target = manager.get_deposit_approval_target()
        approve_target, approve_data = encode_simple_vault_transaction(token.approve(approval_target, amount))
        guard.functions.validateCall(control.address, approve_target, approve_data).call()
        encoded_requests = [encode_simple_vault_transaction(function) for function in request.funcs]
        for target, calldata in encoded_requests:
            guard.functions.validateCall(control.address, target, calldata).call()
    except PROBE_EXECUTION_EXCEPTIONS as e:
        return {"outcome": "guard_validation_error", "message": str(e), "deposit_manager": capability_data, "max_deposit_guidance": max_deposit_guidance}

    try:
        _broadcast(control, simple_vault.functions.performCall(approve_target, approve_data), web3)
        allowance = token.contract.functions.allowance(simple_vault.address, approval_target).call()
        if allowance < raw_amount:
            return {"outcome": "adapter_error", "message": "Guarded approval did not set the requested allowance", "deposit_manager": capability_data, "max_deposit_guidance": max_deposit_guidance}
        request_hashes = []
        for target, calldata in encoded_requests:
            receipt = _broadcast(control, simple_vault.functions.performCall(target, calldata), web3)
            request_hashes.append(receipt["transactionHash"])
    except (*PROBE_EXECUTION_EXCEPTIONS, AnvilProbeTransactionError) as e:
        return {"outcome": "reverted", "message": str(e), "revert_reason": str(e), "deposit_manager": capability_data, "max_deposit_guidance": max_deposit_guidance}

    result = {
        "outcome": "success",
        "message": None,
        "deposit_manager": capability_data,
        "deposit_amount": str(amount),
        "fork_block_number": fork_block_number,
        "execution_mode": "guard_v0_simple_vault_v0",
        "denomination_token_address": token.address,
        "deposit_manager_source": manager_source,
        "max_deposit_guidance": max_deposit_guidance,
    }
    if capability_data["deposit_flow"] == "synchronous":
        shares = vault.vault_contract.functions.balanceOf(simple_vault.address).call()
        if shares <= 0:
            return {**result, "outcome": "adapter_error", "message": "No shares minted to SimpleVaultV0"}
        result["minted_share_amount_raw"] = str(shares)
        denomination_before_redemption = token.fetch_raw_balance_of(simple_vault.address)
        try:
            if isinstance(manager, ERC4626DepositManager):
                redemption = manager.create_redemption_request(owner=simple_vault.address, raw_shares=shares)
            else:
                redemption = manager.create_redemption_request(
                    owner=simple_vault.address,
                    shares=vault.share_token.convert_to_decimals(shares),
                )
            encoded_redemptions = [encode_simple_vault_transaction(function) for function in redemption.funcs]
            for target, calldata in encoded_redemptions:
                guard.functions.validateCall(control.address, target, calldata).call()
        except (*PROBE_EXECUTION_EXCEPTIONS, AssertionError) as e:
            return {**result, "outcome": "guard_validation_error", "message": f"Synchronous redemption validation failed: {e}"}
        try:
            for target, calldata in encoded_redemptions:
                _broadcast(control, simple_vault.functions.performCall(target, calldata), web3)
        except (*PROBE_EXECUTION_EXCEPTIONS, AnvilProbeTransactionError) as e:
            return {**result, "outcome": "reverted", "message": f"Synchronous redemption failed: {e}", "revert_reason": str(e)}
        remaining_shares = vault.vault_contract.functions.balanceOf(simple_vault.address).call()
        denomination_after_redemption = token.fetch_raw_balance_of(simple_vault.address)
        if remaining_shares >= shares:
            return {**result, "outcome": "adapter_error", "message": "Synchronous redemption did not reduce the SimpleVaultV0 share balance"}
        if denomination_after_redemption <= denomination_before_redemption:
            return {**result, "outcome": "adapter_error", "message": "Synchronous redemption returned no denomination tokens to SimpleVaultV0"}
        result["redeemed_asset_amount_raw"] = str(denomination_after_redemption - denomination_before_redemption)
        result["remaining_share_amount_raw"] = str(remaining_shares)
        result["redemption_status_detail"] = "completed"
    else:
        try:
            ticket = request.parse_deposit_transaction(request_hashes)
        except PROBE_EXECUTION_EXCEPTIONS as e:
            return {**result, "outcome": "adapter_error", "message": f"Could not parse async deposit ticket: {e}"}
        if ticket.owner.lower() != simple_vault.address.lower() or ticket.to.lower() != simple_vault.address.lower():
            return {**result, "outcome": "adapter_error", "message": "Async ticket does not belong to SimpleVaultV0"}
        # The request id and all deployed addresses are fork-local diagnostics.
        # Keep them in logs only: persisted state must not look like a real
        # chain record after the Anvil process has exited.
        request_id = getattr(ticket, "request_id", getattr(ticket, "settlement_id", None))
        logger.info("Fork-local async request id for %s: %s", candidate.key, request_id)
        result["status_detail"] = "deposit_request_submitted"
        result["redemption_status_detail"] = "not_exercised_asynchronous"

    if token.fetch_raw_balance_of(control.address) != 0 or vault.vault_contract.functions.balanceOf(control.address).call() != 0:
        return {**result, "outcome": "adapter_error", "message": "Control wallet received vault assets or shares"}
    return result


def run_from_environment() -> int:
    """Run the guarded Anvil probe configured through environment variables.

    Candidate selection comes from the local vault database. The function
    launches one isolated fork per vault and stores the durable result after
    each attempt. A fresh process prevents one blocked upstream state read from
    contaminating later candidates. All-protocol runs can take a long time;
    interactive callers should invoke bounded protocol batches and resume
    using the status file instead of terminating the complete sweep early.

    :return:
        Process exit status ``0`` after all selected chain batches finish.
    :raises RuntimeError:
        If simulation confirmation, provider validation, or snapshot recovery
        fails.
    :raises ValueError:
        If required environment configuration is malformed or absent.
    """
    require_simulation()
    database_path = Path(os.environ.get("VAULT_DATABASE_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    status_path = Path(os.environ.get("VAULT_DEPOSIT_STATUS_PATH", str(DEFAULT_STATUS_PATH))).expanduser()
    selection = os.environ.get("VAULT_SELECTION", "")
    max_vaults = int(os.environ.get("MAX_VAULTS", "0"))
    chain_id_text = os.environ.get("CHAIN_ID")
    chain_id = int(chain_id_text) if chain_id_text else None
    denomination = os.environ.get("DENOMINATION_TOKEN")
    if denomination and not Web3.is_address(denomination):
        raise ValueError(f"DENOMINATION_TOKEN is not a valid address: {denomination}")
    denomination_address = Web3.to_checksum_address(denomination) if denomination else None
    candidates = select_candidates(
        VaultDatabase.read(database_path),
        selection=selection,
        min_tvl=_read_decimal_env("MIN_TVL") if selection == "min_tvl" else None,
        denomination_token=denomination_address,
        protocol=os.environ.get("PROTOCOL"),
        vault_ids=os.environ.get("VAULT_IDS"),
        chain_id=chain_id,
        include_uncertified=os.environ.get("ALLOW_UNCERTIFIED_CANDIDATES", "").lower() == "true",
        max_per_protocol=max_vaults if selection == "all_protocols" and max_vaults > 0 else None,
    )
    if max_vaults > 0 and selection != "all_protocols":
        candidates = candidates[:max_vaults]
    if not candidates:
        raise ValueError("Vault selection produced no deposit-capable candidates")  # noqa: EM101
    if len(candidates) > 1 and os.environ.get("CONFIRM_ALL", "").lower() != "true":
        raise RuntimeError("Set CONFIRM_ALL=true to run a multi-vault probe")  # noqa: EM101
    amount = _read_decimal_env("DEPOSIT_AMOUNT")

    output_rows: list[VaultDepositProbeOutput] = []
    for candidate in candidates:
        candidate_chain_id = candidate.spec.chain_id
        launch = None
        fork_block_number = None
        try:
            launch = fork_network_anvil(read_json_rpc_url(candidate_chain_id))
            web3 = Web3(HTTPProvider(launch.json_rpc_url))
            _assert_anvil_target(web3, launch.json_rpc_url, candidate_chain_id)
            if launch.chain_id != candidate_chain_id:
                raise RuntimeError(f"Anvil launch chain id {launch.chain_id} does not match {candidate_chain_id}")
            fork_block_number = launch.fork_block_number or web3.eth.block_number
            result = probe_candidate(web3, candidate, amount, fork_block_number)
        except PROBE_EXECUTION_EXCEPTIONS as e:
            logger.exception("Probe failed for %s", candidate.key)
            result = {"outcome": "rpc_error", "message": str(e)}
        finally:
            if launch is not None:
                launch.close()
        if fork_block_number is not None:
            result.setdefault("fork_block_number", fork_block_number)
        result.update({"chain_id": candidate_chain_id, "address": candidate.spec.vault_address, "name": candidate.row.get("Name"), "protocol": candidate.row.get("Protocol"), "last_attempt_at": _timestamp()})
        update_status(status_path, candidate.key, result)
        output_rows.append(
            VaultDepositProbeOutput(
                protocol=str(candidate.row.get("Protocol", "<unknown>")),
                address=candidate.spec.vault_address,
                name=str(candidate.row.get("Name", "")),
                denomination_token=candidate.denomination_token_label,
                outcome=str(result["outcome"]),
                status="Ok (generic ERC-4626)" if result["outcome"] == "success" and result.get("deposit_manager_source") == "generic_erc4626" else "Ok" if result["outcome"] == "success" else str(result["outcome"]),
                max_deposit_guidance=str(result.get("max_deposit_guidance", "not available")),
                failure_reason=str(result["message"]) if result.get("message") else None,
                revert_reason=str(result["revert_reason"]) if result.get("revert_reason") else None,
            )
        )
    log_probe_tables(output_rows)
    return 0
