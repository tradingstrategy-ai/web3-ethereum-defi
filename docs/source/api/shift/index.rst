Shift API
=========

`Shift <https://shiftprotocol.xyz/>`__ custom non-ERC-4626 vault support.

Shift is a DeFi vault integration. Admission follows the contract's live
allow-list setting independently of oracle and executor settlement. Legacy
``eth_defi.tokenised_fund.shift`` module paths remain compatibility imports.

Shift valuations expose the last published NAV and share price. Historical
rows carry the oracle publication time separately from the sampled block time.
On 25 November 2026 the ltLLP oracle quote expired and the contract getter
returned zero. The scanner now reads the published feed directly so expiry
keeps the last observed price and its original timestamp.

.. autosummary::
   :toctree: _autosummary_shift
   :recursive:

   eth_defi.erc_4626.vault_protocol.shift.constants
   eth_defi.erc_4626.vault_protocol.shift.descriptions
   eth_defi.erc_4626.vault_protocol.shift.vault
   eth_defi.erc_4626.vault_protocol.shift.historical
