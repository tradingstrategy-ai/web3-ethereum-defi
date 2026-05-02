"""Morpho Blue warning analytics helpers.

Provides pure-data helpers and a structured result type that operate on
:py:class:`MorphoVaultData` dicts returned by
:py:func:`~eth_defi.erc_4626.vault_protocol.morpho.offchain_metadata.fetch_morpho_vault_data`,
plus a diagnostic printer for CLI scripts.

All list fields use sorted output so JSON / Series exports are deterministic
regardless of the order the API returns warnings.

Main entry point: :py:func:`analyze_morpho_flags` returns a
:py:class:`MorphoFlagAnalytics` dataclass that bundles all derived fields and
serialises to a human-readable string via ``str()``.
"""

from dataclasses import dataclass
from typing import Any

from eth_defi.erc_4626.vault_protocol.morpho.offchain_metadata import MorphoVaultData

#: Number of hex characters to display from a Morpho market ID before truncating
_MARKET_ID_PREFIX_LEN = 10


@dataclass(slots=True)
class MorphoFlagAnalytics:
    """Structured analytics derived from Morpho Blue API warning data.

    All list fields are sorted for deterministic JSON output.

    Typical usage::

        data = vault.morpho_offchain_data
        if data is not None:
            analytics = analyze_morpho_flags(data)
            print(analytics)  # human-readable summary
            print(analytics.note)  # pipeline note text or None
    """

    #: Sorted vault-level warning type strings (any severity).
    #: Example: ``["not_whitelisted", "short_timelock"]``
    vault_flag_types: list[str]

    #: Sorted market-level warning type strings (any severity).
    #: Example: ``["bad_debt_unrealized", "not_whitelisted"]``
    market_flag_types: list[str]

    #: Sorted RED-level warning type strings across vault and market warnings.
    #: Non-empty when :py:attr:`~eth_defi.vault.flag.VaultFlag.morpho_issues` must be set.
    #: Example: ``["bad_debt_unrealized", "short_timelock"]``
    red_flags: list[str]

    #: Sorted YELLOW-level warning type strings across vault and market warnings.
    #: YELLOW flags do not trigger :py:attr:`~eth_defi.vault.flag.VaultFlag.morpho_issues`.
    #: Example: ``["bad_debt_realized", "not_whitelisted"]``
    yellow_flags: list[str]

    #: Human-readable note for the pipeline / ``get_notes()`` output.
    #: ``None`` when there are no RED warnings.
    note: str | None

    def __str__(self) -> str:
        """Return a compact one-line summary for logging and diagnostics."""
        parts = []
        if self.vault_flag_types:
            parts.append(f"vault=[{', '.join(self.vault_flag_types)}]")
        if self.market_flag_types:
            parts.append(f"market=[{', '.join(self.market_flag_types)}]")
        if self.red_flags:
            parts.append(f"RED=[{', '.join(self.red_flags)}]")
        if self.yellow_flags:
            parts.append(f"YELLOW=[{', '.join(self.yellow_flags)}]")
        return " ".join(parts) if parts else "no warnings"


def analyze_morpho_flags(data: MorphoVaultData) -> MorphoFlagAnalytics:
    """Build a :py:class:`MorphoFlagAnalytics` summary from raw Morpho API data.

    Single entry point that computes all derived warning fields in one pass.
    Prefer this over calling the individual helper functions separately.

    :param data:
        Morpho vault data from the GraphQL API.

    :return:
        Fully populated :py:class:`MorphoFlagAnalytics` instance.
    """
    vault_flag_types = sorted({w["type"] for w in data.get("vault_warnings", [])})
    market_flag_types = sorted({w["type"] for w in data.get("market_warnings", [])})

    red_vault = {w["type"] for w in data.get("vault_warnings", []) if w.get("level") == "RED"}
    red_market = {w["type"] for w in data.get("market_warnings", []) if w.get("level") == "RED"}
    red_flags = sorted(red_vault | red_market)

    yellow_vault = {w["type"] for w in data.get("vault_warnings", []) if w.get("level") == "YELLOW"}
    yellow_market = {w["type"] for w in data.get("market_warnings", []) if w.get("level") == "YELLOW"}
    yellow_flags = sorted(yellow_vault | yellow_market)

    note = f"Morpho has flagged this vault with the following issues: {', '.join(red_flags)}" if red_flags else None

    return MorphoFlagAnalytics(
        vault_flag_types=vault_flag_types,
        market_flag_types=market_flag_types,
        red_flags=red_flags,
        yellow_flags=yellow_flags,
        note=note,
    )


