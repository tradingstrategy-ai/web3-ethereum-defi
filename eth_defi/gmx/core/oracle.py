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

    def get_recent_prices(self) -> PriceData:
        """Get raw output of the GMX rest v2 api for signed prices.

        :return: Dictionary containing raw output for each token as its keys
        """
        logging.debug("Fetching recent prices from GMX oracle API")
        raw_output = self._make_query().json()

        # Log the raw response structure
        if "signedPrices" in raw_output:
            num_prices = len(raw_output["signedPrices"])
            logging.debug(f"Oracle API returned prices for {num_prices} tokens")

            # Log first few token addresses and their min/max prices
            for i, price_data in enumerate(raw_output["signedPrices"][:5]):
                token_addr = price_data.get("tokenAddress", "N/A")
                min_price = price_data.get("minPrice", "N/A")
                max_price = price_data.get("maxPrice", "N/A")
                logging.debug(f"  Token {i + 1}: {token_addr} - Min: {min_price}, Max: {max_price}")
        else:
            logging.warning(f"Oracle API response missing 'signedPrices' key. Keys: {list(raw_output.keys())}")

        result = self._process_output(raw_output)
        logging.debug(f"Processed oracle prices: {len(result)} tokens available for trading")
        return result

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
        try:
            logging.debug(f"Processing oracle output with {len(output.get('signedPrices', []))} prices")
            for i in output["signedPrices"]:
                token_addr = i.get("tokenAddress", "unknown")
                processed[token_addr] = i
                logging.debug(f"  Added price for token: {token_addr}")

            logging.debug(f"Successfully processed {len(processed)} token prices")
        except KeyError as e:
            logging.error(f"Error processing oracle output - missing key: {e}. Output keys: {list(output.keys())}")
            raise
        except Exception as e:
            logging.error(f"Unexpected error processing oracle output: {e}", exc_info=True)
            raise

        return processed
