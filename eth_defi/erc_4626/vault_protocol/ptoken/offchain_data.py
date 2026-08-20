"""Address-scoped pToken display data."""

from typing import Final, TypedDict

from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.ptoken.constants import PTOKEN_BTC_3X_LONG_VAULT, PTOKEN_HOOD_3X_LONG_VAULT


class PTokenVaultOffchainData(TypedDict):
    """Display data for one reviewed pToken vault."""

    #: Listing-friendly summary.
    short_description: str

    #: Longer product and provenance description.
    description: str


#: Reviewed copy is intentionally address-scoped while the issuer is unknown.
PTOKEN_VAULT_DATA: Final[dict[HexAddress, PTokenVaultOffchainData]] = {
    PTOKEN_BTC_3X_LONG_VAULT: {
        "short_description": "Currently not yet identified pToken issuer; BTC (3x Long) product.",
        "description": "Currently not yet identified. This Robinhood Chain pToken is labelled **BTC (3x Long)** and is denominated in USDG. It is one of two reviewed asynchronous pToken vaults deployed through the same unverified factory and upgradeable beacon, with a shared unlabelled manager. The vault uses Arcus's USDG deposit infrastructure; this integration does not establish Arcus ownership or authorship. Its leverage maintenance, redemption timetable and complete fee terms have not been independently verified.",
    },
    PTOKEN_HOOD_3X_LONG_VAULT: {
        "short_description": "Currently not yet identified pToken issuer; HOOD (3x Long) product.",
        "description": "Currently not yet identified. This Robinhood Chain pToken is labelled **HOOD (3x Long)** and is denominated in USDG. It is one of two reviewed asynchronous pToken vaults deployed through the same unverified factory and upgradeable beacon, with a shared unlabelled manager. The vault uses Arcus's USDG deposit infrastructure; this integration does not establish Arcus ownership or authorship. Its leverage maintenance, redemption timetable and complete fee terms have not been independently verified.",
    },
}


def get_ptoken_vault_offchain_data(vault_address: HexAddress) -> PTokenVaultOffchainData | None:
    """Return reviewed display data for one pToken vault.

    :param vault_address:
        Reviewed pToken vault address.
    :return:
        Address-scoped display data, or ``None`` for another pToken contract.
    """

    return PTOKEN_VAULT_DATA.get(HexAddress(vault_address.lower()))
