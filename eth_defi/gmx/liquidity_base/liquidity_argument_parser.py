"""
GMX Liquidity Argument Parser

This module provides parameter parsing and validation for GMX liquidity operations.
Converts user-friendly parameters (symbols, USD amounts) into contract-ready format.
Migrated from gmx-python-sdk to support all chains including testnets.
"""

import numpy as np
from web3 import Web3

from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.contracts import get_tokens_address_dict
from eth_defi.token import fetch_erc20_details


def _get_token_metadata_dict(web3: Web3, chain: str) -> dict:
    """
    Get token metadata in SDK-compatible format.

    Converts our eth_defi format (symbol -> address) into SDK format
    (address -> {symbol, address, decimals}) required by ArgumentParser.

    :param web3: Web3 connection instance
    :param chain: Network name
    :return: Dictionary mapping addresses to token metadata
    """
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
        except Exception:
            # Fallback for tokens that might not be standard ERC20
            result[address] = {
                "symbol": symbol,
                "address": address,
                "decimals": 18,  # Default to 18 decimals
            }

    return result


class LiquidityArgumentParser:
    """
    Parses and validates liquidity operation parameters for GMX protocol.

    Converts user-friendly parameters into contract-ready format:
    - Symbol names → token addresses
    - USD amounts → wei amounts with proper decimals
    - Validates market compatibility
    """

    def __init__(self, config, is_deposit: bool = False, is_withdrawal: bool = False):
        """
        Initialize parser for specific liquidity operation type.

        :param config: GMXConfigManager with chain and network info
        :param is_deposit: True for deposit operations
        :param is_withdrawal: True for withdrawal operations
        """
        self.parameters_dict = None
        self.is_deposit = is_deposit
        self.is_withdrawal = is_withdrawal
        self.config = config

        # Get web3 connection - config is GMXConfigManager, so use get_web3_connection()
        self.web3 = config.get_web3_connection()

        if is_deposit:
            self.required_keys = [
                "chain",
                "market_key",
                "long_token_address",
                "short_token_address",
                "long_token_amount",
                "short_token_amount",
            ]

        if is_withdrawal:
            self.required_keys = [
                "chain",
                "market_key",
                "out_token_address",
                "gm_amount",
            ]

        self.missing_base_key_methods = {
            "chain": self._handle_missing_chain,
            "market_key": self._handle_missing_market_key,
            "long_token_address": self._handle_missing_long_token_address,
            "short_token_address": self._handle_missing_short_token_address,
            "long_token_amount": self._handle_missing_long_token_amount,
            "short_token_amount": self._handle_missing_short_token_amount,
            "out_token_address": self._handle_missing_out_token_address,
        }

    def process_parameters_dictionary(self, parameters_dict):
        """
        Process and validate liquidity operation parameters.

        :param parameters_dict: User-supplied parameters
        :return: Complete, validated parameters ready for contract interaction
        """
        # Find which keys are missing from required list
        missing_keys = self._determine_missing_keys(parameters_dict)

        self.parameters_dict = parameters_dict

        # Loop through missing keys and call required methods to resolve them
        for missing_key in missing_keys:
            if missing_key in self.missing_base_key_methods:
                self.missing_base_key_methods[missing_key]()

        # If withdrawal, convert GM amount to wei (18 decimals)
        if self.is_withdrawal:
            parameters_dict["gm_amount"] = int(parameters_dict["gm_amount"] * 10**18)

        return self.parameters_dict

    def _determine_missing_keys(self, parameters_dict):
        """Compare keys in dictionary to required keys for operation type."""
        return [key for key in self.required_keys if key not in parameters_dict]

    def _handle_missing_chain(self):
        """Chain must be supplied by user."""
        msg = "Please pass chain name in parameters dictionary!"
        raise Exception(msg)

    def _handle_missing_index_token_address(self):
        """Resolve market token address from symbol."""
        try:
            token_symbol = self.parameters_dict["market_token_symbol"]
        except KeyError:
            msg = "Market Token Address and Symbol not provided!"
            raise Exception(msg)

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        self.parameters_dict["market_token_address"] = self.find_key_by_symbol(tokens, token_symbol)

    def _handle_missing_market_key(self):
        """Resolve market key from market token address."""
        self._handle_missing_index_token_address()
        index_token_address = self.parameters_dict["market_token_address"]

        # Import GMXConfig to create proper config for Markets
        from eth_defi.gmx.config import GMXConfig

        gmx_config = GMXConfig(self.web3, user_wallet_address=self.config.user_wallet_address)

        # Use index token address to find market key from available markets
        self.parameters_dict["market_key"] = self.find_market_key_by_index_address(Markets(gmx_config).get_available_markets(), index_token_address)

    def _handle_missing_long_token_address(self):
        """Resolve long token address from symbol."""
        try:
            long_token_symbol = self.parameters_dict["long_token_symbol"]

            # Special handling for BTC on arbitrum
            if long_token_symbol == "BTC" and self.parameters_dict["chain"] == "arbitrum":
                self.parameters_dict["long_token_address"] = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
                return
            if long_token_symbol is None:
                raise KeyError
        except KeyError:
            self.parameters_dict["long_token_address"] = None
            return

        # Search known tokens for contract address using symbol
        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        self.parameters_dict["long_token_address"] = self.find_key_by_symbol(tokens, long_token_symbol)

    def _handle_missing_short_token_address(self):
        """Resolve short token address from symbol."""
        try:
            short_token_symbol = self.parameters_dict["short_token_symbol"]
            if short_token_symbol is None:
                raise KeyError
        except KeyError:
            self.parameters_dict["short_token_address"] = None
            return

        # Search known tokens for contract address using symbol
        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        self.parameters_dict["short_token_address"] = self.find_key_by_symbol(tokens, short_token_symbol)

    def _handle_missing_out_token_address(self):
        """Resolve output token address from symbol and validate compatibility."""
        try:
            out_token_symbol = self.parameters_dict["out_token_symbol"]
            if out_token_symbol is None:
                raise KeyError
        except KeyError:
            msg = "Must provide either out token symbol or address"
            raise Exception(msg)

        # Search known tokens for contract address using symbol
        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        out_token_address = self.find_key_by_symbol(tokens, out_token_symbol)

        # Import GMXConfig to create proper config for Markets
        from eth_defi.gmx.config import GMXConfig

        gmx_config = GMXConfig(self.web3, user_wallet_address=self.config.user_wallet_address)

        # Get market info to validate token is valid for this market
        markets = Markets(gmx_config).get_available_markets()
        market = markets[self.parameters_dict["market_key"]]

        # Special handling for BTC on arbitrum
        if out_token_symbol == "BTC" and self.parameters_dict["chain"] == "arbitrum":
            out_token_address = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"

        # Validate out token is either long or short token of the market
        if out_token_address not in [
            market["long_token_address"],
            market["short_token_address"],
        ]:
            msg = "Out token must be either the long or short token of the market"
            raise Exception(msg)
        else:
            self.parameters_dict["out_token_address"] = out_token_address

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

    def _handle_missing_long_token_amount(self):
        """Calculate long token amount from USD value using oracle prices."""
        if self.parameters_dict["long_token_address"] is None:
            self.parameters_dict["long_token_amount"] = 0
            return

        prices = OraclePrices(chain=self.config.chain).get_recent_prices()

        # Map testnet address to mainnet for oracle lookup
        oracle_address = self._get_oracle_address_for_token(self.parameters_dict["long_token_address"], self.parameters_dict["chain"])

        price = np.median(
            [
                float(prices[oracle_address]["maxPriceFull"]),
                float(prices[oracle_address]["minPriceFull"]),
            ]
        )

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        decimal = tokens[self.parameters_dict["long_token_address"]]["decimals"]
        oracle_factor = decimal - 30

        price = price * 10**oracle_factor

        self.parameters_dict["long_token_amount"] = int((self.parameters_dict["long_token_usd"] / price) * 10**decimal)

    def _handle_missing_short_token_amount(self):
        """Calculate short token amount from USD value using oracle prices."""
        if self.parameters_dict["short_token_address"] is None:
            self.parameters_dict["short_token_amount"] = 0
            return

        prices = OraclePrices(chain=self.parameters_dict["chain"]).get_recent_prices()

        # Map testnet address to mainnet for oracle lookup
        oracle_address = self._get_oracle_address_for_token(self.parameters_dict["short_token_address"], self.parameters_dict["chain"])

        price = np.median(
            [
                float(prices[oracle_address]["maxPriceFull"]),
                float(prices[oracle_address]["minPriceFull"]),
            ]
        )

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        decimal = tokens[self.parameters_dict["short_token_address"]]["decimals"]
        oracle_factor = decimal - 30

        price = price * 10**oracle_factor

        self.parameters_dict["short_token_amount"] = int((self.parameters_dict["short_token_usd"] / price) * 10**decimal)

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
