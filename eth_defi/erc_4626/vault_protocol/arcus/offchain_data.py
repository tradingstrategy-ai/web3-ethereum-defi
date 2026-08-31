"""Reviewed Arcus pToken display data.

Arcus documents pToken mechanics in its `product announcement
<https://arcus.xyz/blog/ptokens-a-new-primitive-on-arcus>`__ and exposes live
values through its public vault API. The scanner keeps reviewed address-scoped
copy locally so metadata reads stay deterministic and do not depend on an
offchain service.

Arcus does not identify the operator behind the address returned by the
``manager()`` accessor, so this module does not attribute a manager name.
"""

from typing import Final, TypedDict

from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.arcus.constants import ARCUS_BTC_3X_LONG_VAULT, ARCUS_HOOD_3X_LONG_VAULT
from eth_defi.types import Percent

#: Entry fee reported by the reviewed production pToken API records.
ARCUS_PTOKEN_DEPOSIT_FEE: Final[Percent] = 0.0025

#: Profit share reported by the reviewed production pToken API records.
ARCUS_PTOKEN_PERFORMANCE_FEE: Final[Percent] = 0.0

ARCUS_PTOKEN_NOTES_TEMPLATE: Final[str] = "A pToken holder's return follows the token's NAV per share, representing a proportionate claim on the vault's USDG collateral and {market} perpetual position after funding, fees and slippage. It is not simply {leverage} times the asset's total return, as automatic threshold-based rebalancing makes performance path-dependent. The pooled account, rather than individual holders, maintains the collateral required for the position. Read the [announcement](https://arcus.xyz/blog/ptokens-a-new-primitive-on-arcus) for more details."


class ArcusVaultOffchainData(TypedDict):
    """Reviewed display data for one production Arcus pToken."""

    #: Listing-friendly pToken summary.
    short_description: str

    #: Longer pToken strategy summary.
    description: str

    #: Explanation of pToken return and collateral mechanics.
    notes: str

    #: Entry fee as a fraction of the deposited amount.
    deposit_fee: Percent

    #: Manager profit share as a fraction of profit.
    performance_fee: Percent


#: Address-scoped product copy for reviewed production pTokens.
#:
#: The implementation deliberately does not extrapolate this data to every
#: contract that shares the Arcus detection signal.
ARCUS_VAULT_DATA: Final[dict[HexAddress, ArcusVaultOffchainData]] = {
    ARCUS_BTC_3X_LONG_VAULT: ArcusVaultOffchainData(
        short_description="Arcus pToken targeting 3x long BTC perpetual exposure.",
        description="Arcus pBTC3x targets 3x long BTC exposure through perpetual futures. Each token represents a pro-rata claim on the vault's USDG collateral and open BTC perpetual position.",
        notes=ARCUS_PTOKEN_NOTES_TEMPLATE.format(market="BTC", leverage=3),
        deposit_fee=ARCUS_PTOKEN_DEPOSIT_FEE,
        performance_fee=ARCUS_PTOKEN_PERFORMANCE_FEE,
    ),
    ARCUS_HOOD_3X_LONG_VAULT: ArcusVaultOffchainData(
        short_description="Arcus pToken targeting 3x long HOOD perpetual exposure.",
        description="Arcus pHOOD3x targets 3x long HOOD exposure through perpetual futures. Each token represents a pro-rata claim on the vault's USDG collateral and open HOOD perpetual position.",
        notes=ARCUS_PTOKEN_NOTES_TEMPLATE.format(market="HOOD", leverage=3),
        deposit_fee=ARCUS_PTOKEN_DEPOSIT_FEE,
        performance_fee=ARCUS_PTOKEN_PERFORMANCE_FEE,
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
