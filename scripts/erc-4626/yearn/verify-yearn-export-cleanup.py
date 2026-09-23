#!/usr/bin/env python3
"""Verify Yearn attribution in a generated public vault JSON file.

The check is read-only and uses the same official Kong registry as the export
cleanup hook. Run it after a local or production-shaped JSON build with
``VAULT_JSON`` set when the default path is not suitable.
"""

import json
import logging
import os
import time
from pathlib import Path

from eth_typing import HexAddress
from requests.exceptions import RequestException
from tabulate import tabulate

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.yearn.export_cleanup import fetch_yearn_export_catalogue

logger = logging.getLogger(__name__)
MONAD_CHAIN_ID = 143


def _record_key(record: dict[str, object]) -> tuple[int, HexAddress]:
    """Extract a normalised chain/address key from one exported record.

    New records expose separate ``chain_id`` and ``address`` values. The
    combined ``id`` fallback keeps the verifier useful with older exports.

    :param record:
        One public vault JSON record.
    :return:
        Numeric chain ID and lowercase vault address.
    :raises TypeError, ValueError:
        If the record has no usable vault identity.
    """

    chain_id = record.get("chain_id")
    address = record.get("address")
    if chain_id is None or address is None:
        vault_id = record.get("id")
        if not isinstance(vault_id, str) or "-" not in vault_id:
            raise ValueError(f"Vault record has no chain/address identity: {record!r}")
        chain_text, address = vault_id.split("-", 1)
        chain_id = int(chain_text)
    return int(chain_id), HexAddress(str(address).lower())


def main() -> int:  # noqa: PLR0914 - one-pass diagnostic keeps the script standalone
    """Check Yearn rows and print a diagnostic Monad table.

    The command performs one live catalogue request and one pass over the
    generated JSON. It is read-only and reports every mismatch before returning
    a failing process status.

    :return:
        Process exit status; non-zero indicates an attribution mismatch.
    """

    log_level = os.environ.get("LOG_LEVEL", "info").upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO), format="%(levelname)s %(message)s")
    json_path = Path(os.environ.get("VAULT_JSON", "~/.tradingstrategy/vaults/top_vaults_by_chain.json")).expanduser()
    started_at = time.perf_counter()

    try:
        catalogue = fetch_yearn_export_catalogue()
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            message = "Generated JSON root must be an object"
            raise ValueError(message)
        records = payload.get("vaults")
        if not isinstance(records, list):
            message = "Generated JSON does not contain a vaults list"
            raise ValueError(message)
    except (OSError, RequestException, ValueError) as error:
        logger.error("Could not verify Yearn export %s: %s", json_path, error)
        return 1

    mismatches: list[str] = []
    monad_rows: list[list[object]] = []
    excluded_feature = ERC4626Feature.yearn_registry_excluded.name
    yearn_features = {feature.name for feature in (ERC4626Feature.yearn_v3_like, ERC4626Feature.yearn_compounder_like, ERC4626Feature.yearn_tokenised_strategy, ERC4626Feature.yearn_morpho_compounder_like)}

    for record in records:
        if not isinstance(record, dict):
            mismatches.append("non-object vault record")
            continue
        try:
            key = _record_key(record)
        except (TypeError, ValueError) as error:
            mismatches.append(str(error))
            continue
        protocol = record.get("protocol")
        features = set(record.get("features") or [])
        excluded = excluded_feature in features
        protocol_slug = record.get("protocol_slug")
        has_yearn_protocol = protocol == "Yearn" or protocol_slug == "yearn"
        if has_yearn_protocol and key not in catalogue:
            mismatches.append(f"Yearn row is absent from official catalogue: {key}")
        if excluded and (has_yearn_protocol or record.get("curator_slug") == "yearn" or "yearn.fi" in str(record.get("link", ""))):
            mismatches.append(f"Excluded row still has Yearn attribution: {key}")
        if key[0] == MONAD_CHAIN_ID and features.intersection(yearn_features | {excluded_feature}):
            monad_rows.append([key[1], protocol, record.get("protocol_slug"), record.get("link"), excluded])

    logger.info(
        "Checked %d vaults against %d official Yearn entries in %.2fs",
        len(records),
        len(catalogue),
        time.perf_counter() - started_at,
    )
    print(tabulate(monad_rows, headers=["address", "protocol", "slug", "link", "excluded"], tablefmt="github"))
    if mismatches:
        for mismatch in mismatches:
            logger.error("%s", mismatch)
        return 1
    logger.info("Yearn export attribution is consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
