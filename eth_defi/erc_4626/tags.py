"""Reviewed strategy classifications for generic ERC-4626 wrappers.

These deployments lack a protocol-specific adapter. Keep chain identity in the
lookup so an unrelated deployment at the same address is never classified.
"""

from eth_defi.vault.strategy_tag import StrategyTag

#: Reviewed generic wrappers keyed by chain ID and lowercase address.
STRATEGY_TAGS: dict[tuple[int, str], set[StrategyTag]] = {
    #: Vault: Arbitrum syrupUSDC/USDC Morpho Looper (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Exact Yearn strategy address from the current official yvUSD report; underlying protocol names do not establish management.
    #: Sources:
    #: - https://curation.yearn.fi/report/yearn-yvusd/
    (1, "0x2f56d106c6df739bdbb777c2fee79ffaed88d179"): {StrategyTag.lending, StrategyTag.lending_looping},
}

#: Reviewed 2026-10-22 but deliberately unclassified: the Yearn report lists
#: Morpho V2 Sentora PYUSD Convertor (0x3d2467cbf82332dbfb38997cbc4d2192694d9490)
#: and the revoked USDC to sUSDS Lender (0x7130570bcefcedbe9d15b5b11a33006156460f8f).
#: The listed names establish neither exact strategy mechanics nor carry trading.
#: https://curation.yearn.fi/report/yearn-yvusd/


def lookup_erc4626_strategy_tags(chain_id: int, address: str) -> set[StrategyTag] | None:
    """Resolve a reviewed generic wrapper's strategy without network access.

    The address is normalised before lookup and a copy protects maintained
    classifications from mutation by callers.

    :param chain_id:
        Deployment chain ID.
    :param address:
        Vault address, accepting mixed case.
    :return:
        A fresh set of tags, or ``None`` when the strategy is not maintained.
    """
    tags = STRATEGY_TAGS.get((chain_id, address.lower()))
    return set(tags) if tags is not None else None
