#!/usr/bin/env python3
"""Scan ERC-4626 vaults across all supported chains.

- Scan vaults and optionally prices for multiple chains
- Track success/failure status per chain
- Retry failed chains automatically
- Display live console dashboard
- Write detailed logs
- Run post-processing after all chains complete
- Supports looped mode with per-chain/protocol cycle intervals

Usage:

.. code-block:: shell

    # Scan all chains (vaults only, no prices) — single run
    python scripts/erc-4626/scan-vaults-all-chains.py

    # Scan all chains with prices
    SCAN_PRICES=true python scripts/erc-4626/scan-vaults-all-chains.py

    # Include Hyperliquid native (Hypercore) vaults
    SCAN_HYPERCORE=true python scripts/erc-4626/scan-vaults-all-chains.py

    # Include GRVT native vaults
    SCAN_GRVT=true python scripts/erc-4626/scan-vaults-all-chains.py

    # Include both Ethereum and Robinhood Lighter native pools
    SCAN_LIGHTER=true python scripts/erc-4626/scan-vaults-all-chains.py

    # Include Hibachi native vaults
    SCAN_HIBACHI=true python scripts/erc-4626/scan-vaults-all-chains.py

    # Include ApeX native vaults
    SCAN_APEX=true python scripts/erc-4626/scan-vaults-all-chains.py

    # Core3 risk intelligence enrichment runs by default when CORE3_API_KEY is set.
    # Disable it explicitly if needed.
    SKIP_CORE3=true python scripts/erc-4626/scan-vaults-all-chains.py

    # Custom retry count
    RETRY_COUNT=2 python scripts/erc-4626/scan-vaults-all-chains.py

    # Test mode - scan only specific chains (comma-separated)
    TEST_CHAINS=Berachain,Gnosis python scripts/erc-4626/scan-vaults-all-chains.py

    # Test mode without post-processing
    TEST_CHAINS=Berachain,Gnosis SKIP_POST_PROCESSING=true python scripts/erc-4626/scan-vaults-all-chains.py

    # Disable specific chains (skip them)
    DISABLE_CHAINS=Plasma,Katana python scripts/erc-4626/scan-vaults-all-chains.py

    # Looped mode: tick every 1h, major EVM chains on 8h, native protocols on 4h, Core3 and rest on 24h
    LOOP_INTERVAL_SECONDS=3600 \\
    SCAN_CYCLES="Ethereum=8h,Base=8h,Arbitrum=8h,Hypercore=4h,GRVT=4h,Lighter Ethereum=4h,Lighter Robinhood=4h,Hibachi=4h,ApeX=4h,Core3=24h" \\
    DEFAULT_CYCLE=24h \\
    SCAN_HYPERCORE=true SCAN_GRVT=true SCAN_LIGHTER=true SCAN_HIBACHI=true SCAN_APEX=true \\
    python scripts/erc-4626/scan-vaults-all-chains.py

Manual testing:

.. code-block:: shell

    # Test with Berachain and Gnosis (fast chains for testing)
    # Make sure you have set up .local-test.env with RPC URLs
    source .local-test.env && \\
    TEST_CHAINS=Berachain,Gnosis \\
    SKIP_POST_PROCESSING=true \\
    MAX_WORKERS=20 \\
    LOG_LEVEL=info \\
    poetry run python scripts/erc-4626/scan-vaults-all-chains.py

    # Test with prices enabled
    source .local-test.env && \\
    TEST_CHAINS=Berachain,Gnosis \\
    SCAN_PRICES=true \\
    SKIP_POST_PROCESSING=true \\
    MAX_WORKERS=20 \\
    LOG_LEVEL=info \\
    poetry run python scripts/erc-4626/scan-vaults-all-chains.py

    # Test retry logic with intentionally bad RPC (will fail and retry)
    source .local-test.env && \\
    TEST_CHAINS=Gnosis \\
    RETRY_COUNT=2 \\
    SKIP_POST_PROCESSING=true \\
    JSON_RPC_GNOSIS=http://invalid-rpc-url \\
    poetry run python scripts/erc-4626/scan-vaults-all-chains.py

Environment variables:
    - SCAN_PRICES: "true" or "false" (default: "false")
    - SCAN_HYPERCORE: "true" to scan Hyperliquid native (Hypercore) vaults via REST API (default: "false")
    - SCAN_GRVT: "true" to scan GRVT native vaults via public endpoints (default: "false")
    - SCAN_LIGHTER: "true" to scan both Ethereum and Robinhood Lighter native pools via public endpoints (default: "false")
    - SCAN_HIBACHI: "true" to scan Hibachi native vaults via public endpoints (default: "false")
    - SCAN_APEX: "true" to scan ApeX native vaults and due history via public endpoints (default: "false")
    - SCAN_VAULT_SETTLEMENTS: "false" to skip per-chain Lagoon and D2 settlement event scanning.
      When enabled, events are stored in vault-settlements.duckdb before price cleaning;
      vault_settlement_at is merged into the cleaned price frame during cleaning (default: "true")
    - VAULT_SETTLEMENT_START_BLOCK: Optional inclusive settlement scan start block for forced backfills.
    - VAULT_SETTLEMENT_END_BLOCK: Optional inclusive settlement scan end block for forced backfills.
    - SKIP_CORE3: "true" to skip Core3 risk intelligence enrichment (default: "false").
      Core3 is default-on enrichment data for the top-vaults JSON, unlike optional native
      vault sources that use opt-in SCAN_* flags.
    - CORE3_API_KEY: Core3 API key. If missing, Core3 is disabled for the run with a warning.
    - CORE3_DATABASE_PATH: Path to Core3 DuckDB (default: ~/.tradingstrategy/vaults/core3/core3.duckdb)
    - CORE3_MAX_WORKERS: Number of Core3 API worker threads (default: "8")
    - CORE3_FETCH_SECTIONS: "false" to skip detailed Core3 section endpoints (default: "true")
    - RETRY_COUNT: Number of retry attempts (default: "1")
    - MAX_WORKERS: Number of parallel workers (default: "50")
    - FREQUENCY: "1h" or "1d" (default: "1h")
    - LOG_LEVEL: Logging level (default: "warning")
    - TEST_CHAINS: Comma-separated list of chain names to scan (default: all chains)
    - CHAIN_ORDER: Comma-separated list of chain names to scan in order (whitespace allowed, chains not listed are skipped)
    - DISABLE_CHAINS: Comma-separated list of chain names to skip (whitespace allowed)
    - SKIP_POST_PROCESSING: "true" to skip post-processing steps (default: "false")
    - SKIP_CLEANING: "true" to skip price cleaning step (default: "false")
    - SKIP_SPARKLINES: "true" to skip sparkline image export to R2 (default: "false")
    - SKIP_METADATA: "true" to skip protocol/stablecoin metadata export to R2 (default: "false")
    - SKIP_DATA: "true" to skip data file (parquet, pickle) export to R2 (default: "false")
    - REFRESH_STABLECOIN_RATES: "false" to skip CoinGecko stablecoin rate refresh before metadata export (default: "true")
    - FORCE_STABLECOIN_RATE_REFRESH: "true" to bypass the same-day stablecoin rate gate (default: "false")
    - STABLECOIN_RATE_TIMEOUT: CoinGecko timeout in seconds for stablecoin rate refresh (default: "20")
    - COINGECKO_DEMO_API_KEY: Optional CoinGecko demo API key for stablecoin rate refreshes
    - JSON_RPC_<CHAIN>: RPC URL for each chain (required per chain)
    - LOOP_INTERVAL_SECONDS: Seconds between ticks in looped mode (default: "0" = single run)
    - SCAN_CYCLES: Per-chain/protocol cycle overrides, e.g. "Ethereum=8h,Base=8h,Arbitrum=8h,Hypercore=4h,GRVT=4h,Lighter Ethereum=4h,Lighter Robinhood=4h,ApeX=4h"
      The legacy "Lighter=4h" form still applies the interval to both deployments.
    - DEFAULT_CYCLE: Default cycle interval for items not in SCAN_CYCLES (default: "24h")
    - MAX_CYCLES: Exit after N cycles in looped mode, for testing (default: "0" = unlimited)
    - FORCE_RESCAN: "true" to ignore cycle state and rescan all items on the first cycle (default: "false")
    - PIPELINE_DATA_DIR: Override base directory for all pipeline files (default: ~/.tradingstrategy/vaults)
    - HYPERSYNC_CONCURRENCY: Number of concurrent Hypersync stream requests (default: "1").
      Set higher for faster throughput at the cost of more API pressure.
    - HYPERSYNC_RPM: Hypersync API requests-per-minute limit (default: 150, 75% of the 200 RPM free-tier limit). Lower after persistent 429 errors.

Example CHAIN_ORDER for all chains:
    CHAIN_ORDER="Sonic, Monad, Hyperliquid, Base, Arbitrum, Ethereum, Linea, Gnosis, Zora, Polygon, Avalanche, Berachain, Unichain, Hemi, Plasma, Binance, Mantle, Katana, Ink, Blast, Soneium, Optimism"
"""

import datetime
import os


def _print_early_startup_banner() -> None:
    """Print a boot marker before importing the scanner implementation."""
    log_level = os.environ.get("LOG_LEVEL", "warning")
    timestamp = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    print(f"{timestamp} Starting vault scanner, LOG_LEVEL={log_level}", flush=True)


if __name__ == "__main__":
    _print_early_startup_banner()
    from eth_defi.vault.scan_all_chains import main

    main()
