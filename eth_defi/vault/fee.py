"""Vault fee modes."""

import enum
from dataclasses import dataclass

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

    def is_internalised(self) -> bool:
        """Are the fees internalised in the share price?"""
        return self != VaultFeeMode.externalised


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
    "Yearn": VaultFeeMode.internalised_skimming,
    "Goat Protocol": VaultFeeMode.internalised_skimming,
    "USDai": VaultFeeMode.internalised_skimming,
    "AUTO Finance": VaultFeeMode.internalised_minting,
    "NashPoint": VaultFeeMode.internalised_skimming,
    "LLAMMA": VaultFeeMode.internalised_skimming,
    "Summer.fi": VaultFeeMode.internalised_minting,
    "Silo Finance": VaultFeeMode.internalised_minting,
}


@dataclass
class FeeData:
    """Track vault fee parameters.

    - Offer methods to calculate gross/net fees based on the vault fee mode
    - `None` means fee unknown: protocol not recognized, or fee data not available
    """

    #: Determines is the vault share price is fees-net or fees-gross
    fee_mode: VaultFeeMode | None

    #: Fee for this class
    management: float | None

    #: Fee for this class
    performance: float | None

    #: Fee for this class
    deposit: float | None

    #: Fee for this class
    withdraw: float | None

    @property
    def internalised(self) -> bool | None:
        if self.fee_mode is None:
            return None

        return self.fee_mode.is_internalised() if self.fee_mode else None

    def get_net_fees(self) -> "FeeData":
        """Get net fees paid by the user on deposit/withdraw.

        - Determined by the vault fee mode
        """
        if self.internalised:
            return FeeData(
                fee_mode=self.fee_mode,
                management=0,
                performance=0,
                deposit=0.0,
                withdraw=0.0,
            )
        else:
            return self


#: Could not read fee data from the smart contract / unsupported protocol
BROKEN_FEE_DATA = FeeData(
    fee_mode=None,
    management=None,
    performance=None,
    deposit=None,
    withdraw=None,
)


def get_vault_fee_mode(vault_protocol_name: str, address: HexAddress | str) -> VaultFeeMode | None:
    """Get vault fee mode by protocol name.

    :return:
        None if unknown
    """
    return VAULT_PROTOCOL_FEE_MATRIX.get(vault_protocol_name)
