Monthly vault report API
------------------------

Automates the monthly `best-performing stablecoin vaults <https://tradingstrategy.ai/blog>`__
blog post: vault rankings, HTML tables, PNG charts and a
`Ghost <https://ghost.org/docs/admin-api/>`__ draft post stub that an editor
completes with the monthly news.

- Vault metrics come from the public top vaults JSON export, so the report
  matches the `live vault dashboard <https://tradingstrategy.ai/trading-view/vaults>`__
- Share price history for the charts comes from the Pro vault dataset download
- The previous report is read with the Ghost Content API to carry over
  evergreen sections, and the draft is created with the Ghost Admin API

Run it with ``scripts/erc-4626/generate-monthly-vault-report.py``. See
``eth_defi/vault_report/README-vault-report.md`` for the operator guide.

.. autosummary::
   :toctree: _autosummary_vault_report
   :recursive:

   eth_defi.vault_report.data
   eth_defi.vault_report.sections
   eth_defi.vault_report.charts
   eth_defi.vault_report.post
   eth_defi.vault_report.ghost
   eth_defi.vault_report.report
