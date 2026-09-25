"""Generate the monthly best-performing stablecoin vaults blog post.

Downloads the latest vault metrics and prices, renders tables and charts into
a local bundle, and creates a Ghost draft post for the editor to complete.
See ``eth_defi/vault_report/README-vault-report.md``.

Example:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/generate-monthly-vault-report.py

Environment variables:

- ``VAULT_PRO_API_KEY``: Pro vault data API key, to download the vault price Parquet
- ``TOP_VAULTS_JSON``: use a local top vaults JSON instead of downloading the public one
- ``VAULT_PRICES_PARQUET``: use a local cleaned vault price Parquet instead of downloading it
- ``CACHE_DIR``: download cache, default ``~/.cache/tradingstrategy/vault-report``
- ``OUTPUT_DIR``: report bundle directory, default ``{CACHE_DIR}/reports/{post slug}``
- ``GHOST_CONTENT_API_URL``, ``GHOST_CONTENT_API_KEY``: read the previous report post (optional)
- ``GHOST_ADMIN_API_URL``: defaults to ``GHOST_CONTENT_API_URL``
- ``GHOST_ADMIN_API_KEY``: ``{id}:{secret}`` Admin API key; when set, upload charts and create the draft post
- ``GHOST_OVERWRITE_DRAFT``: set ``true`` to replace an existing draft with the same slug
- ``MIN_TVL``: minimum TVL for the main listing, default 200,000 USD
- ``TOP_N``: vaults per listing, default 50
- ``RENDER_CHARTS``: set ``false`` to skip chart rendering
- ``CHART_THEME``: ``dark`` (default, the website look) or ``light``
- ``CHECK_SPARKLINES``: set ``false`` to leave sparklines out of the tables
- ``LOG_LEVEL``: default ``info``
"""

import datetime
import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.utils import setup_console_logging
from eth_defi.vault_report.data import fetch_vault_report_data
from eth_defi.vault_report.ghost import GhostAdminClient, GhostContentClient
from eth_defi.vault_report.post import REPORT_SLUG_PREFIX, make_report_slug, read_changelog_entries
from eth_defi.vault_report.report import generate_monthly_vault_report, publish_report_draft
from eth_defi.vault_report.sections import ReportCriteria
from eth_defi.vault_report.theme import get_theme

logger = logging.getLogger(__name__)

#: Repository changelog, used to suggest report content updates
CHANGELOG_PATH = Path(__file__).resolve().parents[2] / "CHANGELOG.md"

#: Maximum changelog entries offered to the editor
MAX_CHANGELOG_ENTRIES = 15


def _env_path(name: str) -> Path | None:
    """Read an optional path from an environment variable.

    :param name:
        Environment variable name.

    :return:
        Expanded path, or ``None`` if the variable is not set.
    """
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def _env_flag(name: str) -> bool:
    """Read a ``true``/``false`` environment variable, defaulting to false.

    :param name:
        Environment variable name.

    :return:
        Flag value.
    """
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def main() -> None:
    """Generate the report bundle and the Ghost draft."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    cache_dir = _env_path("CACHE_DIR") or Path("~/.cache/tradingstrategy/vault-report").expanduser()
    criteria = ReportCriteria(
        min_tvl=float(os.environ.get("MIN_TVL", "200000")),
        top_n=int(os.environ.get("TOP_N", "50")),
    )

    data = fetch_vault_report_data(
        cache_dir=cache_dir / "downloads",
        top_vaults_json_path=_env_path("TOP_VAULTS_JSON"),
        prices_path=_env_path("VAULT_PRICES_PARQUET"),
        api_key=os.environ.get("VAULT_PRO_API_KEY"),
    )

    content_api_url = os.environ.get("GHOST_CONTENT_API_URL")
    content_api_key = os.environ.get("GHOST_CONTENT_API_KEY")
    previous = None
    if content_api_url and content_api_key:
        previous = GhostContentClient(content_api_url, content_api_key).fetch_latest_post_by_slug_prefix(REPORT_SLUG_PREFIX)
        logger.info("Previous report: %s", previous.slug if previous else "not found")
    else:
        logger.warning("GHOST_CONTENT_API_URL / GHOST_CONTENT_API_KEY not set, not reading the previous report")

    changelog_since = previous.published_at.date() if previous else (data.data_end_at - datetime.timedelta(days=31)).date()
    changelog_entries = read_changelog_entries(CHANGELOG_PATH, since=changelog_since)[:MAX_CHANGELOG_ENTRIES]

    output_dir = _env_path("OUTPUT_DIR") or cache_dir / "reports" / make_report_slug(data.data_end_at)
    report = generate_monthly_vault_report(
        data,
        output_dir=output_dir,
        criteria=criteria,
        previous=previous,
        changelog_entries=changelog_entries,
        render_charts=os.environ.get("RENDER_CHARTS", "true").strip().lower() != "false",
        theme=get_theme(os.environ.get("CHART_THEME", "dark")),
        cache_dir=cache_dir / "assets",
        check_sparklines=os.environ.get("CHECK_SPARKLINES", "true").strip().lower() != "false",
    )

    rows = [[key, len(section.vaults_df), section.vaults_df.iloc[0]["name"]] for key, section in report.sections.items()]
    print(tabulate(rows, headers=["Section", "Vaults", "Top vault"], tablefmt="fancy_grid"))

    admin_api_key = os.environ.get("GHOST_ADMIN_API_KEY")
    admin_api_url = os.environ.get("GHOST_ADMIN_API_URL") or content_api_url
    if admin_api_key and admin_api_url:
        client = GhostAdminClient(admin_api_url, admin_api_key)
        post = publish_report_draft(report, client, overwrite_draft=_env_flag("GHOST_OVERWRITE_DRAFT"))
        print(f"Ghost draft ready: {client.get_editor_url(post)}")
    else:
        print("GHOST_ADMIN_API_KEY not set, no Ghost draft created")

    print(f"Report: {report.title}")
    print(f"Local preview: {output_dir / 'preview.html'}")


if __name__ == "__main__":
    main()
