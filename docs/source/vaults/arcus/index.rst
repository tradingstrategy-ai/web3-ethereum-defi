Arcus
=====

`Arcus <https://arcus.xyz/>`__ publishes Stock Token and perpetual-futures
markets on Robinhood Chain across equities, crypto, commodities and indices.

The library detects the reviewed Arcus pToken wrapper family only on Robinhood
Chain. Detection is deliberately narrow: it calls the single ``bridgeVault()``
selector and requires the observed Arcus bridge-vault address. The reviewed
`BTC (3x Long) <https://robinhoodchain.blockscout.com/address/0x4472C69d299382F8847ebCE4FC6Ed8e295510E3e>`__
and `HOOD (3x Long) <https://robinhoodchain.blockscout.com/address/0xe24CABDf76DD1c2576049167eB1755C84b985C36>`__
contracts return the same bridge-vault value. Both are BeaconProxy contracts;
their current implementation is not source-verified.

The resulting reader provides standard ERC-4626 read operations only. It does
not advertise a deposit/redeem manager, a binding withdrawal time, or a
management/performance fee because those behaviours have not been certified.
The product labels alone do not prove leverage maintenance, rebalancing, fees,
or redemption terms. The reader uses small, reviewed address-scoped copy for
BTC and HOOD and does not use Arcus's unrelated exchange-market API as a pToken
data or NAV source.

All reviewed pTokens return the same unlabelled EOA from ``manager()``. The
library therefore attributes them to Arcus as protocol-curated products, rather
than creating an unsupported third-party curator identity. This attribution is
limited to the reviewed Arcus contract family; it is not derived from that
generic accessor.

.. autosummary::
   :toctree: _autosummary_arcus
   :recursive:

   eth_defi.erc_4626.vault_protocol.arcus.constants
   eth_defi.erc_4626.vault_protocol.arcus.offchain_data
   eth_defi.erc_4626.vault_protocol.arcus.vault
