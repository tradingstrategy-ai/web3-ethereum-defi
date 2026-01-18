Sentiment API
-------------

`Sentiment protocol <https://www.sentiment.xyz/>`__ integration.

Sentiment is a decentralised onchain lending protocol that enables users to programmatically lend and borrow
digital assets on Ethereum and L2 chains. The protocol features isolated pools for risk management,
allowing lenders to choose their risk exposure, with externalized risk management controlled by third-party operators.

The protocol uses a SuperPool architecture where deposits are aggregated across multiple underlying
lending pools. SuperPools are ERC-4626 compliant vault aggregators that manage deposits with configurable
allocation strategies. Fees are taken from interest earned and new shares are minted to the fee recipient.

Links
~~~~~

- `Homepage <https://www.sentiment.xyz/>`__
- `Documentation <https://docs.sentiment.xyz/>`__
- `GitHub <https://github.com/sentimentxyz/protocol-v2>`__
- `Twitter <https://x.com/sentimentxyz>`__
- `Audits <https://github.com/sentimentxyz/protocol-v2/tree/master/audits>`__
- `DefiLlama <https://defillama.com/protocol/sentiment>`__

Fees
~~~~

Sentiment SuperPools have a configurable fee that is taken from interest earned. The fee is expressed as a
value out of 1e18, where 1e18 represents 100%. The fee is internalised and new shares are minted to the
fee recipient when interest accrues.

.. autosummary::
   :toctree: _autosummary_sentiment
   :recursive:

   eth_defi.erc_4626.vault_protocol.sentiment.vault
