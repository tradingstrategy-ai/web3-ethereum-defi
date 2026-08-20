"""Reviewed Arcus pToken deployment constants."""

from eth_typing import HexAddress

#: Robinhood Chain mainnet chain id.
ARCUS_CHAIN_ID = 4663

#: Bridge vault returned by the reviewed Arcus pTokens.
#:
#: Observed by calling ``bridgeVault()`` at block 34,581,629 on Robinhood
#: Chain. This uncommon pToken accessor is used as the conservative Arcus
#: classifier until a source-verified deployment registry is published.
ARCUS_BRIDGE_VAULT = HexAddress("0xd42c46c7bad6a54b38395f846b09981ce75fb8e2")

#: Reviewed production Arcus BTC (3x Long) pToken vault.
ARCUS_BTC_3X_LONG_VAULT = HexAddress("0x4472c69d299382f8847ebce4fc6ed8e295510e3e")

#: Reviewed production Arcus HOOD (3x Long) pToken vault.
ARCUS_HOOD_3X_LONG_VAULT = HexAddress("0xe24cabdf76dd1c2576049167eb1755c84b985c36")
