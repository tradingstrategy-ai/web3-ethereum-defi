"""GMX Synthetic token details fetching and caching.

Fetch GMX synthetic token data from APIs and cache results for efficient access.
"""

# TODO: We might not this anymore

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cached_property
from typing import Optional, Any, TypeAlias

import cachetools
from eth_typing import HexAddress

from eth_defi.chain import get_chain_name
from eth_defi.gmx.retry import make_gmx_api_request

logger = logging.getLogger(__name__)

#: Default cache for GMX token details
DEFAULT_GMX_TOKEN_CACHE = cachetools.LRUCache(512)

#: GMX token address type alias
GMXTokenAddress: TypeAlias = str


@dataclass(slots=True)
class GMXSyntheticTokenDetails:
    """GMX Synthetic token Python representation.

    A helper class to work with GMX synthetic tokens from their API.
    Similar to TokenDetails but designed for GMX API data structure.

    Example usage:

    .. code-block:: python

        # Fetch all GMX tokens for Arbitrum
        tokens = fetch_gmx_synthetic_tokens(chain_id=42161)
        usdc_token = next(t for t in tokens if t.symbol == "USDC")
        print(f"USDC address on Arbitrum: {usdc_token.address}")

    Key differences from ERC-20 TokenDetails:
    - No web3 contract instance needed
    - Data comes from API, not blockchain calls
    - Simpler structure (no name or total_supply from API)
    """

    #: Token symbol e.g. "USDC", "ETH"
    symbol: str

    #: Token contract address
    address: HexAddress

    #: Number of decimals for the token
    decimals: int

    #: Chain ID where this token exists
    chain_id: int

    #: Extra metadata for caching and other purposes
    extra_data: dict[str, Any] = field(default_factory=dict)

    def __eq__(self, other):
        """Two GMX tokens are equal if they have same address and chain."""
        if not isinstance(other, GMXSyntheticTokenDetails):
            return False
        return self.address.lower() == other.address.lower() and self.chain_id == other.chain_id

    def __hash__(self):
        """Hash based on chain and address for use in sets/dicts."""
        return hash((self.chain_id, self.address.lower()))

    def __repr__(self):
        return f"<GMX {self.symbol} at {self.address}, {self.decimals} decimals, chain {self.chain_id}>"

    @cached_property
    def address_lower(self) -> str:
        """Get the lowercase version of the address."""
        return self.address.lower()

    def convert_to_decimals(self, raw_amount: int) -> Decimal:
        """Convert raw token units to decimal representation.

        :param raw_amount: Raw token amount as integer
        :return: Decimal representation of the amount

        Example:
            If token has 6 decimals, converts 1000000 -> 1.0
        """
        if not isinstance(raw_amount, int):
            raise ValueError(f"Expected int, got {type(raw_amount)}: {raw_amount}")
        return Decimal(raw_amount) / Decimal(10**self.decimals)

    def convert_to_raw(self, decimal_amount: Decimal) -> int:
        """Convert decimal token amount to raw integer units.

        :param decimal_amount: Decimal amount
        :return: Raw token amount as integer

        Example:
            If token has 6 decimals, converts 1.0 -> 1000000
        """
        if not isinstance(decimal_amount, Decimal):
            raise ValueError(f"Expected Decimal, got {type(decimal_amount)}")
        return int(decimal_amount * (10**self.decimals))

    @staticmethod
    def generate_cache_key(chain_id: int, symbol: str) -> str:
        """Generate cache key for GMX token.

        We cache by (chain_id, symbol) since GMX API gives us symbol-based data.
        This is different from ERC-20 caching which uses address.

        :param chain_id: Blockchain chain ID
        :param symbol: Token symbol
        :return: Cache key string in format "gmx-{chain_id}-{symbol_lower}"
        """
        if not isinstance(chain_id, int):
            raise ValueError(f"Chain ID must be int, got {type(chain_id)}")
        if not isinstance(symbol, str):
            raise ValueError(f"Symbol must be string, got {type(symbol)}")
        return f"gmx-{chain_id}-{symbol.lower()}"

    def export(self) -> dict[str, Any]:
        """Export token details as serializable dictionary.

        Useful for saving to disk cache or API responses.

        Returns:
            dictionary with all token information
        """
        return {
            "symbol": self.symbol,
            "address": self.address,
            "decimals": self.decimals,
            "chain_id": self.chain_id,
            "extra_data": self.extra_data,
        }


class GMXTokenFetchError(Exception):
    """Exception raised when GMX token fetching fails."""

    pass


