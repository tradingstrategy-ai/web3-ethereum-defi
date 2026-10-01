"""Generate the monthly best-performing stablecoin vaults blog post.

Downloads the latest vault metrics and prices, renders the tables and charts,
and creates an unpublished draft post in Ghost for the editor to complete. The
draft is never published. A local bundle with the same content is written too,
for review and debugging. See ``eth_defi/vault_report/README-best-vaults-news.md``.

Creating the draft needs a Ghost Admin API key: ``GHOST_CONTENT_API_KEY`` is
read-only. Without a usable ``GHOST_ADMIN_API_KEY`` the script stops before
downloading anything, unless ``GHOST_DRAFT=false`` asks for the local bundle only.

Example:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/generate-monthly-vault-report.py

Environment variables:

- ``VAULT_PRO_API_KEY``: Pro vault data API key, to download the vault price Parquet
- ``TOP_VAULTS_JSON``: use a local top vaults JSON instead of downloading the public one
- ``VAULT_PRICES_PARQUET``: use a local cleaned vault price Parquet instead of downloading it
- ``CACHE_DIR``: download cache, default ``~/.cache/tradingstrategy/vault-report``
- ``OUTPUT_DIR``: report bundle directory, default ``{CACHE_DIR}/reports/{post slug}``
- ``GHOST_CONTENT_API_URL``, ``GHOST_CONTENT_API_KEY``: read the previous report post and the latest podcast episodes (optional)
- ``GHOST_ADMIN_API_URL``: defaults to ``GHOST_CONTENT_API_URL``
- ``GHOST_ADMIN_API_KEY``: ``{id}:{secret}`` Admin API key of a custom integration, or a staff
  access token; uploads the charts and creates the draft post
- ``GHOST_DRAFT``: set ``false`` to only write the local bundle, without a Ghost draft
- ``GHOST_OVERWRITE_DRAFT``: ``true`` to replace an existing draft with the same slug if nobody has
  edited it since this pipeline wrote it, compared with the record in ``{CACHE_DIR}/ghost-drafts/``;
  ``force`` to replace it without the comparison
- ``MIN_TVL``: minimum TVL for the best-performing vault tables, default 100,000 USD
- ``TOP_N``: vaults per best-performing table, default 20
- ``RENDER_CHARTS``: set ``false`` to skip chart rendering
- ``BROWSER_PATH``: Chrome binary for Kaleido chart rendering, when Chrome was not installed
  with ``plotly_get_chrome``, see ``eth_defi/vault_report/README-vault-report.md``
- ``CHART_THEME``: ``dark`` (default, the website look) or ``light``
- ``CHECK_SPARKLINES``: set ``false`` to leave sparklines out of the tables
- ``VAULT_CHECK_AGENT``: ``claude`` or ``codex`` to run the investability check
  of the top lists, see ``eth_defi/vault_report/README-vault-report.md``;
  ``reuse`` to only reuse earlier decisions; unset or ``none`` to skip the check
- ``VAULT_CHECK_MODEL``: model override for the check agent CLI, default
  ``claude-sonnet-5-5`` for Claude to limit the token spend
- ``VAULT_CHECK_EFFORT``: Claude CLI thinking effort override, default ``medium``
- ``VAULT_CHECK_DECISIONS``: comma-separated directories with earlier check
  decisions to reuse, e.g. a previous run's report bundle
- ``VAULT_CHECK_OVERRIDES``: JSON file of hand-written decisions that override the agent
- ``VAULT_CHECK_TIMEOUT``: agent timeout per round in minutes, default 60
- ``EXCLUDED_VAULTS_DIR``: where the dated Markdown record of the vaults the check left out is
  written, default ``eth_defi/vault_report/excluded-vaults`` in the repository; a copy goes to the bundle
- ``MAX_WORKERS``: parallel threads for the check's onchain probes, default 8
- ``LOG_LEVEL``: default ``info``
"""

import datetime
import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.utils import setup_console_logging
from eth_defi.vault_report.data import fetch_vault_report_data
from eth_defi.vault_report.ghost import ADMIN_API_KEY_HELP, DraftRecord, GhostAdminClient, GhostAPIError, GhostContentClient
from eth_defi.vault_report.podcasts import fetch_latest_podcast_episodes
from eth_defi.vault_report.post import REPORT_SLUG_PREFIX, make_report_slug, read_changelog_entries
from eth_defi.vault_report.report import generate_monthly_vault_report, publish_report_draft
from eth_defi.vault_report.sections import ReportCriteria
from eth_defi.vault_report.theme import get_theme
from eth_defi.vault_report.vault_checks import VaultCheckSettings, excluded_rows

logger = logging.getLogger(__name__)

#: Repository changelog, used to suggest report content updates
CHANGELOG_PATH = Path(__file__).resolve().parents[2] / "CHANGELOG.md"

