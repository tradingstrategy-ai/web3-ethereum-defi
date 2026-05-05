"""Synchronise the tracked Twitter/X handles to an X list.

This script performs only the X list membership synchronisation step.  It does
not scan RSS, LinkedIn, or Twitter posts.

Run in Docker using the ``post-scanner`` service environment:

.. code-block:: shell

    docker compose build vault-scanner
    docker compose run --rm -T --no-deps --entrypoint python post-scanner scripts/feed/sync-x-list.py

Required environment variables:

- ``TWITTER_BEARER_TOKEN``
- ``TWITTER_CONSUMER_KEY``
- ``TWITTER_SECRET_KEY``
- ``TWITTER_ACCESS_TOKEN``
- ``TWITTER_ACCESS_TOKEN_SECRET``

Optional environment variables:

- ``X_LIST_ID``: X list ID override. If not set, the script resolves the list
  by ``X_LIST_NAME``.
- ``X_LIST_NAME``: X list name to resolve, default ``Best builders in DeFi``.
- ``DB_PATH``: DuckDB path, default
  ``~/.tradingstrategy/vaults/vault-post-database.duckdb``
- ``MAPPINGS_DIR``: feeder YAML root, default ``eth_defi/data/feeds``
- ``LOG_LEVEL``: logging level, default ``info``
"""

import os
from pathlib import Path

from eth_defi.feed.constants import DEFAULT_X_LIST_NAME
from eth_defi.feed.database import DEFAULT_VAULT_POST_DATABASE, VaultPostDatabase
from eth_defi.feed.sources import FEEDS_DATA_DIR, load_post_sources
from eth_defi.feed.twitter_api import TwitterUserCache, resolve_x_list_id_by_name, sync_x_list_members
from eth_defi.utils import setup_console_logging

REQUIRED_ENV_VARS = (
    "TWITTER_BEARER_TOKEN",
    "TWITTER_CONSUMER_KEY",
    "TWITTER_SECRET_KEY",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_TOKEN_SECRET",
)


def _get_required_env(name: str) -> str:
    """Read a required environment variable.

    :param name:
        Environment variable name.

    :return:
        Environment variable value.

    :raise RuntimeError:
        If the environment variable is not set.
    """

    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _get_db_path() -> Path:
    """Read the configured feed database path.

    :return:
        DuckDB database path.
    """

    db_path = os.environ.get("DB_PATH")
    return Path(db_path).expanduser() if db_path else DEFAULT_VAULT_POST_DATABASE


def _get_mappings_dir() -> Path:
    """Read the configured feeder YAML directory.

    :return:
        Feeder YAML root directory.
    """

    mappings_dir = os.environ.get("MAPPINGS_DIR")
    return Path(mappings_dir).expanduser() if mappings_dir else FEEDS_DATA_DIR


def _get_x_list_id() -> str:
    """Resolve the target X list ID.

    ``X_LIST_ID`` is an explicit operator override.  If it is not set,
    resolve the default list name against the lists owned by the authenticated
    OAuth user.

    :return:
        Numeric X list ID.
    """

    list_id = os.environ.get("X_LIST_ID")
    if list_id:
        return list_id

    list_name = os.environ.get("X_LIST_NAME", DEFAULT_X_LIST_NAME)
    return resolve_x_list_id_by_name(
        list_name,
        _get_required_env("TWITTER_CONSUMER_KEY"),
        _get_required_env("TWITTER_SECRET_KEY"),
        _get_required_env("TWITTER_ACCESS_TOKEN"),
        _get_required_env("TWITTER_ACCESS_TOKEN_SECRET"),
    )


def main() -> None:
    """Synchronise all configured Twitter/X handles to the configured X list."""

    setup_console_logging(
        default_log_level="info",
        log_file=Path("logs/sync-x-list.log"),
    )

    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    db_path = _get_db_path()
    mappings_dir = _get_mappings_dir()
    list_id = _get_x_list_id()

    sources, feeders_skipped, aliases = load_post_sources(mappings_dir)
    handles = sorted({source.source_key for source in sources if source.source_type == "twitter"})

    if not handles:
        raise RuntimeError(f"No Twitter/X handles found in {mappings_dir}")

    with VaultPostDatabase(db_path) as db:
        added = sync_x_list_members(
            list_id,
            handles,
            _get_required_env("TWITTER_CONSUMER_KEY"),
            _get_required_env("TWITTER_SECRET_KEY"),
            _get_required_env("TWITTER_ACCESS_TOKEN"),
            _get_required_env("TWITTER_ACCESS_TOKEN_SECRET"),
            TwitterUserCache(),
            _get_required_env("TWITTER_BEARER_TOKEN"),
            db,
        )
        db.save()

    print(f"Synced {len(handles)} Twitter/X handles to list {list_id}; added {added} new members. Skipped {feeders_skipped} disabled feeders and {len(aliases)} aliases.")


if __name__ == "__main__":
    main()
