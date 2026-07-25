"""Seed the Foundry (Anvil) fork RPC cache from repository-supplied defaults.

Anvil persists every archive ``eth_call`` / ``eth_getStorageAt`` it replays to
``~/.foundry/cache/rpc/<network>/<block>/`` (see the "warm RPC cache" section of
:mod:`eth_defi.testing.anvil_fork_pool`). CI restores that directory between runs,
but on a genuinely cold cache — a first run, an evicted GitHub Actions cache, a
fresh developer checkout — every fixed-block fork re-hammers the upstream archive,
which is the dominant cause of the flaky-fork failures documented in
:file:`docs/README-test-suite-performance.md`.

This module lets the **repository ship default cache files** so a cold cache
starts warm. The committed seed covers every *fixed* fork block the test suite
uses — the canonical midnight blocks plus the per-test pinned blocks — because
only a reproducible block is worth caching. Chains that must fork the chain tip
(see :data:`NON_CACHEABLE_CHAIN_NETWORKS`) are deliberately excluded. Committed seed files live under
:data:`DEFAULT_SEED_DIR` (mirroring Foundry's ``<network>/<block>/…`` layout);
an additional external seed directory can be supplied via the
``ETH_DEFI_RPC_CACHE_SEED_DIR`` environment variable (e.g. a large cache
downloaded from durable storage). Seeding is **non-destructive**: an existing
(warmer, more recent) cache file is never overwritten.

The seeding is wired into the session-scoped ``anvil_fork_pool`` setup in
``tests/conftest.py`` so it runs once per xdist worker before any fork launches.
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

#: Repository-shipped default cache tree. Mirrors Foundry's on-disk layout:
#: ``<network-name>/<block>/storage.json`` (network *name*, not chain id). Ship
#: dense caches for every fixed fork block the suite uses. See the ``README.md``
#: in this directory for how to capture them.
DEFAULT_SEED_DIR: Path = Path(__file__).parent / "rpc_cache_seed"

#: Chains whose fork tests cannot use a fixed historical block, so their fork
#: cache must **not** be committed. Monad provides no archive-complete historical
#: state, so its tests fork the chain tip: the block number differs on every run,
#: a committed cache entry would never be hit again, and it would grow the
#: repository for nothing. See the Monad chain-quirk notes in ``CLAUDE.md``.
NON_CACHEABLE_CHAIN_NETWORKS: frozenset[str] = frozenset({"monad"})

#: Environment variable naming an *additional* seed directory (same layout),
#: applied after the repo default. Use for a large cache kept outside git.
SEED_DIR_ENV_VAR: str = "ETH_DEFI_RPC_CACHE_SEED_DIR"

#: Environment variable overriding the destination Foundry RPC cache directory
#: (defaults to ``~/.foundry/cache/rpc``). Primarily for tests.
CACHE_DIR_ENV_VAR: str = "FOUNDRY_RPC_CACHE_DIR"


def get_foundry_rpc_cache_dir() -> Path:
    """Return the Foundry fork RPC cache directory.

    :return:
        ``$FOUNDRY_RPC_CACHE_DIR`` if set, otherwise ``~/.foundry/cache/rpc``.
    """
    override = os.environ.get(CACHE_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".foundry" / "cache" / "rpc"


def _copy_file_atomic(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst`` atomically, creating parent directories.

    Writes to a temporary file in the destination directory then ``os.replace``s
    it into place, so a concurrent xdist worker never observes a half-written
    cache file.

    :param src:
        Source cache file.

    :param dst:
        Destination path (overwritten atomically).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dst.parent, prefix=".seed-", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copy2(src, tmp_path)
        os.replace(tmp_path, dst)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def seed_foundry_rpc_cache(
    seed_dir: Path,
    cache_dir: Path | None = None,
    *,
    overwrite: bool = False,
) -> int:
    """Copy repository-supplied cache files into the Foundry RPC cache.

    Mirrors every file under ``seed_dir`` into ``cache_dir`` preserving the
    relative ``<network>/<block>/…`` layout. **Non-destructive by default**: a
    file that already exists in the live cache is skipped, because the live copy
    is at least as fresh (a fork may have extended it with more state). Nothing is
    ever deleted from the live cache.

    :param seed_dir:
        Source directory of committed/default cache files. A missing directory is
        a no-op (returns ``0``).

    :param cache_dir:
        Destination Foundry RPC cache directory. Defaults to
        :func:`get_foundry_rpc_cache_dir`.

    :param overwrite:
        When ``True``, replace existing live cache files too. Default ``False``
        (never clobber a warmer live cache).

    :return:
        Number of files copied into the live cache.
    """
    if not seed_dir.exists():
        return 0

    cache_dir = cache_dir or get_foundry_rpc_cache_dir()
    copied = 0
    for src in seed_dir.rglob("*"):
        if not src.is_file():
            continue
        relative = src.relative_to(seed_dir)
        # Only mirror Foundry's <network>/<block>/<file> tree (>= 3 path parts);
        # skip repo docs living at the seed-dir root (e.g. README.md).
        if len(relative.parts) < 3:
            continue
        dst = cache_dir / relative
        if dst.exists() and not overwrite:
            continue
        _copy_file_atomic(src, dst)
        copied += 1

    if copied:
        logger.info("Seeded %d Foundry fork RPC cache file(s) from %s into %s", copied, seed_dir, cache_dir)
    return copied


def seed_default_foundry_rpc_cache(cache_dir: Path | None = None) -> int:
    """Seed the Foundry RPC cache from the repo default and env-supplied dirs.

    Applies :data:`DEFAULT_SEED_DIR` first, then any directory named by
    ``$ETH_DEFI_RPC_CACHE_SEED_DIR``. Both are optional; a missing directory is
    silently skipped. Safe to call once per test session per xdist worker.

    :param cache_dir:
        Destination cache directory. Defaults to :func:`get_foundry_rpc_cache_dir`.

    :return:
        Total number of files copied across all seed directories.
    """
    cache_dir = cache_dir or get_foundry_rpc_cache_dir()
    total = seed_foundry_rpc_cache(DEFAULT_SEED_DIR, cache_dir)

    external = os.environ.get(SEED_DIR_ENV_VAR)
    if external:
        total += seed_foundry_rpc_cache(Path(external).expanduser(), cache_dir)

    return total
