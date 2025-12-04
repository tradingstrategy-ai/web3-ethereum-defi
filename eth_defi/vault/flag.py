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

#: Vault manual blacklist flags and notes.
#:
#: The reason notes is a guess.
#:
#: Make sure address is lowercased
VAULT_FLAGS_AND_NOTES: dict[str, tuple[VaultFlag, str]] = {
    # Borrowable USDC Deposit, SiloId: 127
    "0x2433d6ac11193b4695d9ca73530de93c538ad18a": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Curve LLAMMA IBTC / crvUSD
    "0xe296ee7f83d1d95b3f7827ff1d08fe1e4cf09d8d": (VaultFlag.illiquid, HIDDEN_VAULT),
    # Silo Finance Borrowable USDC Deposit in ARB Silo
    "0xb739ae19620f7ecb4fb84727f205453aa5bc1ad2": (VaultFlag.illiquid, XUSD_MESSAGE),
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
}

for addr in VAULT_FLAGS_AND_NOTES.keys():
    assert addr.lower() == addr, f"Vault address must be lowercased: {addr}"
