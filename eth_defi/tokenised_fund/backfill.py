"""Coordinate tokenised-fund metadata and historical backfills.

Each protocol owns its implementation in
``eth_defi.tokenised_fund.<protocol>.backfill`` beside its vault adapter. This
module provides the stable registry used by the generic operator script.
"""

import logging
import os
from collections.abc import Callable, Iterable

from eth_defi.tokenised_fund.asseto.backfill import main as backfill_asseto
from eth_defi.tokenised_fund.centrifuge.backfill import main as backfill_centrifuge
from eth_defi.tokenised_fund.fdit.backfill import main as backfill_fdit
from eth_defi.tokenised_fund.franklin.backfill import main as backfill_franklin
from eth_defi.tokenised_fund.kinexys.backfill import main as backfill_kinexys
from eth_defi.tokenised_fund.kaio.backfill import main as backfill_kaio
from eth_defi.tokenised_fund.libeara.backfill import main as backfill_libeara
from eth_defi.tokenised_fund.midas.backfill import main as backfill_midas
from eth_defi.tokenised_fund.ondo.backfill import main as backfill_ondo
from eth_defi.tokenised_fund.openeden.backfill import main as backfill_openeden
from eth_defi.tokenised_fund.securitize.backfill import main as backfill_securitize
from eth_defi.tokenised_fund.spiko.backfill import main as backfill_spiko
from eth_defi.tokenised_fund.superstate.backfill import main as backfill_superstate
from eth_defi.tokenised_fund.sygnum.backfill import main as backfill_sygnum
from eth_defi.tokenised_fund.theo.backfill import main as backfill_theo
from eth_defi.tokenised_fund.usyc.backfill import main as backfill_usyc
from eth_defi.tokenised_fund.wisdomtree.backfill import main as backfill_wisdomtree
from eth_defi.tokenised_fund.wisdomtree.nav import WISDOMTREE_DATASPAN_API_KEY_ENV
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

BackfillFunction = Callable[[], None]

#: Canonical protocol selectors accepted by the ``PROTOCOLS`` environment variable.
PROTOCOL_BACKFILLS: dict[str, BackfillFunction] = {
    "asseto": backfill_asseto,
    "centrifuge": backfill_centrifuge,
    "fdit": backfill_fdit,
    "franklin": backfill_franklin,
    "kinexys": backfill_kinexys,
    "kaio": backfill_kaio,
    "libeara": backfill_libeara,
    "midas": backfill_midas,
    "ondo": backfill_ondo,
    "openeden": backfill_openeden,
    "securitize": backfill_securitize,
    "spiko": backfill_spiko,
    "superstate": backfill_superstate,
    "sygnum": backfill_sygnum,
    "theo": backfill_theo,
    "usyc": backfill_usyc,
    "wisdomtree": backfill_wisdomtree,
}


def parse_protocols(value: str | None) -> tuple[str, ...]:
    """Parse and validate a comma-separated protocol selection.

    An unset or blank value selects every registered protocol in deterministic
    registry order. Duplicate selectors are removed without changing order.

    :param value: Raw ``PROTOCOLS`` environment value.
    :return: Validated protocol slugs.
    :raise ValueError: If an unknown protocol is requested.
    """

    requested = tuple(dict.fromkeys(part.strip().lower().replace("-", "_") for part in (value or "").split(",") if part.strip()))
    selected = requested or tuple(PROTOCOL_BACKFILLS)
    unknown = tuple(protocol for protocol in selected if protocol not in PROTOCOL_BACKFILLS)
    if unknown:
        supported = ", ".join(PROTOCOL_BACKFILLS)
        message = f"Unknown tokenised-fund protocols: {', '.join(unknown)}. Supported protocols: {supported}"
        raise ValueError(message)
    return selected


def run_protocol_backfills(protocols: Iterable[str]) -> tuple[str, ...]:
    """Run selected protocol backfills sequentially.

    Protocol backfills intentionally share the configured vault database,
    reader-state and Parquet paths, so deterministic sequential execution
    avoids concurrent file replacement.

    :param protocols: Validated protocol slugs.
    :return: Completed protocol slugs.
    """

    completed: list[str] = []
    for protocol in protocols:
        logger.info("Starting tokenised-fund backfill: %s", protocol)
        PROTOCOL_BACKFILLS[protocol]()
        completed.append(protocol)
        logger.info("Completed tokenised-fund backfill: %s", protocol)
    return tuple(completed)


def configure_optional_private_backfills(protocols_value: str | None, protocols: tuple[str, ...]) -> None:
    """Make the default all-protocol run tolerate unavailable private data.

    WisdomTree metadata is public on-chain, but its official NAV history needs
    a private DataSpan API key. The implicit all-protocol workflow therefore
    registers its metadata and skips only the private price scan when neither
    the key nor an explicit price-scan choice is present. Explicit WisdomTree
    selections continue to fail closed without the credential.

    :param protocols_value: Raw ``PROTOCOLS`` environment value.
    :param protocols: Validated protocol selection.
    :return: None.
    """

    is_implicit_all = not (protocols_value or "").strip()
    scan_choice_is_explicit = "WISDOMTREE_SCAN_PRICES" in os.environ
    has_api_key = bool(os.environ.get(WISDOMTREE_DATASPAN_API_KEY_ENV))
    if is_implicit_all and "wisdomtree" in protocols and not scan_choice_is_explicit and not has_api_key:
        os.environ["WISDOMTREE_SCAN_PRICES"] = "false"
        logger.warning(
            "Skipping WisdomTree private NAV history because %s is not set; public on-chain metadata will still be updated",
            WISDOMTREE_DATASPAN_API_KEY_ENV,
        )


def main() -> None:
    """Run tokenised-fund backfills selected through environment variables.

    ``DRY_RUN`` defaults to ``true`` for the aggregate entry point. Operators
    must explicitly set ``DRY_RUN=false`` before any protocol may write.
    ``PROTOCOLS`` defaults to all registered integrations.

    :return: None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    if not os.environ.get("DRY_RUN", "").strip():
        os.environ["DRY_RUN"] = "true"
    protocols_value = os.environ.get("PROTOCOLS")
    protocols = parse_protocols(protocols_value)
    configure_optional_private_backfills(protocols_value, protocols)
    logger.info("Tokenised-fund backfill plan: protocols=%s dry_run=%s", ",".join(protocols), os.environ["DRY_RUN"])
    completed = run_protocol_backfills(protocols)
    logger.info("Tokenised-fund backfill finished: %s", ",".join(completed))
