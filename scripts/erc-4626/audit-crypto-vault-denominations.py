"""Audit symbol-whitelist coverage for the private crypto-vaults bundle.

The report only reads the scanner metadata and optional raw price Parquet. It
does not make network calls or modify scanner state.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd
from tabulate import tabulate

from eth_defi.utils import setup_console_logging
from eth_defi.vault.denomination import (
    DENOMINATION_SYMBOLS,
    DenominationFamily,
    classify_denomination,
    normalise_denomination_symbol,
)
from eth_defi.vault.vaultdb import DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)

#: Symbol fragments that identify multi-asset pool denominations, not wrappers.
_POOL_SYMBOL_MARKERS = ("/", "BPT", "VAMM", "SAMM", "CLAMM", "CVAMM")


def _is_crypto_candidate(symbol: object) -> bool:
    """Return whether an unsupported symbol looks ETH/BTC related.

    Pandas represents missing symbols as floating-point ``NaN`` values, so the
    audit boundary accepts arbitrary scalars and rejects non-strings.

    :param symbol:
        Raw denomination symbol or Pandas missing-value scalar.
    :return:
        ``True`` for a symbol that should be considered for whitelist review.
    """
    normalised = normalise_denomination_symbol(symbol) if isinstance(symbol, str) else None
    if not normalised or normalised[0].isdigit() or normalised.endswith("LP") or any(marker in normalised for marker in _POOL_SYMBOL_MARKERS):
        return False
    return "ETH" in normalised or "BTC" in normalised


def _build_vault_rows(vault_db: VaultDatabase, raw_ids: set[str], *, has_raw_prices: bool) -> tuple[list[dict[str, Any]], list[str]]:
    """Build audit rows and genuine consistency errors from metadata.

    Unsupported symbols that merely contain ``ETH`` or ``BTC`` are recorded as
    advisory review candidates. Substring matching cannot distinguish wrappers
    from LP, basket or unrelated protocol tokens and therefore is not a strict
    validation rule.

    :param vault_db:
        Read-only vault metadata database.
    :param raw_ids:
        Vault IDs present in the shared raw history.
    :param has_raw_prices:
        Whether raw-price coverage is available for validation.
    :return:
        Tabular vault rows and strict-validation errors.
    """
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for spec, vault in vault_db.rows.items():
        vault_id = spec.as_string_id()
        raw_symbol = vault.get("Denomination")
        symbol = normalise_denomination_symbol(raw_symbol)
        family = classify_denomination(raw_symbol)
        token = vault.get("_denomination_token") or {}
        current_nav = float(vault.get("NAV") or 0)
        rows.append(
            {
                "id": vault_id,
                "chain_id": spec.chain_id,
                "address": spec.vault_address,
                "denomination_symbol": symbol,
                "family": family.value,
                "denomination_token_address": token.get("address"),
                "denomination_token_decimals": token.get("decimals"),
                "current_nav": current_nav,
                "has_raw_price": vault_id in raw_ids if has_raw_prices else None,
            }
        )
        if has_raw_prices and current_nav > 0 and family is not DenominationFamily.unsupported and vault_id not in raw_ids:
            errors.append(f"Classified vault has no raw price coverage: {vault_id}")
    return rows, errors


def _summarise_vault_rows(frame: pd.DataFrame, whitelist: set[str]) -> dict[str, Any]:
    """Create JSON report sections from the vault-level audit rows.

    :param frame:
        Audit rows returned by :func:`_build_vault_rows`.
    :param whitelist:
        Reviewed ETH/BTC symbols.
    :return:
        JSON-serialisable report sections.
    """
    selected = frame[frame["family"] != DenominationFamily.unsupported.value]
    selected_by_family_chain = selected.groupby(["family", "chain_id"], dropna=False).agg(vault_count=("id", "count")).reset_index().to_dict(orient="records")
    symbols = frame.groupby(["family", "denomination_symbol"], dropna=False).agg(vault_count=("id", "count"), chains=("chain_id", lambda values: sorted(set(values)))).reset_index().to_dict(orient="records")
    candidate_vaults = frame[(frame["family"] == DenominationFamily.unsupported.value) & frame["denomination_symbol"].map(_is_crypto_candidate)]
    candidates = candidate_vaults.groupby("denomination_symbol", dropna=False).agg(vault_count=("id", "count"), chains=("chain_id", lambda values: sorted(set(values))), max_current_nav=("current_nav", "max"), raw_price_vault_count=("has_raw_price", "sum")).reset_index()
    candidates.sort_values(["max_current_nav", "raw_price_vault_count", "denomination_symbol"], ascending=[False, False, True], inplace=True)
    return {
        "selected_by_family_chain": selected_by_family_chain,
        "symbols": symbols,
        "selected_vaults": selected.to_dict(orient="records"),
        "symbol_review_candidates": candidates.to_dict(orient="records"),
        "whitelist_symbols_absent_from_vault_db": sorted(whitelist - set(frame["denomination_symbol"].dropna())),
        "whitelisted_symbol_observations": frame[frame["denomination_symbol"].isin(whitelist)].to_dict(orient="records"),
    }


def build_audit_report(vault_db: VaultDatabase, prices_df: pd.DataFrame | None) -> tuple[dict[str, Any], list[str]]:
    """Build the JSON report and strict-validation errors.

    :param vault_db:
        Read-only vault metadata database.
    :param prices_df:
        Optional shared raw price history.
    :return:
        Report document and errors that cause strict-mode failure.
    """
    raw_ids = set(prices_df["chain"].astype(str) + "-" + prices_df["address"].astype(str).str.lower()) if prices_df is not None else set()
    rows, errors = _build_vault_rows(vault_db, raw_ids, has_raw_prices=prices_df is not None)
    report = {
        "schema_version": 1,
        **_summarise_vault_rows(pd.DataFrame(rows), set(DENOMINATION_SYMBOLS)),
        "raw_price_rows": len(prices_df) if prices_df is not None else None,
        "errors": errors,
    }
    return report, errors


def main() -> None:
    """Run the read-only crypto denomination coverage audit.

    Configuration uses ``VAULT_DATABASE``, ``UNCLEANED_PRICE_DATABASE``, optional
    ``CRYPTO_VAULT_AUDIT_REPORT`` and ``CRYPTO_VAULT_AUDIT_STRICT``.

    :return:
        ``None`` after printing and optionally writing the audit report.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    vault_db_path = Path(os.environ.get("VAULT_DATABASE") or os.environ.get("VAULT_DB", DEFAULT_VAULT_DATABASE)).expanduser()
    raw_price_path = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)).expanduser()
    report_path = os.environ.get("CRYPTO_VAULT_AUDIT_REPORT")
    strict = os.environ.get("CRYPTO_VAULT_AUDIT_STRICT", "false").lower() == "true"
    logger.info("Reading vault metadata from %s", vault_db_path)
    vault_db = VaultDatabase.read(vault_db_path)
    if raw_price_path.exists():
        logger.info("Reading raw price identities from %s", raw_price_path)
        prices_df = pd.read_parquet(raw_price_path, columns=["chain", "address"])
    else:
        logger.warning("Raw price Parquet is absent; skipping history coverage checks: %s", raw_price_path)
        prices_df = None
    report, errors = build_audit_report(vault_db, prices_df)
    print(tabulate(report["selected_by_family_chain"], headers="keys", tablefmt="github"))
    print(tabulate(report["symbol_review_candidates"], headers="keys", tablefmt="github"))
    if report_path:
        Path(report_path).expanduser().write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if strict and errors:
        raise SystemExit("\n".join(errors))


if __name__ == "__main__":
    main()