#: Where the dated Markdown records of the vaults the investability check left out are written, in the repository
EXCLUDED_VAULTS_DIR = Path(__file__).resolve().parents[2] / "eth_defi" / "vault_report" / "excluded-vaults"

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


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a ``true``/``false`` environment variable.

    :param name:
        Environment variable name.

    :param default:
        Value when the variable is not set.

    :return:
        Flag value: ``1``, ``true`` and ``yes`` are true.
    """
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes"}


def create_admin_client() -> GhostAdminClient | None:
    """Create the Ghost Admin API client for the draft post, or stop with instructions.

    :return:
        The client, or ``None`` when ``GHOST_DRAFT=false`` asks for the local bundle only.

    :raise SystemExit:
        The draft is wanted but no usable Admin API key or URL is set.
    """
    if not _env_flag("GHOST_DRAFT", default=True):
        logger.info("GHOST_DRAFT=false: writing the local bundle only, no Ghost draft")
        return None
    bundle_only = "Set GHOST_DRAFT=false to only write the local bundle."
    admin_api_key = os.environ.get("GHOST_ADMIN_API_KEY")
    admin_api_url = os.environ.get("GHOST_ADMIN_API_URL") or os.environ.get("GHOST_CONTENT_API_URL")
    if not admin_api_url:
        raise SystemExit(f"GHOST_ADMIN_API_URL or GHOST_CONTENT_API_URL must be set to create the Ghost draft. {bundle_only}")
    if not admin_api_key:
        raise SystemExit(f"GHOST_ADMIN_API_KEY is not set. {ADMIN_API_KEY_HELP} {bundle_only}")
    try:
        return GhostAdminClient(admin_api_url, admin_api_key)
    except GhostAPIError as e:
        raise SystemExit(f"{e} {bundle_only}") from None


def main() -> None:
    """Generate the report bundle and the Ghost draft."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    # Fail before any download when the Ghost draft cannot be created
    admin_client = create_admin_client()
    # "true" replaces an existing draft only if unedited since the pipeline wrote it, "force" in any case
    force_overwrite = os.environ.get("GHOST_OVERWRITE_DRAFT", "").strip().lower() == "force"
    overwrite_draft = force_overwrite or _env_flag("GHOST_OVERWRITE_DRAFT")

    cache_dir = _env_path("CACHE_DIR") or Path("~/.cache/tradingstrategy/vault-report").expanduser()
    defaults = ReportCriteria()
    criteria = ReportCriteria(
        min_tvl=float(os.environ.get("MIN_TVL", defaults.min_tvl)),
        top_n=int(os.environ.get("TOP_N", defaults.top_n)),
    )

    data = fetch_vault_report_data(
        cache_dir=cache_dir / "downloads",
        top_vaults_json_path=_env_path("TOP_VAULTS_JSON"),
        prices_path=_env_path("VAULT_PRICES_PARQUET"),
        api_key=os.environ.get("VAULT_PRO_API_KEY"),
    )

    if admin_client:
        # Check the Admin API login and that the slug is free or a replaceable draft,
        # before the investability check and chart rendering take minutes
        slug = make_report_slug(data.data_end_at)
        draft_record_path = cache_dir / "ghost-drafts" / f"{slug}.json"
        try:
            admin_client.fetch_writable_draft(slug, overwrite_draft=overwrite_draft, last_write=DraftRecord.load(draft_record_path), force=force_overwrite)
        except GhostAPIError as e:
            raise SystemExit(f"Cannot create the Ghost draft {slug}: {e}") from None
        logger.info("Ghost Admin API ready, the draft %s will be created at %s", slug, admin_client.api_url)

    content_api_url = os.environ.get("GHOST_CONTENT_API_URL")
    content_api_key = os.environ.get("GHOST_CONTENT_API_KEY")
    previous = None
    podcasts = []
    if content_api_url and content_api_key:
        content_client = GhostContentClient(content_api_url, content_api_key)
        previous = content_client.fetch_latest_post_by_slug_prefix(REPORT_SLUG_PREFIX)
        logger.info("Previous report: %s", previous.slug if previous else "not found")
        podcasts = fetch_latest_podcast_episodes(content_client)
    else:
        logger.warning("GHOST_CONTENT_API_URL / GHOST_CONTENT_API_KEY not set, not reading the previous report or the podcast episodes")

    changelog_since = previous.published_at.date() if previous else (data.data_end_at - datetime.timedelta(days=31)).date()
    changelog_entries = read_changelog_entries(CHANGELOG_PATH, since=changelog_since)[:MAX_CHANGELOG_ENTRIES]

    output_dir = _env_path("OUTPUT_DIR") or cache_dir / "reports" / make_report_slug(data.data_end_at)
    report = generate_monthly_vault_report(
        data,
        output_dir=output_dir,
        criteria=criteria,
        previous=previous,
        changelog_entries=changelog_entries,
        render_charts=_env_flag("RENDER_CHARTS", default=True),
        theme=get_theme(os.environ.get("CHART_THEME", "dark")),
        cache_dir=cache_dir / "assets",
        check_sparklines=_env_flag("CHECK_SPARKLINES", default=True),
        vault_checks=VaultCheckSettings.from_env(),
        podcasts=podcasts,
        excluded_vaults_dir=_env_path("EXCLUDED_VAULTS_DIR") or EXCLUDED_VAULTS_DIR,
    )

    rows = [[key, len(section.vaults_df), section.vaults_df.iloc[0]["name"]] for key, section in report.sections.items()]
    print(tabulate(rows, headers=["Section", "Vaults", "Top vault"], tablefmt="fancy_grid"))
    if report.vault_checks:
        excluded = excluded_rows(report.vault_checks)
        print(tabulate([[row["name"], row["protocol"], row["suspicious_item"], row["blacklist"]] for row in excluded], headers=["Excluded vault", "Protocol", "Suspicious item", "Blacklisted"], tablefmt="fancy_grid"))

    if admin_client:
        post = publish_report_draft(report, admin_client, overwrite_draft=overwrite_draft, force_overwrite=force_overwrite, draft_record_path=draft_record_path)
        print(f"Unpublished Ghost draft ready, open it in the Ghost editor: {admin_client.get_editor_url(post)}")
    else:
        print("GHOST_DRAFT=false: no Ghost draft created")

    if report.excluded_vaults_path:
        print(f"Excluded vaults: {report.excluded_vaults_path}")
    print(f"Report: {report.title}")
    print(f"Local preview: {output_dir / 'preview.html'}")


if __name__ == "__main__":
    main()
