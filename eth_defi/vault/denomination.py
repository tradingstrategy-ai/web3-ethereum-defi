"""Classify vault denomination symbols for the crypto-vault export.

The classifier intentionally relies on the denomination symbol persisted in
the vault database.  Contract addresses are reported by the audit command but
are not an inclusion criterion, so the policy remains explicit and auditable.
"""

import hashlib
import json
from decimal import Decimal
from enum import Enum

from eth_defi.stablecoin_metadata import is_stablecoin_like


class DenominationFamily(str, Enum):
    """Supported denomination families for the crypto-vault bundle."""

    #: USD-stablecoin denomination supported by the public bundle.
    stablecoin = "stablecoin"

    #: ETH-like denomination selected through the reviewed symbol whitelist.
    eth = "eth"

    #: BTC-like denomination selected through the reviewed symbol whitelist.
    btc = "btc"

    #: Symbol outside the supported stablecoin/ETH/BTC policy.
    unsupported = "unsupported"


#: Stable selection and output order for the isolated crypto-vaults bundle.
CRYPTO_DENOMINATION_FAMILY_ORDER = (
    DenominationFamily.stablecoin,
    DenominationFamily.eth,
    DenominationFamily.btc,
)

#: Stable output order for family-level metadata and manifests.
CRYPTO_DENOMINATION_FAMILY_NAMES = tuple(family.value for family in CRYPTO_DENOMINATION_FAMILY_ORDER)


#: Fixed USD guideline price for one stablecoin unit.
STABLECOIN_USD_GUIDELINE_RATE = Decimal("1")

#: Fixed USD guideline price for one ETH-like underlying unit.
ETH_USD_GUIDELINE_RATE = Decimal("2000")

#: Fixed USD guideline price for one BTC-like underlying unit.
BTC_USD_GUIDELINE_RATE = Decimal("60000")


#: Reviewed ETH/BTC denomination symbols mapped to family and wrapper kind.
#:
#: Keep this in Python rather than package data: it is a small, code-owned
#: inclusion policy, not operator-maintained external metadata. Symbols must
#: already be uppercase because lookups normalise only case and whitespace.
DENOMINATION_SYMBOLS: dict[str, tuple[DenominationFamily, str]] = {
    "ETH": (DenominationFamily.eth, "native"),
    "WETH": (DenominationFamily.eth, "wrapped"),
    "WETH.E": (DenominationFamily.eth, "bridged"),
    "STETH": (DenominationFamily.eth, "liquid_staking"),
    "WSTETH": (DenominationFamily.eth, "liquid_staking"),
    "RETH": (DenominationFamily.eth, "liquid_staking"),
    "CBETH": (DenominationFamily.eth, "liquid_staking"),
    "SFRXETH": (DenominationFamily.eth, "liquid_staking"),
    "FRXETH": (DenominationFamily.eth, "liquid_staking"),
    "ANKRETH": (DenominationFamily.eth, "liquid_staking"),
    "SWETH": (DenominationFamily.eth, "liquid_staking"),
    "OSETH": (DenominationFamily.eth, "liquid_staking"),
    "ETHX": (DenominationFamily.eth, "liquid_staking"),
    "EETH": (DenominationFamily.eth, "restaking"),
    "WEETH": (DenominationFamily.eth, "restaking"),
    "EZETH": (DenominationFamily.eth, "restaking"),
    "RSETH": (DenominationFamily.eth, "restaking"),
    "RSWETH": (DenominationFamily.eth, "restaking"),
    "PUFETH": (DenominationFamily.eth, "restaking"),
    "UNIETH": (DenominationFamily.eth, "restaking"),
    "METH": (DenominationFamily.eth, "liquid_staking"),
    "CMETH": (DenominationFamily.eth, "liquid_staking"),
    "YNETH": (DenominationFamily.eth, "yield_bearing"),
    "YNETHX": (DenominationFamily.eth, "yield_bearing"),
    "APXETH": (DenominationFamily.eth, "yield_bearing"),
    "TETH": (DenominationFamily.eth, "protocol_receipt"),
    "WBETH": (DenominationFamily.eth, "liquid_staking"),
    "BETH": (DenominationFamily.eth, "liquid_staking"),
    "SETH2": (DenominationFamily.eth, "liquid_staking"),
    "AWETH": (DenominationFamily.eth, "protocol_receipt"),
    "AETHWETH": (DenominationFamily.eth, "protocol_receipt"),
    "CETH": (DenominationFamily.eth, "protocol_receipt"),
    "CWETH": (DenominationFamily.eth, "protocol_receipt"),
    "BTC": (DenominationFamily.btc, "native"),
    "WBTC": (DenominationFamily.btc, "wrapped"),
    "WBTC.E": (DenominationFamily.btc, "bridged"),
    "CBBTC": (DenominationFamily.btc, "wrapped"),
    "IBTC": (DenominationFamily.btc, "wrapped"),
    "TBTC": (DenominationFamily.btc, "wrapped"),
    "TBTCV2": (DenominationFamily.btc, "wrapped"),
    "FBTC": (DenominationFamily.btc, "bridged"),
    "LBTC": (DenominationFamily.btc, "yield_bearing"),
    "EBTC": (DenominationFamily.btc, "protocol_receipt"),
    "KBTC": (DenominationFamily.btc, "wrapped"),
    "MBTC": (DenominationFamily.btc, "wrapped"),
    "SBTC": (DenominationFamily.btc, "wrapped"),
    "XBTC": (DenominationFamily.btc, "wrapped"),
    "BTCB": (DenominationFamily.btc, "bridged"),
    "BTC.B": (DenominationFamily.btc, "bridged"),
    "RENBTC": (DenominationFamily.btc, "bridged"),
    "HBTC": (DenominationFamily.btc, "wrapped"),
    "OBTC": (DenominationFamily.btc, "wrapped"),
    "UNIBTC": (DenominationFamily.btc, "restaking"),
    "PUMPBTC": (DenominationFamily.btc, "restaking"),
    "SOLVBTC": (DenominationFamily.btc, "yield_bearing"),
    "SOLVBTC.BBN": (DenominationFamily.btc, "restaking"),
    "DLCBTC": (DenominationFamily.btc, "protocol_receipt"),
    "SWBTC": (DenominationFamily.btc, "protocol_receipt"),
    "CLBTC": (DenominationFamily.btc, "protocol_receipt"),
    "VBTC": (DenominationFamily.btc, "protocol_receipt"),
}


