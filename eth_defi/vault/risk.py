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
    "Lagoon Finance": VaultTechnicalRisk.minimal,
    "IPOR Fusion": VaultTechnicalRisk.minimal,
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
    "Llama Lend": VaultTechnicalRisk.low,
    "Foxify": VaultTechnicalRisk.dangerous,
    "Liquidity Royalty Tranching": None,
    "cSigma Finance": VaultTechnicalRisk.severe,
    "Spark": VaultTechnicalRisk.negligible,
    "Teller": VaultTechnicalRisk.severe,
    "Deltr": VaultTechnicalRisk.dangerous,
    "Upshift": VaultTechnicalRisk.severe,
    "Sky": VaultTechnicalRisk.negligible,
    "Maple": VaultTechnicalRisk.negligible,
    "Centrifuge": VaultTechnicalRisk.negligible,
    "Ethena": VaultTechnicalRisk.negligible,
    "Decentralized USD": VaultTechnicalRisk.severe,
    "Liquidity Royalty Tranching": VaultTechnicalRisk.severe,
    "Term Finance": VaultTechnicalRisk.low,
    # The vault does not give any indication what kind of underlying activity and positions
    # the vault has. Users cannot assess what they are investing in.
    # See https://app.superform.xyz/vault/1_0x942bed98560e9b2aa0d4ec76bbda7a7e55f6b2d6
    "Superform": VaultTechnicalRisk.severe,
    "Royco": None,
    "ETH Strategy": VaultTechnicalRisk.low,
    "Yuzu Money": VaultTechnicalRisk.low,
    "Altura": VaultTechnicalRisk.severe,
    "Spectra": VaultTechnicalRisk.low,
    "Gearbox": VaultTechnicalRisk.low,
    "Mainstreet Finance": None,
    "YieldFi": None,
    "Resolv": None,
    "Curvance": None,
    "Singularity Finance": None,
    "Brink": None,
    "Accountable": VaultTechnicalRisk.severe,
    "YieldNest": VaultTechnicalRisk.low,
    "Dolomite": None,
    # No public GitHub repository for the contract development
    "HypurrFi": VaultTechnicalRisk.severe,
    "Fluid": VaultTechnicalRisk.low,
    "ZeroLend": VaultTechnicalRisk.dangerous,
    "USDX Money": None,
    "Hyperlend": VaultTechnicalRisk.severe,
    "Sentiment": VaultTechnicalRisk.low,
    "infiniFi": None,
    # Unverified smart contract source code
    "Renalta": VaultTechnicalRisk.dangerous,
    # Avant - strategies are not published smart contracts
    "Avant": VaultTechnicalRisk.severe,
    # aarnâ - new protocol, risk not yet assessed
    "aarnâ": None,
    # Yo - YoVault_V2 source was not available on Github, and the development seems not to be transparent
    "Yo": VaultTechnicalRisk.severe,
    # Frax - extensively audited, open source, well-established protocol
    "Frax": VaultTechnicalRisk.low,
    # Hyperdrive - unverified smart contracts, suffered $782k exploit in 2025
    "Hyperdrive": VaultTechnicalRisk.dangerous,
    # BaseVol - options protocol on Base, audited by FailSafe, Diamond proxy architecture
    "BaseVol": VaultTechnicalRisk.severe,
    # sBOLD - audited by ChainSecurity, open source on GitHub
    "sBOLD": VaultTechnicalRisk.low,
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
    # Superform vault - no indication of underlying activity or positions
    # https://app.superform.xyz/vault/1_0x942bed98560e9b2aa0d4ec76bbda7a7e55f6b2d6
    "0x942bed98560e9b2aa0d4ec76bbda7a7e55f6b2d6": VaultTechnicalRisk.blacklisted,
}


def get_vault_risk(
    protocol_name: str,
    vault_address: HexAddress | str | None = None,
    default=None,
) -> VaultTechnicalRisk | None:
    """Get technical and developer risk associated with a particular vault"""

    from eth_defi.vault.flag import BAD_FLAGS, get_vault_special_flags

    # Check for xUSD incidents
    flags = get_vault_special_flags(vault_address)
    if flags & BAD_FLAGS:
        return VaultTechnicalRisk.blacklisted

    if vault_address:
        risk = VAULT_SPECIFIC_RISK.get(vault_address.lower())
        if risk:
            return risk

    return VAULT_PROTOCOL_RISK_MATRIX.get(protocol_name, default)


