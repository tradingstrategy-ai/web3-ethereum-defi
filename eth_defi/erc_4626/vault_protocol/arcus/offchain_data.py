"""Reviewed Arcus pToken display data.

Arcus's public market API describes exchange markets, not pToken contracts or
pToken accounting.  In particular, it must not supply a pToken NAV, strategy,
fee, or curator value.  Keep the small set of reviewed product descriptions
local and address-scoped instead of coupling vault reads to unrelated API data.

The ``curator_name`` is a protocol attribution.  It is not inferred from the
unlabelled address returned by the generic ``manager()`` accessor.
"""

from typing import Final, TypedDict

from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.arcus.constants import ARCUS_BTC_3X_LONG_VAULT, ARCUS_HOOD_3X_LONG_VAULT


class ArcusVaultOffchainData(TypedDict):
    """Reviewed display data for one production Arcus pToken."""

    #: Protocol-level curator attribution.
    curator_name: str

    #: Listing-friendly pToken summary.
    short_description: str

    #: Longer, conservative pToken explanation.
    description: str


#: Address-scoped product copy for reviewed production pTokens.
#:
#: The implementation deliberately does not extrapolate this data to every
#: contract that shares the Arcus detection signal.  Product names alone do not
#: establish leverage maintenance, rebalancing, fees, or redemption terms.
ARCUS_VAULT_DATA: Final[dict[HexAddress, ArcusVaultOffchainData]] = {
    ARCUS_BTC_3X_LONG_VAULT: ArcusVaultOffchainData(
        curator_name="Arcus",
        short_description="Arcus pToken labelled BTC (3x Long).",
        description=("This reviewed Robinhood Chain pToken is labelled **BTC (3x Long)**. The integration does not independently verify its leverage maintenance, rebalancing, fee schedule, or redemption terms."),
    ),
    ARCUS_HOOD_3X_LONG_VAULT: ArcusVaultOffchainData(
        curator_name="Arcus",
        short_description="Arcus pToken labelled HOOD (3x Long).",
        description=("This reviewed Robinhood Chain pToken is labelled **HOOD (3x Long)**. The integration does not independently verify its leverage maintenance, rebalancing, fee schedule, or redemption terms."),
    ),
}


def get_arcus_vault_offchain_data(vault_address: HexAddress) -> ArcusVaultOffchainData | None:
    """Return reviewed static data for a production Arcus pToken.

    This lookup performs no network I/O.  The caller is already an
    :class:`~eth_defi.erc_4626.vault_protocol.arcus.vault.ArcusVault`, so its
    classifier has established the Robinhood Chain contract family.

    :param vault_address:
        Arcus pToken contract address.
    :return:
        Reviewed product copy, or ``None`` when the pToken has not been
        individually reviewed.
    """

    return ARCUS_VAULT_DATA.get(HexAddress(vault_address.lower()))
