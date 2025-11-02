"""
GMX Order Argument Parser

This module provides parameter parsing and validation for GMX orders.
Converts user-friendly parameters (symbols, USD amounts) into contract-ready format.
"""

import numpy as np
from web3 import Web3

from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.utils import determine_swap_route
from eth_defi.gmx.contracts import get_tokens_address_dict
from eth_defi.token import fetch_erc20_details

# Module-level caches to avoid repeated expensive calls
_MARKETS_CACHE: dict[str, dict] = {}  # Key: chain name
_TOKEN_METADATA_CACHE: dict[tuple[int, str], dict] = {}  # Key: (chain_id, chain_name)


def _get_token_metadata_dict(web3: Web3, chain: str, use_cache: bool = True) -> dict:
    """
    Get token metadata in SDK-compatible format with caching.

    Converts our eth_defi format (symbol -> address) into SDK format
    (address -> {symbol, address, decimals}) required by ArgumentParser.

    Uses module-level caching to avoid repeated RPC calls for token metadata.

    :param web3: Web3 connection instance
    :param chain: Network name
    :param use_cache: Whether to use cached values. Default is True.
    :return: Dictionary mapping addresses to token metadata
    """
    # Check cache first
    chain_id = web3.eth.chain_id
    cache_key = (chain_id, chain)

    if use_cache and cache_key in _TOKEN_METADATA_CACHE:
        return _TOKEN_METADATA_CACHE[cache_key].copy()

    # Get our format: {symbol: address}
    tokens_by_symbol = get_tokens_address_dict(chain)

    # Convert to SDK format: {address: {symbol, address, decimals}}
    result = {}
    for symbol, address in tokens_by_symbol.items():
        try:
            token_details = fetch_erc20_details(web3, address)
            result[address] = {
                "symbol": symbol,
                "address": address,
                "decimals": token_details.decimals,
            }
        except Exception as e:
            # Fallback for tokens that might not be standard ERC20
            result[address] = {
                "symbol": symbol,
                "address": address,
                "decimals": 18,  # Default to 18 decimals
            }

    # Cache the result
    if use_cache:
        _TOKEN_METADATA_CACHE[cache_key] = result.copy()

    return result


