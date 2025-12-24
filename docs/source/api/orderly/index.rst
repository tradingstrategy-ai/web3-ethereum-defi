Orderly API
-----------

Preface
~~~~~~~

Orderly is a shared orderbook `perpetual futures <https://tradingstrategy.ai/glossary/perpetual-future>`__ `DEX <https://tradingstrategy.ai/glossary/decentralised-exchange>`__ with multiple "frontends" called Orderly clients.

  Orderly is a combination of an orderbook-based trading infrastructure and a robust liquidity layer offering perpetual futures orderbooks. Unlike traditional platforms, Orderly doesn’t have a front end; instead, it operates at the core of the ecosystem, providing essential services to projects built on top of it.

You can acces the same Orderly order book through different frontends with different fee arrangements. Orderly is "multichain" meaning you can make deposits to their centralised hot wallet from any blockchain, including non-EVM chains like Near.

Orderly offers "smart contract trading" with a complex functionality called delegate signer:

- `Orderly and delegate signer explanation <https://orderly.network/docs/build-on-omnichain/user-flows/delegate-signer#smart-contract-trading>`__

More

- `Orderly changelog <https://orderly.network/docs/changelog/evm>`__
- `Docs <https://orderly.network/docs/home>`__
- `Omnivault (Orderly market making vault run by Kronos) <https://orderly.network/docs/introduction/orderly-omniVault/overview>`__
- `Orderly native strategy vaults <https://orderly.network/docs/strategy-vault/strategy-vault/public/vault-info>`__

Purpose
~~~~~~~

The main goal of this API is to manage deposits and withdrawals into Orderly from `vaults <https://tradingstrategy.ai/glossary/vault>`__.

The actual trading on Orderly happens using `CCXT connector <https://tradingstrategy.ai/glossary/ccxt>`__ like one for `Mode perps <https://www.mode.network/ecosystem>`__ frontend.

Orderly offers its own unique way of managing user deposits and creating smart contract compatible trading API keys.

.. autosummary::
   :toctree: _autosummary_orderly
   :recursive:

   eth_defi.orderly.api
   eth_defi.orderly.vault
   eth_defi.orderly.constants

