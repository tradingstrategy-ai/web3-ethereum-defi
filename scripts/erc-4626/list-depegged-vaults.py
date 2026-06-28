"""Report vaults blacklisted because their denomination stablecoin has depegged.

Cross-references the vault metadata database against the stablecoin depeg
markers (``depegged_at`` in ``eth_defi/data/stablecoins/*.yaml``) and prints:

- A per-stablecoin summary of how many vaults are blacklisted and their TVL.
- The total nominal TVL we blacklist due to denomination-token depegs, plus an
  estimate of the real USD value at the current (depegged) exchange rate.
- An optional per-vault detail table.

The depeg detection reuses the exact production lookup
(:class:`eth_defi.feed.stablecoin_rate.StablecoinRateFeeder`), so the numbers
here match what :func:`eth_defi.research.vault_metrics.calculate_vault_record`
blacklists in the export pipeline.

Usage:

.. code-block:: shell

    # Use the locally cached vault database (~/.tradingstrategy/vaults/vault-metadata-db.pickle)
    poetry run python scripts/erc-4626/list-depegged-vaults.py

    # Point at an explicitly downloaded database and hide the per-vault detail
    VAULT_DB_PATH=/tmp/vault-metadata-db.pickle SHOW_DETAIL=false \
        poetry run python scripts/erc-4626/list-depegged-vaults.py

Environment variables:

- ``VAULT_DB_PATH``: Optional. Path to the vault metadata database pickle.
  Default: ``~/.tradingstrategy/vaults/vault-metadata-db.pickle``.
- ``STABLECOINS_DIR``: Optional. Path to the stablecoin metadata YAML directory.
  Default: the packaged ``eth_defi/data/stablecoins`` directory.
- ``MIN_TVL``: Optional. Ignore vaults whose NAV is below this nominal value.
  Default: 0 (include everything).
- ``SHOW_DETAIL``: Optional. Print the per-vault detail table. Default: true.
- ``LOG_LEVEL``: Optional. Default: warning.
"""

import logging
import os
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from tabulate import tabulate

from eth_defi.chain import get_chain_name
from eth_defi.feed.stablecoin_rate import STABLECOINS_DATA_DIR, StablecoinRateFeeder
from eth_defi.token import normalise_token_symbol
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def _to_float(value: Decimal | float | int | None) -> float:
    """Coerce an optional NAV/Decimal value to a plain float."""
    if value is None:
        return 0.0
    return float(value)


def _format_usd(value: float | None) -> str:
    """Format a USD amount for tabular output."""
    if value is None:
        return "-"
    return f"${value:,.0f}"


def _format_rate(value: float | None) -> str:
    """Format a USD exchange rate for tabular output."""
    if value is None:
        return "-"
    return f"{value:.4f}"


def main() -> None:
    """Build and print the depegged-denomination vault blacklist report."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))

    db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    stablecoins_dir = Path(os.environ.get("STABLECOINS_DIR", str(STABLECOINS_DATA_DIR))).expanduser()
    min_tvl = float(os.environ.get("MIN_TVL", "0"))
    show_detail = os.environ.get("SHOW_DETAIL", "true").lower() in ("1", "true", "yes")

    assert db_path.exists(), f"Vault database not found: {db_path}. Download vault-metadata-db.pickle or set VAULT_DB_PATH."

    logger.info("Reading vault database %s", db_path)
    vault_db = VaultDatabase.read(db_path)

    # The feeder is the single source of truth for depeg + rate lookups, matching production.
    feeder = StablecoinRateFeeder(data_dir=stablecoins_dir)

    # Per-stablecoin aggregation keyed by the normalised denomination symbol.
    stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "nominal": 0.0, "real": 0.0, "rate": None})
    detail_rows: list[list] = []

    for spec, row in vault_db.rows.items():
        denomination_token = row.get("_denomination_token") or {}
        chain_id = spec.chain_id
        token_address = denomination_token.get("address")
        token_symbol = denomination_token.get("symbol") or row.get("Denomination")

        if not feeder.is_depegged_stablecoin_token(chain_id=chain_id, address=token_address, symbol=token_symbol):
            continue

        nav = _to_float(row.get("NAV"))
        if nav < min_tvl:
            continue

        symbol = normalise_token_symbol(token_symbol) or (token_symbol or "?").upper()
        usd_rate = feeder.get_denomination_token_rate_section(chain_id=chain_id, address=token_address, symbol=token_symbol).usd_rate
        real_usd = nav * usd_rate if usd_rate is not None else None

        entry = stats[symbol]
        entry["count"] += 1
        entry["nominal"] += nav
        entry["real"] += real_usd or 0.0
        entry["rate"] = usd_rate

        detail_rows.append(
            [
                symbol,
                get_chain_name(chain_id),
                row.get("Name") or "?",
                row.get("Protocol") or "?",
                _format_usd(nav),
                _format_usd(real_usd),
                spec.vault_address,
            ]
        )

    # Summary table sorted by nominal TVL impact.
    summary_rows = []
    total_count = 0
    total_nominal = 0.0
    total_real = 0.0
    for symbol, entry in sorted(stats.items(), key=lambda kv: kv[1]["nominal"], reverse=True):
        total_count += entry["count"]
        total_nominal += entry["nominal"]
        total_real += entry["real"]
        summary_rows.append([symbol, _format_rate(entry["rate"]), entry["count"], _format_usd(entry["nominal"]), _format_usd(entry["real"])])

    summary_rows.append(["TOTAL", "", total_count, _format_usd(total_nominal), _format_usd(total_real)])

    print()
    print(f"Vault database: {db_path}")
    print(f"Vaults scanned: {len(vault_db.rows):,}")
    print(f"Depegged stablecoin symbols: {', '.join(sorted(feeder.depegged_symbols)) or '(none)'}")
    print(f"Depegged stablecoin contracts: {len(feeder.depegged_contracts)}")
    print()
    print("Blacklisted vaults by depegged denomination stablecoin")
    print(
        tabulate(
            summary_rows,
            headers=["Stablecoin", "USD rate", "Vaults", "Nominal TVL", "Est. real USD"],
            tablefmt="fancy_grid",
            colalign=("left", "right", "right", "right", "right"),
        )
    )

    if show_detail and detail_rows:
        detail_rows.sort(key=lambda r: (r[0], r[1]))
        print()
        print("Per-vault detail")
        print(
            tabulate(
                detail_rows,
                headers=["Stablecoin", "Chain", "Vault", "Protocol", "Nominal TVL", "Est. real USD", "Address"],
                tablefmt="fancy_grid",
                colalign=("left", "left", "left", "left", "right", "right", "left"),
            )
        )


if __name__ == "__main__":
    main()
