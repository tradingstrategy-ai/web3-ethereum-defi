Research, metrics and charts
----------------------------

`eth_defi.research` module contains functions needed for quantitative research of DeFi data

- `OHLCV candle charts <https://tradingstrategy.ai/glossary/ohlcv>`__
- DeFi vault metrics
- K-ratio of regularly sampled wealth and share prices
- Vault benchmarks, correlations and listings
- Vault data clean up ("wrangling")
- Setting up Jupyter notebook rendering modes

Sparkline generation uses a native SVG renderer, Pillow drawing and box
downsampling with fpng encoding for the square PNG, and CairoSVG for the
table PNG.
The scanner publishes all three images to R2 during post-processing. The
exporter reads the daily crypto-vault Parquet and stores per-vault cadence and
retry state in the pipeline data directory. Low-TVL vaults (below 5,000
stablecoin units, 2.5 ETH or 0.1 BTC) are successfully completed at most once
per 72 hours. The standalone benchmark renders samples without reading or
writing publication state.

.. autosummary::
   :toctree: _autosummary_research
   :recursive:

   eth_defi.research.candle
   eth_defi.research.k_ratio
   eth_defi.research.sparkline
   eth_defi.research.sparkline_export
   eth_defi.research.metrics_freshness
   eth_defi.research.value_table
   eth_defi.research.vault_benchmark
   eth_defi.research.vault_correlation
   eth_defi.research.vault_metrics
   eth_defi.research.wrangle_vault_prices
   eth_defi.research.rolling_returns
   eth_defi.research.markdown_table
   eth_defi.research.notebook
