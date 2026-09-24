Derive v3 native vaults
-----------------------

The `operator README <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/scripts/derive/README-Derive-vaults.md>`__
describes inspection, storage and shared scanner switches.

Derive v3 vaults are managed subaccounts with native shares, rather than
ERC-4626 contracts. The `Derive v3 vault guide
<https://docs.derive.xyz/vaults/create-a-vault>`__ describes their accounting,
fees and curator role. The public `vault listing
<https://docs.derive.xyz/api-reference/vault-shareholders/publicget_vaults>`__
and `performance history
<https://docs.derive.xyz/api-reference/vault-shareholders/publicget_vault_performance_history>`__
provide discovery, live NAV and sampled share prices without authentication.

Run a testnet scan with::

   DERIVE_V3_NETWORK=testnet poetry run python scripts/derive/scan-v3-vaults.py

``DERIVE_V3_NETWORK=mainnet`` selects the production API. ``DB_PATH`` changes
the DuckDB destination and ``VAULT_IDS`` filters native subaccount IDs. By
default, testnet and mainnet use separate files under
``~/.tradingstrategy/vaults/``. Each observation retains the deployment name
as well. The shared exporter reads mainnet rows only. The collector keeps
source decimal strings and stores the API's daily
performance points without inventing prices when NAV is unavailable. The
public `currency listing
<https://docs.derive.xyz/api-reference/market-data/publicget_all_currencies>`__
resolves each vault's internal deposit-asset address to a symbol and decimal
count, where available. The full public vault record is retained as JSON so
new fee, benchmark or access fields can be inspected without a rescan.

Current Derive-side limits
~~~~~~~~~~~~~~~~~~~~~~~~~~

As checked on 24 September 2026, the public mainnet ``get_vaults`` response
contains no vaults and testnet lists 18. The public
performance endpoint offers 1-hour, 8-hour, 24-hour and weekly samples, with
at most 10,000 points per page. Its documentation calls ``ts`` milliseconds,
but the live testnet returned Unix seconds; the client accepts both response
units and sends pagination bounds in Unix seconds, as the API specifies.
The shared ``First seen`` date uses the earliest stored performance point,
falling back to the first scan when no history exists; it is not an exact
vault creation date.

The `deposit and withdrawal guide
<https://docs.derive.xyz/vaults/deposits-withdrawals>`__ specifies a curator
settlement queue. A shareholder request is an intent, not a completed
deposit or redemption. The curator quotes and settles each request, subject
to a configured cooldown, slippage checks and available vault liquidity.
Derive says withdrawals must be processed within 14 days; delays can put a
vault at risk of being frozen and delisted. The current collector is read-only
and does not submit, cancel or settle these requests.

The `fee guide <https://docs.derive.xyz/vaults/fees>`__ explains that fees
settle as dilutive share mints during deposit and withdrawal settlement. The
historical ``share_price`` is the sampled mark-to-market series; the live
``simulated_share_price_usd`` includes a hypothetical accrued-fee settlement.
They should not be combined into a single history without defining the
accounting basis. Curator-supplied descriptions and whitelist settings also
need separate validation before they are treated as investment facts.
The source also has no ERC-20 vault share token or ERC-4626 deposit manager.
The ``whitelist_only`` field describes account approval separately from the
vault's closed status; an open request still requires curator settlement.

Position transparency shortcoming
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Derive v3 does not expose current vault positions through its public API.
This is a protocol transparency shortcoming: public NAV, share prices and
vault settlement history cannot reveal gross long and short exposure,
position concentration or options risk for an arbitrary vault. The `v3 API
specification <https://docs.derive.xyz/openapi.json>`__ instead provides
``private/get_positions`` and ``private/get_subaccount`` for authenticated
wallet sessions. A curator must delegate access to its vault subaccount,
such as with a `read-only session key
<https://docs.derive.xyz/authentication/access-scopes>`__, before these
positions can be collected. Until then, exposure metrics are unknown and
must remain null rather than zero.

API modules
~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary_derive
   :recursive:

   eth_defi.derive.v3_vaults
   eth_defi.derive.v3_vault_metrics
   eth_defi.derive.v3_vault_data_export
   eth_defi.derive.v3_constants
   eth_defi.derive.tags
