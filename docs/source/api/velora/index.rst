Velora API
----------

Introduction
============

Velora (formerly ParaSwap) is a DEX aggregator that aggregates liquidity across multiple decentralised exchanges.
It provides optimal swap routes and executes trades atomically.

Unlike CoW Swap which uses an offchain order book and presigning, Velora executes swaps atomically
in a single transaction by calling the Augustus Swapper contract directly with calldata from the Velora API.

Key differences from CoW Swap:

- **Atomic execution**: Swaps execute in a single transaction (no offchain order book)
- **Simpler integration**: No presigning or order polling required
- **Market API**: Uses the Market API (not Delta API) for Safe multisig compatibility

Technicals
==========

`eth_defi` provides Velora integration for smart contracts and vaults through the Velora Market API.
See :py:mod:`eth_defi.erc_4626.vault_protocol.lagoon.velora` for details on how to perform
`Lagoon vault <https://tradingstrategy.ai/glossary/lagoon>`__ automated trading with Velora.

Swap flow:

1. Fetch quote from Velora API (GET /prices)
2. Build swap transaction from Velora API (POST /transactions/:network)
3. Approve TokenTransferProxy via vault's performCall()
4. Execute swap via swapAndValidateVelora() on TradingStrategyModuleV0

Links:

- `Velora developer documentation <https://developers.velora.xyz>`__
- `Twitter <https://x.com/paraswap>`__

.. warning::

   When approving tokens, approve the TokenTransferProxy contract, NOT the Augustus Swapper.
   Funds may be lost if approved to Augustus directly.

.. autosummary::
   :toctree: _autosummary_velora
   :recursive:

   eth_defi.velora.api
   eth_defi.velora.constants
   eth_defi.velora.quote
   eth_defi.velora.swap
