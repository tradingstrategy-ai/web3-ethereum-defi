Perp DEX vault account metrics
==============================

Native perpetual DEX vaults use a common account-observation pipeline. Each
collector stores the smallest set of source facts needed to reproduce exposure:
one optional account equity observation and one signed current quote notional
for each non-zero open position. It intentionally does not collect cross-margin,
portfolio-margin, allocated margin, leverage, liquidation or order information.

Data flow and Parquet contract
------------------------------

The protocol collector writes immutable account and position observations plus a
whitelisted raw payload to its native DuckDB database. Native post-processing
uses the shared temporal join to attach the latest observation to each raw price
row. If a daily or delayed price source has not emitted a row at the collection
time, a bounded generic alignment step may attach the newest observation to only
that account's latest price row. The generic bound is 48 hours: Lighter's
daily API can remain at the previous UTC midnight until the following day.
The original measurement timestamp is never changed. The cleaner keeps the same fields in
``cleaned-vault-prices-1h.parquet`` and the JSON export adds the derived values
under ``other_data.perp_dex``. JSON never reads a protocol DuckDB database.

The cleaned fields are ``perp_long_notional``, ``perp_short_notional``,
``perp_open_position_count``, ``perp_largest_position_notional``,
``perp_quote_asset``, ``perp_position_data_status`` and
``perp_metrics_observed_at``. The timestamp uses one-second resolution, which is
sufficient for account-level metrics, and is always exported alongside the
values. Gross notional, net notional and largest-position concentration are
derived by consumers from these fundamental fields:
``gross = long + short``, ``net = long - short`` and
``concentration = largest / gross`` when gross is non-zero.
Maximum position leverage and deployed-cash percentage cannot be reproduced
reliably without protocol-specific margin allocation facts, so they remain
explicitly out of scope.

An ordinary observation is joined backwards by effective time. The bounded
latest-row alignment described above handles feeds such as Lighter whose newest
daily price row is timestamped at UTC midnight. An observation is marked
``stale`` after six hours, but its numeric values are retained with
``perp_metrics_observed_at`` so consumers can apply their own freshness policy.
The production scheduler collects Hyperliquid and Lighter every four hours, so
the six-hour boundary allows one delayed cycle without presenting older data as
fresh. A transient ``source_error`` has no measured position state and is not
aligned backwards over a valid as-of observation.
An explicit unavailable source state leaves every numeric position field null;
it never represents a zero-position portfolio. Raw and cleaned Parquet files
containing these metrics embed the immutable protocol-capability registry used
to collect them.

Protocol availability
---------------------

=================  =======================  ==================  ==========================
Protocol           Public account equity    Public positions    Position availability
=================  =======================  ==================  ==========================
Hyperliquid        ``clearinghouseState``   Yes                 ``available``
Lighter            ``/api/v1/account``      Yes                 ``available``
Pacifica           Unsupported              Unsupported         Unsupported
GRVT               public vault TVL         No                  ``authentication_required``
Hibachi            public vault TVL         No                  ``not_public``
ApeX               public ranking TVL       No                  ``authentication_required``
=================  =======================  ==================  ==========================

Pacifica is unsupported and is not registered as a production source. Parser
groundwork remains behind a TODO until its scanner, DuckDB database, native
price export and mark/position timestamp-skew validation are implemented.
`Hyperliquid's clearinghouse state
<https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint>`__
and the `Lighter account API
<https://apidocs.lighter.xyz/reference/account-1>`__ expose the public current
position-value fields used by the supported adapters. The source timestamp and
valuation basis are retained in DuckDB for auditability; only the common
derived fields are materialised in Parquet.
