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

from eth_defi.abi import get_contract, get_deployed_contract
from eth_defi.gmx.constants import GMX_API_URLS, GMX_API_URLS_BACKUP


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


# GMX contract addresses by network
NETWORK_CONTRACTS = {
    """
    syntheticsreader is the Reader contract 
    syntheticsrouter is the Router contract
    """
    "arbitrum": ContractAddresses(
        datastore=to_checksum_address("0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8"),
        eventemitter=to_checksum_address("0xC8ee91A54287DB53897056e12D9819156D3822Fb"),
        exchangerouter=to_checksum_address("0x602b805EedddBbD9ddff44A7dcBD46cb07849685"),
        depositvault=to_checksum_address("0xF89e77e8Dc11691C9e8757e84aaFbCD8A67d7A55"),
        withdrawalvault=to_checksum_address("0x0628D46b5D145f183AdB6Ef1f2c97eD1C4701C55"),
        ordervault=to_checksum_address("0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5"),
        syntheticsreader=to_checksum_address("0x0537C767cDAC0726c76Bb89e92904fe28fd02fE1"),
        syntheticsrouter=to_checksum_address("0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6"),
        glvreader=to_checksum_address("0xd4f522c4339Ae0A90a156bd716715547e44Bed65"),
        chainlinkpricefeedprovider=to_checksum_address("0x527FB0bCfF63C47761039bB386cFE181A92a4701"),
        chainlinkdatastreamprovider=to_checksum_address("0xF4122dF7Be4Ccd46D7397dAf2387B3A14e53d967"),
        gmoracleprovider=to_checksum_address("0x5d6B84086DA6d4B0b6C0dF7E02f8a6A039226530"),
        orderhandler=to_checksum_address("0xfc9Bc118fdDb89FF6fF720840446D73478dE4153"),
        oracle=to_checksum_address("0x918b60bA71bAdfaDA72EF3A6C6F71d0C41D4785C"),
    ),
    "avalanche": ContractAddresses(
        datastore=to_checksum_address("0x2F0b22339414ADeD7D5F06f9D604c7fF5b2fe3f6"),
        eventemitter=to_checksum_address("0xDb17B211c34240B014ab6d61d4A31FA0C0e20c26"),
        exchangerouter=to_checksum_address("0x2b76df209E1343da5698AF0f8757f6170162e78b"),
        depositvault=to_checksum_address("0x90c670825d0C62ede1c5ee9571d6d9a17A722DFF"),
        withdrawalvault=to_checksum_address("0xf5F30B10141E1F63FC11eD772931A8294a591996"),
        ordervault=to_checksum_address("0xD3D60D22d415aD43b7e64b510D86A30f19B1B12C"),
        syntheticsreader=to_checksum_address("0x618fCEe30D9A26e8533C3B244CAd2D6486AFf655"),
        syntheticsrouter=to_checksum_address("0x820F5FfC5b525cD4d88Cd91aCf2c28F16530Cc68"),
        glvreader=to_checksum_address("0xae9596a1C438675AcC75f69d32E21Ac9c8fF99bD"),
    ),
}


# ABI loading function
def _load_abi(filename: str) -> list:
    """Load ABI from JSON file in the eth_defi/abi/gmx directory."""
    current_dir = Path(__file__).parent.parent
    abi_path = current_dir / "abi" / "gmx" / filename
    with open(abi_path, "r") as f:
        return json.load(f)


# TODO: Replace this to fetch the addresses dynamically from https://raw.githubusercontent.com/gmx-io/gmx-synthetics/refs/heads/v2.2-branch/docs/contracts.json
# Token addresses by network
NETWORK_TOKENS = {
    "arbitrum": {"WETH": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "WBTC": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", "USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "USDT": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", "ARB": "0x912CE59144191C1204E64559FE8253a0e49E6548", "LINK": "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4", "wstETH": "0x5979D7b546E38E414F7E9822514be443A4800529"},
    "avalanche": {"WAVAX": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7", "WETH": "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB", "WBTC": "0x50b7545627a5162F82A992c33b87aDc75187B218", "USDC": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", "USDT": "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7"},
}


def get_contract_addresses(chain: str) -> ContractAddresses:
    """
    Get GMX contract addresses for a specific network.

    :param chain: Network name ("arbitrum" or "avalanche")
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
                # Regular key
                clean_contracts[key] = value

    if chain not in clean_contracts:
        raise ValueError(f"Unsupported chain: {chain}. Supported: {list(clean_contracts.keys())}")

    return clean_contracts[chain]


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
    clean_api_urls = _get_clean_api_urls()
    clean_backup_urls = _get_clean_backup_urls()

    if chain not in clean_api_urls:
        raise ValueError(f"Unsupported chain: {chain}. Supported: {list(clean_api_urls.keys())}")

    base_url = clean_api_urls[chain]

    try:
        # Try primary API endpoint
        response = requests.get(f"{base_url}/tokens", timeout=10)
        response.raise_for_status()

        token_infos = response.json()["tokens"]

        # Convert to symbol -> address mapping
        tokens_dict = {}
        for token_info in token_infos:
            symbol = token_info.get("symbol", "").upper()
            address = token_info.get("address", "")
            if symbol and address:
                tokens_dict[symbol] = to_checksum_address(address)

        return tokens_dict

    except (requests.RequestException, KeyError, ValueError) as e:
        # Try backup API endpoint
        try:
            backup_url = clean_backup_urls[chain]
            response = requests.get(f"{backup_url}/tokens", timeout=10)
            response.raise_for_status()

            token_infos = response.json()["tokens"]

            # Convert to symbol -> address mapping
            tokens_dict = {}
            for token_info in token_infos:
                symbol = token_info.get("symbol", "").upper()
                address = token_info.get("address", "")
                if symbol and address:
                    tokens_dict[symbol] = to_checksum_address(address)

            return tokens_dict

        except (requests.RequestException, KeyError, ValueError):
            # Fall back to hardcoded tokens if API fails
            if chain in NETWORK_TOKENS:
                return NETWORK_TOKENS[chain]
            else:
                raise ValueError(f"Failed to fetch tokens for {chain} and no fallback available")


def get_token_address(chain: str, symbol: str) -> Optional[str]:
    """
    Get address for a specific token on a network.

    :param chain: Network name
    :param symbol: Token symbol
    :return: Token address or None if not found
    """
    tokens = get_tokens_address_dict(chain)
    return tokens.get(symbol.upper())


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
