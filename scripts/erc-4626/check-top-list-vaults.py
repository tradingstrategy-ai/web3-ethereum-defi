"""Run the vault report investability check without rendering the report.

Collects the report's top lists with their buffers, probes the in-scope
vaults, runs the check agent (Claude CLI or Codex CLI) with the
``check-top-list-vaults`` skill, and prints the decisions and the resulting
``flag.py`` blacklist diff. The decision files go to ``OUTPUT_DIR``, where
``generate-monthly-vault-report.py`` reuses them when run with the same data.

See ``eth_defi/vault_report/README-vault-report.md``.

Example:

.. code-block:: shell

    source .local-test.env && VAULT_CHECK_AGENT=claude \\
    poetry run python scripts/erc-4626/check-top-list-vaults.py

Environment variables:

- ``VAULT_CHECK_AGENT``: ``claude``, ``codex`` or ``reuse``, default ``claude``
- ``VAULT_CHECK_MODEL``, ``VAULT_CHECK_EFFORT``, ``VAULT_CHECK_DECISIONS``, ``VAULT_CHECK_OVERRIDES``,
  ``VAULT_CHECK_TIMEOUT``, ``MAX_WORKERS``: see ``generate-monthly-vault-report.py``
- ``TOP_VAULTS_JSON``, ``VAULT_PRICES_PARQUET``, ``VAULT_PRO_API_KEY``, ``CACHE_DIR``: report inputs
- ``OUTPUT_DIR``: where the check files are written, default the report bundle directory
- ``JSON_RPC_{CHAIN}``: RPC URLs for the probed chains
- ``LOG_LEVEL``: default ``info``
"""

import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.utils import setup_console_logging
from eth_defi.vault_report.data import fetch_vault_report_data
from eth_defi.vault_report.post import make_report_slug
from eth_defi.vault_report.report import run_report_checks
from eth_defi.vault_report.sections import ReportCriteria, filter_eligible_vaults, select_comparable_vaults
from eth_defi.vault_report.vault_checks import VaultCheckSettings, show_flag_diff

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the check and print its decisions."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    cache_dir = Path(os.environ.get("CACHE_DIR", "~/.cache/tradingstrategy/vault-report")).expanduser()
    data = fetch_vault_report_data(
        cache_dir=cache_dir / "downloads",
        top_vaults_json_path=Path(os.environ["TOP_VAULTS_JSON"]) if os.environ.get("TOP_VAULTS_JSON") else None,
        prices_path=Path(os.environ["VAULT_PRICES_PARQUET"]) if os.environ.get("VAULT_PRICES_PARQUET") else None,
        api_key=os.environ.get("VAULT_PRO_API_KEY"),
    )
    output_dir = Path(os.environ["OUTPUT_DIR"]) if os.environ.get("OUTPUT_DIR") else cache_dir / "reports" / make_report_slug(data.data_end_at)
    output_dir.mkdir(parents=True, exist_ok=True)

    criteria = ReportCriteria()
    settings = VaultCheckSettings.from_env(default_agent="claude")
    assert settings is not None, "VAULT_CHECK_AGENT=none disables the check; use claude, codex or reuse"
    comparable_df = select_comparable_vaults(filter_eligible_vaults(data.vaults_df, data.data_end_at, criteria))
    result = run_report_checks(comparable_df, data, output_dir, settings, criteria)

    rows = []
    for vault_id, decision in result.decisions.items():
        if decision.decision == "not_in_scope":
            continue
        candidate = result.candidates.get(vault_id)
        rows.append([candidate.name if candidate else vault_id, candidate.protocol if candidate else "", decision.decision, decision.suspicious_item or "", decision.confidence or "", "yes" if decision.blacklist else ""])
    print(tabulate(sorted(rows, key=lambda row: row[2]), headers=["Vault", "Protocol", "Decision", "Suspicious item", "Confidence", "Blacklisted"], tablefmt="fancy_grid"))
    diff = show_flag_diff(settings.repository_root)
    print(f"\nflag.py changes to review:\n{diff}" if diff else "\nNo flag.py changes")
    print(f"Check files: {output_dir}")


if __name__ == "__main__":
    main()
