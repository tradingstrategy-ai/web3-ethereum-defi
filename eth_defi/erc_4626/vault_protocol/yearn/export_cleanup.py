"""Best-effort Yearn attribution cleanup for public vault exports.

Yearn-compatible ERC-4626 interfaces are reusable by third parties. The
official Kong registry is therefore used as the positive allow-list for public
Yearn attribution. Technical Yearn features remain on the row so the scanner's
adapter behaviour is unchanged; the rule does not make an ownership or safety
claim about the contract.
"""

from __future__ import annotations

import dataclasses
import logging

import pandas as pd
import requests
from eth_typing import HexAddress
from requests.exceptions import RequestException

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name, is_generic_erc4626_protocol_slug
from eth_defi.erc_4626.vault_protocol.yearn.endorsement import YEARN_REGISTRY_REQUEST_TIMEOUT, YEARN_VAULT_REGISTRY_URL
from eth_defi.research.vault_metrics import slugify_protocol
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

#: Fail open if the positive Yearn catalogue is implausibly small. Checking the
#: positive set itself protects against a large but malformed registry response.
MINIMUM_YEARN_EXPORT_CATALOGUE_ENTRIES = 100

#: HTTP timeout shared with the scanner's Yearn registry check.
YEARN_EXPORT_REGISTRY_REQUEST_TIMEOUT = YEARN_REGISTRY_REQUEST_TIMEOUT

#: Chain-neutral block explorer link used for generic ERC-4626 attribution.
GENERIC_VAULT_LINK_TEMPLATE = "https://routescan.io/address/{address}"

YearnVaultKey = tuple[int, HexAddress]


def _is_positive_yearn_entry(record: object) -> bool:
    """Return whether one raw Kong record explicitly identifies Yearn.

    Only the literal Boolean ``true`` is accepted. Missing, empty, malformed,
    and false inclusion values are not positive catalogue evidence.

    :param record:
        One decoded registry item.
    :return:
        ``True`` when its inclusion object says ``isYearn``.
    """

    if not isinstance(record, dict):
        return False
    inclusion = record.get("inclusion")
    return isinstance(inclusion, dict) and inclusion.get("isYearn") is True


def parse_yearn_export_catalogue(payload: object) -> frozenset[YearnVaultKey]:
    """Parse positive Yearn chain/address keys from the official registry.

    The complete response is reduced to compact lookup keys. A small positive
    set is rejected so a partial upstream response cannot mass-reclassify
    otherwise valid Yearn rows.

    :param payload:
        JSON-decoded response from :data:`YEARN_VAULT_REGISTRY_URL`.
    :return:
        Lowercase ``(chain_id, address)`` keys for official Yearn entries.
    :raises ValueError:
        If the response is not a plausible complete registry response.
    """

    if not isinstance(payload, list):
        message = "Yearn export registry response must be a JSON array"
        raise ValueError(message)

    positive: set[YearnVaultKey] = set()
    for record in payload:
        if not _is_positive_yearn_entry(record):
            continue
        assert isinstance(record, dict)
        chain_id = record.get("chainId")
        address = record.get("address")
        if isinstance(chain_id, int) and isinstance(address, str) and address.startswith("0x"):
            positive.add((chain_id, HexAddress(address.lower())))

    if len(positive) < MINIMUM_YEARN_EXPORT_CATALOGUE_ENTRIES:
        message = f"Yearn export registry returned only {len(positive)} positive entries"
        raise ValueError(message)
    return frozenset(positive)


def fetch_yearn_export_catalogue() -> frozenset[YearnVaultKey]:
    """Fetch and validate the official Yearn catalogue for one export build.

    Reads `Yearn's public Kong vault registry
    <https://kong.yearn.fi/api/rest/list/vaults>`__. The surrounding hook
    handles availability and validation failures as a fail-open condition.

    :return:
        Positive Yearn chain/address keys.
    :raises RequestException, ValueError:
        If the source is unavailable or fails validation. The caller's hook
        boundary catches these failures and leaves the export unchanged.
    """

    response = requests.get(YEARN_VAULT_REGISTRY_URL, timeout=YEARN_EXPORT_REGISTRY_REQUEST_TIMEOUT)
    response.raise_for_status()
    return parse_yearn_export_catalogue(response.json())


