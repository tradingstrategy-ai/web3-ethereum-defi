"""Run targeted tokenised-fund price backfills through the recurring scanner.

The legacy protocol backfills may refresh metadata, lead discovery and prices
with protocol-specific implementations. This module is intentionally limited
to raw price history already owned by :mod:`eth_defi.tokenised_fund.scan`, so a
manual repair exercises the same address-scoped write path as the scheduler.
"""

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate

from eth_defi.provider.env import read_json_rpc_url
from eth_defi.tokenised_fund.scan import (
    TokenisedFundPriceScanContext,
    TokenisedFundPriceScanResult,
    TokenisedFundPriceScanSpec,
    load_tokenised_fund_target_specs,
    resolve_target_start_blocks,
    run_tokenised_fund_price_scan,
    select_tokenised_fund_price_scanners,
)
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)

EVM_ADDRESS_LENGTH = 42


@dataclass(slots=True, frozen=True)
class TokenisedFundPriceBackfillConfig:
    """Operator configuration for one recurring-path price backfill."""

    #: Shared reviewed vault metadata database.
    vault_db_path: Path

    #: Shared raw vault-price Parquet file.
    raw_price_path: Path

    #: Selected recurring price-feed definitions.
    scanners: tuple[TokenisedFundPriceScanSpec, ...]

    #: Optional lowercase token-address restriction.
    vault_addresses: frozenset[str] | None

    #: Historical reader worker count.
    max_workers: int

    #: Shared Hypersync stream concurrency.
    hypersync_concurrency: int

    #: Whether to print the target plan without writing prices.
    dry_run: bool


def parse_bool_env(name: str, default: bool) -> bool:  # noqa: FBT001 - environment defaults are configuration values.
    """Read a strict boolean environment variable.

    Empty values use ``default``. Explicit values must be ``true`` or
    ``false`` so a typo cannot accidentally start a production rewrite.

    :param name:
        Environment variable name.
    :param default:
        Value used when the variable is absent or empty.
    :return:
        Parsed boolean value.
    :raise ValueError:
        If a configured value is not a boolean.
    """

    raw_value = os.environ.get(name, "").strip().lower()
    if not raw_value:
        return default
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    raise ValueError(f"{name} must be true or false, got: {raw_value}")


def parse_path_env(name: str, default: Path) -> Path:
    """Read an optional path environment variable.

    :param name:
        Environment variable name.
    :param default:
        Path used when the variable is absent or empty.
    :return:
        Expanded configured or default path.
    """

    return Path(os.environ.get(name, str(default))).expanduser()


def parse_vault_addresses(value: str | None) -> frozenset[str] | None:
    """Parse an optional comma-separated lowercase token-address filter.

    The recurring scanner stores and compares lowercase addresses, so this
    parser normalises operator input at the boundary.

    :param value:
        Raw ``TOKENISED_FUND_PRODUCTS`` environment value.
    :return:
        Lowercase address set, or ``None`` when no filter was supplied.
    :raise ValueError:
        If an entry is not a 20-byte hexadecimal EVM address.
    """

    values = frozenset(item.strip().lower() for item in (value or "").split(",") if item.strip())
    if not values:
        return None
    invalid = sorted(address for address in values if not address.startswith("0x") or len(address) != EVM_ADDRESS_LENGTH or any(character not in "0123456789abcdef" for character in address[2:]))
    if invalid:
        raise ValueError(f"TOKENISED_FUND_PRODUCTS contains invalid EVM addresses: {', '.join(invalid)}")
    return values


def resolve_configured_chain_ids(target_specs: Iterable[VaultSpec], vault_addresses: frozenset[str] | None) -> frozenset[int]:
    """Return target chains that have a configured JSON-RPC URL.

    Missing RPC URLs remain visible in the scan result diagnostics. Filtering
    them here prevents a targeted manual run from treating unrelated chains as
    operator-enabled.

    :param target_specs:
        Registered exact target specifications.
    :param vault_addresses:
        Optional lowercase address filter.
    :return:
        Chain ids with a configured RPC URL.
    """

    enabled_chain_ids: set[int] = set()
    for target in target_specs:
        if vault_addresses is not None and target.vault_address not in vault_addresses:
            continue
        try:
            read_json_rpc_url(target.chain_id)
        except ValueError:
            logger.warning("Skipping chain %d because its JSON-RPC URL is not configured", target.chain_id)
        else:
            enabled_chain_ids.add(target.chain_id)
    return frozenset(enabled_chain_ids)


