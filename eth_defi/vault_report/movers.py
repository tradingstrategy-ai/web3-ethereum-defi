"""Month-over-month changes in the top vault ranking.

The previous ranking is read from the previous report's best-performing vaults
table (Ghost post HTML). Vault links in older posts look like
``/trading-view/{chain}/vaults/{slug}?a={address}``. Newer posts use
``/trading-view/vaults/{slug}``. Links are resolved to vault ids by address
when present, otherwise by the vault slug in the current top vaults export.

Future reports also store their rankings in ``report.json``, see
:py:func:`eth_defi.vault_report.report.write_report_manifest`.

The previous table may include vaults that the current report lists in a
different section, e.g. perp DEX vaults before they were separated. Previous
ranks are therefore recalculated within the current ranking universe.
"""

import html
import logging
import re
import urllib.parse
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class RankChange:
    """Rank change of one vault in the current top list."""

    #: Vault id
    vault_id: str

    #: Rank in the current report, 1-based
    current_rank: int

    #: Rank in the previous report within the current ranking universe, or ``None`` for new entries
    previous_rank: int | None

    @property
    def status(self) -> str:
        """``new``, ``up``, ``down`` or ``same``."""
        if self.previous_rank is None:
            return "new"
        if self.current_rank < self.previous_rank:
            return "up"
        if self.current_rank > self.previous_rank:
            return "down"
        return "same"


def parse_ranked_vault_links(post_html: str, heading_id: str) -> list[str]:
    """Read vault links, in rank order, from the first table after a heading.

    :param post_html:
        Previous report post HTML.

    :param heading_id:
        ``id`` of the section ``<h2>``, e.g. ``the-best-performing-vaults``.

    :return:
        Vault page URLs in table order. Empty if the section or table is missing.
    """
    heading = re.search(rf'<h2 id="{re.escape(heading_id)}">', post_html)
    if not heading:
        return []
    table = re.search(r"<table.*?</table>", post_html[heading.end() :], flags=re.DOTALL)
    if not table:
        return []
    links = []
    for row in re.findall(r"<tr>(.*?)</tr>", table.group(0), flags=re.DOTALL):
        match = re.search(r'<a href="([^"]*/vaults/[^"]+)"', row)
        if match:
            links.append(html.unescape(match.group(1)))
    return links


def resolve_vault_id(link: str, vaults_df: pd.DataFrame) -> str | None:
    """Resolve a vault page link to a vault id.

    :param link:
        Vault page URL from a report table.

    :param vaults_df:
        Current vault metrics with ``address``, ``vault_slug`` and ``id`` columns.

    :return:
        Vault id, or ``None`` if the vault is not in the current export.
    """
    parsed = urllib.parse.urlparse(link)
    slug = parsed.path.rstrip("/").split("/")[-1]
    address = urllib.parse.parse_qs(parsed.query).get("a", [None])[0]

    if address:
        candidates = vaults_df.loc[vaults_df["address"].str.lower() == address.lower()]
        if len(candidates) > 1:
            candidates = candidates.loc[candidates["vault_slug"] == slug] if (candidates["vault_slug"] == slug).any() else candidates
        if len(candidates):
            return candidates.index[0]

    candidates = vaults_df.loc[vaults_df["vault_slug"] == slug]
    return candidates.index[0] if len(candidates) else None


def calculate_rank_changes(previous_ids: list[str | None], current_ids: list[str], top_n: int = 20) -> tuple[list[RankChange], list[str]]:
    """Compare the current top list with the previous one.

    :param previous_ids:
        Previous ranking as resolved vault ids. ``None`` entries are ignored.

    :param current_ids:
        The full current ranking universe in rank order, not truncated.
        Previous ranks are recalculated within this universe.

    :param top_n:
        Size of the compared top list.

    :return:
        Tuple (rank changes of the current top list, vault ids that dropped out of the top list).
    """
    universe = set(current_ids)
    previous_in_universe = [vault_id for vault_id in dict.fromkeys(previous_ids) if vault_id in universe]
    previous_rank = {vault_id: rank for rank, vault_id in enumerate(previous_in_universe, start=1)}

    current_top = current_ids[:top_n]
    changes = [RankChange(vault_id, rank, previous_rank.get(vault_id)) for rank, vault_id in enumerate(current_top, start=1)]
    dropped = [vault_id for vault_id in previous_in_universe[:top_n] if vault_id not in set(current_top)]
    logger.info("Top %d movers: %d new entries, %d dropped, %d previous links unresolved", top_n, sum(c.status == "new" for c in changes), len(dropped), sum(i is None for i in previous_ids))
    return changes, dropped
