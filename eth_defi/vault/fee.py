"""Vault fee modes."""

import enum

from eth_typing import HexAddress


class VaultFeeMode(enum.Enum):
    """How vault protocol account its fees.

    - Externalised fees: fees are deducted from the redemption amount when user withdraws.
    - Internalised fees: fees are baked into the share price (asset amount) and continuously taken from the profit.
      There are no fees on withdraw.
    """

    #: Vault fees are baked into the share price (asset amount).
    #:
    #: Fees are taken from the profit at the moment profit is made,
    #: and send to another address.
    #:
    #: Example protocols: Yearn, Harvest Finance, USDAi.
    internalised_skimming = "internalised_skimming"

    #: Vault fees are baked into the share price (asset amount).
    #:
    #: Fees are taken from the profit at the moment profit is made.
    #: and corresponding number of shares is minted to the vault owner.
    #:
    #: Example protocols: AUTO Finance
    internalised_minting = "internalised_minting"

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
    "Euler": VaultFeeMode.internalised_skimming,
    "Morpho": VaultFeeMode.internalised_skimming,
    "Enzyme": VaultFeeMode.internalised_skimming,
    "Lagoon": VaultFeeMode.externalised,
    "Velvet Capital": VaultFeeMode.internalised_skimming,
    "Umami": VaultFeeMode.externalised,
    # Unverified contracts, no open source repo
    # https://arbiscan.io/address/0xd15a07a4150b0c057912fe883f7ad22b97161591#code
    "Peapods": None,
    "Ostium": VaultFeeMode.feeless,
    "Gains": VaultFeeMode.feeless,
    "Plutus": VaultFeeMode.internalised_skimming,
    "Harvest Finance": VaultFeeMode.internalised_skimming,
    "D2 Finance": VaultFeeMode.internalised_skimming,
    "Untangle Finance": VaultFeeMode.externalised,
    "Yearn v3": VaultFeeMode.internalised_skimming,
    "Yearn tokenised strategy": VaultFeeMode.internalised_skimming,
    "Goat Protocol": VaultFeeMode.internalised_skimming,
    "USDai": VaultFeeMode.internalised_skimming,
    "AUTO Finance": VaultFeeMode.internalised_minting,
    "NashPoint": VaultFeeMode.internalised_skimming,
}


def get_vault_fee_mode(vault_protocol_name: str, address: HexAddress | str) -> VaultFeeMode | None:
    """Get vault fee mode by protocol name.

    :return:
        None if unknown
    """
    return VAULT_PROTOCOL_FEE_MATRIX.get(vault_protocol_name)
