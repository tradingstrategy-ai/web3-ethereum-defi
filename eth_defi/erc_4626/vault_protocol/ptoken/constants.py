"""Reviewed pToken vault deployment constants."""

from typing import Final

from eth_typing import HexAddress

#: Robinhood Chain mainnet chain id.
PTOKEN_CHAIN_ID: Final = 4663

#: Reviewed BTC 3x Long pToken vault.
PTOKEN_BTC_3X_LONG_VAULT = HexAddress("0x4472c69d299382f8847ebce4fc6ed8e295510e3e")

#: Reviewed HOOD 3x Long pToken vault.
PTOKEN_HOOD_3X_LONG_VAULT = HexAddress("0xe24cabdf76dd1c2576049167eb1755c84b985c36")

#: Address-scoped production pToken deployments.
#:
#: The issuer has not been identified. Do not widen this registry from the
#: shared Arcus USDG deposit proxy: infrastructure use is not product ownership.
PTOKEN_VAULTS: Final[frozenset[HexAddress]] = frozenset(
    {
        PTOKEN_BTC_3X_LONG_VAULT,
        PTOKEN_HOOD_3X_LONG_VAULT,
    },
)
