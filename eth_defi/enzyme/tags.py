"""Maintained strategy classifications for Enzyme vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: OpalAccess - LiquidStone 2.
    #: Added: 2026-08-23.
    #: Decision material: BlackOpal describes LiquidStone II as a hybrid
    #: strategy investing in short-duration Brazilian credit-card receivables
    #: alongside an onchain liquid sleeve. This is real-world credit exposure
    #: implemented through more than one investment sleeve.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x1B6d1EDf854CA5d8A7c32DDb79C24B117eBc6433?network=base
    #: - https://www.blackopal.finance/
    "0x1b6d1edf854ca5d8a7c32ddb79c24b117ebc6433": {
        StrategyTag.multistrategy,
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
    },
}


def get_strategy_tags(address: HexAddress) -> set[StrategyTag] | None:
    """Return the maintained strategy tags for an Enzyme shares address.

    Enzyme Blue VaultProxy and Onyx Shares deployments both use their share
    token as the vault identity. The address mapping is therefore shared by
    the two adapters, while retaining the ``None`` result that distinguishes
    a vault with no researched strategy classification from an empty tag set.

    :param address:
        Canonical Enzyme Blue VaultProxy or Onyx Shares address.
    :return:
        A mutable copy of the researched strategy tags, or ``None`` if the
        address has no documented classification.
    """

    return lookup_strategy_tags(STRATEGY_TAGS, address)
