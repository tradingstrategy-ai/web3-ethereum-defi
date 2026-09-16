DEX aggregator router decoding
------------------------------

Identify which DEX aggregator routed a swap transaction and decode the user's
slippage bound from the router calldata, using a ``debug_traceTransaction``
call path. Used for execution-quality research on proprietary AMMs.

.. autosummary::
   :toctree: _autosummary_dex_aggregator
   :recursive:

   eth_defi.dex_aggregator.router_calldata
