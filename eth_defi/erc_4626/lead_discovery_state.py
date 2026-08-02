"""Persist and validate the bounded cache for vault lead discovery.

The state documents in this module intentionally contain only cache provenance.
The vault metadata pickle remains the authoritative store for discovered leads
and their extracted metadata.
"""

import ast
import datetime
import hashlib
import inspect
import json
import textwrap
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atomicwrites import atomic_write

from eth_defi.erc_4626.discovery_base import get_vault_discovery_events

LEAD_DISCOVERY_STATE_SCHEMA_VERSION = 1
DEFAULT_LEAD_DISCOVERY_STATE_TIMEOUT = datetime.timedelta(days=7)

# Bump this whenever a change alters values persisted by create_vault_scan_record()
# or its protocol adapters.  The discovery cursor alone cannot detect those
# changes, but existing rows must be regenerated before they are exported.
VAULT_METADATA_REFRESH_VERSION = 3


def _remove_docstring(node: ast.AST) -> None:
    """Remove a function or class docstring before source hashing.

    Docstrings and callable names are documentation rather than discovery
    behaviour. Excluding them prevents a comment-only change from invalidating
    every chain's bounded discovery cache.

    :param node:
        AST node whose first body statement may be a docstring.
    """

    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) or not node.body:
        return
    first_statement = node.body[0]
    if isinstance(first_statement, ast.Expr) and isinstance(first_statement.value, ast.Constant) and isinstance(first_statement.value.value, str):
        node.body.pop(0)


def hash_function_source(function: Callable[..., Any]) -> str:
    """Create a stable SHA-256 digest for Python function behaviour.

    The normalisation follows trade-executor's ``hash_function()`` helper: it
    hashes a dedented AST without docstrings or the function name. Source-less
    functions, such as cloudpickled callables, fall back to their bytecode.

    :param function:
        Callable whose implementation determines cache validity.
    :return:
        Full hexadecimal SHA-256 digest.
    """

    assert callable(function), f"Not a function: {function!r}"
    try:
        source = inspect.getsource(function)
    except OSError:
        return hashlib.sha256(function.__code__.co_code).hexdigest()

    source = textwrap.dedent(source)
    module = ast.parse(source)
    if len(module.body) != 1 or not isinstance(module.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    function_node = module.body[0]
    function_node.name = ""
    for node in ast.walk(module):
        _remove_docstring(node)
    return hashlib.sha256(ast.dump(module, annotate_fields=False).encode("utf-8")).hexdigest()


def create_lead_discovery_signature(enabled_chains: Iterable[tuple[str, str]]) -> tuple[str, dict[str, Any]]:
    """Create the cache signature from detection code, metadata version, and enabled chains.

    Scheduler and price-scan settings do not invalidate a lead cache. Metadata
    changes use an explicit version because adapter behaviour is not a direct
    dependency of the event-discovery function hash.

    :param enabled_chains:
        Iterable of ``(chain name, RPC environment variable)`` pairs for
        chains whose ``scan_vaults`` setting is enabled.
    :return:
        Full signature and the canonical mapping retained for diagnostics.
    """

    configuration = {
        "lead_detection_function_hash": hash_function_source(get_vault_discovery_events),
        "vault_metadata_refresh_version": VAULT_METADATA_REFRESH_VERSION,
        "enabled_chains": [{"name": name, "rpc_environment_variable": env_var} for name, env_var in sorted(enabled_chains)],
    }
    encoded = json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), configuration


@dataclass(frozen=True, slots=True)
class LeadDiscoveryState:
    """Successful incremental lead and metadata refresh cache state for one EVM chain.

    The state records cache provenance only. The vault metadata database remains
    the authoritative store for discovered leads and extracted metadata.
    """

    #: Verified EVM chain identifier.
    chain_id: int

    #: Current discovery configuration signature.
    signature: str

    #: Canonical signature inputs retained for operator diagnostics.
    signature_configuration: dict[str, Any]

    #: Naive UTC timestamp when lead and metadata refresh persistence ended.
    completed_at: datetime.datetime

    #: Last block covered by the completed discovery.
    completed_block: int

    def as_dict(self) -> dict[str, Any]:
        """Serialise this state as a stable JSON document.

        :return:
            JSON-compatible cache envelope.
        """

        return {
            "schema_version": LEAD_DISCOVERY_STATE_SCHEMA_VERSION,
            "chain_id": self.chain_id,
            "signature": self.signature,
            "signature_configuration": self.signature_configuration,
            "completed_at": self.completed_at.isoformat(),
            "completed_block": self.completed_block,
        }


def get_lead_discovery_state_path(data_dir: Path, chain_id: int) -> Path:
    """Return the per-chain lead-discovery cache path.

    :param data_dir:
        Persistent vault-pipeline data directory.
    :param chain_id:
        Verified EVM chain identifier.
    :return:
        Per-chain JSON state filename.
    """

    return data_dir / f"lead-discovery-state-{chain_id}.json"


def load_lead_discovery_state(path: Path) -> tuple[LeadDiscoveryState | None, str | None]:
    """Load a cache state document without trusting malformed content.

    :param path:
        JSON state document to load.
    :return:
        Loaded state and ``None``, or ``None`` and a cache-miss reason.
    """

    if not path.exists():
        return None, "state file does not exist"
    try:
        document = json.loads(path.read_text())
        if document.get("schema_version") != LEAD_DISCOVERY_STATE_SCHEMA_VERSION:
            return None, "unknown state schema version"
        completed_at = datetime.datetime.fromisoformat(document["completed_at"])
        if completed_at.tzinfo is not None:
            return None, "state completion timestamp is not naive UTC"
        state = LeadDiscoveryState(
            chain_id=int(document["chain_id"]),
            signature=str(document["signature"]),
            signature_configuration=dict(document["signature_configuration"]),
            completed_at=completed_at,
            completed_block=int(document["completed_block"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as e:
        return None, f"could not read state: {e}"
    return state, None


def validate_lead_discovery_state(
    state: LeadDiscoveryState,
    chain_id: int,
    signature: str,
    now: datetime.datetime,
    timeout: datetime.timedelta,
    *,
    has_metadata_cursor: bool,
) -> str | None:
    """Return a cache-miss reason or ``None`` for a valid state.

    :param state:
        Deserialised cache state.
    :param chain_id:
        Current verified EVM chain identifier.
    :param signature:
        Current discovery configuration signature.
    :param now:
        Current naive UTC time.
    :param timeout:
        Positive maximum cache age.
    :param has_metadata_cursor:
        Whether the metadata database recorded a completed cursor for this
        chain, including chains whose discovery found zero leads.
    :return:
        ``None`` for a hit, otherwise a human-readable miss reason.
    """

    if state.chain_id != chain_id:
        return f"state chain id {state.chain_id} does not match {chain_id}"
    if state.signature != signature:
        return "lead discovery signature changed"
    if not has_metadata_cursor:
        return "vault metadata database has no discovery cursor"
    age = now - state.completed_at
    if age < datetime.timedelta(0):
        return "state completion time is in the future"
    if age >= timeout:
        return f"state expired after {age} (timeout {timeout})"
    return None


def save_lead_discovery_state(state: LeadDiscoveryState, path: Path) -> None:
    """Atomically persist successful lead and metadata refresh state.

    :param state:
        Fully completed cache state to write.
    :param path:
        Destination JSON document.
    :return:
        None.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="w", overwrite=True) as output:
        json.dump(state.as_dict(), output, indent=2, sort_keys=True)
