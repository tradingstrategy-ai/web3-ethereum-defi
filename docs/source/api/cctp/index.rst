Circle CCTP V2 API
------------------

`Circle CCTP <https://developers.circle.com/cctp>`__ (Cross-Chain Transfer Protocol) V2 integration
for cross-chain native USDC transfers using burn-and-mint.

This module provides:

- Cross-chain USDC transfer initiation (``depositForBurn``)
- Attestation polling from Circle's Iris API
- Message relaying on the destination chain (``receiveMessage``)
- Guard whitelisting helpers for Lagoon vault integration
- Fork testing helpers for crafting CCTP messages and bypassing attestation

For additional details on the CCTP V2 integration, including message format specification,
fork testing approach, testnet configuration, and security considerations, see the
`CCTP integration guide <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/eth_defi/cctp/README-cctp.md>`__.

.. autosummary::
   :toctree: _autosummary_cctp
   :recursive:

   eth_defi.cctp.constants
   eth_defi.cctp.transfer
   eth_defi.cctp.attestation
   eth_defi.cctp.receive
   eth_defi.cctp.whitelist
   eth_defi.cctp.testing
