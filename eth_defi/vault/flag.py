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


_empty_set = set()


def get_vault_special_flags(address: str | HexAddress) -> set[VaultFlag]:
    """Get all special vault flags."""
    entry = VAULT_FLAGS_AND_NOTES.get(address)
    if entry:
        return entry[0]
    return _empty_set


def get_notes(address: HexAddress | str) -> str | None:
    """Get notes related to the flags."""
    entry = VAULT_FLAGS_AND_NOTES.get(address)
    if entry:
        return entry[1]
    return None


XUSD_MESSAGE = "Vault likely illiquid due to Stream xUSD exposure issues. You may lose all of your deposits."

HIDDEN_VAULT = "Vault not actively listed on any known website. Likely unmaintained. You may lose your deposits."

BROKEN_VAULT = "Onchain metrics coming out of this vault do not make sense and it's likely the smart contract is broken."

#: Vault manual blacklist flags and notes.
#:
#: The reason notes is a guess.
#:
#: Make sure address is lowercased
VAULT_FLAGS_AND_NOTES: dict[str, tuple[VaultFlag, str]] = {
    # Borrowable USDC Deposit, SiloId: 127
    "0x2433d6ac11193b4695d9ca73530de93c538ad18a": (VaultFlag.illiquid, XUSD_MESSAGE),
    # https://tradingstrategy.ai/trading-view/sonic/vaults/borrowable-xusd-deposit-siloid-112
    "0x172a687c397e315dbe56ed78ab347d7743d0d4fa": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Curve LLAMMA IBTC / crvUSD
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
    # atvPTmax
    "0xd24e4a98b5fd90ff21a9cc5e2c1254de8084cd81": (VaultFlag.broken, BROKEN_VAULT),
    # Yearn USDC to USDS Depositor strategy
    "0x39c0aec5738ed939876245224afc7e09c8480a52": (VaultFlag.broken, BROKEN_VAULT),
    # Peapods broken 42?
    "0x4b5c90dc6bc08a10a24487726e614e9d148362e1": (VaultFlag.broken, BROKEN_VAULT),
}

for addr in VAULT_FLAGS_AND_NOTES.keys():
    assert addr.lower() == addr, f"Vault address must be lowercased: {addr}"
