"""Vaukt risk classification."""

import enum

from eth_typing import HexAddress


class VaultTechnicalRisk(enum.Enum):
    """Vault risk profile enum.

    This risk profile classification is about the technical risk of the vault.
    Outside technical risk, you have market risk, volatility risk and such risk factors which can be modelled separately using finance best practices.

    - Used to classify vaults by their risk profile
    - How this vault risk compares to other vaults (All vaults are high risk compared to the traditional finance)
    - Having a point of time technical audit does not meaningfully lower the risk, because all systems should be evaluated as a whole
      and have continuous transparency and open source development to be considered low risk.
    """

    #: Fully open sourced and audited vaults with transparent strategies,
    #: all data easily readable onchain. Vouched and promoted by reputable people in the space.
    #: Vaults have robust developer documentation and explanation of APIs, mechanisms and risks.
    #:
    #: E.g. Euler, Morpho, Enzyme, vaults.
    low = 1

    #: The vault is built on the good known protocol like Lagoon and Enzyme, but
    #: includes manual execution, custom smart contracts and permissions by the curator that add to the risk.
    #:
    #: E.g. Lagoon.
    #:
    lowish = 5

    #: No public Github repository to follow the development, but still publishes full source and providers integrator documentation and transparency
    #:
    #:
    high = 20

    #: Only partial source code published.
    #:
    #: No Github repository, not all contracts have been verified.
    #:
    #: E.g. Velvet Capital, Umami.
    #:
    extra_high = 40

    #: The contracts of the protocol do not follow any best practices.
    #:
    #: Example dangerous level red flags include:
    #: - Smart contracts are unverified on blockchain explorers: in the case of website goes down, it's difficult for users to get their funds out or known what's happening
    #: - Not following blockchain development best practices
    #: - Unaudited code
    #: - No Github repository of open development
    #:
    #: E.g. Peapods.
    #:
    dangerous = 50

    #: This vault is blacklisted because it is known not to be "real" in a sense
    #: it is a developer test, using fake stablecoins or tokens, etc.
    #:
    #: By blacklisting vaults, we get them off the reports.
    #:
    blacklisted = 999

    def get_risk_level_name(self) -> str:
        return self.name.replace("_", " ").title()


#: Default classification of vault protocols by their risk profile.
#:
#: See :py:func:`eth_defi.erc_4626.core.get_vault_protocol_name` for the names list.
#:
VAULT_PROTOCOL_RISK_MATRIX = {"Euler": VaultTechnicalRisk.low, "Morpho": VaultTechnicalRisk.low, "Enzyme": VaultTechnicalRisk.low, "Lagoon": VaultTechnicalRisk.lowish, "Velvet Capital": VaultTechnicalRisk.extra_high, "Umami": VaultTechnicalRisk.extra_high, "Peapods": VaultTechnicalRisk.dangerous, "Ostium": VaultTechnicalRisk.high, "Gains": VaultTechnicalRisk.high, "Plutus": VaultTechnicalRisk.dangerous, "Harvest Finance": VaultTechnicalRisk.lowish, "D2 Finance": VaultTechnicalRisk.extra_high, "Untangle Finance": VaultTechnicalRisk.lowish}

#: Particular vaults that are broken, misleading or otherwise problematic.
#: Users do not want to interact with these and they cause confusion, so we just drop them from reports.
#:
#: Lower case address mapping to problem vaults
VAULT_SPECIFIC_RISK = {
    # Kitsune
    # https://arbiscan.io/address/0xe5a4f22fcb8893ba0831babf9a15558b5e83446f#code
    "0xe5a4f22fcb8893ba0831babf9a15558b5e83446f": VaultTechnicalRisk.blacklisted,
}


def get_vault_risk(
    protocol_name: str,
    vault_address: HexAddress | str | None = None,
    default=None,
):
    """Get technical and developer risk associated with a particular vault"""

    if vault_address:
        risk = VAULT_SPECIFIC_RISK.get(vault_address.lower())
        if risk:
            return risk

    return VAULT_PROTOCOL_RISK_MATRIX.get(protocol_name, default)
