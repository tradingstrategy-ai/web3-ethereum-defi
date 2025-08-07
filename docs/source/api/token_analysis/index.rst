Token analysis API
------------------

This is Python documentation for high-level token analysis API integrations.

Functionality includes:

- Checking if an ERC-20 token is a scam, honeypot or similar

Supported analytic backends

- Supports `Token Risk from Hexen <https://hexens.io/solutions/token-risks-api>`__
- Supports TokenSniffer from Solidly

.. autosummary::
   :toctree: _autosummary_token_analysis
   :recursive:

   eth_defi.token_analysis.tokenrisk
   eth_defi.token_analysis.tokensniffer
   eth_defi.token_analysis.base
   eth_defi.token_analysis.blacklist

