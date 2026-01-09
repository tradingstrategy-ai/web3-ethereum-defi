"""
GMX Oracle Price Data Module.

This module provides access to GMX protocol oracle price feeds across supported networks.
"""

from typing import Optional

import requests
import time
import logging
import random

from eth_typing import HexAddress
from eth_utils import to_checksum_address
from requests import Response

from eth_defi.gmx.contracts import _get_clean_api_urls, _get_clean_backup_urls
from eth_defi.gmx.types import PriceData

# Module-level cache for oracle prices with timestamps
# Key: chain name, Value: (prices dict, timestamp)
_ORACLE_PRICES_CACHE: dict[str, tuple[dict, float]] = {}
_CACHE_TTL_SECONDS = 10  # Cache oracle prices for 10 seconds

# Testnet to mainnet token address mappings
# Used to translate testnet addresses to mainnet equivalents for oracle price lookups
# Since testnets use mainnet oracle endpoints, we need this mapping
_TESTNET_TO_MAINNET_ADDRESSES: dict[str, dict[HexAddress, HexAddress]] = {
    # Arbitrum Sepolia → Arbitrum mainnet
    "arbitrum_sepolia": {
        # WETH/ETH
        "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        # USDC
        "0x3321Fd36aEaB0d5CdfD26f4A3A93E2D2aAcCB99f": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        # USDC.SG (synthetic) - maps to same USDC on mainnet
        "0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        # BTC
        "0xF79cE1Cf38A09D572b021B4C5548b75A14082F12": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
    },
    # Avalanche Fuji → Avalanche mainnet
    "avalanche_fuji": {
        # Add mappings when Fuji testnet is tested
    },
}


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

        logging.info("Using oracle for chain '%s' (requested chain: '%s')", oracle_chain, chain)
        self.oracle_url = clean_api_urls[oracle_chain] + "/signed_prices/latest"
        self.backup_oracle_url = clean_backup_urls.get(oracle_chain, "") + "/signed_prices/latest" if clean_backup_urls.get(oracle_chain) else None

    def _translate_address_for_oracle(self, address: HexAddress) -> HexAddress:
        """Translate testnet token address to mainnet equivalent for oracle lookup.

        Testnets use mainnet oracle endpoints, so we need to map testnet
        token addresses to their mainnet equivalents before looking up prices.

        :param address: Token address (testnet or mainnet)
        :return: Mainnet token address for oracle lookup
        """
        # Check if we have a mapping for this chain
        if self.chain in _TESTNET_TO_MAINNET_ADDRESSES:
            # Normalise address to checksum format for consistent lookup
            try:
                checksum_addr = to_checksum_address(address)
            except ValueError:
                # Invalid address format, return as-is
                return address

            # Look up mainnet equivalent
            mapping = _TESTNET_TO_MAINNET_ADDRESSES[self.chain]
            mainnet_addr = mapping.get(checksum_addr)

            if mainnet_addr:
                logging.debug("Translated testnet address %s to mainnet %s", checksum_addr, mainnet_addr)
                return mainnet_addr
            else:
                # No mapping found - address might already be mainnet or unknown token
                logging.debug("No testnet mapping found for %s, using as-is", checksum_addr)
                return checksum_addr

        # Not a testnet chain, but still normalise to checksum for consistent lookup
        try:
            return to_checksum_address(address)
        except ValueError:
            return address

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
                logging.debug("Using cached oracle prices (age: %.1fs)", age)
                return cached_prices

        # Fetch fresh prices
        raw_output = self._make_query().json()
        prices = self._process_output(raw_output)

        # Cache the result
        if use_cache:
            _ORACLE_PRICES_CACHE[self.chain] = (prices, time.time())

        return prices

    def get_price_for_token(self, token_address: HexAddress, use_cache: bool = True) -> dict | None:
        """Get oracle price for a specific token, handling testnet address translation.

        This is the recommended method for getting token prices as it automatically
        handles testnet-to-mainnet address mapping.

        :param token_address: Token address (testnet or mainnet)
        :param use_cache: Whether to use cached oracle prices
        :return: Price data dict or None if not found
        """
        # Translate testnet address to mainnet if needed
        lookup_address = self._translate_address_for_oracle(token_address)

        # Get all oracle prices
        oracle_prices = self.get_recent_prices(use_cache=use_cache)

        # Look up by mainnet address
        return oracle_prices.get(lookup_address)

    def _make_query(self, max_retries=5, initial_backoff=1, max_backoff=60) -> Response | None:
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
        urls = [self.oracle_url]
        if self.backup_oracle_url:
            urls.append(self.backup_oracle_url)

        last_exception = None

        for url in urls:
            attempts = 0
            backoff = initial_backoff

            while attempts < max_retries:
                try:
                    logging.debug("Querying oracle at %s", url)
                    response = requests.get(url, timeout=30)  # Added timeout for safety
                    response.raise_for_status()  # Raise exception for 4XX/5XX status codes
                    return response

                except requests.exceptions.HTTPError as e:
                    # Don't retry client errors (4xx)
                    if 400 <= e.response.status_code < 500:
                        logging.error("Oracle client error %s: %s", e.response.status_code, e)
                        last_exception = e
                        break  # Break inner loop, try next URL

                    # 5xx errors fall through to general RequestException handling (retry)
                    attempts += 1
                    last_exception = e

                except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                    attempts += 1
                    last_exception = e

                if attempts >= max_retries:
                    logging.warning("Failed to query oracle at %s after %s attempts: %s", url, max_retries, str(last_exception))
                    break

                # Add jitter to avoid thundering herd problem
                jitter = random.uniform(0, 0.1 * backoff)
                wait_time = backoff + jitter

                logging.debug("Request failed: %s. Retrying in %.2f seconds (attempt %s/%s)", str(last_exception), wait_time, attempts, max_retries)
                time.sleep(wait_time)

                # Exponential backoff with capping
                backoff = min(backoff * 2, max_backoff)

        # If we exhausted all URLs and retries
        if last_exception:
            logging.error("All oracle URLs failed. Last error: %s", str(last_exception))
            raise last_exception

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
