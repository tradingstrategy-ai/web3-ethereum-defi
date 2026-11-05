"""Numbered migrations preserve data, reject active daemons and recover crashes."""

import json
import re
import subprocess
import sys
from contextlib import AbstractContextManager
from pathlib import Path

import pytest
from filelock import Timeout

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.utils import wait_other_writers
from eth_defi.vault import scan_all_chains
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.scanner_lock import ScannerDaemonRunning, acquire_scanner_lifecycle_guard
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

PROVENANCE = {"in_docker": False, "git_commit_hash": "a" * 40, "docker_image_hash": None, "git_dirty": True}
SPEC = VaultSpec(1, "0x0000000000000000000000000000000000000001")


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide an isolated pipeline with no inherited storage overrides."""
    for variable in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(variable, raising=False)
    path = tmp_path / "pipeline"
    path.mkdir()
    VaultDatabase(rows={SPEC: {"Name": "before", "_description": "First. Second."}}).write(path / "vault-metadata-db.pickle")
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(path))
    return path


@pytest.fixture
def scripts(tmp_path: Path) -> Path:
    """Provide a private resource directory for test-only fault injection."""
    path = tmp_path / "scripts"
    path.mkdir()
    return path


def write_script(directory: Path, name: str, body: str) -> Path:
    """Create a documented test migration without changing packaged resources.

    :param directory: Isolated resource directory.
    :param name: Numbered filename.
    :param body: Indented migration implementation.
    :return: Created script path.
    """
    path = directory / name
    path.write_text('"""Test migration.\nPurpose: test\nScope: fixture\nBranch: test\nPR title: test\nPR: https://example.invalid/1\nPreserves: unrelated data\nExpected: test assertions\n"""\nfrom eth_defi.vault.base import VaultSpec\nimport os\n\ndef migrate(context):\n' + body + "\n")
    return path


def run(storage: Path, scripts: Path, *, dry_run: bool = False) -> list[str]:
    """Execute only the isolated test inventory.

    :param storage: Isolated data directory.
    :param scripts: Test resource directory.
    :param dry_run: Whether to preview only.
    :return: Executed script names.
    """
    return runner.run_pending_migrations(data_dir=storage, scripts_dir=scripts, dry_run=dry_run, provenance=PROVENANCE)


def test_batch_preview_stages_prior_changes_and_apply_skips_success(storage: Path, scripts: Path) -> None:
    """The second preview sees the first proposal while all real bytes stay intact."""
    first = write_script(scripts, "001-name.py", '    next(iter(context.metadata().rows.values()))["Name"] = "after"\n    context.commit_metadata()')
    second = write_script(scripts, "002-check.py", '    assert next(iter(context.metadata().rows.values()))["Name"] == "after"\n    context.report(checked=True)\n    context.commit_metadata()')
    sentinels = ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb")
    for name in sentinels:
        (storage / name).write_bytes(b"do not change history")
    before = {p.name: p.read_bytes() for p in storage.iterdir()}
    assert run(storage, scripts, dry_run=True) == [first.name, second.name]
    assert {p.name: p.read_bytes() for p in storage.iterdir()} == before
    assert run(storage, scripts) == [first.name, second.name]
    state = runner.load_state(storage / "migration.json")
    assert state[first.name]["success"]
    assert state[second.name]["other_data"]["checked"]
    backup = Path(state[first.name]["other_data"]["backup_paths"][0])
    assert backup.read_bytes() == before["vault-metadata-db.pickle"]
    assert all((storage / name).read_bytes() == before[name] for name in sentinels)
    after = {p.name: p.read_bytes() for p in storage.iterdir()}
    assert run(storage, scripts) == []
    assert {p.name: p.read_bytes() for p in storage.iterdir()} == after


