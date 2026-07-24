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
uses the shared backward-only temporal join to attach the latest observation to
each raw price row. The cleaner keeps the same fields in
``cleaned-vault-prices-1h.parquet`` and the JSON export adds the derived values
under ``other_data.perp_dex``. JSON never reads a protocol DuckDB database.

The cleaned fields are ``perp_long_notional``, ``perp_short_notional``,
``perp_open_position_count``, ``perp_largest_position_notional``,
``perp_quote_asset``, ``perp_position_data_status`` and
``perp_metrics_observed_at``. Gross notional, net notional and largest-position
concentration are derived by consumers from these fundamental fields:
``gross = long + short``, ``net = long - short`` and
``concentration = largest / gross`` when gross is non-zero.

An observation is only joined to price rows at or after its observation time
and is marked ``stale`` after six hours. An explicit unavailable source state
leaves every numeric position field null; it never represents a zero-position
portfolio. Raw and cleaned Parquet files containing these metrics embed the
immutable protocol-capability registry used to collect them.

Protocol availability
---------------------

=================  =======================  ==================  ==========================
Protocol           Public account equity    Public positions    Position availability
=================  =======================  ==================  ==========================
Hyperliquid        ``clearinghouseState``   Yes                 ``available``
Lighter            ``/api/v1/account``      Yes                 ``available``
Pacifica           Parser prepared          Not yet routed      Not exported yet
GRVT               public vault TVL         No                  ``authentication_required``
Hibachi            public vault TVL         No                  ``not_public``
ApeX               public ranking TVL       No                  ``authentication_required``
=================  =======================  ==================  ==========================

Pacifica has a tested public parser that values positions using the same-cycle
public mark price, but it is not registered as a production source until its
scanner, DuckDB database and native price export are wired into this path.
Hyperliquid and Lighter use their public current position-value field. The
source timestamp and valuation basis are retained in DuckDB for auditability;
only the common derived fields are materialised in Parquet.
