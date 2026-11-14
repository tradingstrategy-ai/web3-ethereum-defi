T3tris API
----------

`T3tris <https://t3tris.finance/>`__ integration.

T3tris is a tokenised vault protocol for professional asset managers. Its
vaults expose ERC-4626 share accounting and add protocol-specific asynchronous
deposit and redemption request flows for closed vault mode.

The adapter detects T3tris vaults through the protocol-specific
``getGrossTVL()`` getter, reads fee data from the live vault ABI, and enriches
vault rows with offchain metadata from the T3tris app API.

:py:class:`~eth_defi.erc_4626.vault_protocol.t3tris.deposit_redeem.T3trisDepositManager`
implements the closed-mode request, settlement and claim lifecycle, and
``GuardV0.whitelistT3tris()`` admits its four calls for guarded custody.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/vaults/protocols/t3tris>`__
- `Homepage <https://t3tris.finance/>`__
- `Vault app <https://earn.t3tris.finance/vaults>`__
- `Developer documentation <https://docs.t3tris.finance/>`__
- `Research notes <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/eth_defi/erc_4626/vault_protocol/t3tris/README-t3tris.md>`__

.. autosummary::
   :toctree: _autosummary_t3tris
   :recursive:

   eth_defi.erc_4626.vault_protocol.t3tris.vault
   eth_defi.erc_4626.vault_protocol.t3tris.offchain_metadata
