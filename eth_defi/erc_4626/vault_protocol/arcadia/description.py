"""Deterministic Arcadia lending descriptions for pools without narrative copy.

Templates describe the mechanics documented in the `Arcadia lender guide
<https://docs.arcadia.finance/users/lenders>`__. They make no assumptions
about fees, liquidity guarantees or the protection of a particular tranche.
"""

# Public website copy deliberately uses typographic apostrophes.
# ruff: noqa: RUF001

import re
from dataclasses import dataclass

from eth_defi.chain import CHAIN_NAMES
from eth_defi.erc_4626.vault_protocol.arcadia.offchain_metadata import ArcadiaVaultMetadata, normalise_text
from eth_defi.vault.base import normalise_vault_description, normalise_vault_short_description

#: Maximum listing-summary length, including any ellipsis.
MAX_SHORT_DESCRIPTION_LENGTH = 200
#: Canonical source for generated lending-strategy explanations.
LENDING_DOCUMENTATION_URL = "https://docs.arcadia.finance/users/lenders"


@dataclass(slots=True, frozen=True)
class ArcadiaVaultDescriptions:
    """Resolved website copy for an Arcadia lending tranche.

    Provider narrative takes precedence; otherwise both fields are generated
    from the same known token, chain and tranche identity.
    """

    #: Compact listing copy.
    short_description: str
    #: Full narrative, optionally including a source link.
    description: str


def _normalise_label(value: str | None) -> str:
    """Normalise a display label for equality comparisons.

    Case, repeated whitespace and trailing punctuation do not turn a pool
    label into narrative copy.

    :param value: Optional display label.
    :return: Case-folded, whitespace-normalised label.
    """
    return (normalise_vault_description(value) or "").casefold().rstrip(". !?:;—\u2013-")


def _tranche_designation(vault_name: str | None, metadata: ArcadiaVaultMetadata | None) -> str | None:
    """Read an explicit senior or junior designation from known names.

    Provider tranche names take precedence over the onchain name. No default
    ranking is inferred for an unrecognised name.

    :param vault_name: Optional onchain vault name.
    :param metadata: Optional provider tranche metadata.
    :return: ``senior``, ``junior``, or ``None``.
    """
    names = (metadata.tranche_name if metadata else None, vault_name)
    return next((match.group(1).lower() for name in names if name and (match := re.match(r"^\s*(senior|junior)\b", name, flags=re.IGNORECASE))), None)


def is_arcadia_narrative(description: str | None, *, vault_name: str | None, asset_symbol: str | None, metadata: ArcadiaVaultMetadata | None = None) -> bool:
    """Distinguish narrative from empty text and known pool labels.

    Equality to a known label is the only content heuristic; even a brief
    provider sentence is preserved when it explains the product.

    :param description: Candidate provider or persisted description.
    :param vault_name: Onchain vault name, when available.
    :param asset_symbol: Denomination symbol, when available.
    :param metadata: Optional matching Arcadia API record.
    :return: Whether the candidate contains narrative rather than a label.
    """
    value = _normalise_label(normalise_text(description))
    labels = {"", _normalise_label(vault_name), _normalise_label(asset_symbol)}
    symbols = {symbol for symbol in (asset_symbol, metadata.pool_name if metadata else None) if symbol}
    labels.update(_normalise_label(f"Arcadia {symbol} Pool") for symbol in symbols)
    tranche = _tranche_designation(vault_name, metadata)
    if tranche:
        labels.update(_normalise_label(f"{tranche} {symbol}") for symbol in symbols)
    if metadata:
        labels.update((_normalise_label(metadata.pool_name), _normalise_label(metadata.tranche_name)))
    return value not in labels


def extract_arcadia_short_description(description: str) -> str:
    """Extract the first sentence within the listing character budget.

    Long sentences are cut at a word boundary with an ellipsis, rather than
    dropping a valid short description altogether.

    :param description: Provider narrative or generated summary.
    :return: A summary of at most 200 characters.
    """
    sentence = normalise_vault_short_description(description)
    if len(sentence) <= MAX_SHORT_DESCRIPTION_LENGTH:
        return sentence
    truncated = sentence[: MAX_SHORT_DESCRIPTION_LENGTH - 1].rsplit(" ", maxsplit=1)[0]
    return truncated.rstrip(".,;:") + "…"


def resolve_arcadia_descriptions(
    *,
    chain_id: int,
    vault_name: str | None,
    asset_symbol: str | None,
    metadata: ArcadiaVaultMetadata | None = None,
) -> ArcadiaVaultDescriptions:
    """Prefer Arcadia narrative or generate factual lending copy.

    Only an explicit senior/junior name establishes tranche terminology.
    Missing token and chain information is omitted, with no live financial
    metrics interpolated into either description.

    :param chain_id: Vault chain ID used for its display name.
    :param vault_name: Onchain vault name, also usable during an API outage.
    :param asset_symbol: Known denomination-token symbol, never a guessed ticker.
    :param metadata: Optional API record matched by chain and tranche address.
    :return: Full and compact descriptions ready for existing export fields.
    """
    narrative = normalise_vault_description(metadata.description) if metadata else None
    if narrative and is_arcadia_narrative(narrative, vault_name=vault_name, asset_symbol=asset_symbol, metadata=metadata):
        return ArcadiaVaultDescriptions(short_description=extract_arcadia_short_description(narrative), description=narrative)

    tranche = _tranche_designation(vault_name, metadata)
    chain = {4663: "Robinhood Chain"}.get(chain_id, CHAIN_NAMES.get(chain_id))
    chain_suffix = f" on {chain}" if chain else ""
    asset = normalise_text(asset_symbol)
    supply = f"supplying {asset}" if asset else "supplying assets"
    pool = f"Arcadia Finance’s {asset} lending pool" if asset else "an Arcadia Finance lending pool"
    position = f"the {tranche} tranche of " if tranche else ""
    short_pool = f"{position}Arcadia Finance’s lending pool"
    short = f"Earn interest by {supply} to {short_pool}{chain_suffix}."
    supplied_asset = asset or "the pool’s underlying asset"
    long = f"This vault represents {position}{pool}{chain_suffix}. Depositors supply {supplied_asset}, which borrowers use through collateralised Arcadia accounts, and receive shares that accrue lending interest. Interest rates vary with pool utilisation. Depositors are exposed to smart contract risk and potential losses if borrower liquidations do not cover outstanding debt. See [Arcadia’s lending documentation]({LENDING_DOCUMENTATION_URL})."
    return ArcadiaVaultDescriptions(short_description=extract_arcadia_short_description(short), description=long)