def normalise_denomination_symbol(symbol: str | None) -> str | None:
    """Normalise a denomination symbol without heuristic rewriting.

    Only outer whitespace and letter case are normalised.  Bridge suffixes and
    protocol prefixes remain meaningful and must be listed explicitly in the
    reviewed Python dictionary.

    :param symbol:
        Raw vault-metadata denomination symbol.
    :return:
        Uppercase symbol, or ``None`` for a missing/blank value.
    """
    if symbol is None:
        return None
    normalised = symbol.strip().upper()
    return normalised or None


def get_denomination_whitelist_digest() -> str:
    """Calculate the immutable digest of the reviewed symbol whitelist.

    :return:
        SHA-256 hex digest of the canonical Python dictionary content.
    """
    payload = {symbol: {"family": family.value, "wrapper_kind": wrapper_kind} for symbol, (family, wrapper_kind) in DENOMINATION_SYMBOLS.items()}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def classify_denomination(symbol: str | None) -> DenominationFamily:
    """Classify one vault denomination using stablecoin precedence and a dictionary.

    Stablecoin classification intentionally delegates to the existing shared
    stablecoin helper to retain the public export's membership behaviour.

    :param symbol:
        Raw denomination token symbol from vault metadata.
    :return:
        Stablecoin, ETH-like, BTC-like, or unsupported family.
    """
    if symbol is None or not symbol.strip():
        return DenominationFamily.unsupported
    # The established stablecoin helper has case-sensitive aliases such as
    # ``sUSDe`` and ``USDC.e``. Consult it with the original persisted symbol
    # before normalising ETH/BTC whitelist lookups, preserving public output.
    if is_stablecoin_like(symbol):
        return DenominationFamily.stablecoin
    normalised = normalise_denomination_symbol(symbol)
    assert normalised is not None
    entry = DENOMINATION_SYMBOLS.get(normalised)
    return entry[0] if entry is not None else DenominationFamily.unsupported


def get_denomination_wrapper_kind(symbol: str | None) -> str | None:
    """Look up the reviewed wrapper kind for an ETH/BTC-like symbol.

    :param symbol:
        Raw denomination token symbol from vault metadata.
    :return:
        Wrapper kind, or ``None`` for stablecoins and unsupported symbols.
    """
    normalised = normalise_denomination_symbol(symbol)
    entry = DENOMINATION_SYMBOLS.get(normalised) if normalised is not None else None
    return entry[1] if entry is not None else None


def convert_usd_threshold_to_denomination(
    usd_threshold: Decimal,
    denomination_symbol: str,
) -> Decimal:
    """Convert a USD low-TVL guideline to a denomination-family threshold.

    ETH-like and BTC-like wrappers normalise through their reviewed canonical
    underlying and use fixed USD 2,000/ETH and USD 60,000/BTC guideline rates.
    This is stable filtering guidance, not a live valuation oracle.

    :param usd_threshold:
        Positive USD-denominated filtering guideline.
    :param denomination_symbol:
        Vault denomination symbol resolved through the shared classifier.
    :return:
        Threshold in the normalised denomination family unit.
    """
    if usd_threshold < 0:
        message = "USD threshold must not be negative"
        raise ValueError(message)
    family = classify_denomination(denomination_symbol)
    if family is DenominationFamily.stablecoin:
        return usd_threshold / STABLECOIN_USD_GUIDELINE_RATE
    if family is DenominationFamily.eth:
        return usd_threshold / ETH_USD_GUIDELINE_RATE
    if family is DenominationFamily.btc:
        return usd_threshold / BTC_USD_GUIDELINE_RATE
    raise ValueError(f"Unsupported denomination symbol for threshold conversion: {denomination_symbol!r}")
