"""Vault status flags."""

import enum

from eth_typing import HexAddress


class VaultFlag(str, enum.Enum):
    """Flags indicating the status of a vault."""

    #: We can deposit now
    deposit = "deposit"

    #: We can redeem now
    redeem = "redeem"

    #: Vault is paused
    paused = "paused"

    #: Vault is in trading mode - we can expect vault to generate yield
    trading = "trading"

    #: Vault is not in trading mode - any deposit are unlikely to generate yield right now
    idle = "idle"

    #: Vault is illiquid
    #:
    #: E.g. Stream xUSD episode
    #:
    illiquid = "illiquid"

    #: Vault is broken
    #:
    #: Onchain metrics coming out of it do not make sense
    #:
    broken = "broken"

    #: The contract will steal your money
    malicious = "malicious"

    #: Abnormal TVL
    abnormal_tvl = "abnormal_tvl"

    # Properitary trading
    proprietary_trading = "proprietary_trading"

    #: This vault represents an underlying wrapped asset like a share
    wrapped_asset = "wrapped_asset"

    #: Vault ls missing in the protocol official website and might be a spoof attempt
    unofficial = "unofficial"

    #: Vault has abnormal price behaviour on low TVL
    abnormal_price_on_low_tvl = "abnormal_price_on_low_tvl"

    #: This vault is a subvault used by other vaults
    subvault = "subvault"


#: Don't touch vaults with these flags
BAD_FLAGS = {
    VaultFlag.illiquid,
    VaultFlag.broken,
    VaultFlag.malicious,
    VaultFlag.abnormal_tvl,
    VaultFlag.unofficial,
    VaultFlag.abnormal_price_on_low_tvl,
    VaultFlag.subvault,
}


_empty_set = set()


def get_vault_special_flags(address: str | HexAddress) -> set[VaultFlag]:
    """Get all special vault flags."""
    entry = VAULT_FLAGS_AND_NOTES.get(address.lower())
    if entry:
        if entry[0]:
            return {entry[0]}
    return _empty_set


def get_notes(address: HexAddress | str) -> str | None:
    """Get notes related to the flags."""
    entry = VAULT_FLAGS_AND_NOTES.get(address.lower())
    if entry:
        return entry[1]
    return None


def is_flagged_vault(address: HexAddress | str) -> bool:
    """Is this vault flagged for any special reason?"""
    assert address.startswith("0x"), f"Invalid address: {address}"
    return VAULT_FLAGS_AND_NOTES.get(address) is not None


XUSD_MESSAGE = "Vault likely illiquid due to Stream xUSD exposure issues. You may lose all of your deposits."

HIDDEN_VAULT = "Vault not actively listed on any known website. Likely unmaintained. You may lose your deposits."

BROKEN_VAULT = "Onchain metrics coming out of this vault do not make sense and it's likely the smart contract is broken."

MALICIOUS_VAULT = "This vault is reported as malicious, and may have some sort of mechanism to steal funds."

MAINST_VAULT = "Main Street Market related products were wiped out in Oct 10th event https://x.com/Main_St_Finance/status/1976972055951147194"

ABNORMAL_TVL = "The TVL on this vault is abnormal"

UNKNOWN_VAULT = "Vault is not known, not listed on the website of the protocol"

FOXIFY_VAULT = "Foxify offers perp DEX and funding for proprietary trades. This vault is associated with this activity, but it is not publicly described how the vault works."

PENDLE_LOOPING = "Abnormal high yield due to Pendle looping - more info here https://x.com/ssmccul/status/2006016219275501936"

ZEROLEND_SUPERFORM_WITHDRAW_ONLY = "All ZeroLend vaults on Superform are in withdraw-only mode. Support could not give an answer on why."

LOW_TVL_ABNORMAL_PRICE = "Low-TVL vault with abnormal price behaviour"

MISSING_IN_PROTOCOL_FRONTEND = "This vault is missing in the protocol's primary website and cannot be verified."

