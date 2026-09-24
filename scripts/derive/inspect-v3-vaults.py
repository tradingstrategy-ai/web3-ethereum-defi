"""Inspect public Derive v3 native vaults on mainnet and testnet.

Read-only operator command. It never opens the production DuckDB, metadata
pickle or price Parquet. Select ``DERIVE_V3_NETWORK=both|mainnet|testnet`` and
optionally ``VAULT_IDS=123,456`` or ``HISTORY_SAMPLE_LIMIT=5``.

Example::

    DERIVE_V3_NETWORK=both poetry run python scripts/derive/inspect-v3-vaults.py
"""

import logging
import os
from itertools import islice

from requests import RequestException
from tabulate import tabulate

from eth_defi.derive.v3_vaults import DeriveV3VaultClient
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main() -> None:  # noqa: PLR0914 - one operator summary covers both deployments
    """Display read-only source summaries for the selected deployments.

    The public `Derive v3 API <https://docs.derive.xyz/api-reference/>`__ is
    queried directly. A missing history series or empty mainnet listing is
    reported explicitly instead of fabricating a price.

    :return: ``None``.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    selection = os.environ.get("DERIVE_V3_NETWORK", "both").lower()
    if selection not in {"both", "mainnet", "testnet"}:
        message = "DERIVE_V3_NETWORK must be both, mainnet or testnet"
        raise ValueError(message)
    networks = ("mainnet", "testnet") if selection == "both" else (selection,)
    ids_value = os.environ.get("VAULT_IDS", "").strip()
    vault_ids = {int(value.strip()) for value in ids_value.split(",") if value.strip()} if ids_value else None
    sample_limit = int(os.environ.get("HISTORY_SAMPLE_LIMIT", "5"))
    if not 1 <= sample_limit <= 10_000:  # noqa: PLR2004 - API page limit
        message = "HISTORY_SAMPLE_LIMIT must be between 1 and 10000"
        raise ValueError(message)

    errors: list[str] = []
    for network in networks:
        client = DeriveV3VaultClient(network=network)
        try:
            vaults = list(client.fetch_vaults())
            selected = [vault for vault in vaults if vault_ids is None or vault.subaccount_id in vault_ids]
            assets = client.fetch_spot_assets() if selected else {}
            rows = []
            for vault in selected:
                symbol, decimals = assets.get(vault.deposit_spot_asset.lower(), ("unknown", None))
                try:
                    points = list(islice(client.fetch_vault_performance(vault.subaccount_id, limit=sample_limit), sample_limit))
                    dates = [point.timestamp for point in points]
                    sample = ", ".join(str(point.share_price) for point in points[:sample_limit])
                    history = f"latest {len(points)} points ({min(dates):%Y-%m-%d} to {max(dates):%Y-%m-%d}; prices: {sample})" if dates else "no history"
                except (RequestException, ValueError, KeyError, TypeError) as exc:
                    history = f"error: {exc}"
                    errors.append(f"{network} vault {vault.subaccount_id} history: {exc}")
                rows.append((vault.subaccount_id, vault.name, vault.curator, f"{symbol} ({decimals})", vault.nav_usd, vault.share_price_usd, vault.management_fee_bps, vault.performance_fee_bps, vault.whitelist_only, vault.closed, vault.cooldown_sec, history))
            full_vault_fields = "no vault"
            if selected:
                try:
                    full_vault_fields = ", ".join(sorted(client.fetch_vault(selected[0].subaccount_id)))
                except (RequestException, ValueError, KeyError, TypeError) as exc:
                    full_vault_fields = f"error: {exc}"
                    errors.append(f"{network} vault {selected[0].subaccount_id} full record: {exc}")
            print(f"Derive v3 {network}: {len(vaults)} listed, {len(selected)} selected")
            print(tabulate(rows, headers=("ID", "Name", "Curator", "Deposit asset", "NAV USD", "Live share USD", "Mgmt bps", "Perf bps", "Whitelist", "Closed", "Cooldown s", "History"), tablefmt="github"))
            print(f"Full-vault response fields: {full_vault_fields}")
        except (RequestException, ValueError, KeyError, TypeError) as exc:
            logger.error("Derive v3 %s inspection failed: %s", network, exc)
            print(f"Derive v3 {network}: API error: {exc}")
            errors.append(f"{network} listing: {exc}")
        finally:
            client.close()
    if errors:
        message = "Derive v3 inspection had source errors: " + "; ".join(errors)
        raise RuntimeError(message)


if __name__ == "__main__":
    main()