def test_failure_preserves_progress_and_retries_with_first_timestamp(storage: Path, scripts: Path) -> None:
    """A failure cannot run successors or erase recovery data on a no-op retry."""
    first = write_script(scripts, "001-repair.py", '    next(iter(context.metadata().rows.values()))["Name"] = "after"\n    context.commit_metadata()\n    raise ValueError("injected failure")')
    write_script(scripts, "002-later.py", "    context.report(later=True)")
    with pytest.raises(ValueError, match="injected"):
        run(storage, scripts)
    initial = runner.load_state(storage / "migration.json")
    assert set(initial) == {first.name}
    entry = initial[first.name]
    assert entry["other_data"]["status"] == "failed"
    assert entry["other_data"]["backup_paths"]
    first.write_text(first.read_text().replace('    raise ValueError("injected failure")', "    context.report(recovered=True)"))
    run(storage, scripts)
    final = runner.load_state(storage / "migration.json")[first.name]
    assert final["success"]
    assert final["first_run_at"] == entry["first_run_at"]
    assert final["last_run_at"] >= entry["last_run_at"]
    assert final["modified_files"] == []
    assert final["other_data"]["backup_paths"] == entry["other_data"]["backup_paths"]
    assert final["other_data"]["previous_script_sha256"] == entry["script_sha256"]


def test_process_crash_after_commit_recovers_without_rewriting(storage: Path, scripts: Path) -> None:
    """A native-style process exit leaves a running record that can safely resume."""
    path = write_script(scripts, "001-crash.py", '    next(iter(context.metadata().rows.values()))["Name"] = "committed"\n    context.commit_metadata()\n    os._exit(23)')
    command = "from pathlib import Path; from eth_defi.vault_protocols.migration import run_pending_migrations; import sys; run_pending_migrations(dry_run=False,data_dir=Path(sys.argv[1]),scripts_dir=Path(sys.argv[2]),provenance={'in_docker':False})"
    process = subprocess.run([sys.executable, "-c", command, str(storage), str(scripts)], capture_output=True, timeout=30)
    assert process.returncode == 23
    entry = runner.load_state(storage / "migration.json")[path.name]
    assert entry["other_data"]["status"] == "running"
    committed = (storage / "vault-metadata-db.pickle").read_bytes()
    path.write_text(path.read_text().replace("    os._exit(23)", "    context.report(recovered=True)"))
    run(storage, scripts)
    recovered = runner.load_state(storage / "migration.json")[path.name]
    assert recovered["other_data"]["previous_status"] == "interrupted"
    assert recovered["other_data"]["backup_paths"] == entry["other_data"]["backup_paths"]
    assert (storage / "vault-metadata-db.pickle").read_bytes() == committed


