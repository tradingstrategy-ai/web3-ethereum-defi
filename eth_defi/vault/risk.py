"""Vault risk classification.

- What are the vault risk levels and how they are classified
- The current data of known protocols
"""

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

    The unverified smart contracts are the biggest red flag, because
    we cannot verify if they match what the audit says (if there is any).
    """

    #: See vault technicak risk matrix documentation.
    negligible = 1

    #: See vault technicak risk matrix documentation.
    minimal = 10

    #: See vault technicak risk matrix documentation.
    low = 20

    #: See vault technicak risk matrix documentation.
    high = 30

    #: See vault technicak risk matrix documentation.
    severe = 40

    #: See vault technicak risk matrix documentation.
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
VAULT_PROTOCOL_RISK_MATRIX = {
    "Euler": VaultTechnicalRisk.negligible,
    "Morpho": VaultTechnicalRisk.negligible,
    "Enzyme": VaultTechnicalRisk.negligible,
    "Lagoon": VaultTechnicalRisk.minimal,
    "Velvet Capital": VaultTechnicalRisk.high,
    "Umami": VaultTechnicalRisk.severe,
    # Unverified contracts, no open source repo
    # https://arbiscan.io/address/0xd15a07a4150b0c057912fe883f7ad22b97161591#code
    "Peapods": VaultTechnicalRisk.dangerous,
    "Ostium": VaultTechnicalRisk.high,
    "Gains": VaultTechnicalRisk.high,
    # No audits
    "Plutus": VaultTechnicalRisk.severe,
    "Harvest Finance": VaultTechnicalRisk.low,
    "D2 Finance": VaultTechnicalRisk.high,
    "Untangle Finance": VaultTechnicalRisk.low,
    "Yearn v3": VaultTechnicalRisk.minimal,
    "Yearn tokenised strategy": VaultTechnicalRisk.minimal,
    "Goat Protocol": VaultTechnicalRisk.low,
    "USDai": VaultTechnicalRisk.low,
    "AUTO Finance": VaultTechnicalRisk.low,
    "NashPoint": VaultTechnicalRisk.low,
}

#: Particular vaults that are broken, misleading or otherwise problematic.
#: Users do not want to interact with these and they cause confusion, so we just drop them from reports.
#:
#: Lower case address mapping to problem vaults
VAULT_SPECIFIC_RISK = {
    # Kitsune
    # https://arbiscan.io/address/0xe5a4f22fcb8893ba0831babf9a15558b5e83446f#code
    "0xe5a4f22fcb8893ba0831babf9a15558b5e83446f": VaultTechnicalRisk.blacklisted,

    # kUSDC
    # https://basescan.org/address/0x43e3e6ffb2e363e64cd480cbb7cd0cf47bc6b477
    "0x43E3E6FFb2E363E64cD480Cbb7cd0CF47bc6b477": VaultTechnicalRisk.blacklisted,
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