SUBVAULT = "This vault is likely not intended to be directly exposed to the end users. It may be used by other vaults as a part of the strategy mix and has erratic TVL."

YIELDNEST_YNRWAX = """ynRWAx: Tokenized Australian residential real estate credit earning 11% APY, allocated to mortgage-backed loans on verified house-and-land developments. Made safe in collaboration with a fully licensed and insured fund manager, [Kimber Capital](https://kimbercapital.au/) (AFS Licence No. 425278).

Fees: 0%.

Fixed Maturity Date: 15 Oct, 2026.

Although the vault has long lock up matching the duration of the underlying real-world asset instrument, [the share token can be traded against the secondary liquidity available at Curve DEX](https://www.curve.finance/dex/ethereum/pools/factory-stable-ng-650/swap).
"""

ETH_STRATEGY_ESPN = """ESPN (ETH Strategy Perpetual Note) lends USDS to ETH Strategy, but instead of receiving interest, ESPN receives a long-dated ETH call option. To extract yield from this long-dated call option, ESPN systematically sells shorter-dated call options on [Derive](https://www.derive.xyz/). The symmetry between the long-dated convertibles acquired and short-dated calls sold keeps the strategy balanced in USD terms. 

[Discussion about the ESPN vault](https://x.com/TradingProtocol/status/2011043276283900198).
"""

