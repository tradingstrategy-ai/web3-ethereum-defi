"""
GMX Oracle Price Data Module.

This module provides access to GMX protocol oracle price feeds across supported networks.
"""

from typing import Optional

import requests
import time
import logging
import random

from requests import Response

from eth_defi.gmx.contracts import _get_clean_api_urls, _get_clean_backup_urls
from eth_defi.gmx.types import PriceData

# Module-level cache for oracle prices with timestamps
# Key: chain name, Value: (prices dict, timestamp)
_ORACLE_PRICES_CACHE: dict[str, tuple[dict, float]] = {}
_CACHE_TTL_SECONDS = 10  # Cache oracle prices for 10 seconds


class OraclePrices:
    """GMX Oracle Prices API client.

    Provides access to GMX protocol oracle price feeds across supported networks.
    Handles API requests with retry logic and exponential backoff.

    :param chain: Blockchain network name (e.g., 'arbitrum', 'avalanche')
    :raises ValueError: If unsupported chain is provided
    """

    def __init__(self, chain: str) -> None:
        self.chain = chain

        # Get API URLs from constants via helper functions
        clean_api_urls = _get_clean_api_urls()
        clean_backup_urls = _get_clean_backup_urls()

        # Testnet fallback mapping - use mainnet oracles for testnets
        testnet_to_mainnet = {
            "arbitrum_sepolia": "arbitrum",
            "avalanche_fuji": "avalanche",
        }

        # Use mainnet oracle for testnets that don't have their own
        oracle_chain = testnet_to_mainnet.get(chain, chain)

        if oracle_chain not in clean_api_urls:
            raise ValueError(f"Unsupported chain: {chain}. Supported: {list(clean_api_urls.keys()) + list(testnet_to_mainnet.keys())}")

        logging.info(f"Using oracle for chain '{oracle_chain}' (requested chain: '{chain}')")
        self.oracle_url = clean_api_urls[oracle_chain] + "/signed_prices/latest"
        self.backup_oracle_url = clean_backup_urls.get(oracle_chain, "") + "/signed_prices/latest" if clean_backup_urls.get(oracle_chain) else None

    def get_recent_prices(self, use_cache: bool = True, cache_ttl: float = None) -> PriceData:
        """Get raw output of the GMX rest v2 api for signed prices.

        Uses module-level caching with TTL to avoid repeated API calls.
        Oracle prices change frequently but not every second, so short-term
        caching (10s default) provides significant performance improvement.

        :param use_cache: Whether to use cached values. Default is True.
        :param cache_ttl: Cache TTL in seconds. If None, uses module default (10s).
        :return: Dictionary containing raw output for each token as its keys
        """
        ttl = cache_ttl if cache_ttl is not None else _CACHE_TTL_SECONDS

        # Check cache if enabled
        if use_cache and self.chain in _ORACLE_PRICES_CACHE:
            cached_prices, cached_time = _ORACLE_PRICES_CACHE[self.chain]
            age = time.time() - cached_time

            if age < ttl:
                logging.debug(f"Using cached oracle prices (age: {age:.1f}s)")
                return cached_prices

        # Fetch fresh prices
        raw_output = self._make_query().json()
        prices = self._process_output(raw_output)

        # Cache the result
        if use_cache:
            _ORACLE_PRICES_CACHE[self.chain] = (prices, time.time())

        return prices

    def _make_query(self, max_retries=5, initial_backoff=1, max_backoff=60) -> Optional[Response]:
        """Make request using oracle URL with retry mechanism.

        :param max_retries: Maximum number of retry attempts
        :param initial_backoff: Initial backoff time in seconds
        :type initial_backoff: float
        :param max_backoff: Maximum backoff time in seconds
        :type max_backoff: float
        :return: Raw request response
        :rtype: requests.models.Response
        :raises requests.exceptions.RequestException: If all retry attempts fail
        """
        url = self.oracle_url
        attempts = 0
        backoff = initial_backoff

        while attempts < max_retries:
            try:
                logging.debug(f"Querying oracle at {url}")
                response = requests.get(url, timeout=30)  # Added timeout for safety
                response.raise_for_status()  # Raise exception for 4XX/5XX status codes
                return response

            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                attempts += 1

                if attempts >= max_retries:
                    logging.error(f"Failed to query oracle after {max_retries} attempts: {str(e)}")
                    raise

                # Add jitter to avoid thundering herd problem
                jitter = random.uniform(0, 0.1 * backoff)
                wait_time = backoff + jitter

                logging.debug(f"Request failed: {str(e)}. Retrying in {wait_time:.2f} seconds (attempt {attempts}/{max_retries})")
                time.sleep(wait_time)

                # Exponential backoff with capping
                backoff = min(backoff * 2, max_backoff)
        return None

    @staticmethod
    def _process_output(output: dict) -> dict:
        """
        Take the API response and create a new dictionary where the index token addresses are the keys.

        :param output: Dictionary of rest API response
        :type output: dict
        :return: Processed dictionary with token addresses as keys
        :rtype: dict
        """
        processed: dict = {}
        for i in output["signedPrices"]:
            processed[i["tokenAddress"]] = i

        return processed


def clear_oracle_prices_cache():
    """Clear the module-level oracle prices cache.

    Call this if you need to force refresh of oracle prices.
    """
    global _ORACLE_PRICES_CACHE
    _ORACLE_PRICES_CACHE.clear()
