.. _aave-v3-funding-rates:

Aave v3 WBTC funding rate history
==================================

Preface
-------

This tutorial shows how to download the full history of WBTC borrow and supply
rates from `Aave v3 <https://tradingstrategy.ai/glossary/aave>`__ on Ethereum
mainnet and save it to a local Parquet file for offline analysis.

Aave tracks interest rates through `ReserveDataUpdated
<https://github.com/aave/aave-v3-core/blob/v1.16.2/contracts/protocol/libraries/logic/ReserveLogic.sol#L31>`__
events.  Each event records three rates for a token reserve:

- **Supply rate** (``liquidity_rate``) — the annualised yield lenders earn.
- **Variable borrow rate** (``variable_borrow_rate``) — the annualised cost
  for borrowers on variable-rate loans.
- **Stable borrow rate** (``stable_borrow_rate``) — the annualised cost for
  borrowers on fixed-rate loans (deprecated in Aave v3.2).

On-chain values are stored as ray-precision unsigned 256-bit integers scaled
by 10²⁷.  The script divides each value by 10²⁷ to obtain a float fraction
and then computes APR and APY percentages using the
`Aave APR/APY formulas <https://docs.aave.com/developers/v/2.0/guides/apy-and-apr>`__.

The script also saves the normalised **liquidity index** and **variable borrow
index** (both starting near 1.0 and growing over time), which are needed to
compute the accrued interest between two points in time:

.. code-block:: text

    accrued_interest = amount × (index_end / index_start − 1)

We use `Envio HyperSync <https://docs.envio.dev/docs/HyperSync/overview>`__ to
stream all matching events in about one minute, compared to hours with a
standard JSON-RPC ``eth_getLogs`` scan.  The result is a Parquet file that
covers the entire Aave v3 history from block 16,291,127 (January 2023) to the
current chain tip.

The script is **resumable**: re-running it fetches only new events since the
last stored block.

About the code
--------------

- :py:class:`AaveRateReader` builds a HyperSync log query that filters by:

  - the Aave v3 pool contract address
  - ``ReserveDataUpdated`` event topic0
  - WBTC reserve address as topic1

- :py:meth:`AaveRateReader.decode_event` converts all raw ray-precision values
  to human-readable floats and computes APR/APY in one pass.

- The streaming loop follows the same pattern as
  :py:class:`~eth_defi.aave_v3.liquidation.AaveLiquidationReader`.

- On the first run the script starts at block ``16_291_127``; on subsequent
  runs it resumes from ``max(block_number) + 1`` in the existing Parquet file.

- Output is saved to ``~/.tradingstrategy/aave/wbtc-rates-ethereum.parquet``
  with columns:
  ``block_number``, ``timestamp``, ``transaction_hash``, ``log_index``,
  ``reserve``,
  ``liquidity_rate``, ``stable_borrow_rate``, ``variable_borrow_rate``
  (float fractions),
  ``liquidity_index``, ``variable_borrow_index`` (normalised floats),
  ``deposit_apr``, ``variable_borrow_apr``, ``stable_borrow_apr``,
  ``deposit_apy``, ``variable_borrow_apy``, ``stable_borrow_apy``
  (percent values).

Running the code
----------------

Set the Ethereum RPC endpoint and optionally a HyperSync API key:

.. code-block:: shell

    export JSON_RPC_ETHEREUM=https://eth-mainnet.example.com/...
    export HYPERSYNC_API_KEY=...           # optional but recommended

    poetry run python scripts/aave-v3/scan-funding-rates.py

After about one minute you will see output like:

.. code-block:: none

    Saved 264,993 rows to /home/user/.tradingstrategy/aave/wbtc-rates-ethereum.parquet (34.06 MiB)

    Most recent 10 WBTC rate events on Aave v3 Ethereum:
      block_number  timestamp         deposit_apr    variable_borrow_apr    stable_borrow_apr
    --------------  ----------------  -------------  ---------------------  -------------------
          24684302  2026-03-18 12:14  0.0042%        0.3290%                0.0000%
          24684321  2026-03-18 12:18  0.0042%        0.3290%                0.0000%
          24684347  2026-03-18 12:23  0.0042%        0.3290%                0.0000%
          24684353  2026-03-18 12:25  0.0042%        0.3290%                0.0000%
          24684363  2026-03-18 12:27  0.0042%        0.3290%                0.0000%
          24684372  2026-03-18 12:28  0.0042%        0.3290%                0.0000%
          24684374  2026-03-18 12:29  0.0042%        0.3290%                0.0000%
          24684378  2026-03-18 12:30  0.0042%        0.3290%                0.0000%
          24684431  2026-03-18 12:40  0.0041%        0.3288%                0.0000%
          24684436  2026-03-18 12:41  0.0041%        0.3286%                0.0000%

    Total rows : 264,993
    Date range : 2023-01-27 — 2026-03-18

Run the script again to fetch only new blocks since the last run:

.. code-block:: shell

    poetry run python scripts/aave-v3/scan-funding-rates.py
    # Already up to date.

Example code
------------

.. literalinclude:: ../../../scripts/aave-v3/scan-funding-rates.py
   :language: python
