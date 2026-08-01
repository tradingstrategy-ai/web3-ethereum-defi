"""Ship a prebuilt ERC-20 / vault token cache so tests do not refetch it over RPC.

Vault token lookups are cached at two levels, and **both only engage when the
vault's ``token_cache`` is a :py:class:`~eth_defi.token.TokenDiskCache`**:

- the vault → denomination / share **token address** resolution
  (:py:mod:`eth_defi.erc_4626.vault_token`), gated on
  ``isinstance(self.token_cache, TokenDiskCache)`` in
  :py:mod:`eth_defi.erc_4626.vault`;
- the ERC-20 **token metadata** (``name`` / ``symbol`` / ``decimals`` /
  ``supply``) read by :py:func:`eth_defi.token.fetch_erc20_details`.

The default cache — :py:data:`eth_defi.token.DEFAULT_TOKEN_CACHE` — is an
in-memory ``cachetools.LRUCache``. It therefore (a) silently disables the
address-resolution cache entirely, and (b) starts empty in every new process,
and pytest runs many (one per ``pytest-xdist`` worker). Vaults consequently
re-read token addresses and metadata over RPC on every cold fork, sequentially,
per worker. On a throttled CI provider each of those reads can stall — the same
failure mode documented in :mod:`eth_defi.testing.anvil_fork_pool`.

This module ships that data **in the repository** as a small SQLite
:py:class:`~eth_defi.token.TokenDiskCache` (:data:`TOKEN_CACHE_SEED_PATH`) and
installs it as the default vault token cache for the test session, so both cache
levels engage and start warm.

Rebuilding the shipped cache
----------------------------

The seed is regenerated from a real run rather than hand-maintained. Set
``ETH_DEFI_TOKEN_CACHE_REBUILD=1`` and run the tests you want covered; every
token resolved during the run is merged back into the seed at session end:

.. code-block:: shell

    source .local-test.env && \
        ETH_DEFI_TOKEN_CACHE_REBUILD=1 \
        poetry run pytest tests/erc_4626/vault_protocol/ -m "not slow"
    git add eth_defi/testing/token_cache_seed/

See :file:`eth_defi/testing/README.md` for the create / update / purge process,
and :mod:`eth_defi.testing.rpc_cache` for the sibling Anvil fork-state seed.
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path

from eth_defi.token import TokenDiskCache

logger = logging.getLogger(__name__)

#: Committed SQLite token cache shipped with the repository.
TOKEN_CACHE_SEED_PATH: Path = Path(__file__).parent / "token_cache_seed" / "tokens.sqlite"

#: When truthy, tokens resolved during the session are merged back into
#: :data:`TOKEN_CACHE_SEED_PATH` at session end.
REBUILD_ENV_VAR: str = "ETH_DEFI_TOKEN_CACHE_REBUILD"

#: When truthy, skip installing the shipped cache (e.g. to measure cold behaviour).
DISABLE_ENV_VAR: str = "ETH_DEFI_TOKEN_CACHE_DISABLE"

_TRUTHY = {"1", "true", "yes"}


def _env_flag(name: str) -> bool:
    """Read a truthy environment flag.

    :param name: Environment variable name.
    :return: ``True`` when set to ``1`` / ``true`` / ``yes``.
    """
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def is_token_cache_seeding_disabled() -> bool:
    """Check whether the caller asked to skip token cache seeding.

    :return: ``True`` when ``$ETH_DEFI_TOKEN_CACHE_DISABLE`` is truthy.
    """
    return _env_flag(DISABLE_ENV_VAR)


def is_token_cache_rebuild_requested() -> bool:
    """Check whether the shipped token cache should be regenerated.

    :return: ``True`` when ``$ETH_DEFI_TOKEN_CACHE_REBUILD`` is truthy.
    """
    return _env_flag(REBUILD_ENV_VAR)


def install_token_cache(worker_id: str = "master") -> TokenDiskCache:
    """Install the shipped token cache as the default for vaults in this session.

    Copies :data:`TOKEN_CACHE_SEED_PATH` to a private per-worker working file
    (so concurrent ``pytest-xdist`` workers never contend on one SQLite file and
    the committed seed is never mutated in place), opens it as a
    :py:class:`~eth_defi.token.TokenDiskCache`, and points
    ``eth_defi.vault.base.DEFAULT_TOKEN_CACHE`` at it. Every vault constructed
    without an explicit ``token_cache`` then uses the disk cache, which is what
    enables the address-resolution cache as well as the metadata cache.

    :param worker_id:
        ``pytest-xdist`` worker id, used to keep working files separate.

    :return:
        The installed cache. When no seed is committed yet this is an empty
        cache, so a rebuild run can still fill it.
    """
    # Imported here so the module can be used without importing the vault stack.
    from eth_defi.vault import base as vault_base

    working_dir = Path(tempfile.gettempdir()) / "eth-defi-token-cache"
    working_dir.mkdir(parents=True, exist_ok=True)
    working_path = working_dir / f"tokens-{worker_id}.sqlite"

    if TOKEN_CACHE_SEED_PATH.exists():
        # Fresh copy per session so a previous run cannot leave stale entries.
        shutil.copy2(TOKEN_CACHE_SEED_PATH, working_path)

    cache = TokenDiskCache(working_path)
    vault_base.DEFAULT_TOKEN_CACHE = cache
    logger.info("Installed shipped token cache for worker %s with %d entries", worker_id, len(cache))
    return cache


def merge_into_token_cache_seed(cache: TokenDiskCache, seed_path: Path = TOKEN_CACHE_SEED_PATH) -> int:
    """Merge tokens resolved during the session back into the shipped seed.

    Used by the rebuild flow so the committed seed is regenerated from a real
    test run instead of being hand-maintained. Existing seed entries are
    preserved; only new keys are added.

    :param cache:
        Session cache to read (as returned by :func:`install_token_cache`).

    :param seed_path:
        Committed SQLite cache to update. Created if missing.

    :return:
        Number of new entries written to the seed file.
    """
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed = TokenDiskCache(seed_path)
    try:
        written = 0
        # NOTE: PersistentKeyValueStore.items() yields the *encoded* (JSON string)
        # values straight from SQLite, while __getitem__ decodes them. Read through
        # the item accessor so we hand decoded dicts to the destination's
        # encode_value(), which mutates them.
        for key in list(cache.keys()):
            if key in seed:
                continue
            seed[key] = cache[key]
            written += 1
        seed.commit()
    finally:
        seed.close()

    logger.info("Merged %d new token cache entries into %s", written, seed_path)
    return written
