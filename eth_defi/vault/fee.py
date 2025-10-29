"""Vault fee modes."""

import enum

from eth_typing import HexAddress


class VaultFeeMode(enum.Enum):
    """How vault protocol account its fees."""

    #: Vault fees are baked into the share price (asset amount).
    #:
    #: Example protocols: Yearn, Harvest Finance, USDAi.
    internalised = "internalised"

    #: Vault fees are taken from the user explicitly at the redemption time.
    #:
    #: Example protocols: Lagoon Finance.
    externalised = "externalised"

    #: This protocol has no fees.
    feeless = "feeless"


#: Different vault fee extraction methods by different protocols
#:
#: See :py:func:`eth_defi.erc_4626.core.get_vault_protocol_name` for the names list.
#:
VAULT_PROTOCOL_FEE_MATRIX = {
    "Euler": VaultFeeMode.internalised,
    "Morpho": VaultFeeMode.internalised,
    "Enzyme": VaultFeeMode.internalised,
    "Lagoon": VaultFeeMode.externalised,
    "Velvet Capital": VaultFeeMode.internalised,
    "Umami": VaultFeeMode.externalised,
    # Unverified contracts, no open source repo
    # https://arbiscan.io/address/0xd15a07a4150b0c057912fe883f7ad22b97161591#code
    "Peapods": None,
    "Ostium": VaultFeeMode.feeless,
    "Gains": VaultFeeMode.feeless,
    "Plutus": VaultFeeMode.externalised,
    "Harvest Finance": VaultFeeMode.internalised,
    "D2 Finance": VaultFeeMode.externalised,
    "Untangle Finance": VaultFeeMode.externalised,
    "Yearn v3": VaultFeeMode.internalised,
    "Yearn tokenised strategy": VaultFeeMode.internalised,
    "Goat Protocol": VaultFeeMode.internalised,
    "USDai": VaultFeeMode.internalised,
}


def get_vault_fee_mode(vault_protocol_name: str, address: HexAddress | str) -> VaultFeeMode | None:
    """Get vault fee mode by protocol name.

    :return:
        None if unknown
    """
    return VAULT_PROTOCOL_FEE_MATRIX.get(vault_protocol_name)
