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

The resulting reader provides standard ERC-4626 read operations and reviewed,
address-scoped product metadata for BTC and HOOD. The metadata includes a
reviewed snapshot of the deposit fee and profit share published by Arcus's
public pToken vault API without making runtime API requests. It does not
advertise a deposit/redeem manager or binding withdrawal time because those
behaviours have not been certified. The pToken mechanics and automatic
threshold-based rebalancing are described in Arcus's
`pToken product announcement <https://arcus.xyz/blog/ptokens-a-new-primitive-on-arcus>`__.

Every vault detected as part of this contract family automatically receives
the ``directional_leverage`` and ``perpetual_futures`` strategy tags. These
defaults also cover products without an address-specific display overlay.
USDG denomination describes the accounting currency; it does not establish
stablecoin-only economic exposure or a lending strategy.

The cleaner retains the first funded pToken observation and preserves large
leveraged NAV moves instead of replacing them through the generic spike
heuristic. Published absolute returns start at the first valid stored price,
which can be later than deployment. Annualised returns require the full
requested lookback, or 30 days for lifetime CAGR. Sparklines can be published
from two valid observations at distinct timestamps, including on the first day.

All reviewed pTokens return the same unlabelled EOA from ``manager()``. The
library displays Arcus as the protocol-level curator and manager for these
address-scoped products. This attribution comes from the reviewed Arcus
contract-family classification and curator registry; it does not identify the
operator behind the generic ``manager()`` address.

.. autosummary::
   :toctree: _autosummary_arcus
   :recursive:

   eth_defi.erc_4626.vault_protocol.arcus.constants
   eth_defi.erc_4626.vault_protocol.arcus.tags
   eth_defi.erc_4626.vault_protocol.arcus.offchain_data
   eth_defi.erc_4626.vault_protocol.arcus.vault
