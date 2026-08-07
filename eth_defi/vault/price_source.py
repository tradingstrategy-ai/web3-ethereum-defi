"""Classify how a vault share price is obtained."""

import enum


class PriceSource(str, enum.Enum):
    """Machine-readable source of a vault's share-price observations.

    The classification describes the data path used by the vault adapter, not
    the legal publisher of the underlying fund NAV. Values are stable public
    export strings consumed by vault-data clients. A null value is valid and
    means the adapter has not yet been mapped to a known price source; it does
    not imply that the vault has no price source.
    """

    #: Read directly from historical smart-contract state.
    smart_contract_state = "smart-contract-state"

    #: Read from a protocol or product API.
    api = "api"

    #: Reconstructed or estimated because no authoritative price series exists.
    approximation = "approximation"

    #: Explicitly configured constant share price.
    fixed_price = "fixed-price"

    #: Read from a RedStone oracle or fundamental-value feed.
    redstone = "redstone"

    #: Read from a Chronicle oracle or Proof of Asset feed.
    chronicle = "chronicle"
