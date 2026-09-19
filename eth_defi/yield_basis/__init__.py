"""YieldBasis Earn market support.

The first integration covers the four reviewed Ethereum yb-LP/LT
markets. It intentionally keeps the protocol-native context separate from
the common vault Parquet schema so USD performance and underlying-asset
performance can be compared without treating either as a guaranteed yield.
"""

from eth_defi.yield_basis.addresses import (
    YIELD_BASIS_ACTIVE_MARKETS,
    YIELD_BASIS_FACTORY,
    YIELD_BASIS_STABLECOIN,
)

__all__ = [
    "YIELD_BASIS_ACTIVE_MARKETS",
    "YIELD_BASIS_FACTORY",
    "YIELD_BASIS_STABLECOIN",
]
