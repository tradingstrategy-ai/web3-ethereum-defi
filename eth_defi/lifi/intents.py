"""LI.FI Intents API (not yet implemented).

LI.FI offers two separate API systems for cross-chain transfers:

Classic API (``li.quest/v1``)
    The established API used by :py:mod:`eth_defi.lifi.quote` and
    :py:mod:`eth_defi.lifi.crosschain`. The user receives a pre-built
    transaction from ``GET /v1/quote``, signs it, and broadcasts it
    directly to the blockchain. Supports 40+ chains with comprehensive
    token coverage.

Intents API (``order.li.fi``)
    A newer solver-marketplace model built on the Open Intents Framework
    (ERC-7683). Instead of executing a pre-built transaction, the user
    locks funds in a resource lock (escrow) contract and submits an
    intent. Multiple solvers compete to fulfil the order, potentially
    offering better pricing and faster settlement since solvers can
    front capital before cross-chain settlement completes.

    Key benefits over the classic API:

    - Competitive pricing via solver marketplace
    - Quotes include a ``validUntil`` timestamp (the classic API does not)
    - Potentially faster execution (solvers front capital)
    - ERC-7683 / OIF standards-based

    **Current limitations (as of March 2026):**

    - Only 3 chains supported: Ethereum, Arbitrum, Base
    - Limited token pairs: USDC-USDC, USDT-USDT, ETH-ETH cross-chain;
      USDC-ETH, USDT-ETH same-chain
    - Requires a different execution flow (escrow deposit, order
      submission, solver fulfilment) incompatible with the classic
      sign-and-broadcast model
    - No API key required for bridge operations; key only needed for
      solver-side operations

    Because of the limited chain and token support, the Intents API is
    not yet suitable for our cross-chain gas feeding use case which
    targets chains like Monad, Hyperliquid, and Avalanche. The classic
    API is not being deprecated — LI.FI positions Intents as an
    additive option.

    When the Intents API expands its chain coverage, this module can be
    implemented to provide an alternative execution path.

See also:

- `LI.FI Intents documentation <https://docs.li.fi/lifi-intents/introduction>`__
- `LI.FI Intents API reference <https://order.li.fi/docs>`__
- `LI.FI classic API reference <https://docs.li.fi/api-reference/introduction>`__
"""
