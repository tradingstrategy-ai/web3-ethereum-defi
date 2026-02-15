Circle CCTP V2 API
------------------

`Circle CCTP <https://developers.circle.com/cctp>`__ (Cross-Chain Transfer Protocol) V2 integration
for cross-chain native USDC transfers using burn-and-mint.

This module provides:

- Cross-chain USDC transfer initiation (``depositForBurn``)
- Attestation polling from Circle's Iris API
- Message relaying on the destination chain (``receiveMessage``)
- Guard whitelisting helpers for Lagoon vault integration

.. autosummary::
   :toctree: _autosummary_cctp
   :recursive:

   eth_defi.cctp.constants
   eth_defi.cctp.transfer
   eth_defi.cctp.attestation
   eth_defi.cctp.receive
   eth_defi.cctp.whitelist