@pytest.mark.parametrize("dry_run", [True, False])
def test_daemon_blocks_before_discovery_or_state(storage: Path, scripts: Path, dry_run: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even an empty inventory must refuse the running daemon before reading state."""

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("Discovery must not happen before daemon guard")

    monkeypatch.setattr(runner, "discover_migrations", unexpected)
    before = {p.name: p.read_bytes() for p in storage.iterdir()}
    with acquire_scanner_lifecycle_guard(storage), pytest.raises(ScannerDaemonRunning, match="Stop the scanner"):
        run(storage, scripts, dry_run=dry_run)
    assert {p.name: p.read_bytes() for p in storage.iterdir()} == before


@pytest.mark.parametrize("kill", [False, True])
def test_sleeping_process_blocks_and_exit_releases_guard(storage: Path, scripts: Path, kill: bool) -> None:
    """The guard survives sleep and cannot leave a stale lock after process death."""
    code = "import os,sys,time; from pathlib import Path; from eth_defi.vault.scanner_lock import acquire_scanner_lifecycle_guard;\nwith acquire_scanner_lifecycle_guard(Path(sys.argv[1])):\n os.write(1,b'ready\\n'); time.sleep(30)"
    process = subprocess.Popen([sys.executable, "-c", code, str(storage)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdout.readline() == b"ready\n"
        with pytest.raises(ScannerDaemonRunning):
            run(storage, scripts, dry_run=True)
    finally:
        process.kill() if kill else process.terminate()
        process.communicate(timeout=10)
    assert run(storage, scripts, dry_run=True) == []


def test_scanner_wrapper_holds_guard_during_initialisation(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both daemon configuration paths wrap all scanner work in the lifetime guard."""
    monkeypatch.setenv("VAULT_SCANNER_DAEMON", "true")
    monkeypatch.setenv("LOOP_INTERVAL_SECONDS", "0")
    entered = []

    def inspect_guard() -> None:
        with pytest.raises(ScannerDaemonRunning):
            with acquire_scanner_lifecycle_guard(storage):
                pass
        entered.append(True)

    monkeypatch.setattr(scan_all_chains, "_main", inspect_guard)
    scan_all_chains.main()
    assert entered == [True]
    with acquire_scanner_lifecycle_guard(storage), pytest.raises(SystemExit) as exc:
        scan_all_chains.main()
    assert exc.value.code == 1
    assert entered == [True]
    monkeypatch.setenv("VAULT_SCANNER_DAEMON", "false")
    monkeypatch.setenv("LOOP_INTERVAL_SECONDS", "60")
    scan_all_chains.main()
    assert entered == [True, True]


def test_inventory_and_success_identity_protection(storage: Path, scripts: Path) -> None:
    """Renames, edits and late lower counters cannot silently run after success."""
    path = write_script(scripts, "010-original.py", "    context.report(ok=True)")
    run(storage, scripts)
    original = path.read_text()
    path.write_text(original + "\n# changed\n")
    with pytest.raises(ValueError, match="Successful migration changed"):
        run(storage, scripts)
    path.write_text(original)
    renamed = path.rename(scripts / "010-renamed.py")
    with pytest.raises(ValueError, match="renamed"):
        run(storage, scripts)
    renamed.rename(path)
    late = write_script(scripts, "002-late.py", "    pass")
    with pytest.raises(ValueError, match="Out-of-order"):
        run(storage, scripts)
    late.unlink()
    write_script(scripts, "010-duplicate.py", "    pass")
    with pytest.raises(ValueError, match="Duplicate"):
        tuple(runner.discover_migrations(scripts))


def test_discovery_numeric_order_without_imports(scripts: Path) -> None:
    """Listing parses bytes but cannot execute script top-level side effects."""
    path = write_script(scripts, "1000-later.py", "    pass")
    path.write_text(path.read_text() + '\nraise AssertionError("must not import")\n')
    write_script(scripts, "999-earlier.py", "    pass")
    assert [s.number for s in runner.discover_migrations(scripts)] == [999, 1000]


@pytest.mark.parametrize("content", ["[]", '{"broken": {}}', "not json", '{"same": 1, "same": 2}', '{"value": NaN}', '{"value": Infinity}'])
def test_corrupt_state_is_never_reset(storage: Path, scripts: Path, content: str) -> None:
    """Corrupt operational state aborts with its original bytes intact."""
    path = storage / "migration.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        run(storage, scripts)
    assert path.read_text() == content


def test_atomic_save_failure_preserves_original(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed replacement neither truncates the journal nor leaves temp files."""
    path = storage / "migration.json"
    path.write_text("{}\n")

    def fail_replace(*args: object) -> None:
        raise OSError("injected disk failure")

    monkeypatch.setattr(runner.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        runner.save_state(path, {"new": {}})
    assert path.read_text() == "{}\n"
    assert not list(storage.glob(".migration-*"))


def test_uncommitted_staging_cannot_be_marked_success(storage: Path, scripts: Path) -> None:
    """Forgetting the commit helper fails instead of journalling a false success."""
    path = write_script(scripts, "001-forgot.py", '    next(iter(context.metadata().rows.values()))["Name"] = "uncommitted"')
    original = (storage / "vault-metadata-db.pickle").read_bytes()
    with pytest.raises(ValueError, match="did not commit"):
        run(storage, scripts)
    assert not runner.load_state(storage / "migration.json")[path.name]["success"]
    assert (storage / "vault-metadata-db.pickle").read_bytes() == original


@pytest.mark.parametrize("commit", [None, "", "unspecified", "a" * 39, "g" * 40])
def test_docker_provenance_required_before_attempt(storage: Path, scripts: Path, commit: str | None) -> None:
    """A missing or invalid image commit cannot create an apply attempt."""
    write_script(scripts, "001-noop.py", "    pass")
    with pytest.raises(ValueError, match="git commit stamped inside the image"):
        runner.run_pending_migrations(data_dir=storage, scripts_dir=scripts, dry_run=False, provenance={"in_docker": True, "git_commit_hash": commit})
    assert not (storage / "migration.json").exists()


@pytest.mark.parametrize("legacy_image_hash", [None, "obsolete-host-value"])
def test_stamped_docker_apply_without_image_hash(storage: Path, scripts: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_image_hash: str | None) -> None:
    """The image's commit stamp is sufficient, independently of host image hashes."""
    build_root = tmp_path / "image"
    build_root.mkdir()
    commit = "b" * 40
    (build_root / "GIT_VERSION_HASH.txt").write_text(commit)
    if legacy_image_hash is None:
        monkeypatch.delenv("DOCKER_IMAGE_HASH", raising=False)
    else:
        monkeypatch.setenv("DOCKER_IMAGE_HASH", legacy_image_hash)
    original_exists = Path.exists

    def docker_exists(path: Path) -> bool:
        """Expose a container marker while retaining actual version-file reads."""
        return path == Path("/.dockerenv") or original_exists(path)

    monkeypatch.setattr(Path, "exists", docker_exists)
    version = runner.read_provenance(data_root=build_root)
    assert version["in_docker"] is True
    assert version["docker_image_hash"] is None
    assert version["git_commit_hash"] == commit
    script = write_script(scripts, "001-stamped.py", '    next(iter(context.metadata().rows.values()))["Name"] = "after"\n    context.commit_metadata()')
    assert runner.run_pending_migrations(data_dir=storage, scripts_dir=scripts, dry_run=False, provenance=version) == [script.name]
    entry = runner.load_state(storage / "migration.json")[script.name]
    assert entry["success"] is True
    assert entry["git_commit_hash"] == commit
    assert entry["docker_image_hash"] is None
    assert VaultDatabase.read(storage / "vault-metadata-db.pickle").rows[SPEC]["Name"] == "after"


def test_cli_list_works_while_guarded_and_invalid_boolean_fails(storage: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The operator can inspect pending work while the scanner is running."""
    monkeypatch.setattr(sys, "argv", ["vault-migrate", "list"])
    with acquire_scanner_lifecycle_guard(storage):
        runner.main()
    output = capsys.readouterr().out
    assert "009-normalise" in output
    assert "Migration summary" not in output
    monkeypatch.setattr(sys, "argv", ["vault-migrate", "run"])
    monkeypatch.setenv("DRY_RUN", "treu")
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 1
    assert not (storage / "migration.json").exists()


def test_simultaneous_startup_has_single_owner(storage: Path) -> None:
    """Contending processes cannot both begin a daemon or migration operation."""
    code = "import os,sys; from pathlib import Path; from eth_defi.vault.scanner_lock import acquire_scanner_lifecycle_guard,ScannerDaemonRunning; sys.stdin.readline();\ntry:\n with acquire_scanner_lifecycle_guard(Path(sys.argv[1])):\n  os.write(1,b'owner\\n'); sys.stdin.readline()\nexcept ScannerDaemonRunning:\n os.write(1,b'blocked\\n')"
    children = [subprocess.Popen([sys.executable, "-c", code, str(storage)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    try:
        for child in children:
            child.stdin.write(b"start\n")
            child.stdin.flush()
        assert sorted(child.stdout.readline() for child in children) == [b"blocked\n", b"owner\n"]
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=10)
    with acquire_scanner_lifecycle_guard(storage):
        pass


def test_writer_lock_wait_retains_lifecycle_guard(storage: Path, scripts: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A busy one-shot writer cannot invert lock order or let a daemon start."""

    def bounded_writer_lock(path: Path, timeout: int) -> AbstractContextManager[None]:
        assert timeout == 60
        with pytest.raises(ScannerDaemonRunning):
            with acquire_scanner_lifecycle_guard(storage):
                pass
        return wait_other_writers(path, timeout=0.1)

    monkeypatch.setattr(runner, "wait_other_writers", bounded_writer_lock)
    with wait_other_writers(storage / "scan-pipeline"), pytest.raises(Timeout):
        run(storage, scripts)
    assert not (storage / "migration.json").exists()
    assert run(storage, scripts) == []


def test_invalid_cli_syntax_exits_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI distinguishes unsupported commands from operational failures."""
    monkeypatch.setattr(sys, "argv", ["vault-migrate", "force"])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2


@pytest.mark.parametrize("failure", ["SystemExit(0)", "SystemExit(2)", "KeyboardInterrupt()"])
def test_script_exit_is_failure_not_cli_success(storage: Path, scripts: Path, failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A plugin cannot report success by exiting before validation finishes."""
    script = write_script(scripts, "001-exit.py", f"    raise {failure}")
    specs = tuple(runner.discover_migrations(scripts))
    monkeypatch.setattr(runner, "discover_migrations", lambda _directory=None: iter(specs))
    monkeypatch.setattr(runner, "read_provenance", lambda: PROVENANCE)
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setattr(sys, "argv", ["vault-migrate", "run"])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 1
    assert runner.load_state(storage / "migration.json")[script.name]["other_data"]["status"] == "failed"


def test_valid_journal_rejects_duplicate_records_and_nonfinite_progress(storage: Path, scripts: Path) -> None:
    """Otherwise valid records cannot conceal corruption behind permissive JSON."""
    script = write_script(scripts, "001-noop.py", "    pass")
    run(storage, scripts)
    path = storage / "migration.json"
    entry = runner.load_state(path)[script.name]
    encoded = json.dumps(entry)
    path.write_text('{"001-noop.py":' + encoded + ',"001-noop.py":' + encoded + "}")
    with pytest.raises(ValueError, match="Duplicate keys"):
        runner.load_state(path)
    entry["other_data"]["count"] = float("nan")
    path.write_text(json.dumps({script.name: entry}))
    with pytest.raises(ValueError, match="Out of range float"):
        runner.load_state(path)


def test_metadata_reports_non_row_database_changes(storage: Path, scripts: Path) -> None:
    """A future metadata migration must audit changes to scan boundaries too."""
    script = write_script(scripts, "001-boundary.py", "    context.metadata().last_scanned_block[1] = 123\n    context.commit_metadata()")
    run(storage, scripts)
    report = runner.load_state(storage / "migration.json")[script.name]["other_data"]
    assert report["changed_database_fields"] == ["last_scanned_block"]
    assert report["changed_rows"] == 0


@pytest.mark.parametrize("dry_run", [True, False])
def test_metadata_preserves_old_discovery_records(storage: Path, scripts: Path, dry_run: bool) -> None:
    """Older persisted leads with missing slots do not break the audit comparison."""
    path = storage / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    lead = PotentialVaultMatch(chain=SPEC.chain_id, address=SPEC.vault_address, first_seen_at_block=1, first_seen_at=native_datetime_utc_now())
    del lead.configuration_count
    database.leads[SPEC] = lead
    database.write(path)
    original = path.read_bytes()
    script = write_script(scripts, "001-metadata.py", '    next(iter(context.metadata().rows.values()))["Name"] = "after"\n    context.commit_metadata()')
    assert run(storage, scripts, dry_run=dry_run) == [script.name]
    restored = VaultDatabase.read(path)
    assert not hasattr(restored.leads[SPEC], "configuration_count")
    assert restored.leads[SPEC].first_seen_at_block == 1
    if dry_run:
        assert path.read_bytes() == original
        assert not (storage / "migration.json").exists()
    else:
        assert restored.rows[SPEC]["Name"] == "after"
        assert runner.load_state(storage / "migration.json")[script.name]["other_data"]["changed_database_fields"] == []


def summary_rows(output: str) -> dict[str, list[str]]:
    """Extract rendered migration rows while ignoring table alignment.

    Assert semantic cells rather than terminal-width padding or the table's
    separator line. Script names contain no whitespace.

    :param output: Captured stdout from one runner invocation.
    :return: Script-name keys with result, changed-row and changed-field cells.
    """
    return {cells[0]: cells[1:] for line in output.splitlines() if line[:1].isdigit() for cells in [re.split(r"\s{2,}", line.strip())]}


@pytest.mark.parametrize("dry_run", [True, False])
def test_run_summary_reports_changed_unchanged_and_unknown_counts(storage: Path, scripts: Path, dry_run: bool, capsys: pytest.CaptureFixture[str]) -> None:
    """Keep real zero counts distinct from missing reports in both modes.

    The table reads invocation reports without adding journal writes in
    preview or persisting any new reporting schema during apply.

    :param storage: Isolated pipeline directory.
    :param scripts: Private migration inventory.
    :param dry_run: Preview versus persistent apply.
    :param capsys: Capture the final stdout table.
    :return: None after checking outcomes, counts and preview preservation.
    """
    first = write_script(scripts, "001-change.py", '    next(iter(context.metadata().rows.values()))["Name"] = "after"\n    context.commit_metadata()')
    second = write_script(scripts, "002-unchanged.py", "    context.commit_metadata()")
    third = write_script(scripts, "003-other-work.py", "    context.report(checked=True)")
    before = {p.name: p.read_bytes() for p in storage.iterdir()}
    assert run(storage, scripts, dry_run=dry_run) == [first.name, second.name, third.name]
    output = capsys.readouterr().out
    assert output.count("Migration summary (") == 1
    assert f"Migration summary ({'preview' if dry_run else 'apply'})" in output
    status = "Previewed" if dry_run else "Applied"
    assert summary_rows(output) == {first.name: [status, "1", "1"], second.name: [status, "0", "0"], third.name: [status, "—", "—"]}
    if dry_run:
        assert {p.name: p.read_bytes() for p in storage.iterdir()} == before
    else:
        state = runner.load_state(storage / "migration.json")
        assert state[first.name]["success"]
        assert "result" not in state[first.name]["other_data"]


@pytest.mark.parametrize("dry_run", [True, False])
def test_run_summary_includes_skipped_migrations_without_old_counts(storage: Path, scripts: Path, dry_run: bool, capsys: pytest.CaptureFixture[str]) -> None:
    """Show skipped migrations without implying their historic changes ran again.

    A mixed batch includes already applied work and new work. The all-skipped
    batch still prints the table and an explicit no-pending-work message.

    :param storage: Isolated pipeline directory.
    :param scripts: Private migration inventory.
    :param dry_run: Mode for the mixed and all-skipped invocations.
    :param capsys: Capture and clear prior invocation output.
    :return: None after checking skipped outcomes and current counts.
    """
    first = write_script(scripts, "001-applied.py", '    next(iter(context.metadata().rows.values()))["Name"] = "after"\n    context.commit_metadata()')
    assert run(storage, scripts) == [first.name]
    second = write_script(scripts, "002-pending.py", "    context.commit_metadata()")
    capsys.readouterr()
    assert run(storage, scripts, dry_run=dry_run) == [second.name]
    output = capsys.readouterr().out
    assert summary_rows(output) == {first.name: ["Skipped (already applied)", "—", "—"], second.name: ["Previewed" if dry_run else "Applied", "0", "0"]}
    assert "No migrations pending." not in output
    if dry_run:
        run(storage, scripts)
    capsys.readouterr()
    before = {p.name: p.read_bytes() for p in storage.iterdir()}
    assert run(storage, scripts, dry_run=dry_run) == []
    output = capsys.readouterr().out
    assert output.count("Migration summary (") == 1
    assert "No migrations pending." in output
    assert all(cells == ["Skipped (already applied)", "—", "—"] for cells in summary_rows(output).values())
    assert {p.name: p.read_bytes() for p in storage.iterdir()} == before


@pytest.mark.parametrize("dry_run", [True, False])
def test_run_summary_empty_inventory(storage: Path, scripts: Path, dry_run: bool, capsys: pytest.CaptureFixture[str]) -> None:
    """An empty inventory still produces a labelled table and no-work message.

    Neither execution mode should confuse an empty validated inventory with a
    failed preflight.

    :param storage: Isolated pipeline directory.
    :param scripts: Empty private migration inventory.
    :param dry_run: Preview versus persistent apply.
    :param capsys: Capture the stdout summary.
    :return: None after checking the empty summary and unchanged storage.
    """
    before = {p.name: p.read_bytes() for p in storage.iterdir()}
    assert run(storage, scripts, dry_run=dry_run) == []
    output = capsys.readouterr().out
    assert "Changed rows" in output and "Changed fields" in output
    assert "No migrations pending." in output
    assert "did not start" not in output
    assert summary_rows(output) == {}
    assert {p.name: p.read_bytes() for p in storage.iterdir()} == before


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("failure", ["ValueError", "KeyboardInterrupt", "SystemExit"])
def test_run_summary_failure_retains_exception_and_stops_batch(storage: Path, scripts: Path, dry_run: bool, failure: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Show completed, failed and unattempted work without swallowing failures.

    A failed migration may already have committed metadata. Its counts remain
    unavailable in the table rather than implying a completed or rolled-back
    operation; the journal retains the existing partial-progress evidence.

    :param storage: Isolated pipeline directory.
    :param scripts: Three-script inventory with an injected middle failure.
    :param dry_run: Preview versus persistent apply.
    :param failure: Ordinary error, interrupt or script-requested exit.
    :param capsys: Capture the final table emitted during exception unwinding.
    :return: None after checking output, failure identity and preserved state.
    """
    first = write_script(scripts, "001-good.py", '    next(iter(context.metadata().rows.values()))["Name"] = "after"\n    context.commit_metadata()')
    second = write_script(scripts, "002-failure.py", '    next(iter(context.metadata().rows.values()))["_description"] = "partly changed"\n    context.commit_metadata()\n    raise ' + failure + '("injected failure")')
    third = write_script(scripts, "003-later.py", '    raise AssertionError("must not execute")')
    error_type = {"ValueError": ValueError, "KeyboardInterrupt": KeyboardInterrupt, "SystemExit": SystemExit}[failure]
    before = {p.name: p.read_bytes() for p in storage.iterdir()}
    with pytest.raises(error_type, match="injected failure"):
        run(storage, scripts, dry_run=dry_run)
    output = capsys.readouterr().out
    assert output.count("Migration summary (") == 1
    assert summary_rows(output) == {first.name: ["Previewed" if dry_run else "Applied", "1", "1"], second.name: ["Failed", "—", "—"], third.name: ["Not run", "—", "—"]}
    if dry_run:
        assert {p.name: p.read_bytes() for p in storage.iterdir()} == before
    else:
        state = runner.load_state(storage / "migration.json")
        assert state[first.name]["success"]
        assert state[second.name]["other_data"]["status"] == "failed"
        assert state[second.name]["other_data"]["backup_paths"]
        assert third.name not in state


def test_run_summary_preflight_failure_does_not_claim_no_pending_work(storage: Path, scripts: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A guarded invocation cannot infer an empty inventory from blocked reads.

    The runner must retain its lifecycle-before-discovery rule and explain why
    no per-migration outcomes are available.

    :param storage: Isolated pipeline directory.
    :param scripts: Private inventory that must not be read while blocked.
    :param capsys: Capture the preflight-failure summary.
    :return: None after checking the guard exception and accurate message.
    """
    with acquire_scanner_lifecycle_guard(storage), pytest.raises(ScannerDaemonRunning):
        run(storage, scripts)
    output = capsys.readouterr().out
    assert "Migration batch did not start." in output
    assert "No migrations pending." not in output


@pytest.mark.parametrize("fail_migration", [True, False])
def test_run_summary_output_failure_preserves_result(storage: Path, scripts: Path, monkeypatch: pytest.MonkeyPatch, fail_migration: bool) -> None:
    """A broken output stream must not change success or the original failure.

    Reporting runs during exception unwinding and can encounter a closed
    terminal or pipe independently of the migration's error.

    :param storage: Isolated pipeline directory.
    :param scripts: Private migration inventory for success and failure cases.
    :param monkeypatch: Simulate an output stream refusing summary writes.
    :param fail_migration: Whether the migration fails before summary output.
    :return: None after checking the return value or original failure.
    """
    script = write_script(scripts, "001-work.py", '    raise ValueError("original failure")' if fail_migration else "    context.report(ok=True)")

    def fail_output(*args: object, **kwargs: object) -> None:
        raise BrokenPipeError("closed output")

    monkeypatch.setattr(runner, "_print_run_summary", fail_output)
    if fail_migration:
        with pytest.raises(ValueError, match="original failure"):
            run(storage, scripts)
    else:
        assert run(storage, scripts) == [script.name]
        assert runner.load_state(storage / "migration.json")[script.name]["success"]


@pytest.mark.parametrize("count", [True, -1, "3"])
def test_run_summary_non_count_reports_remain_unknown(storage: Path, scripts: Path, capsys: pytest.CaptureFixture[str], count: bool | int | str) -> None:
    """Do not display malformed reporting values as known change counts.

    Script reports are extensible JSON data, not a schema for numeric counts.
    Booleans, negative numbers and text must not look like valid quantities.

    :param storage: Isolated pipeline directory.
    :param scripts: Private migration inventory.
    :param capsys: Capture the final table.
    :param count: Non-count value supplied by a script's custom report.
    :return: None after checking both quantities remain unavailable.
    """
    script = write_script(scripts, "001-report.py", f"    context.report(changed_rows={count!r}, changed_fields={count!r})")
    assert run(storage, scripts) == [script.name]
    assert summary_rows(capsys.readouterr().out)[script.name] == ["Applied", "—", "—"]


def test_run_summary_retry_does_not_reuse_failed_attempt_counts(storage: Path, scripts: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Use only the current successful attempt's reports after a retry.

    A failed migration can leave reported counts in its journal. Retrying a
    corrected script without counts must not revive those historic values.

    :param storage: Isolated pipeline directory.
    :param scripts: Private retryable migration inventory.
    :param capsys: Clear failed-attempt output and capture the retry.
    :return: None after checking the corrected script's counts are unknown.
    """
    script = write_script(scripts, "001-retry.py", '    context.report(changed_rows=8, changed_fields=9)\n    raise ValueError("retry me")')
    with pytest.raises(ValueError, match="retry me"):
        run(storage, scripts)
    capsys.readouterr()
    write_script(scripts, script.name, "    context.report(recovered=True)")
    assert run(storage, scripts) == [script.name]
    assert summary_rows(capsys.readouterr().out)[script.name] == ["Applied", "—", "—"]
