"""Classify vault denomination symbols for the crypto-vault export.

The classifier intentionally relies on the denomination symbol persisted in
the vault database.  Contract addresses are reported by the audit command but
are not an inclusion criterion, so the policy remains explicit and auditable.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from strictyaml import load

from eth_defi.stablecoin_metadata import is_stablecoin_like


class DenominationFamily(str, Enum):
    """Supported denomination families for the crypto-vault bundle."""

    stablecoin = "stablecoin"
    eth = "eth"
    btc = "btc"
    unsupported = "unsupported"


#: Stable selection and output order for the isolated crypto-vaults bundle.
CRYPTO_DENOMINATION_FAMILY_ORDER = (
    DenominationFamily.stablecoin,
    DenominationFamily.eth,
    DenominationFamily.btc,
)

#: Families selected by the isolated crypto-vaults cleaner.
CRYPTO_DENOMINATION_FAMILIES = frozenset(CRYPTO_DENOMINATION_FAMILY_ORDER)

#: Stable output order for family-level metadata and manifests.
CRYPTO_DENOMINATION_FAMILY_NAMES = tuple(family.value for family in CRYPTO_DENOMINATION_FAMILY_ORDER)


@dataclass(frozen=True, slots=True)
class DenominationWhitelistEntry:
    """One reviewed ETH-like or BTC-like denomination symbol.

    The entry carries display-only wrapper metadata in addition to the family.
    It deliberately does not contain addresses: classification must remain
    symbol based across chains and wrapper deployments.

    :param symbol:
        Uppercase, whitespace-normalised denomination symbol.
    :param family:
        ETH-like or BTC-like family.
    :param canonical_underlying:
        Canonical native asset used for fixed guideline conversions.
    :param wrapper_kind:
        Human-readable token wrapper category.
    :param display_name:
        Optional display label for metadata consumers.
    """

    #: Uppercase reviewed symbol.
    symbol: str

    #: ETH-like or BTC-like family.
    family: DenominationFamily

    #: Canonical native underlying, ``ETH`` or ``BTC``.
    canonical_underlying: str

    #: Wrapper category such as ``wrapped`` or ``liquid_staking``.
    wrapper_kind: str

    #: Optional human-readable display name.
    display_name: str | None = None


#: Fixed USD guideline price for one stablecoin unit.
STABLECOIN_USD_GUIDELINE_RATE = Decimal("1")

#: Fixed USD guideline price for one ETH-like underlying unit.
ETH_USD_GUIDELINE_RATE = Decimal("2000")

#: Fixed USD guideline price for one BTC-like underlying unit.
BTC_USD_GUIDELINE_RATE = Decimal("60000")


def normalise_denomination_symbol(symbol: str | None) -> str | None:
    """Normalise a denomination symbol without heuristic rewriting.

    Only outer whitespace and letter case are normalised.  Bridge suffixes and
    protocol prefixes remain meaningful and must be listed explicitly in the
    reviewed YAML whitelist.

    :param symbol:
        Raw vault-metadata denomination symbol.
    :return:
        Uppercase symbol, or ``None`` for a missing/blank value.
    """
    if symbol is None:
        return None
    normalised = symbol.strip().upper()
    return normalised or None


def _get_whitelist_path() -> Path:
    """Return the packaged denomination-whitelist location.

    :return:
        Repository/package data path for the YAML whitelist.
    """
    return Path(__file__).parents[1] / "data" / "crypto_assets" / "denomination-symbols.yaml"


def get_denomination_whitelist_digest() -> str:
    """Calculate the immutable digest of the reviewed symbol whitelist.

    :return:
        SHA-256 hex digest of the YAML source consumed by this process.
    """
    return hashlib.sha256(_get_whitelist_path().read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def load_denomination_whitelist() -> dict[str, DenominationWhitelistEntry]:
    """Load and validate the reviewed ETH/BTC symbol whitelist.

    The loader rejects duplicate symbols and accidental stablecoin overlaps at
    process startup.  This turns policy mistakes into a direct configuration
    error instead of letting precedence silently hide an entry.

    :return:
        Mapping of normalised symbols to validated entries.
    """
    raw_data: dict[str, Any] = load(_get_whitelist_path().read_text(encoding="utf-8")).data
    if raw_data.get("schema_version") != "1":
        raise ValueError(f"Unsupported denomination whitelist schema: {raw_data.get('schema_version')!r}")

    result: dict[str, DenominationWhitelistEntry] = {}
    for family_name in (DenominationFamily.eth.value, DenominationFamily.btc.value):
        family = DenominationFamily(family_name)
        entries = raw_data.get(family_name)
        if not isinstance(entries, dict):
            raise ValueError(f"Whitelist {family_name!r} entries must be a mapping")
        for raw_symbol, raw_entry in entries.items():
            symbol = normalise_denomination_symbol(raw_symbol)
            if symbol is None:
                raise ValueError(f"Whitelist {family_name!r} contains a blank symbol")
            if symbol in result:
                raise ValueError(f"Whitelist symbol {symbol!r} appears in both ETH and BTC families")
            if is_stablecoin_like(symbol):
                raise ValueError(f"Whitelist symbol {symbol!r} conflicts with stablecoin classification")
            if not isinstance(raw_entry, dict):
                raise ValueError(f"Whitelist entry {symbol!r} must be a mapping")
            canonical_underlying = str(raw_entry.get("canonical_underlying", family.value)).upper()
            expected_underlying = "ETH" if family is DenominationFamily.eth else "BTC"
            if canonical_underlying != expected_underlying:
                raise ValueError(f"Whitelist entry {symbol!r} has invalid canonical underlying {canonical_underlying!r}")
            wrapper_kind = raw_entry.get("wrapper_kind")
            if not isinstance(wrapper_kind, str) or not wrapper_kind:
                raise ValueError(f"Whitelist entry {symbol!r} needs wrapper_kind")
            display_name = raw_entry.get("display_name")
            if display_name is not None and not isinstance(display_name, str):
                raise ValueError(f"Whitelist entry {symbol!r} display_name must be a string")
            result[symbol] = DenominationWhitelistEntry(
                symbol=symbol,
                family=family,
                canonical_underlying=canonical_underlying,
                wrapper_kind=wrapper_kind,
                display_name=display_name,
            )
    return result


def classify_denomination(symbol: str | None) -> DenominationFamily:
    """Classify one vault denomination using stablecoin precedence and YAML.

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
    entry = load_denomination_whitelist().get(normalised)
    return entry.family if entry is not None else DenominationFamily.unsupported


def get_denomination_whitelist_entry(symbol: str | None) -> DenominationWhitelistEntry | None:
    """Look up reviewed wrapper metadata for an ETH/BTC-like symbol.

    :param symbol:
        Raw denomination token symbol from vault metadata.
    :return:
        Reviewed entry, or ``None`` for stablecoins and unsupported symbols.
    """
    normalised = normalise_denomination_symbol(symbol)
    return load_denomination_whitelist().get(normalised) if normalised is not None else None


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
