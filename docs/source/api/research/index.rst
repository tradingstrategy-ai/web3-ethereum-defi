Research, metrics and charts
----------------------------

`eth_defi.research` module contains functions needed for quantitative research of DeFi data

- `OHLCV candle charts <https://tradingstrategy.ai/glossary/ohlcv>`__
- DeFi vault metrics
- Vault benchmarks, correlations and listings
- Vault data clean up ("wrangling")
- Setting up Jupyter notebook rendering modes

Sparkline generation uses deterministic native SVG and Pillow PNG renderers.
The batch exporter consumes the daily crypto-vault Parquet, publishes both
formats to R2, and keeps per-vault cadence and retry state in the pipeline data
directory. Low-TVL vaults (below 5,000 stablecoin units, 2.5 ETH or 0.1 BTC)
are successfully completed at most once per 72 hours; the standalone
benchmark renders samples without reading or writing publication state.

.. autosummary::
   :toctree: _autosummary_research
   :recursive:

   eth_defi.research.candle
   eth_defi.research.sparkline
   eth_defi.research.sparkline_export
   eth_defi.research.value_table
   eth_defi.research.vault_benchmark
   eth_defi.research.vault_correlation
   eth_defi.research.vault_metrics
   eth_defi.research.wrangle_vault_prices
   eth_defi.research.rolling_returns
   eth_defi.research.markdown_table
   eth_defi.research.notebook
