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

        if chain not in clean_api_urls:
            raise ValueError(f"Unsupported chain: {chain}. Supported: {list(clean_api_urls.keys())}")

        self.oracle_url = clean_api_urls[chain] + "/signed_prices/latest"
        self.backup_oracle_url = clean_backup_urls.get(chain, "") + "/signed_prices/latest" if clean_backup_urls.get(chain) else None

    def get_recent_prices(self) -> PriceData:
        """Get raw output of the GMX rest v2 api for signed prices.

        :return: Dictionary containing raw output for each token as its keys
        """
        raw_output = self._make_query().json()
        return self._process_output(raw_output)

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
