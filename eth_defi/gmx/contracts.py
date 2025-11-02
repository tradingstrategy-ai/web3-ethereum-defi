"""
GMX Protocol Contract Infrastructure

This module provides contract addresses, ABIs, and utility functions for interacting
with GMX protocol contracts across supported networks.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract
from cchecksum import to_checksum_address

from eth_defi.abi import get_deployed_contract
from eth_defi.gmx.constants import GMX_API_URLS, GMX_API_URLS_BACKUP, GMX_CONTRACTS_JSON_URL
from eth_defi.gmx.api import GMXAPI


# Helper function to extract actual API URLs (filtering out docstring keys)
def _get_clean_api_urls() -> dict[str, str]:
    """Extract actual API URLs, filtering out docstring keys."""
    clean_urls = {}
    for key, value in GMX_API_URLS.items():
        if isinstance(value, str) and value.startswith("https://"):
            # Handle case where chain name is embedded at end of docstring key
            if key.endswith("arbitrum"):
                clean_urls["arbitrum"] = value
            elif key.endswith("avalanche"):
                clean_urls["avalanche"] = value
            else:
                # Regular key
                clean_urls[key] = value
    return clean_urls


def _get_clean_backup_urls() -> dict[str, str]:
    """Extract actual backup API URLs, filtering out docstring keys."""
    clean_urls = {}
    for key, value in GMX_API_URLS_BACKUP.items():
        if isinstance(value, str) and value.startswith("https://"):
            # Handle case where chain name is embedded at end of docstring key
            if key.endswith("arbitrum"):
                clean_urls["arbitrum"] = value
            elif key.endswith("avalanche"):
                clean_urls["avalanche"] = value
            else:
                # Regular key
                clean_urls[key] = value
    return clean_urls


@dataclass(slots=True)
class ContractAddresses:
    """GMX contract addresses for a specific network."""

    datastore: HexAddress
    eventemitter: HexAddress
    exchangerouter: HexAddress
    depositvault: HexAddress
    withdrawalvault: HexAddress
    ordervault: HexAddress
    syntheticsreader: HexAddress
    syntheticsrouter: HexAddress
    glvreader: HexAddress
    chainlinkpricefeedprovider: Optional[HexAddress] = None
    chainlinkdatastreamprovider: Optional[HexAddress] = None
    gmoracleprovider: Optional[HexAddress] = None
    orderhandler: Optional[HexAddress] = None
    oracle: Optional[HexAddress] = None


# Keep only networks that won't be fetched dynamically
NETWORK_CONTRACTS = {
    "arbitrum_sepolia": ContractAddresses(
        datastore=to_checksum_address("0xCF4c2C4c53157BcC01A596e3788fFF69cBBCD201"),
        eventemitter=to_checksum_address("0xa973c2692C1556E1a3d478e745e9a75624AEDc73"),
        exchangerouter=to_checksum_address("0x657F9215FA1e839FbA15cF44B1C00D95cF71ed10"),
        depositvault=to_checksum_address("0x809Ea82C394beB993c2b6B0d73b8FD07ab92DE5A"),
        withdrawalvault=to_checksum_address("0x7601c9dBbDCf1f5ED1E7Adba4EFd9f2cADa037A5"),
        ordervault=to_checksum_address("0x1b8AC606de71686fd2a1AEDEcb6E0EFba28909a2"),
        syntheticsreader=to_checksum_address("0x37a0A165389B2f959a04685aC8fc126739e86926"),
        syntheticsrouter=to_checksum_address("0x72F13a44C8ba16a678CAD549F17bc9e06d2B8bD2"),
        glvreader=to_checksum_address("0x4843D570c726cFb44574c1769f721a49c7e9c350"),
        chainlinkpricefeedprovider=to_checksum_address("0xa76BF7f977E80ac0bff49BDC98a27b7b070a937d"),
        chainlinkdatastreamprovider=to_checksum_address("0x13d6133F9ceE27B6C9A4559849553F10A45Bd9a4"),
        gmoracleprovider=to_checksum_address("0xFcE6f3D7a312C16ddA64dB049610f3fa4a477627"),
        orderhandler=to_checksum_address("0x96332063e9dAACF93A7379CCa13BC2C8Ff5809cb"),
        oracle=to_checksum_address("0x0dC4e24C63C24fE898Dda574C962Ba7Fbb146964"),
    ),
}


def _fetch_contract_addresses_from_url(chain: str) -> Optional[ContractAddresses]:
    """Fetch contract addresses for a chain from the GMX contracts.json URL."""
    # Fetch from URL
    url = GMX_CONTRACTS_JSON_URL
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        contracts_data = response.json()

        # Get contracts for the specified chain
        if chain not in contracts_data:
            return None

        contracts_list = contracts_data[chain]

        # Map contract names to addresses
        contract_map = {}
        for contract_info in contracts_list:
            name = contract_info.get("contractName", "")
            address = contract_info.get("contractAddress", "")
            if name and address:
                contract_map[name.lower()] = to_checksum_address(address)

        # Create ContractAddresses instance based on available contracts
        # Map the exact contract names to field names in ContractAddresses
        field_mappings = {
            "datastore": "datastore",
            "eventemitter": "eventemitter",
            "exchangerouter": "exchangerouter",
            "depositvault": "depositvault",
            "withdrawalvault": "withdrawalvault",
            "ordervault": "ordervault",
            "syntheticsreader": "reader",  # The synthetics reader is called "Reader" in the JSON
            "syntheticsrouter": "router",  # The synthetics router is called "Router" in the JSON
            "glvreader": "glvreader",
        }

        # Additional optional fields
        optional_field_mappings = {
            "chainlinkpricefeedprovider": "chainlinkpricefeedprovider",
            "chainlinkdatastreamprovider": "chainlinkdatastreamprovider",
            "gmoracleprovider": "gmoracleprovider",
            "orderhandler": "orderhandler",
            "oracle": "oracle",
        }

        # Build the contract addresses dict
        addresses_dict = {}

        # Map the required fields
        for field_name, contract_name in field_mappings.items():
            if contract_name.lower() in contract_map:
                addresses_dict[field_name] = contract_map[contract_name.lower()]
            else:
                # If we can't find the specific contract, we can't return a valid ContractAddresses
                return None

        # Map the optional fields
        for field_name, contract_name in optional_field_mappings.items():
            if contract_name.lower() in contract_map:
                addresses_dict[field_name] = contract_map[contract_name.lower()]

        # Create the ContractAddresses object
        contract_addresses = ContractAddresses(**addresses_dict)

        # Apply chain-specific overrides
        # Arbitrum: Use the latest ExchangeRouter from arbitrum-deployments.md
        # The contracts.json still contains the old ExchangeRouter (0x602b805...) which requires
        # ROUTER_PLUGIN authorization. The new ExchangeRouter (0x87d66368...) works without it.
        # Source: https://raw.githubusercontent.com/gmx-io/gmx-synthetics/refs/heads/main/docs/arbitrum-deployments.md
        if chain == "arbitrum":
            contract_addresses.exchangerouter = to_checksum_address("0x87d66368cD08a7Ca42252f5ab44B2fb6d1Fb8d15")

        return contract_addresses
    except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError) as e:
        # Return None if there's an error fetching or parsing
        return None


def _fetch_tokens_from_gmx_api(chain: str) -> Optional[dict[str, str]]:
    """Fetch token addresses for a chain from GMX API."""
    try:
        # Use the updated GMXAPI constructor that accepts chain directly
        api = GMXAPI(chain=chain)

        # Fetch tokens from GMX API
        token_data = api.get_tokens()
        token_infos = token_data.get("tokens", [])

        # Convert to symbol -> address mapping
        tokens_dict = {}
        for token_info in token_infos:
            symbol = token_info.get("symbol", "").upper()
            address = token_info.get("address", "")
            if symbol and address:
                tokens_dict[symbol] = to_checksum_address(address)

        return tokens_dict

    except Exception as e:
        # No fallback - raise error with helpful message
        raise ValueError(f"Failed to fetch token addresses for {chain} from GMX API. Error: {str(e)}. Please check your internet connection and try again.")


# ABI loading function
def _load_abi(filename: str) -> list:
    """Load ABI from JSON file in the eth_defi/abi/gmx directory."""
    current_dir = Path(__file__).parent.parent
    abi_path = current_dir / "abi" / "gmx" / filename
    with open(abi_path, "r") as f:
        return json.load(f)


# Token addresses by network - fallback values when API calls fail
# Token addresses by network - fallback values when API calls fail
NETWORK_TOKENS = {
    "arbitrum": {
        "WETH": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "ETH": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # ETH and WETH are treated the same for GMX
        "WBTC": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
        "USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "USDT": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        "ARB": "0x912CE59144191C1204E64559FE8253a0e49E6548",
        "LINK": "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4",
        "wstETH": "0x5979D7b546E38E414F7E9822514be443A4800529",
    },
    "avalanche": {
        "WAVAX": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",
        "AVAX": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",  # AVAX and WAVAX are treated the same for GMX
        "WETH": "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB",
        "WBTC": "0x50b7545627a5162F82A992c33b87aDc75187B218",
        "USDC": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
        "USDT": "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
    },
    "arbitrum_sepolia": {
        "WETH": "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73",
        "ETH": "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73",  # ETH and WETH are treated the same for GMX
        "BTC": "0xF79cE1Cf38A09D572b021B4C5548b75A14082F12",
        "USDC": "0x3321Fd36aEaB0d5CdfD26f4A3A93E2D2aAcCB99f",
        "USDC.SG": "0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773",
        "CRV": "0xD5DdAED48B09fa1D7944bd662CB05265FCD7077C",
    },
}


# Token metadata by network including symbol, decimals, and synthetic flag
NETWORK_TOKENS_METADATA = {
    "arbitrum_sepolia": {
        "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73": {
            "symbol": "WETH",
            "decimals": 18,
            "synthetic": False,
        },  # Also represents ETH
        "0x3321Fd36aEaB0d5CdfD26f4A3A93E2D2aAcCB99f": {
            "symbol": "USDC",
            "decimals": 6,
            "synthetic": False,
        },
        "0xF79cE1Cf38A09D572b021B4C5548b75A14082F12": {
            "symbol": "BTC",
            "decimals": 8,
            "synthetic": False,
        },
        "0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773": {
            "symbol": "USDC.SG",
            "decimals": 6,
            "synthetic": False,
        },
        "0xD5DdAED48B09fa1D7944bd662CB05265FCD7077C": {
            "symbol": "CRV",
            "decimals": 18,
            "synthetic": True,
        },
    },
}


# Testnet token to mainnet oracle address mapping
# Testnets don't have their own oracles, so we map testnet token addresses to mainnet
# token addresses for oracle price lookups
TESTNET_TO_MAINNET_ORACLE_TOKENS = {
    # Arbitrum Sepolia testnet → Arbitrum mainnet oracle addresses
    "0xD5DdAED48B09fa1D7944bd662CB05265FCD7077C": "0xe5f01aeAcc8288E9838A60016AB00d7b6675900b",  # CRV
    "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
    "0xF79cE1Cf38A09D572b021B4C5548b75A14082F12": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",  # BTC/WBTC
    "0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
    "0x3321Fd36aEaB0d5CdfD26f4A3A93E2D2aAcCB99f": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC.SG
}


def get_contract_addresses(chain: str) -> ContractAddresses:
    """
    Get GMX contract addresses for a specific network.

    :param chain: Network name ("arbitrum", "avalanche", or "arbitrum_sepolia")
    :return: Contract addresses for the network
    :raises ValueError: If chain is not supported
    """
    # Handle the docstring keys in NETWORK_CONTRACTS
    clean_contracts = {}
    for key, value in NETWORK_CONTRACTS.items():
        if isinstance(value, ContractAddresses):
            # Handle case where chain name is embedded at end of docstring key
            if key.endswith("arbitrum"):
                clean_contracts["arbitrum"] = value
            elif key.endswith("avalanche"):
                clean_contracts["avalanche"] = value
            else:
                # Regular key (like arbitrum_sepolia)
                clean_contracts[key] = value

    # For arbitrum and avalanche, always fetch from GMX API (no fallback)
    if chain == "arbitrum":
        # Fetch from GMX contracts.json URL
        dynamic_addresses = _fetch_contract_addresses_from_url("arbitrum")
        if dynamic_addresses is not None:
            return dynamic_addresses
        else:
            # No fallback - raise error
            raise ValueError(f"Failed to fetch contract addresses for {chain} from GMX API ({GMX_CONTRACTS_JSON_URL}). Please check your internet connection and try again. The API may be temporarily unavailable.")
    elif chain == "avalanche":
        # Fetch from GMX contracts.json URL
        dynamic_addresses = _fetch_contract_addresses_from_url("avalanche")
        if dynamic_addresses is not None:
            return dynamic_addresses
        else:
            # No fallback - raise error
            raise ValueError(f"Failed to fetch contract addresses for {chain} from GMX API ({GMX_CONTRACTS_JSON_URL}). Please check your internet connection and try again. The API may be temporarily unavailable.")
    elif chain in clean_contracts:
        # This will now properly handle arbitrum_sepolia and other non-dynamic networks
        return clean_contracts[chain]
    else:
        raise ValueError(
            f"Unsupported chain: {chain}. Supported: {list(clean_contracts.keys()) + ['arbitrum', 'avalanche']}",
        )


def get_reader_contract(web3: Web3, chain: str) -> Contract:
    """
    Get SyntheticsReader contract instance for a specific network.

    :param web3: Web3 connection instance
    :param chain: Network name
    :return: Web3 contract instance for SyntheticsReader
    """
    addresses = get_contract_addresses(chain)
    return get_deployed_contract(web3, "gmx/Reader.json", addresses.syntheticsreader)


def get_datastore_contract(web3: Web3, chain: str) -> Contract:
    """
    Get DataStore contract instance for a specific network.

    :param web3: Web3 connection instance
    :param chain: Network name
    :return: Web3 contract instance for DataStore
    """
    addresses = get_contract_addresses(chain)
    return get_deployed_contract(web3, "gmx/DataStore.json", addresses.datastore)


def get_tokens_address_dict(chain: str) -> dict[str, str]:
    """
    Get token address mapping for a specific network from GMX API.

    :param chain: Network name
    :return: Dictionary mapping token symbols to addresses
    :raises ValueError: If chain is not supported or API request fails
    """
    # Fetch tokens using GMXAPI - always from API, no fallback
    tokens_dict = _fetch_tokens_from_gmx_api(chain)
    if tokens_dict is not None:
        return tokens_dict
    else:
        raise ValueError(f"Failed to fetch token addresses for {chain} from GMX API. Please check your internet connection and try again.")


def get_token_address(chain: str, symbol: str, web3: Optional[Web3] = None) -> Optional[str]:
    """
    Get address for a specific token on a network.

    :param chain: Network name
    :param symbol: Token symbol
    :param web3: Web3 connection instance (optional, not required for API calls)
    :return: Token address or None if not found
    """
    return get_token_address_normalized(chain, symbol, web3)


def get_exchange_router_contract(web3: Web3, chain: str) -> Contract:
    """
    Get ExchangeRouter contract instance for a specific network.

    :param web3: Web3 connection instance
    :param chain: Network name
    :return: Web3 contract instance for ExchangeRouter
    """
    addresses = get_contract_addresses(chain)
    return get_deployed_contract(web3, "gmx/ExchangeRouter.json", addresses.exchangerouter)


def get_oracle_contract(web3: Web3, chain: str) -> Optional[Contract]:
    """
    Get Oracle contract instance for a specific network.

    :param web3: Web3 connection instance
    :param chain: Network name
    :return: Web3 contract instance for Oracle, or None if not available for the chain
    """
    addresses = get_contract_addresses(chain)
    if addresses.oracle:
        return get_deployed_contract(web3, "gmx/Oracle.json", addresses.oracle)
    return None


def get_glv_reader_contract(web3: Web3, chain: str) -> Contract:
    """
    Get GLV Reader contract instance for a specific network.

    :param web3: Web3 connection instance
    :param chain: Network name
    :return: Web3 contract instance for GLV Reader
    """
    addresses = get_contract_addresses(chain)
    return get_deployed_contract(web3, "gmx/GlvReader.json", addresses.glvreader)


def get_token_balance_contract(web3: Web3, contract_address: HexAddress) -> Contract:
    return get_deployed_contract(web3, "gmx/balance.json", contract_address)


def get_tokens_metadata_dict(chain: str) -> dict[str, dict]:
    """
    Get token metadata mapping for a specific network.

    :param chain: Network name
    :return: Dictionary mapping token addresses to metadata (symbol, decimals, synthetic)
    """
    return NETWORK_TOKENS_METADATA.get(chain, {})


def get_token_metadata(chain: str, address: str) -> Optional[dict]:
    """
    Get metadata for a specific token on a network.

    :param chain: Network name
    :param address: Token address
    :return: Token metadata dictionary or None if not found
    """
    tokens_metadata = get_tokens_metadata_dict(chain)
    return tokens_metadata.get(address)


def normalize_gmx_token_symbol(chain: str, token_symbol: str) -> str:
    """Normalize token symbol to the canonical form used by GMX for a given chain.

    On GMX, ETH and WETH are treated as the same token, as are AVAX and WAVAX.
    This function returns the canonical symbol that should be used to look up
    the token address.

    :param chain: Network name
    :param token_symbol: Original token symbol (e.g., "ETH", "WETH")
    :return: Canonical token symbol (e.g., always "WETH" for ETH/WETH on Arbitrum)
    """
    token_symbol_upper = token_symbol.upper()

    if chain in ["arbitrum", "arbitrum_sepolia"] and token_symbol_upper in ["ETH", "WETH"]:
        return "WETH"  # On Arbitrum chains, both ETH and WETH map to WETH
    elif chain in ["avalanche", "avalanche_fuji"] and token_symbol_upper in ["AVAX", "WAVAX"]:
        return "WAVAX"  # On Avalanche chains, both AVAX and WAVAX map to WAVAX
    else:
        return token_symbol_upper


def get_token_address_normalized(chain: str, symbol: str, web3: Optional[Web3] = None) -> Optional[str]:
    """Get address for a specific token on a network, with proper normalization for GMX.

    This function handles the special case where ETH and WETH are treated as the same
    token on GMX protocol, as well as AVAX and WAVAX on Avalanche.

    :param chain: Network name
    :param symbol: Token symbol (ETH/WETH will be normalized)
    :param web3: Web3 connection instance (optional, not required for API calls)
    :return: Token address or None if not found
    """
    normalized_symbol = normalize_gmx_token_symbol(chain, symbol)
    tokens = get_tokens_address_dict(chain)
    return tokens.get(normalized_symbol)
