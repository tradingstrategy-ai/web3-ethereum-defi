T3tris API
----------

`T3tris <https://t3tris.finance/>`__ integration.

T3tris is a tokenised vault protocol for professional asset managers. Its
vaults expose ERC-4626 share accounting and add protocol-specific asynchronous
deposit and redemption request flows for closed vault mode.

The adapter detects T3tris vaults through the protocol-specific
``getGrossTVL()`` getter, reads fee data from the live vault ABI, and enriches
vault rows with offchain metadata from the T3tris app API.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/t3tris>`__
- `Homepage <https://t3tris.finance/>`__
- `Vault app <https://app.t3tris.finance/vaults>`__
- `Documentation repository <https://github.com/t3tris-finance/mdoc-t3tris>`__
- `Research notes <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/eth_defi/erc_4626/vault_protocol/t3tris/README-t3tris.md>`__

.. autosummary::
   :toctree: _autosummary_t3tris
   :recursive:

   eth_defi.erc_4626.vault_protocol.t3tris.vault
   eth_defi.erc_4626.vault_protocol.t3tris.offchain_metadata