def get_morpho_red_flags(data: MorphoVaultData) -> list[str]:
    """Return all RED-level warning type strings across vault and market warnings.

    :param data:
        Morpho vault data from the GraphQL API.

    :return:
        Sorted list of RED warning type strings, e.g.
        ``["bad_debt_unrealized", "short_timelock"]``.
        Empty list when there are no RED warnings.
    """
    return analyze_morpho_flags(data).red_flags


def get_morpho_vault_flag_types(data: MorphoVaultData) -> list[str]:
    """Return all vault-level warning type strings (any severity).

    :param data:
        Morpho vault data from the GraphQL API.

    :return:
        Sorted list of vault-warning type strings,
        e.g. ``["not_whitelisted", "short_timelock"]``.
    """
    return analyze_morpho_flags(data).vault_flag_types


def get_morpho_market_flag_types(data: MorphoVaultData) -> list[str]:
    """Return all market-level warning type strings (any severity).

    :param data:
        Morpho vault data from the GraphQL API.

    :return:
        Sorted list of market-warning type strings,
        e.g. ``["bad_debt_unrealized", "not_whitelisted"]``.
    """
    return analyze_morpho_flags(data).market_flag_types


def generate_morpho_issue_note(data: MorphoVaultData) -> str | None:
    """Generate a human-readable note for RED-level Morpho warnings.

    Returns ``None`` when there are no RED warnings so callers can use a
    simple ``notes = notes or generate_morpho_issue_note(data)`` pattern.

    :param data:
        Morpho vault data from the GraphQL API.

    :return:
        Note string such as
        ``"Morpho has flagged this vault with the following issues: bad_debt_unrealized, short_timelock"``,
        or ``None`` if there are no RED warnings.
    """
    return analyze_morpho_flags(data).note


def format_morpho_flag_analytics(vault: Any) -> str:
    """Return a formatted Morpho Blue warning summary for a vault as a string.

    Builds a multi-line human-readable block suitable for CLI diagnostic scripts
    (e.g. ``check-vault-onchain.py``). The caller decides how to output it — typically
    via ``print(format_morpho_flag_analytics(vault))``.

    Returns a single-line message when the vault has no Morpho offchain data.

    Includes:

    - Vault-level and market-level warning type sets
    - Per-warning detail lines (severity level, type, market ID, bad-debt USD / share)
    - The pipeline note text from :py:attr:`MorphoFlagAnalytics.note`

    :param vault:
        A :py:class:`~eth_defi.erc_4626.vault_protocol.morpho.vault_v1.MorphoV1Vault`
        or :py:class:`~eth_defi.erc_4626.vault_protocol.morpho.vault_v2.MorphoV2Vault`
        instance.

    :return:
        Formatted multi-line string with the warning summary.
    """
    data = vault.morpho_offchain_data
    if data is None:
        return "  No data (vault not indexed by Morpho Blue API)"

    analytics = analyze_morpho_flags(data)
    lines: list[str] = []

    lines.append(f"  Vault-level warning types:  {analytics.vault_flag_types or 'none'}")
    lines.append(f"  Market-level warning types: {analytics.market_flag_types or 'none'}")

    for w in data.get("vault_warnings", []):
        lines.append(f"    vault  [{w.get('level', '?'):6s}] {w.get('type', '?')}")

    for w in data.get("market_warnings", []):
        extra = ""
        if w.get("bad_debt_usd") is not None:
            extra = f"  bad_debt_usd={w['bad_debt_usd']:.2f}  bad_debt_share={w.get('bad_debt_share', 0):.4f}"
        market_id = w.get("market_id", "?")
        short_id = market_id[:_MARKET_ID_PREFIX_LEN] + "..." if len(market_id) > _MARKET_ID_PREFIX_LEN else market_id
        lines.append(f"    market [{w.get('level', '?'):6s}] {w.get('type', '?')}  market_id={short_id}{extra}")

    if analytics.note:
        lines.append(f"\n  Note: {analytics.note}")

    return "\n".join(lines)
