"""Discover, preview and apply numbered vault database repairs.

Run ``poetry run vault-migrate list`` or ``DRY_RUN=false poetry run
vault-migrate run``. The scanner daemon must be stopped, even for a preview.
The authoring and recovery contract lives in ``.claude/docs/migration-scripts.md``.
Resource loading follows https://docs.python.org/3/library/importlib.resources.html.
"""

import argparse
import ast
import datetime
import hashlib
import importlib.resources
import json
import logging
import os
import pickle
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import ModuleType
from typing import Any

from tabulate import tabulate

import eth_defi
import migration
from eth_defi.compat import native_datetime_utc_now
from eth_defi.rpc_url import install_rpc_logging, redact_rpc_urls
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.scanner_lock import acquire_scanner_lifecycle_guard
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir
from eth_defi.version_info import VersionInfo

logger = logging.getLogger(__name__)
SCRIPT_NAME = re.compile(r"([0-9]{3,})-([a-z0-9]+(?:-[a-z0-9]+)*)\.py\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REQUIRED_HEADERS = ("Purpose:", "Scope:", "Branch:", "PR title:", "PR:", "Preserves:", "Expected:")


@dataclass(slots=True, frozen=True)
class MigrationSpec:
    """Validated script bytes and identity, without importing operational code."""

    #: Complete journal key.
    name: str
    #: Numeric execution order.
    number: int
    #: Original resource path for diagnostics.
    path: Path
    #: Exact bytes executed after validation.
    source: bytes
    #: Content checksum.
    sha256: str
    #: Author's operational documentation.
    description: str


def discover_migrations(scripts_dir: Path | None = None) -> Iterator[MigrationSpec]:
    """Enumerate valid scripts in numeric order without executing imports.

    Validate the complete inventory before yielding any script. An explicit
    directory is an internal test seam, never an operator scope override.

    :param scripts_dir: Test resource directory, or packaged resources.
    :return: Numerically ordered migration specifications.
    :raises ValueError: If naming, headers, signature or package roots conflict.
    """
    if scripts_dir is None:
        root = Path(eth_defi.__file__).resolve().parent.parent
        if Path(migration.__file__).resolve().parent.parent != root:
            raise ValueError("Migration resources and eth_defi must come from the same installation")
        scripts_dir = Path(str(importlib.resources.files("migration") / "scripts"))
    specs = []
    counters = set()
    for path in sorted(scripts_dir.iterdir()):
        if path.name == "__init__.py" or path.suffix != ".py":
            continue
        match = SCRIPT_NAME.fullmatch(path.name)
        if not match or int(match[1]) == 0 or path.is_symlink():
            raise ValueError(f"Invalid migration resource: {path.name}")
        number = int(match[1])
        if number in counters:
            raise ValueError(f"Duplicate migration counter: {number}")
        counters.add(number)
        source = path.read_bytes()
        tree = ast.parse(source, filename=str(path))
        description = ast.get_docstring(tree) or ""
        if any(header not in description for header in REQUIRED_HEADERS):
            raise ValueError(f"Missing migration documentation in {path.name}")
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "migrate"]
        if len(functions) != 1 or len(functions[0].args.args) != 1 or functions[0].args.vararg or functions[0].args.kwarg or functions[0].args.kwonlyargs or functions[0].args.posonlyargs:
            raise ValueError(f"Expected migrate(context) in {path.name}")
        specs.append(MigrationSpec(path.name, number, path, source, hashlib.sha256(source).hexdigest(), description))
    yield from sorted(specs, key=lambda spec: spec.number)