def _yearn_candidates(vault_db: VaultDatabase) -> pd.DataFrame:
    """Project Yearn-attributed metadata into a small join frame.

    The projection keeps the vectorised membership operation independent from
    the much wider metadata rows and avoids any per-vault network reads.

    :param vault_db:
        In-memory vault metadata database.
    :return:
        DataFrame with ``vault_id``, ``chain_id``, lowercase ``address`` and
        flags distinguishing current Yearn rows from already-excluded generic
        rows that may have stale sticky attribution.
    """

    candidates: list[dict[str, object]] = []
    for spec, row in vault_db.items():
        detection = row.get("_detection_data")
        if detection is None:
            continue
        features = set(row.get("features") or getattr(detection, "features", set()))
        is_yearn_protocol = row.get("Protocol") == "Yearn"
        is_generic_excluded = ERC4626Feature.yearn_registry_excluded in features and (row.get("Protocol") == "ERC-4626" or is_generic_erc4626_protocol_slug(row.get("protocol_slug")))
        if not is_yearn_protocol and not is_generic_excluded:
            continue
        candidates.append(
            {
                "vault_id": spec.as_string_id(),
                "chain_id": detection.chain,
                "address": str(detection.address).lower(),
                "is_yearn_protocol": is_yearn_protocol,
                "has_exclusion_marker": is_generic_excluded,
            }
        )
    return pd.DataFrame.from_records(
        candidates,
        columns=["vault_id", "chain_id", "address", "is_yearn_protocol", "has_exclusion_marker"],
    )


def _set_yearn_excluded_attribution(row: VaultRow, address: HexAddress) -> None:
    """Remove Yearn attribution while preserving technical features.

    The remaining non-Yearn features determine the replacement protocol. Most
    rejected rows therefore become generic ERC-4626, while a more specific
    retained protocol feature is allowed to win.

    :param row:
        Mutable vault metadata row owned by the current export.
    :param address:
        Lowercase vault address used for the generic explorer link.
    :return:
        ``None``.
    """

    detection: ERC4262VaultDetection = row["_detection_data"]
    features = set(row.get("features") or detection.features) | set(detection.features)
    features.add(ERC4626Feature.yearn_registry_excluded)
    row["features"] = features
    row["_detection_data"] = dataclasses.replace(detection, features=features)

    # A stale adapter-provided protocol curator must not reintroduce Yearn in
    # the public export after the protocol attribution has been removed.
    if row.get("_curator_slug") == "yearn":
        row["_curator_slug"] = None
    row["Protocol"] = get_vault_protocol_name(features)
    row["protocol_slug"] = slugify_protocol(row["Protocol"])
    generic_link = GENERIC_VAULT_LINK_TEMPLATE.format(address=address)
    existing_link = row.get("Link")
    if isinstance(existing_link, str) and existing_link.lower() == generic_link.lower():
        # Preserve an existing checksum representation so repeated export
        # hooks do not create needless sticky-state churn.
        row["Link"] = existing_link
    else:
        row["Link"] = generic_link
    row["Features"] = ", ".join(sorted(feature.name for feature in features))


def clean_yearn_vault_metadata(vault_db: VaultDatabase) -> set[str]:
    """Best-effort cleanup of false Yearn protocol attribution.

    The function performs one catalogue request and one Pandas membership
    operation for the current export. It considers rows currently labelled
    Yearn and already-generic rows carrying the exclusion marker, so stale
    sticky curator or homepage fields are corrected too. Any request or
    validation error is logged and converted to an empty change set so callers
    can continue publishing the rest of the vault data.

    :param vault_db:
        In-memory database loaded for the current JSON export.
    :return:
        Vault IDs whose export attribution was cleaned.
    """

    candidates = _yearn_candidates(vault_db)
    if candidates.empty:
        logger.info("Yearn export cleanup: no Yearn candidates")
        return set()

    try:
        catalogue = fetch_yearn_export_catalogue()
    except (RequestException, ValueError) as error:
        logger.warning("Yearn export cleanup skipped: %s", error)
        return set()

    official_keys = pd.MultiIndex.from_tuples(sorted(catalogue), names=["chain_id", "address"])
    candidate_keys = pd.MultiIndex.from_frame(candidates[["chain_id", "address"]])
    candidates["official"] = candidate_keys.isin(official_keys)
    rejected = candidates.loc[(~candidates["official"]) & candidates["is_yearn_protocol"]]
    cleanup = candidates.loc[((~candidates["official"]) & candidates["is_yearn_protocol"]) | candidates["has_exclusion_marker"]]

    changed: set[str] = set()
    for candidate in cleanup.itertuples(index=False):
        spec = VaultSpec.parse_string(candidate.vault_id, separator="-")
        row = vault_db.get(spec)
        if row is None:
            continue
        detection = row.get("_detection_data")
        if detection is None:
            continue
        _set_yearn_excluded_attribution(row, candidate.address)
        changed.add(candidate.vault_id)

    logger.info(
        "Yearn export cleanup: catalogue=%d candidates=%d rejected=%d changed=%d",
        len(catalogue),
        len(candidates),
        len(rejected),
        len(changed),
    )
    return changed
