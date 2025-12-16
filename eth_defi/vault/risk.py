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
    "IPOR": VaultTechnicalRisk.minimal,
    "Velvet Capital": VaultTechnicalRisk.high,
    "Umami": VaultTechnicalRisk.severe,
    # Unverified contracts, no open source repo
    # https://arbiscan.io/address/0xd15a07a4150b0c057912fe883f7ad22b97161591#code
    "Peapods": VaultTechnicalRisk.dangerous,
    "Ostium": VaultTechnicalRisk.high,
    "gTrade": VaultTechnicalRisk.high,
    # No audits
    "Plutus": VaultTechnicalRisk.severe,
    "Harvest Finance": VaultTechnicalRisk.low,
    "D2 Finance": VaultTechnicalRisk.high,
    "Untangle Finance": VaultTechnicalRisk.low,
    "Yearn": VaultTechnicalRisk.minimal,
    "Goat Protocol": VaultTechnicalRisk.low,
    "USDai": VaultTechnicalRisk.low,
    "AUTO Finance": VaultTechnicalRisk.low,
    "NashPoint": VaultTechnicalRisk.low,
    "Silo Finance": VaultTechnicalRisk.low,
    "Summer.fi": VaultTechnicalRisk.low,
    "LLAMMA": VaultTechnicalRisk.low,
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
    "0x43e3e6ffb2e363e64cd480cbb7cd0cf47bc6b477": VaultTechnicalRisk.blacklisted,

    # Savings GYD
    # Protocol no longer active?
    # https://x.com/TradingProtocol/status/1999448052034076863
    "0xea50f402653c41cadbafd1f788341db7b7f37816": VaultTechnicalRisk.blacklisted,

    # Yearn ARB/USDC.e silo strategy vault
    # 100% utilisation on Silo V1, likely cooked
    # https://gov.yearn.fi/t/what-is-the-status-of-silo-lender-arb-usdc-e/14572
    # "0x9fa306b1f4a6a83fec98d8ebbabedff78c407f6b": VaultTechnicalRisk.low,
}


def get_vault_risk(
    protocol_name: str,
    vault_address: HexAddress | str | None = None,
    default=None,
):
    """Get technical and developer risk associated with a particular vault"""

    from eth_defi.vault.flag import get_vault_special_flags, VaultFlag

    # Check for xUSD incidents
    flags = get_vault_special_flags(vault_address)
    if VaultFlag.illiquid in flags:
        return VaultTechnicalRisk.blacklisted

    if vault_address:
        risk = VAULT_SPECIFIC_RISK.get(vault_address.lower())
        if risk:
            return risk

    return VAULT_PROTOCOL_RISK_MATRIX.get(protocol_name, default)


# Multicall3 is 0xcA11bde05977b3631167028862bE2a173976CA11

_BROKEN_VAULT_CONTRACTS = {
    "0x7994157F0c9E6199B15e480FdAcf702aC4F6d8bB",
    "0xc3C12A9E63E466a3ba99E07f3EF1F38b8B81AE1B",
    "0x89567EA00650df98604cd09cDfaC630Cf492e4aB",
    "0xA2851Ec1cF891D138087baC4c06e53499E76B7cB",
    "0x4B6Ddb08E3cA085dD52266e7FD8Ec91010f6F8B5",
    "0xE1746AA4c9489AcABaB5E5fcfe154A8CD8F40edf",
    "0xeC65d776A9624e1186FabE988280b2f8E13bBf80",
    "0x390066ab82d71c644fADD977c7E0d3A839B80250",
    "0x916a3F321834386DD02dAFd1AbC1AA610Bc2507C",
    "0x061Dcb33bba38B2337eb450b89683F0522B1535f",
    "0xd1C54A7896e52D3337A0Acaa41dEA4c66504Eb18",
    "0x2a7C424a06E1483ceb8F895afACf9561F0786a77",
    "0xDB212BB6dD0c9CBC9Fc0c5FFE88Be35b81CBeB92",
    "0x061Dcb33bba38B2337eb450b89683F0522B1535f",
    "0x33e6ea47f3Dcf45A36FE3e9be1cb9e155D946202",
    "0xf36Fb419A6Bd6eBfe8A16797519deA43e164Ca70",
    "0x37743836B6011D0655cF6608044C705571417371",
    "0x6df33A763e416889724E8913717710CC7c31d8c7",
    "0xE18898c76a39ba4Cd46a544b87ebe1166fbe7052",
    "0xAA29BC726a2E2807aA1d4d79CA610f3e52295d8C",
    "0xc6493bC0b9ebDfE45aEcDA36c4783E1C892c8d99",
    "0xf921798598877baF6808B944413d9A4d6EE15087",
    "0xc5138D4Bd0eC5C51b6B6bDFCB8528aD9c333af97",
    "0x41A5B8Aa081dCD69AcE566061D5B6AdCb92CaE1c",
    "0x71829ed960594f5e764B9854c89A308c70500432",
    "0x9419FBEcFA0a9E38A96bE2d818DAA13dcA72396E",
    "0xbB2Ee36248da67c87777f43Aa19e6158bb319fC7",
    "0xc5138D4Bd0eC5C51b6B6bDFCB8528aD9c333af97",
    "0xc6b330dF38D6eF288C953F1F2835723531073CE2",
    "0x6Ea91B94BcA202851CCBB73ff4a16a9F879EF414",
    "0xcb804B9ceB413C3940134B1e2a022402F3b35d50",
    "0x58383879cEc79368EA71EA96dbbF9625F031F08e",
    "0x145c56886E2D51f25845624342b461F1b02E8423",
    "0x2CcC36852446aD785385D9e4446D4BbEb1cb1BF8",
}

#: Cause excessive gas fees, RPC havoc.
#:
#: Old Ethereum mainnet contracts when revert was not properly existing.
#: Harmless but cause extra RPC load.
#: These fail when we probe contract calls to identify them.
BROKEN_VAULT_CONTRACTS = {addr.lower() for addr in _BROKEN_VAULT_CONTRACTS}
