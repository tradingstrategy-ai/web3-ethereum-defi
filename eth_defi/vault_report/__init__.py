"""Monthly best-performing stablecoin vault report.

Automates the monthly "The best-performing stablecoin vaults" blog post
published on `Trading Strategy blog <https://tradingstrategy.ai/blog>`__:
data loading, vault rankings, tables, charts and a Ghost draft post stub.
The editor writes the news and commentary in Ghost Admin and publishes.

The report reads the same public top vaults export as the website's vault
pages, so the numbers in the post match what readers see there.

Modules, in pipeline order:

- :py:mod:`eth_defi.vault_report.data`: top vaults JSON, price Parquet,
  share price and weekly TVL history, sparkline availability
- :py:mod:`eth_defi.vault_report.sections`: vault groups, eligibility,
  rankings, average yields, TVL changes and table HTML
- :py:mod:`eth_defi.vault_report.benchmarks`: US Treasury bill, BTC and ETH benchmarks
- :py:mod:`eth_defi.vault_report.vault_checks` and
  :py:mod:`eth_defi.vault_report.vault_probes`: the optional LLM agent
  investability check of the top lists
- :py:mod:`eth_defi.vault_report.charts`, :py:mod:`eth_defi.vault_report.branding`,
  :py:mod:`eth_defi.vault_report.theme` and :py:mod:`eth_defi.vault_report.logos`:
  branded chart images
- :py:mod:`eth_defi.vault_report.post` and :py:mod:`eth_defi.vault_report.podcasts`: the post HTML
- :py:mod:`eth_defi.vault_report.ghost`: Ghost Content and Admin API clients
- :py:mod:`eth_defi.vault_report.report`: the orchestration

See ``eth_defi/vault_report/README-vault-report.md`` for the pipeline
description, ``README-blog-post-outline.md`` for the post structure and
selection rules, and ``scripts/erc-4626/generate-monthly-vault-report.py``
for the command line entry point.
"""