# Addresses: ['0x249CAccaE4b8A4BC9E0F8e468d7Cc9EbFc7e0811', '0x249CAccaE4b8A4BC9E0F8e468d7Cc9EbFc7e0811', '0x249CAccaE4b8A4BC9E0F8e468d7Cc9EbFc7e0811']... total 3
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
    "0x872dEF0be6A91B212e67BbD56D37B6Cc9513B7B7",
    "0xB591D637cFd989A21e31873dbE64AFa4BF18f169",
    "0x8529019503c5BD707d8Eb98C5C87bF5237F89135",
    "0x9a2d163aB40F88C625Fd475e807Bbc3556566f80",  # Age old mainnet contract
    "0x249CAccaE4b8A4BC9E0F8e468d7Cc9EbFc7e0811",  # Age old mainnet contract
    "0x055ac8b974F075B86fB963e940407168E677585A",  # Age old mainnet contract
    "0x2A0077eD1dF4BE3963b60191011c76DFE0dD4D9b3",  # Age old mainnet contract
    "0x5AF90c9F0f51e918B19A0bE1A0DcD8238bf414A1",  # Age old mainnet contract
    "0xF7709f447AeBC89b31F42BdDb7C4A6caAED6f566",  # Age old mainnet contract
    "0x1DBFCE32a70787002D48B775e774C17B5673Aa4A",  # Age old mainnet contract
    "0xBa74368AA52AD58d08309f1F549aA63bAB0C7e2A",  # Age old mainnet contract
    "0xBa74368AA52AD58d08309f1F549aA63bAB0C7e2A",  # Age old mainnet contract
    "0x8AF4dfc5c55eF2D3BCE511E4C14d631253533540",  # Age old mainnet contract
    "0xEA5E5B5af68C4D03482A79573222400b905b37F9",  # Age old mainnet contract
    "0xe6A4ECFF9f9179b7bbB910c8A3d0Bfd5de55d3AD",  # Age old mainnet contract
    "0xc5a9938F265690e3c904fC37c27d1B6D0Aab8612",  # Age old mainnet contract
    "0x8763e4686ba2fdCd0b71CeE0411100585C875278",  # Age old mainnet contract
    "0x6BA59f481C50027d65F7714Ff51741aa3629a559",  # Age old mainnet contract
    "0x7A164dB771CF55Cc45b0CC9AbF5dbFB8c28860d7",  # Age old mainnet contract
    "0x2A0077eD1dF4BE3963b60191011c76DFE0dD4D9b",  # Age old mainnet contract
    "0x46CF29Dc3472F2EADC17f01152adEa1f068eF20f",  # Age old mainnet contract
    "0x21f01A22c417864b20fc9CCbB9b709ad38a9ea8dE",  # Age old mainnet contract
    "0xacF999bFA9347e8EbE6816eD30bf44b127233177",  # Age old mainnet contract
    "0x0138C6f526546A0DF647e36D42abcEFb868f412a",  # Age old mainnet contract
    "0x88D371D1FD137c272cEA1E871f801456BF8918dF",  # Age old mainnet contract
    "0xF1d402fCbEb2d0C8946F13196D72dB7258B0B296",  # Age old mainnet contract
    "0x4aEa7cf559F67ceDCAD07E12aE6bc00F07E8cf65",  # Age old mainnet contract
    "0x6323A8711180820b834c0295808c188E7F8cD9e7",  # Age old mainnet contract
    "0xCCDaBEaa4C1C54EfAb58484c791428B22083b432",  # Age old mainnet contract
    "0x811C80a9A4782274F036f06834F9bcA2870FfA67",  # Age old mainnet contract
    "0x3Cb822f51283fE165caBD5b9808BF2D8CBb29b9c1",  # Age old mainnet contract
    "0x6323A8711180820b834c0295808c188E7F8cD9e7",  # Age old mainnet contract
    "0x7b183E4De8912f04d9dC94E1F109578d62D4a9f9",  # Age old mainnet contract
    "0x5B63655e93E1d805F770Aa0f98a99d20c091A9fC",  # Age old mainnet contract
    "0x64EFb9BE474C2d69eCAc0A051f2df664e453A0dD", # Age old mainnet contract]
    "0x3DA70c70B9574FF185b31d70878a8E3094603c4c",  # Age old mainnet contract
    "0x6a6E4ad4a5ca14B940Cd6949b1A90f947AE21c19",  # Broken Gains vault on Berachain - its open PnL feed contract (0x5705554B) causes multicall failures
    "0x5705554BAa86Da01fF4A82d29a1598c5B3A8B476",  # Open PnL feed helper contract for broken Gains vault on Berachain
    "0x8fF6aDBC653405245B6b686E31b14A7da7000281",  # BNB broken contract
}

#: Cause excessive gas fees, RPC havoc.
#:
#: Old Ethereum mainnet contracts when revert was not properly existing.
#: Harmless but cause extra RPC load.
#: These fail when we probe contract calls to identify them.
BROKEN_VAULT_CONTRACTS = {addr.lower() for addr in _BROKEN_VAULT_CONTRACTS}