#: Vault manual blacklist flags and notes.
#:
#: The reason notes is a guess.
#:
#: Make sure address is lowercased
VAULT_FLAGS_AND_NOTES: dict[str, tuple[VaultFlag | None, str]] = {
    # Borrowable USDC Deposit, SiloId: 127
    "0x2433d6ac11193b4695d9ca73530de93c538ad18a": (VaultFlag.illiquid, XUSD_MESSAGE),
    # https://tradingstrategy.ai/trading-view/sonic/vaults/borrowable-xusd-deposit-siloid-112
    "0x172a687c397e315dbe56ed78ab347d7743d0d4fa": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Llama Lend IBTC / crvUSD
    "0xe296ee7f83d1d95b3f7827ff1d08fe1e4cf09d8d": (VaultFlag.illiquid, HIDDEN_VAULT),
    # Silo Finance Borrowable USDC Deposit in ARB Silo
    "0xb739ae19620f7ecb4fb84727f205453aa5bc1ad2": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable scUSD Deposit, SiloId: 125
    "0x0ab02dd08c1555d1a20c76a6ea30e3e36f3e06d4": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Exposure to Elixir
    "0x94643e86aa5e38ddac6c7791c1297f4e40cd96c1": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Exposure to xUSD - Silos
    "0x3014ed70b39be395e1a5eb8ab4c4b8a5378e6522": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x1de3ba67da79a81bc0c3922689c98550e4bd9bc2": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x672b77f0538b53dc117c9ddfeb7377a678d321a6": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0xe0fc62e685e2b3183b4b88b1fe674cfec55a63f7": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x9c4d4800b489d217724155399cd64d07eae603f3": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0xa1627a0e1d0ebca9326d2219b84df0c600bed4b1": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0xacb7432a4bb15402ce2afe0a7c9d5b738604f6f9": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x1320382143d98a80a0b247148a42dd2aa33d9c2d": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0xed9777944a2fb32504a410d23f246463b3f40908": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x61ffbead1d4dc9ffba35eb16fd6cadee9b37b2aa": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x8399c8fc273bd165c346af74a02e65f10e4fd78f": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0xac69cfe6bb269cebf8ab4764d7e678c3658b99f2": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x55555815a5595991c3a0ff119b59aef6c8b55555": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x36e2aa296e798ca6262dc5fad5f5660e638d5402": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x27968d36b937dcb26f33902fa489e5b228b104be": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x6030ad53d90ec2fb67f3805794dbb3fa5fd6eb64": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x7184bea7743ccfbe390f9cd830095a13ef867941": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x2f5dc399b1e31f9808d1ef1256917abd2447c74f": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable USDC Deposit, SiloId: 55, Sonic
    "0x4935fadb17df859667cc4f7bfe6a8cb24f86f8d0": (VaultFlag.illiquid, XUSD_MESSAGE),
    # EVK Vault eUSDC-1, Sonic
    "0x9ccf74e64922d8a48b87aa4200b7c27b2b1d860a": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Frontier Yala USDC
    "0x481d4909d7ca2eb27c4975f08dce07dbef0d3fa7": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Frontier mMEV USDC
    "0x98281466abcf48eaad8c6e22dedd18a3426a93b4": (VaultFlag.illiquid, XUSD_MESSAGE),
    # AvantgardeUSDC Core
    "0x5b56f90340dbaa6a8693dadb141d620f0e154fe6": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable USDC Deposit, SiloId: 23
    "0x5954ce6671d97d24b782920ddcdbb4b1e63ab2de": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable scUSD Deposit, SiloId: 118
    "0xb1412442aa998950f2f652667d5eba35fe66e43f": (VaultFlag.illiquid, XUSD_MESSAGE),
    # MEV Capital scUSD
    "0xb38d431e932fea77d1df0ae0dfe4400c97e597b8": (VaultFlag.illiquid, XUSD_MESSAGE),
    # MEV Capital USDC
    "0x196f3c7443e940911ee2bb88e019fd71400349d9": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable USDC Deposit, SiloId: 170
    "0x7786dba2a1f7a4b0b7abf0962c449154c4f2b8ac": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Valamor aUSDC/USDC/USDT etc.
    # https://x.com/VarlamoreCap/status/1986290754688541003
    "0x3d7b0c3997e48fa3fc96cd057d1fb4e5f891835b": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0xf6f87073cf8929c206a77b0694619dc776f89885": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x2ba39e5388ac6c702cb29aea78d52aa66832f1ee": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x4dc1ce9b9f9ef00c144bfad305f16c62293dc0e8": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x6c09bfdc1df45d6c4ff78dc9f1c13af29eb335d4": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x9a1bf5365edbb99c2c61ca6d9ffad0b705acfc6f": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Euler Re7
    "0xaba9d2d4b6b93c3dc8976d8eb0690cca56431fe4": (VaultFlag.illiquid, XUSD_MESSAGE),
    # K3
    "0xe1a62fdcc6666847d5ea752634e45e134b2f824b": (VaultFlag.unofficial, MISSING_IN_PROTOCOL_FRONTEND),
    # Excellion USDC Vault
    "0xb8a14b03900828f863aedd9dd905363863bc31f4": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Spectra ERC4626 Wrapper: MEV USDC
    "0x92fbb58342164546325602588599b05802c69bbe": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Greenhouse USD ghUSDC
    # https://x.com/Main_St_Finance/status/1976972055951147194
    "0xf6bc16b79c469b94cdd25f3e2334dd4fee47a581": (VaultFlag.illiquid, MAINST_VAULT),
    # Aarna atvPTmax
    # atvPTmax
    "0xd24e4a98b5fd90ff21a9cc5e2c1254de8084cd81": (VaultFlag.broken, BROKEN_VAULT),
    "0x9deb2b3593eb4e1838b233d386a9358448f753e3": (VaultFlag.broken, BROKEN_VAULT),
    "0x332e81368daec705612ff06b3a80b10ae1e5f110": (VaultFlag.broken, BROKEN_VAULT),
    # Yearn USDC to USDS Depositor strategy
    "0x39c0aec5738ed939876245224afc7e09c8480a52": (VaultFlag.broken, BROKEN_VAULT),
    # Peapods broken 42?
    "0x4b5c90dc6bc08a10a24487726e614e9d148362e1": (VaultFlag.broken, BROKEN_VAULT),
    # Mithras
    "0x391b3f70e254d582588b27e97e48d1cfcdf0be7e": (VaultFlag.broken, BROKEN_VAULT),
    # BlueChip USDC Vault (Prime)
    "0x3f604074f3f12ff70c29e6bcc9232c707dc4d970": (VaultFlag.broken, BROKEN_VAULT),
    # Peapods 14
    "0xc2810eb57526df869049fbf4c541791a3255d24c": (VaultFlag.broken, BROKEN_VAULT),
    # Pendle
    "0xd6e094faf9585757f879067ce79c7f6b3c8e4fb0": (VaultFlag.broken, BROKEN_VAULT),
    "0x64fcfd84109768136a687ed9614a9d0b8c6910e2": (VaultFlag.broken, BROKEN_VAULT),
    "0xd87598dd895de1b7fb2ba6af91b152f26baf7bee": (VaultFlag.broken, BROKEN_VAULT),
    # Yield optimizer vault
    "0x3bb60eca398f480f4b7756600c04309de486232e": (VaultFlag.broken, BROKEN_VAULT),
    # Malicious Euler vault?
    # EVK Vault eUSDC-8 on Sonic
    "0x683dbc88b371ae48962b56e36e5a0c34e3ad4caf": (VaultFlag.malicious, MALICIOUS_VAULT),
    # Broken vault?
    # http://localhost:5173/trading-view/vaults/stablecoins/iusd
    "0x36585e7ae4b8a422135618a2c113b8b516067e7a": (VaultFlag.broken, BROKEN_VAULT),
    # Broken vault?
    # Upshift Edge USDC
    "0xeaa3b922e9febca37d1c02d2142a59595094c605": (VaultFlag.broken, BROKEN_VAULT),
    # Velvet USD coin
    "0xe83522f0882493844c48add97ef03281040e3d2d": (VaultFlag.broken, BROKEN_VAULT),
    # Abnormal TVLs
    "0x10019c629aa7c51e3853286b1c7894b17c257e00": (VaultFlag.abnormal_tvl, BROKEN_VAULT),
    "0x21b92610c69c889b6ca972a973f637e9f10885b3": (VaultFlag.abnormal_tvl, ABNORMAL_TVL),
    "0x8bce54605f56f2f711d9b60bdf2433aae8a14aa5": (VaultFlag.abnormal_tvl, ABNORMAL_TVL),
    "0xbcf722b41ff6f2f932721582680ed0116292cc28": (VaultFlag.abnormal_tvl, ABNORMAL_TVL),
    # USDC BaseInvaders
    "0xd1468af648565f11393e4033cb0cd270b62495c9": (VaultFlag.abnormal_tvl, UNKNOWN_VAULT),
    # Peapods Interest Bearing USDC - 17
    "0xeee75954eded526ef98a0cecc027beee4586315e": (VaultFlag.broken, BROKEN_VAULT),
    # Pendle yield vault
    "0x8977aafd34323fa046f51f3c913a30caa7dd17db": (VaultFlag.broken, BROKEN_VAULT),
    # Foxify vault
    "0x3ccff8c929b497c1ff96592b8ff592b45963e732": (VaultFlag.proprietary_trading, FOXIFY_VAULT),
    # KUSDT
    # http://localhost:5173/trading-view/binance/vaults/gtrade-kusdt
    # No idea what's this - unverified
    "0x4f04cb32688ea1954e53c85b846597881ebe9582": (VaultFlag.broken, BROKEN_VAULT),
    # Steakhouse High Yield USDT0 on Arbitrum
    # https://tradingstrategy.ai/trading-view/arbitrum/vaults/steakhouse-high-yield-usdt0
    "0x4739e2c293bdcd835829aa7c5d7fbdee93565d1a": (None, PENDLE_LOOPING),
    # Static RWA ZeroLend USDC
    "0x942bed98560e9b2aa0d4ec76bbda7a7e55f6b2d6": (VaultFlag.illiquid, ZEROLEND_SUPERFORM_WITHDRAW_ONLY),
    # Euler MEV Capital USDC
    "0xa446938b0204aa4055cdfed68ddf0e0d1bab3e9e": (VaultFlag.unofficial, MISSING_IN_PROTOCOL_FRONTEND),
    # Rezerve USDC
    "0xc42d337861878baa4dc820d9e6b6c667c2b57e8a": (VaultFlag.unofficial, MISSING_IN_PROTOCOL_FRONTEND),
    # YieldNest ynRWAx vault on Ethereum - fixed maturity date 15 Oct 2026
    "0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8": (None, YIELDNEST_YNRWAX),
    # Supply USDC on ZeroLend RWA Market
    "0x887d57a509070a0843c6418eb5cffc090dcbbe95": (None, ZEROLEND_SUPERFORM_WITHDRAW_ONLY),
    # Re7 USDC (Euler on Sonic)
    "0xf75ae954d30217b4ee70dbfb33f04162aa3cf260": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # Mainstreet Liquidity Vault (Euler on Sonic)
    "0x5b63bd1574d40d98c6967047f0323cc5d4895775": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # Braindead Digital USDC (Euler on Sonic)
    "0x3710b212b39477df2deaadcf16ef56c384a3d142": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # ymevUSDC (Yearn on Avalanche)
    "0x7aca67a6856bf532a7b2dea9b20253f08bc9a85a": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # Hemi Clearstar USDC.e
    "0x05c2e246156d37b39a825a25dd08d5589e3fd883": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # https://tradingstrategy.ai/trading-view/vaults/lusd-coin-2
    "0x0ddb1ea478f8ef0e22c7706d2903a41e94b1299b": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # https://tradingstrategy.ai/trading-view/vaults/ltether-usd-4
    "0x4c8e1656e042a206eef7e8fcff99bac667e4623e": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # Harvest: USDC Vault (0x0F6d)
    "0x0f6d1d626fd6284c6c1c1345f30996b89b879689": (VaultFlag.subvault, SUBVAULT),
    # Morpho OEV-boosted USDC Compounder
    "0x888239ffa9a0613f9142c808aa9f7d1948a14f75": (VaultFlag.subvault, SUBVAULT),
    # Morpho Gauntlet USDC Prime Compounder
    "0x694e47afd14a64661a04eee674fb331bcdef3737": (VaultFlag.subvault, SUBVAULT),
    # Morpho Gauntlet USDC Prime Compounder
    "0x694e47afd14a64661a04eee674fb331bcdef3737": (VaultFlag.subvault, SUBVAULT),
    # Aave V3 USDS Lender
    "0xd144eaff17b0308a5154444907781382398aac61": (VaultFlag.subvault, SUBVAULT),
    # AaveV3 USDC.e Lender
    "0x85968bf0f1f110c707fef10a59f80118f349c058": (VaultFlag.subvault, SUBVAULT),
    # Curve Boosted crvUSD-sfrxUSD Lender
    "0xf91a9a1c782a1c11b627f6e576d92c7d72cdd4af": (VaultFlag.subvault, SUBVAULT),
    # dgnHYPE (D2 Finance on Arbitrum)
    "0x64167cd42859f64cff2aa4b63c3175ccef9659dd": (VaultFlag.subvault, SUBVAULT),
    # Convex crvUSD-sfrxUSD Lender
    "0x7a26c6c1628c86788526efb81f37a2ffac243a98": (VaultFlag.subvault, SUBVAULT),
    # USDC Fluid Lender
    "0x00c8a649c9837523ebb406ceb17a6378ab5c74cf": (VaultFlag.subvault, SUBVAULT),
    # ETH Strategy Perpetual Note (Ethereum)
    "0xb250c9e0f7be4cff13f94374c993ac445a1385fe": (None, ETH_STRATEGY_ESPN),
    # Apostro aprUSDC (Sonic)
    "0xcca902f2d3d265151f123d8ce8fdac38ba9745ed": (VaultFlag.unofficial, MISSING_IN_PROTOCOL_FRONTEND),
}

for addr in VAULT_FLAGS_AND_NOTES.keys():
    assert addr.lower() == addr, f"Vault address must be lowercased: {addr}"