def _journal_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys instead of silently dropping audit records.

    Applied to every object, this also protects nested progress and status.

    :param pairs: Ordered JSON object members from the decoder.
    :return: Object with unique keys.
    """
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate keys in migration journal")
    return result


def load_state(path: Path) -> dict[str, dict[str, Any]]:
    """Read and validate the journal, never recovering corruption as empty.

    An absent journal denotes a first installation. Existing records must have
    the complete typed schema, including valid UTC attempt timestamps.

    :param path: Migration journal path.
    :return: Validated filename-keyed state.
    :raises ValueError: If existing state is invalid.
    """
    if not path.exists():
        return {}
    state = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_journal_object)
    # Python's decoder accepts NaN/Infinity extensions; the journal does not.
    json.dumps(state, allow_nan=False)
    if not isinstance(state, dict):
        raise ValueError("Migration state must be a dictionary")
    counters = set()
    for name, entry in state.items():
        match = SCRIPT_NAME.fullmatch(name)
        if not match or int(match[1]) <= 0 or int(match[1]) in counters or not isinstance(entry, dict):
            raise ValueError(f"Invalid migration state identity: {name}")
        counters.add(int(match[1]))
        for key, expected in {"first_run_at": str, "last_run_at": str, "success": bool, "modified_files": list, "other_data": dict, "script_sha256": str}.items():
            if type(entry.get(key)) is not expected:
                raise ValueError(f"Invalid {key} in migration state: {name}")
        for key in ("docker_image_hash", "git_commit_hash"):
            if key not in entry or (entry[key] is not None and not isinstance(entry[key], str)):
                raise ValueError(f"Invalid {key} in migration state: {name}")
        times = [datetime.datetime.fromisoformat(entry[key]) for key in ("first_run_at", "last_run_at")]
        if any(t.tzinfo is not None for t in times) or times[0] > times[1]:
            raise ValueError(f"Invalid UTC timestamps: {name}")
        if not SHA256.fullmatch(entry["script_sha256"]) or not all(isinstance(p, str) and Path(p).is_absolute() for p in entry["modified_files"]):
            raise ValueError(f"Invalid checksum or modified files: {name}")
        status = entry["other_data"].get("status")
        if status not in {"running", "succeeded", "failed", "interrupted"} or entry["success"] != (status == "succeeded"):
            raise ValueError(f"Inconsistent migration status: {name}")
        for key in ("backup_paths", "modified_files_all_attempts"):
            values = entry["other_data"].get(key, [])
            if not isinstance(values, list) or not all(isinstance(p, str) for p in values):
                raise ValueError(f"Invalid recovery paths: {name}")
    return state


def save_state(path: Path, state: dict[str, dict[str, Any]]) -> None:
    """Durably replace the journal without exposing a partially written JSON file.

    Serialise before opening a temporary file. Both the file and replacement
    directory entry are synchronised before returning to the caller.

    :param path: Existing pipeline directory's journal path.
    :param state: JSON-serialisable complete journal.
    :return: ``None`` after durable replacement.
    """
    payload = json.dumps(state, indent=2, sort_keys=True, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".migration-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_inventory(specs: tuple[MigrationSpec, ...], state: dict[str, dict[str, Any]]) -> None:
    """Protect recorded identities and execution order against source changes.

    Failed scripts can be fixed, but successful script bytes and names cannot
    change. Previously unseen counters must follow all successful migrations.

    :param specs: Discovered scripts.
    :param state: Validated journal.
    :return: ``None`` when the inventory can execute safely.
    """
    recorded = {int(name.split("-", 1)[0]): name for name in state}
    highest = max((int(name.split("-", 1)[0]) for name, entry in state.items() if entry["success"]), default=0)
    for spec in specs:
        if spec.number in recorded and recorded[spec.number] != spec.name:
            raise ValueError(f"Migration counter {spec.number} was renamed from {recorded[spec.number]} to {spec.name}")
        entry = state.get(spec.name)
        if entry and entry["success"] and entry["script_sha256"] != spec.sha256:
            raise ValueError(f"Successful migration changed: {spec.name}; restore it and add a new numbered migration")
        if entry is None and spec.number < highest:
            raise ValueError(f"Out-of-order migration {spec.name}; renumber above {highest}")
    for name in state.keys() - {spec.name for spec in specs}:
        logger.warning("Journal migration absent from this installation: %s", name)


def read_provenance(data_root: Path | None = None) -> dict[str, Any]:
    """Read build provenance without invoking Docker inside the container.

    Production image IDs are attested by the host launcher. Checkout git
    commands are rooted at this package, not the operator's current directory.

    :param data_root: Optional version-file root for tests.
    :return: Image ID, git commit, dirty status and Docker detection.
    """
    root = data_root or Path(eth_defi.__file__).resolve().parent.parent
    version = VersionInfo.read_docker_version(root)
    result = {"docker_image_hash": os.environ.get("DOCKER_IMAGE_HASH") or None, "git_commit_hash": version.commit_hash, "git_dirty": None, "in_docker": Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()}
    if not result["in_docker"]:
        result["docker_image_hash"] = None
    if not result["in_docker"] and (root / ".git").exists():
        result["git_commit_hash"] = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        result["git_dirty"] = bool(subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True).strip())
    return result


@dataclass(slots=True)
class MigrationContext:
    """Shared staged data and auditable writes for one sequential batch."""

    #: Resolved pipeline storage directory.
    data_dir: Path
    #: Preview keeps changes in memory only.
    dry_run: bool
    #: Currently executing script.
    script_name: str = ""
    #: Latest script's machine-readable reporting data.
    other_data: dict[str, Any] = field(default_factory=dict)
    #: Paths changed in this attempt.
    modified_files: list[str] = field(default_factory=list)
    #: Internal journal callback, disabled for previews.
    _persist: Callable[[], None] | None = field(default=None, repr=False)
    #: Lazily loaded batch database.
    _database: VaultDatabase | None = field(default=None, repr=False)
    #: Last committed or previewed snapshot.
    _snapshot: bytes | None = field(default=None, repr=False)

    def metadata(self) -> VaultDatabase:
        """Return the shared metadata staging object.

        Loading occurs only after the runner acquired its lifecycle guard.

        :return: Existing metadata database, never an empty fallback.
        """
        if self._database is None:
            path = self.data_dir / "vault-metadata-db.pickle"
            if not path.is_file():
                raise FileNotFoundError(path)
            self._database = VaultDatabase.read(path)
            self._snapshot = pickle.dumps(self._database, protocol=4)
        return self._database

    def report(self, **values: Any) -> None:
        """Record JSON-safe progress in this attempt's structured data.

        Reserved status/recovery fields belong to the runner. Previews only
        accumulate an in-memory report.

        :param values: JSON-serialisable metrics, never credentials.
        :return: ``None`` after reporting progress.
        """
        if values.keys() & {"status", "backup_paths", "modified_files_all_attempts"}:
            raise ValueError("Migration report cannot replace runner-owned fields")
        json.dumps(values, allow_nan=False)
        self.other_data.update(values)
        if self._persist:
            self._persist()

    def record_modified_file(self, path: Path, *, backup: bool = False) -> None:
        """Record an actual committed file or backup and retain recovery evidence.

        This is only for completed writes; planned writes must use report().

        :param path: Path actually modified or created.
        :param backup: Whether this path preserves pre-migration bytes.
        :return: ``None`` after journalling the path.
        """
        if self.dry_run:
            raise ValueError("Cannot record a persistent write during preview")
        value = str(path.resolve())
        if value not in self.modified_files:
            self.modified_files.append(value)
        keys = ["modified_files_all_attempts"] + (["backup_paths"] if backup else [])
        for key in keys:
            paths = self.other_data.setdefault(key, [])
            if value not in paths:
                paths.append(value)
        if self._persist:
            self._persist()

    def commit_metadata(self) -> None:
        """Report and validate staged metadata, writing only outside preview.

        Unchanged data is not rewritten. Changed data gets an exclusive backup
        before atomic replacement; readback must match the intended pickle.

        :return: ``None`` after preview staging or a validated durable commit.
        """
        database = self.metadata()
        payload = pickle.dumps(database, protocol=4)
        before = pickle.loads(self._snapshot)
        changes = []
        for spec in before.rows.keys() | database.rows.keys():
            old = before.rows.get(spec, {})
            new = database.rows.get(spec, {})
            row_fields = [key for key in old.keys() | new.keys() if key not in old or key not in new or not _equal(old[key], new[key])]
            if row_fields:
                changes.append({"vault": spec.as_string_id(), "name": str(new.get("Name", "")), "fields": sorted(row_fields)})
                for key in row_fields:
                    logger.info("%s %s %s: %r -> %r", self.script_name, spec.as_string_id(), key, old.get(key), new.get(key))
        database_fields = [item.name for item in fields(database) if item.name != "rows" and not _equal(getattr(before, item.name), getattr(database, item.name))]
        for name in database_fields:
            logger.info("%s database field %s: %r -> %r", self.script_name, name, getattr(before, name), getattr(database, name))
        self.report(changed_rows=len(changes), changed_fields=sum(len(c["fields"]) for c in changes), changes=changes[:100], changes_truncated=len(changes) > 100, changed_database_fields=database_fields)
        if payload == self._snapshot:
            return
        if not self.dry_run:
            path = self.data_dir / "vault-metadata-db.pickle"
            with tempfile.NamedTemporaryFile(dir=self.data_dir, prefix=f"{path.name}.bak-{self.script_name}-", delete=False) as backup:
                with path.open("rb") as source:
                    shutil.copyfileobj(source, backup)
                backup.flush()
                os.fsync(backup.fileno())
            self.record_modified_file(Path(backup.name), backup=True)
            database.write(path)
            self.record_modified_file(path)
            if path.read_bytes() != payload:
                raise ValueError(f"Metadata readback failed for {self.script_name}")
        self._snapshot = payload


def _equal(left: Any, right: Any) -> bool:
    """Compare ordinary metadata values without treating NaN as a change.

    Pickle comparison handles sentinel values that deliberately compare false.

    :param left: Previous field value.
    :param right: Proposed field value.
    :return: Whether the field is equivalent.
    """
    try:
        return bool(left == right) or pickle.dumps(left, protocol=4) == pickle.dumps(right, protocol=4)
    except (TypeError, ValueError):
        return pickle.dumps(left, protocol=4) == pickle.dumps(right, protocol=4)


def _execute(spec: MigrationSpec, context: MigrationContext) -> None:
    """Execute the validated bytes in an isolated module namespace.

    Register temporarily for dataclass/type introspection, then remove it.

    :param spec: Immutable resource snapshot.
    :param context: Current guarded migration attempt.
    :return: ``None`` after successful execution.
    """
    name = f"migration.scripts._run_{spec.number}"
    module = ModuleType(name)
    module.__file__ = str(spec.path)
    sys.modules[name] = module
    try:
        exec(compile(spec.source, str(spec.path), "exec"), module.__dict__)
        module.migrate(context)
        if context._database is not None and pickle.dumps(context._database, protocol=4) != context._snapshot:
            raise ValueError(f"Migration did not commit its staged metadata: {spec.name}")
    finally:
        sys.modules.pop(name, None)


def run_pending_migrations(*, dry_run: bool = True, data_dir: Path | None = None, scripts_dir: Path | None = None, provenance: dict[str, Any] | None = None) -> list[str]:
    """Run all pending migrations under daemon exclusion and writer locking.

    Internal path/provenance arguments support isolated tests. No migration
    state or scripts are read before acquiring the lifecycle guard.

    :param dry_run: Stage only, leaving persistent data untouched.
    :param data_dir: Internal storage override, or pipeline configuration.
    :param scripts_dir: Internal test resource override.
    :param provenance: Internal test provenance override.
    :return: Names successfully executed or previewed in this invocation.
    """
    directory = (data_dir or get_pipeline_data_dir()).expanduser().resolve()
    for key in ("VAULT_DB_PATH", "VAULT_DB"):
        if os.environ.get(key) and Path(os.environ[key]).expanduser().resolve() != directory / "vault-metadata-db.pickle":
            raise ValueError(f"Conflicting {key}; configure PIPELINE_DATA_DIR instead")
    with acquire_scanner_lifecycle_guard(directory):
        with nullcontext() if dry_run else wait_other_writers(directory / "scan-pipeline", timeout=60):
            state_path = directory / "migration.json"
            state = load_state(state_path)
            specs = tuple(discover_migrations(scripts_dir))
            validate_inventory(specs, state)
            version = provenance if provenance is not None else read_provenance()
            if not dry_run and version.get("in_docker"):
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", version.get("docker_image_hash") or "") or not re.fullmatch(r"[0-9a-f]{40}", version.get("git_commit_hash") or ""):
                    raise ValueError("Docker apply requires host-attested DOCKER_IMAGE_HASH and a stamped git commit")
            if not version.get("docker_image_hash") or not version.get("git_commit_hash"):
                logger.warning("Some deployment provenance is unavailable outside a stamped Docker image")
            context = MigrationContext(directory, dry_run)
            completed = []
            for spec in specs:
                previous = state.get(spec.name, {})
                if previous.get("success"):
                    logger.info("Skipping successful migration %s", spec.name)
                    continue
                logger.info("%s migration %s", "Previewing" if dry_run else "Applying", spec.name)
                context.script_name = spec.name
                prior_data = previous.get("other_data", {})
                context.other_data = {key: list(prior_data.get(key, [])) for key in ("backup_paths", "modified_files_all_attempts")}
                context.other_data.update(status="running", git_dirty=version.get("git_dirty"))
                if previous:
                    context.other_data["previous_status"] = "interrupted" if prior_data.get("status") == "running" else prior_data.get("status")
                    if previous["script_sha256"] != spec.sha256:
                        context.other_data["previous_script_sha256"] = previous["script_sha256"]
                        logger.warning("Retrying corrected unsuccessful migration %s", spec.name)
                context.modified_files = []
                context._persist = None
                if not dry_run:
                    now = native_datetime_utc_now().isoformat()
                    state[spec.name] = {"first_run_at": previous.get("first_run_at", now), "last_run_at": now, "docker_image_hash": version.get("docker_image_hash"), "git_commit_hash": version.get("git_commit_hash"), "success": False, "modified_files": context.modified_files, "other_data": context.other_data, "script_sha256": spec.sha256}
                    context._persist = lambda: save_state(state_path, state)
                    context._persist()
                try:
                    _execute(spec, context)
                except (Exception, KeyboardInterrupt, SystemExit):
                    # The plugin boundary must journal arbitrary script failures.
                    context.other_data.update(status="failed", error=redact_rpc_urls(traceback.format_exc()))
                    if context._persist:
                        context._persist()
                    logger.error("Migration %s failed:\n%s", spec.name, context.other_data["error"])
                    raise
                if not dry_run:
                    state[spec.name]["success"] = True
                    context.other_data["status"] = "succeeded"
                    context._persist()
                logger.info("%s complete: %s", spec.name, json.dumps(context.other_data, default=str))
                completed.append(spec.name)
            logger.info("%d migrations %s", len(completed), "previewed" if dry_run else "applied")
            return completed


def main() -> None:
    """Run the environment-configured console interface.

    Listing never acquires the lifecycle guard or imports scripts. Preview is
    the default; a running daemon blocks run before any attempt is recorded.

    :return: ``None`` on success; process exit 1/2 on failure/usage error.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("list", "run"))
    args = parser.parse_args()
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    install_rpc_logging(from_environment=True)
    try:
        if args.command == "list":
            specs = tuple(discover_migrations())
            state = load_state(get_pipeline_data_dir().expanduser().resolve() / "migration.json")
            print(tabulate([(s.name, state.get(s.name, {}).get("other_data", {}).get("status", "pending")) for s in specs], headers=("Migration", "Status")))
            validate_inventory(specs, state)
        else:
            value = os.environ.get("DRY_RUN", "true").strip().lower()
            if value not in {"true", "false"}:
                raise ValueError("DRY_RUN must be true or false")
            run_pending_migrations(dry_run=value == "true")
    except (Exception, KeyboardInterrupt, SystemExit) as exc:
        # Console boundary: script exceptions have already been journalled.
        logger.error("Migration command failed: %s", redact_rpc_urls(str(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
