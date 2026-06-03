Core3 risk intelligence API
---------------------------

`Core3 <https://core3.io/>`__ risk intelligence integration for tracking
Probability of Loss (PoL) scores across crypto projects.

Core3 (formerly CER.live) provides risk ratings across six categories:
security, financial, operational, reputational, regulatory, and dependency.
This module fetches and stores risk data in a local DuckDB database for
historical tracking.

Features:

- Rate-limited API session with retry logic
- DuckDB storage with incremental sync (two-phase backfill + ranged updates)
- Parallel project scanning with ``joblib``
- Raw JSON payload storage for future re-extraction

.. autosummary::
   :toctree: _autosummary_core3
   :recursive:

   eth_defi.core3.session
   eth_defi.core3.database
   eth_defi.core3.scanner
   eth_defi.core3.constants