def build_price_backfill_plan(config: TokenisedFundPriceBackfillConfig) -> list[dict[str, str | int]]:
    """Build the exact target and continuation plan without modifying state.

    The plan deliberately resolves starts through the same helper as the
    recurring scanner. It therefore exposes the exact address-scoped rewrite
    boundary that an execution would use.

    :param config:
        Manual backfill configuration.
    :return:
        One plan row per selected registered product.
    :raise RuntimeError:
        If the shared metadata database does not exist.
    """

    if not config.vault_db_path.exists():
        raise RuntimeError(f"Tokenised-fund metadata database does not exist: {config.vault_db_path}")
    vault_db = VaultDatabase.read(config.vault_db_path)
    plan: list[dict[str, str | int]] = []
    for scanner in config.scanners:
        targets = load_tokenised_fund_target_specs(config.vault_db_path, (scanner,))
        if config.vault_addresses is not None:
            targets = {target for target in targets if target.vault_address in config.vault_addresses}
        target_blocks = [(target, vault_db.rows[target]["_detection_data"].first_seen_at_block) for target in sorted(targets)]
        start_blocks = resolve_target_start_blocks(scanner, config.raw_price_path, target_blocks)
        plan.extend(
            {
                "protocol": scanner.dashboard_name,
                "target": target.as_string_id(),
                "start_block": start_blocks[target],
            }
            for target in sorted(targets)
        )
    return plan


def run_price_backfill(config: TokenisedFundPriceBackfillConfig) -> dict[str, TokenisedFundPriceScanResult]:
    """Run selected recurring price feeds as a manual backfill.

    Each selected feed retains its independent address-scoped Parquet rewrite
    semantics. The function makes no metadata or reader-state changes.

    :param config:
        Manual backfill configuration.
    :return:
        Results keyed by dashboard name.
    """

    target_specs = load_tokenised_fund_target_specs(config.vault_db_path, config.scanners)
    enabled_chain_ids = resolve_configured_chain_ids(target_specs, config.vault_addresses)
    return {
        scanner.dashboard_name: run_tokenised_fund_price_scan(
            scanner,
            TokenisedFundPriceScanContext(
                vault_db_path=config.vault_db_path,
                raw_price_path=config.raw_price_path,
                max_workers=config.max_workers,
                enabled_chain_ids=enabled_chain_ids,
                vault_addresses=config.vault_addresses,
                hypersync_concurrency=config.hypersync_concurrency,
            ),
        )
        for scanner in config.scanners
    }


def create_config_from_environment() -> TokenisedFundPriceBackfillConfig:
    """Build manual backfill configuration from environment variables.

    ``DRY_RUN`` defaults to true because an execution rewrites the shared raw
    Parquet file. Set it to false only after reviewing the printed plan.

    :return:
        Validated manual backfill configuration.
    """

    return TokenisedFundPriceBackfillConfig(
        vault_db_path=parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE),
        raw_price_path=parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE),
        scanners=select_tokenised_fund_price_scanners(os.environ.get("TOKENISED_FUND_PROTOCOLS")),
        vault_addresses=parse_vault_addresses(os.environ.get("TOKENISED_FUND_PRODUCTS")),
        max_workers=int(os.environ.get("TOKENISED_FUND_MAX_WORKERS", "8")),
        hypersync_concurrency=int(os.environ.get("HYPERSYNC_CONCURRENCY", "1")),
        dry_run=parse_bool_env("DRY_RUN", True),
    )


def main() -> None:
    """Print or execute a tokenised-fund price backfill from environment.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    config = create_config_from_environment()
    plan = build_price_backfill_plan(config)
    logger.info("Tokenised-fund recurring-path backfill plan; dry_run=%s\n%s", config.dry_run, tabulate(plan, headers="keys", tablefmt="github"))
    if config.dry_run:
        return
    results = run_price_backfill(config)
    logger.info(
        "Tokenised-fund recurring-path backfill completed\n%s",
        tabulate(
            [
                {
                    "protocol": name,
                    "vaults": result.vault_count,
                    "rows": result.price_rows,
                    "start_block": result.start_block,
                    "end_block": result.end_block,
                    "last_data": result.latest_data_timestamp,
                    "diagnostics": result.diagnostics or "",
                }
                for name, result in results.items()
            ],
            headers="keys",
            tablefmt="github",
        ),
    )
