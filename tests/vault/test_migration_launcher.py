"""Production launcher refuses active/unknown daemons before container creation."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "scripts/run-vault-migrations.sh"


@pytest.fixture
def docker_environment(tmp_path: Path) -> dict[str, str]:
    """Replace Docker with a recording executable, never touching host services."""
    executable = tmp_path / "docker"
    executable.write_text(f"#!{sys.executable}\nimport os,sys\nfrom pathlib import Path\nargs=sys.argv[1:]\nwith open(os.environ['DOCKER_CALLS'],'a') as f: f.write(' '.join(args)+'\\n')\nif args[0]=='compose' and 'ps' in args:\n if os.environ.get('INSPECTION_FAILURE')=='1': sys.exit(1)\n sys.stdout.write(os.environ.get('CONTAINERS',''))\nelif args[0]=='ps':\n if os.environ.get('INSPECTION_FAILURE')=='3': sys.exit(1)\n sys.stdout.write(os.environ.get('NAMED_CONTAINERS',''))\nelif args[0]=='inspect':\n if os.environ.get('INSPECTION_FAILURE')=='2': sys.exit(1)\n sys.stdout.write(os.environ.get('STATUS_'+args[-1],'unknown')+'\\n')\nelif args[:2]==['image','inspect']: sys.stdout.write('sha256:'+'a'*64+'\\n')\nelif args[0]=='compose' and 'run' in args:\n override=Path(args[args.index('-f',2)+1])\n assert 'sha256:'+'a'*64 in override.read_text()\n Path(os.environ['LAUNCHED']).touch()\nelse: sys.exit(3)\n")
    executable.chmod(0o755)
    return os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}", "DOCKER_CALLS": str(tmp_path / "calls"), "LAUNCHED": str(tmp_path / "launched")}


@pytest.mark.parametrize("status", ["running", "paused", "restarting", "removing", "dead", "unknown"])
def test_launcher_refuses_active_or_unknown_service(docker_environment: dict[str, str], status: str) -> None:
    """No one-shot container starts when any service instance is unsafe."""
    env = docker_environment | {"CONTAINERS": "old\nactive\n", "STATUS_old": "exited", "STATUS_active": status}
    result = subprocess.run(["bash", str(LAUNCHER), "run"], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert "Migration blocked" in result.stderr
    assert not Path(env["LAUNCHED"]).exists()


@pytest.mark.parametrize("failure", ["1", "2", "3"])
def test_launcher_fails_closed_on_docker_errors(docker_environment: dict[str, str], failure: str) -> None:
    """Missing Docker access is not evidence that the daemon is stopped."""
    env = docker_environment | {"CONTAINERS": "daemon\n", "INSPECTION_FAILURE": failure}
    result = subprocess.run(["bash", str(LAUNCHER), "run"], env=env, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert not Path(env["LAUNCHED"]).exists()


@pytest.mark.parametrize("containers", ["", "daemon\n"])
def test_launcher_allows_stopped_or_absent_service(docker_environment: dict[str, str], containers: str) -> None:
    """Confirmed safe status allows the pinned image and guarded entry point."""
    env = docker_environment | {"CONTAINERS": containers, "STATUS_daemon": "exited", "DRY_RUN": "false"}
    result = subprocess.run(["bash", str(LAUNCHER), "run"], env=env, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    calls = Path(env["DOCKER_CALLS"]).read_text()
    assert "--entrypoint vault-migrate" in calls
    assert "DRY_RUN=false" in calls
    assert "SCAN_PRICES=false" in calls
    assert Path(env["LAUNCHED"]).exists()


def test_listing_does_not_require_stopping_service(docker_environment: dict[str, str]) -> None:
    """Listing bypasses daemon preflight but retains the pinned image."""
    env = docker_environment | {"CONTAINERS": "daemon", "STATUS_daemon": "running"}
    result = subprocess.run(["bash", str(LAUNCHER), "list"], env=env, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert " ps " not in Path(env["DOCKER_CALLS"]).read_text()


@pytest.mark.skipif(os.environ.get("RUN_MIGRATION_DOCKER_TEST") != "1", reason="Set RUN_MIGRATION_DOCKER_TEST=1 with a built scanner image for the real bind-mount check")
def test_guard_contends_across_docker_bind_mounts(tmp_path: Path) -> None:
    """Two real containers contend on the same inode without production mounts."""
    source = Path(__file__).resolve().parents[2] / "eth_defi/vault/scanner_lock.py"
    image = os.environ.get("MIGRATION_TEST_IMAGE", "vault-scanner:local")
    base = ["docker", "run", "--rm", "--entrypoint", "python", "-v", f"{tmp_path}:/data", "-v", f"{source}:/guard.py:ro", image, "-c"]
    prefix = "import sys; sys.path.insert(0,'/'); from guard import acquire_scanner_lifecycle_guard; from pathlib import Path; "
    holder = subprocess.Popen(base + [prefix + "import os,time;\nwith acquire_scanner_lifecycle_guard(Path('/data')):\n os.write(1,b'ready\\n');time.sleep(12)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert holder.stdout.readline() == b"ready\n"
        contender = subprocess.run(base + [prefix + "\nwith acquire_scanner_lifecycle_guard(Path('/data')): pass"], capture_output=True, timeout=10)
        assert contender.returncode != 0
        assert b"lifecycle guard" in contender.stderr
    finally:
        holder.communicate(timeout=25)
    assert not list(tmp_path.iterdir())


def test_launcher_detects_old_daemon_in_another_project(docker_environment: dict[str, str]) -> None:
    """The fixed production name protects rollout from an unguarded old image."""
    env = docker_environment | {"CONTAINERS": "", "NAMED_CONTAINERS": "old-project-daemon", "STATUS_old-project-daemon": "running"}
    result = subprocess.run(["bash", str(LAUNCHER), "run"], env=env, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 1
    assert "Migration blocked" in result.stderr
    assert not Path(env["LAUNCHED"]).exists()
    assert "compose -f docker-compose.yml ps" in Path(env["DOCKER_CALLS"]).read_text()