def fetch_gmx_synthetic_tokens(
    chain_id: int,
    cache: Optional[cachetools.Cache] = DEFAULT_GMX_TOKEN_CACHE,
    timeout: float = 10.0,
    force_refresh: bool = False,
    max_retries: int = 2,
    retry_delay: float = 0.1,
) -> list[GMXSyntheticTokenDetails]:
    """Fetch GMX synthetic token details from API with caching and retry logic.

    This function fetches all available GMX synthetic tokens for a given chain
    and caches the results to avoid repeated API calls. It implements retry logic
    with exponential backoff and automatic failover to backup API endpoints.

    :param chain_id: Blockchain chain ID (42161 for Arbitrum, 43114 for Avalanche)
    :param cache: Cache instance to use. Set to None to disable caching
    :param timeout: HTTP request timeout in seconds
    :param force_refresh: If True, bypass cache and fetch fresh data
    :param max_retries: Maximum number of retry attempts per endpoint (default: 2)
    :param retry_delay: Initial delay between retries in seconds with exponential backoff (default: 0.1s, resulting in 0.1s, 0.2s delays)
    :return: list of GMXSyntheticTokenDetails objects
    :raises GMXTokenFetchError: If API request fails on both primary and backup
    :raises ValueError: If chain_id is not supported

    Example:

    .. code-block:: python

        # Fetch Arbitrum GMX tokens with automatic retry and failover
        tokens = fetch_gmx_synthetic_tokens(chain_id=42161)
    """
    # Get chain name from chain ID
    try:
        chain_name = get_chain_name(chain_id).lower()  # Lowercase for as the dict keys are in lowercase
    except Exception:
        raise ValueError(f"Unsupported chain ID {chain_id}")

    # Generate cache key for the entire chain's token list
    cache_key = f"gmx-tokens-{chain_id}"

    # Check cache first (unless force refresh requested)
    if cache is not None and not force_refresh:
        cached_tokens = cache.get(cache_key)
        if cached_tokens is not None:
            logger.debug("Returning %s cached GMX tokens for chain %s", len(cached_tokens), chain_id)
            return [
                GMXSyntheticTokenDetails(
                    symbol=token_data["symbol"],
                    address=token_data["address"],
                    decimals=token_data["decimals"],
                    chain_id=chain_id,
                    extra_data={"cached": True},
                )
                for token_data in cached_tokens
            ]

    # Use centralized retry + backup logic
    try:
        api_data = make_gmx_api_request(
            chain=chain_name,
            endpoint="/tokens",
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
    except RuntimeError as e:
        raise GMXTokenFetchError(f"Failed to fetch GMX tokens for chain {chain_name} (chain_id: {chain_id})") from e

    # Validate API response structure
    if "tokens" not in api_data:
        raise GMXTokenFetchError(f"Invalid API response: missing 'tokens' field")

    tokens_data = api_data["tokens"]
    if not isinstance(tokens_data, list):
        raise GMXTokenFetchError(f"Invalid API response: 'tokens' should be a list")

    # Parse and validate token data
    tokens = []
    for token_data in tokens_data:
        try:
            # Validate required fields
            required_fields = ["symbol", "address", "decimals"]
            if not all(field in token_data for field in required_fields):
                logger.warning("Skipping token missing required fields: %s", token_data)
                continue

            # Create token details object
            token = GMXSyntheticTokenDetails(
                symbol=token_data["symbol"],
                address=token_data["address"],
                decimals=int(token_data["decimals"]),
                chain_id=chain_id,
                extra_data={"cached": False},
            )
            tokens.append(token)

        except (KeyError, ValueError, TypeError) as e:
            logger.warning("Skipping invalid token data %s: %s", token_data, e)
            continue

    # Cache the results for future use
    if cache is not None:
        cache_data = [token.export() for token in tokens]
        cache[cache_key] = cache_data
        logger.debug("Cached %s GMX tokens for chain %s", len(tokens), chain_id)

    logger.info("Successfully fetched %s GMX tokens for chain %s", len(tokens), chain_id)
    return tokens


def get_gmx_synthetic_token_by_symbol(
    chain_id: int,
    symbol: str,
    cache: Optional[cachetools.Cache] = DEFAULT_GMX_TOKEN_CACHE,
) -> Optional[GMXSyntheticTokenDetails]:
    """Get a specific GMX token by symbol on a given chain.

    This is a convenience function that fetches all tokens and filters by symbol.
    More efficient than fetching tokens repeatedly when you need just one.

    :param chain_id: Blockchain chain ID
    :param symbol: Token symbol to search for (case-insensitive)
    :param cache: Cache instance to use
    :return: GMXSyntheticTokenDetails if found, None otherwise

    Example:

    .. code-block:: python

        # Get USDC token on Arbitrum
        usdc = get_gmx_synthetic_token_by_symbol(42161, "USDC")
        if usdc:
            print(f"USDC decimals: {usdc.decimals}")
    """
    tokens = fetch_gmx_synthetic_tokens(chain_id, cache=cache)

    # Case-insensitive symbol search
    symbol_lower = symbol.lower()
    for token in tokens:
        if token.symbol.lower() == symbol_lower:
            return token

    return None


def get_gmx_synthetic_token_by_address(
    chain_id: int,
    address: HexAddress,
    cache: Optional[cachetools.Cache] = DEFAULT_GMX_TOKEN_CACHE,
) -> Optional[GMXSyntheticTokenDetails]:
    """Get a specific GMX token by address on a given chain.

    :param chain_id: Blockchain chain ID
    :param address: Token contract address
    :param cache: Cache instance to use
    :return: GMXSyntheticTokenDetails if found, None otherwise
    """
    tokens = fetch_gmx_synthetic_tokens(chain_id, cache=cache)

    # Case-insensitive address search
    address_lower = address.lower()
    for token in tokens:
        if token.address.lower() == address_lower:
            return token

    return None


def reset_gmx_token_cache():
    """Reset the default GMX token cache.

    Useful for testing or when you want to force fresh API calls.
    """
    global DEFAULT_GMX_TOKEN_CACHE
    DEFAULT_GMX_TOKEN_CACHE.clear()


def get_supported_gmx_chains() -> list[int]:
    """Get list of chain IDs that support GMX synthetic tokens.

    :return: list of supported chain IDs
    """
    return list(GMX_API_ENDPOINTS.keys())