class OrderArgumentParser:
    """
    Parses and validates order parameters for GMX protocol.

    Converts user-friendly parameters into contract-ready format:
    - Symbol names → token addresses
    - USD amounts → wei amounts with proper decimals
    - Calculates missing parameters from leverage
    - Validates collateral compatibility
    - Determines optimal swap paths
    """

    def __init__(
        self,
        config,
        is_increase: bool = False,
        is_decrease: bool = False,
        is_swap: bool = False,
    ):
        """
        Initialize parser for specific order type.

        :param config: GMXConfigManager with chain and network info
        :param is_increase: True for opening/increasing positions
        :param is_decrease: True for closing/decreasing positions
        :param is_swap: True for token swaps
        """
        self.config = config
        self.parameters_dict = None
        self.is_increase = is_increase
        self.is_decrease = is_decrease
        self.is_swap = is_swap

        # Get web3 connection - config could be GMXConfig or GMXConfigManager
        if hasattr(config, "get_web3_connection"):
            self.web3 = config.get_web3_connection()
        else:
            # GMXConfig has web3 attribute directly
            self.web3 = config.web3

        # Get chain name for caching - handle both GMXConfig and GMXConfigManager
        if hasattr(config, "chain"):
            # GMXConfig has chain attribute
            chain = config.chain
        else:
            # GMXConfigManager has get_chain() method
            chain = config.get_chain()

        # Check if markets are cached
        if chain not in _MARKETS_CACHE:
            # Import GMXConfig to create proper config for Markets
            from eth_defi.gmx.config import GMXConfig

            # Get user wallet address - handle both types
            user_wallet_address = getattr(config, "user_wallet_address", None) or getattr(config, "_user_wallet_address", None)

            gmx_config = GMXConfig(self.web3, user_wallet_address=user_wallet_address)

            # Get markets info - Markets expects GMXConfig, not GMXConfigManager
            _MARKETS_CACHE[chain] = Markets(gmx_config).get_available_markets()

        self.markets = _MARKETS_CACHE[chain]

        if is_increase:
            self.required_keys = [
                "chain",
                "index_token_address",
                "market_key",
                "start_token_address",
                "collateral_address",
                "swap_path",
                "is_long",
                "size_delta_usd",
                "initial_collateral_delta",
                "slippage_percent",
            ]

        if is_decrease:
            self.required_keys = [
                "chain",
                "index_token_address",
                "market_key",
                "start_token_address",
                "collateral_address",
                "is_long",
                "size_delta_usd",
                "initial_collateral_delta",
                "slippage_percent",
            ]

        if is_swap:
            self.required_keys = [
                "chain",
                "start_token_address",
                "out_token_address",
                "initial_collateral_delta",
                "swap_path",
                "slippage_percent",
            ]

        self.missing_base_key_methods = {
            "chain": self._handle_missing_chain,
            "index_token_address": self._handle_missing_index_token_address,
            "market_key": self._handle_missing_market_key,
            "start_token_address": self._handle_missing_start_token_address,
            "out_token_address": self._handle_missing_out_token_address,
            "collateral_address": self._handle_missing_collateral_address,
            "swap_path": self._handle_missing_swap_path,
            "is_long": self._handle_missing_is_long,
            "slippage_percent": self._handle_missing_slippage_percent,
        }

    def process_parameters_dictionary(self, parameters_dict):
        """
        Process and validate order parameters.

        :param parameters_dict: User-supplied parameters
        :return: Complete, validated parameters ready for contract interaction
        """
        missing_keys = self._determine_missing_keys(parameters_dict)

        self.parameters_dict = parameters_dict

        for missing_key in missing_keys:
            if missing_key in self.missing_base_key_methods:
                self.missing_base_key_methods[missing_key]()

        if not self.is_swap:
            self.calculate_missing_position_size_info_keys()
            self._check_if_max_leverage_exceeded()

        if self.is_increase:
            if self._calculate_initial_collateral_usd() < 2:
                msg = "Position size must be backed by >$2 of collateral!"
                raise Exception(msg)

        self._format_size_info()

        return self.parameters_dict

    def _determine_missing_keys(self, parameters_dict):
        """Compare keys in dictionary to required keys for order type."""
        return [key for key in self.required_keys if key not in parameters_dict]

    def _handle_missing_chain(self):
        """Chain must be supplied by user."""
        msg = "Please pass chain name in parameters dictionary!"
        raise Exception(msg)

    def _handle_missing_index_token_address(self):
        """Resolve index token address from symbol."""
        try:
            token_symbol = self.parameters_dict["index_token_symbol"]

            # Special handling for BTC on avalanche
            if token_symbol == "BTC" and self.parameters_dict["chain"] == "avalanche":
                token_symbol = "WBTC.b"
        except KeyError:
            msg = "Index Token Address and Symbol not provided!"
            raise Exception(msg)

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        self.parameters_dict["index_token_address"] = self.find_key_by_symbol(tokens, token_symbol)

    def _handle_missing_market_key(self):
        """Resolve market key from index token address."""
        index_token_address = self.parameters_dict["index_token_address"]

        # Special handling for WBTC on arbitrum
        if index_token_address == "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f":
            index_token_address = "0x47904963fc8b2340414262125aF798B9655E58Cd"

        # Find market key from markets dict
        self.parameters_dict["market_key"] = self.find_market_key_by_index_address(self.markets, index_token_address)

    def _handle_missing_start_token_address(self):
        """Resolve start token address from symbol."""
        try:
            start_token_symbol = self.parameters_dict["start_token_symbol"]

            # Special handling for BTC on arbitrum
            if start_token_symbol == "BTC" and self.parameters_dict["chain"] == "arbitrum":
                self.parameters_dict["start_token_address"] = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
                return

        except KeyError:
            msg = "Start Token Address and Symbol not provided!"
            raise Exception(msg)

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        self.parameters_dict["start_token_address"] = self.find_key_by_symbol(tokens, start_token_symbol)

    def _handle_missing_out_token_address(self):
        """Resolve output token address from symbol."""
        try:
            out_token_symbol = self.parameters_dict["out_token_symbol"]
        except KeyError:
            msg = "Out Token Address and Symbol not provided!"
            raise Exception(msg)

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        self.parameters_dict["out_token_address"] = self.find_key_by_symbol(tokens, out_token_symbol)

    def _handle_missing_collateral_address(self):
        """Resolve collateral address from symbol."""
        try:
            collateral_token_symbol = self.parameters_dict["collateral_token_symbol"]

            # Special handling for BTC on arbitrum
            if collateral_token_symbol == "BTC" and self.parameters_dict["chain"] == "arbitrum":
                self.parameters_dict["collateral_address"] = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
                return
        except KeyError:
            msg = "Collateral Token Address and Symbol not provided!"
            raise Exception(msg)

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        collateral_address = self.find_key_by_symbol(tokens, collateral_token_symbol)

        # Validate collateral is valid for the market
        if self._check_if_valid_collateral_for_market(collateral_address) and not self.is_swap:
            self.parameters_dict["collateral_address"] = collateral_address

    def _handle_missing_swap_path(self):
        """Determine swap path between tokens."""
        if self.is_swap:
            markets = self.markets
            try:
                self.parameters_dict["swap_path"] = determine_swap_route(
                    markets,
                    self.parameters_dict["start_token_address"],
                    self.parameters_dict["out_token_address"],
                    chain=self.parameters_dict["chain"],
                )[0]
            except TypeError:
                error_message = f"No markets available for {self.parameters_dict['start_token_address']} token"
                raise RuntimeError(error_message)

        # No swap needed if start token == collateral token
        elif self.parameters_dict["start_token_address"] == self.parameters_dict["collateral_address"]:
            self.parameters_dict["swap_path"] = []

        else:
            markets = self.markets
            self.parameters_dict["swap_path"] = determine_swap_route(
                markets,
                self.parameters_dict["start_token_address"],
                self.parameters_dict["collateral_address"],
                chain=self.parameters_dict["chain"],
            )[0]

    @staticmethod
    def _handle_missing_is_long(self):
        """is_long must be supplied by user."""
        msg = "Please indicate if position is_long!"
        raise Exception(msg)

    @staticmethod
    def _handle_missing_slippage_percent(self):
        """slippage_percent must be supplied by user."""
        msg = "Please indicate slippage!"
        raise Exception(msg)

    def _check_if_valid_collateral_for_market(self, collateral_address: str):
        """Validate collateral token is valid for the selected market."""
        market_key = self.parameters_dict["market_key"]

        # Special handling for WBTC market
        if self.parameters_dict["market_key"] == "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f":
            market_key = "0x47c031236e19d024b42f8AE6780E44A573170703"

        market = self.markets[market_key]

        # Collateral must be either long or short token of the market
        if collateral_address in (
            market["long_token_address"],
            market["short_token_address"],
        ):
            return True
        else:
            msg = "Not a valid collateral for selected market!"
            raise Exception(msg)

    @staticmethod
    def find_key_by_symbol(input_dict: dict, search_symbol: str):
        """Find token address by symbol in metadata dict."""
        for key, value in input_dict.items():
            if value.get("symbol") == search_symbol:
                return key
        msg = f'"{search_symbol}" not a known token for GMX v2!'
        raise Exception(msg)

    @staticmethod
    def find_market_key_by_index_address(input_dict: dict, index_token_address: str):
        """Find market key by index token address."""
        for key, value in input_dict.items():
            if value.get("index_token_address") == index_token_address:
                return key
        return None

    def calculate_missing_position_size_info_keys(self):
        """Calculate missing parameters from size/collateral/leverage combinations."""
        # Both size and collateral provided
        if "size_delta_usd" in self.parameters_dict and "initial_collateral_delta" in self.parameters_dict:
            return self.parameters_dict

        # Leverage + collateral provided, calculate size
        elif "leverage" in self.parameters_dict and "initial_collateral_delta" in self.parameters_dict and "size_delta_usd" not in self.parameters_dict:
            initial_collateral_delta_usd = self._calculate_initial_collateral_usd()
            self.parameters_dict["size_delta_usd"] = self.parameters_dict["leverage"] * initial_collateral_delta_usd
            return self.parameters_dict

        # Size + leverage provided, calculate collateral
        elif "size_delta_usd" in self.parameters_dict and "leverage" in self.parameters_dict and "initial_collateral_delta" not in self.parameters_dict:
            collateral_usd = self.parameters_dict["size_delta_usd"] / self.parameters_dict["leverage"]
            self.parameters_dict["initial_collateral_delta"] = self._calculate_initial_collateral_tokens(collateral_usd)
            return self.parameters_dict

        else:
            potential_missing_keys = '"size_delta_usd", "initial_collateral_delta", or "leverage"!'
            msg = f"Required keys are missing or provided incorrectly, please check: {potential_missing_keys}"
            raise Exception(msg)

    def _get_oracle_address_for_token(self, token_address: str, chain: str) -> str:
        """Map testnet token addresses to mainnet equivalents for oracle lookups."""
        # Testnet to mainnet token address mapping
        testnet_to_mainnet_tokens = {
            # Arbitrum Sepolia → Arbitrum mainnet
            "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
            "0xF79cE1Cf38A09D572b021B4C5548b75A14082F12": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",  # BTC
            "0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
            "0xD5DdAED48B09fa1D7944bd662CB05265FCD7077C": "0x2bcC6D6CdBbDC0a4071e48bb3B969b06B3330c07",  # SOL
        }

        # For testnet chains, map to mainnet addresses
        if chain in ["arbitrum_sepolia", "avalanche_fuji"]:
            return testnet_to_mainnet_tokens.get(token_address, token_address)
        return token_address

    def _calculate_initial_collateral_usd(self):
        """Calculate USD value of initial collateral tokens."""
        initial_collateral_delta_amount = self.parameters_dict["initial_collateral_delta"]
        prices = OraclePrices(self.parameters_dict["chain"]).get_recent_prices()

        # Map testnet address to mainnet for oracle lookup
        oracle_address = self._get_oracle_address_for_token(self.parameters_dict["start_token_address"], self.parameters_dict["chain"])

        price = np.median(
            [
                float(prices[oracle_address]["maxPriceFull"]),
                float(prices[oracle_address]["minPriceFull"]),
            ]
        )

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        oracle_factor = tokens[self.parameters_dict["start_token_address"]]["decimals"] - 30
        price = price * 10**oracle_factor

        return price * initial_collateral_delta_amount

    def _calculate_initial_collateral_tokens(self, collateral_usd: float):
        """Calculate token amount from USD value."""
        prices = OraclePrices(self.parameters_dict["chain"]).get_recent_prices()

        # Map testnet address to mainnet for oracle lookup
        oracle_address = self._get_oracle_address_for_token(self.parameters_dict["start_token_address"], self.parameters_dict["chain"])

        price = np.median(
            [
                float(prices[oracle_address]["maxPriceFull"]),
                float(prices[oracle_address]["minPriceFull"]),
            ]
        )

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        oracle_factor = tokens[self.parameters_dict["start_token_address"]]["decimals"] - 30
        price = price * 10**oracle_factor

        return collateral_usd / price

    def _format_size_info(self):
        """Convert amounts to wei with proper decimal precision."""
        if not self.is_swap:
            # USD amounts need 10**30 precision
            self.parameters_dict["size_delta"] = int(self.parameters_dict["size_delta_usd"] * 10**30)

        # Apply token-specific decimal factor - use start token for swaps, collateral token for positions
        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])

        if self.is_swap:
            # For swaps, use start token decimals
            decimal = tokens[self.parameters_dict["start_token_address"]]["decimals"]
        else:
            # For positions, collateral is in start_token initially, then swapped to collateral_token
            # So we need to use start_token decimals since initial_collateral_delta is in start_token
            decimal = tokens[self.parameters_dict["start_token_address"]]["decimals"]

        self.parameters_dict["initial_collateral_delta"] = int(self.parameters_dict["initial_collateral_delta"] * 10**decimal)

    def _check_if_max_leverage_exceeded(self):
        """Validate leverage doesn't exceed maximum (100x)."""
        collateral_usd_value = self._calculate_initial_collateral_usd
        leverage_requested = self.parameters_dict["size_delta_usd"] / collateral_usd_value()

        max_leverage = 100
        if leverage_requested > max_leverage:
            msg = f'Leverage requested "x{leverage_requested:.2f}" cannot exceed x100!'
            raise Exception(msg)
