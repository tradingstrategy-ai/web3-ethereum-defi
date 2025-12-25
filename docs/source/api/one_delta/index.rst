1delta API
-----------

This is Python documentation for high-level `1delta protocol <https://1delta.io/>`_ APIs.

Functionality includes:

- Opening and closing short positions, utilizing Aave v3 lending pool.
- Supply and withdraw collateral to/from Aave v3 lending pool.

Getting started

- See :py:func:`eth_defi.one_delta.deployment.fetch_deployment` to get started

- See :py:func:`eth_defi.one_delta.position.open_short_position` how to open your first leveraged trading position

- See unit tests for more examples

.. autosummary::
   :toctree: _autosummary_1delta
   :recursive:

   eth_defi.one_delta.constants
   eth_defi.one_delta.deployment
   eth_defi.one_delta.position
   eth_defi.one_delta.price
   eth_defi.one_delta.lending
   eth_defi.one_delta.utils

