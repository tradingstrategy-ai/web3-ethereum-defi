"""
GMX Constants Module

This module provides contract addresses and ABIs for the GMX protocol.
"""

from pathlib import Path
import json

# Define the base path relative to this script
base_dir = Path(__file__).resolve().parent

# GMX Is maintaining these APIs and the official documentation can be found here: https://gmx-docs.io/docs/api/rest-v2
GMX_API_URLS: dict = {
    "arbitrum": "https://arbitrum-api.gmxinfra.io",
    "avalanche": "https://avalanche-api.gmxinfra.io",
}

GMX_API_URLS_BACKUP: dict = {
    "arbitrum": "https://arbitrum-api.gmxinfra2.io",
    "avalanche": "https://avalanche-api.gmxinfra2.io",
}

# Contract addresses by chain
GMX_EVENT_EMITTER_ADDRESS = {
    "arbitrum": "0xC8ee91A54287DB53897056e12D9819156D3822Fb",
    "avalanche": "0xDb17B211c34240B014ab6d61d4A31FA0C0e20c26",
}

GMX_DATASTORE_ADDRESS = {
    "arbitrum": "0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8",
    "avalanche": "0x2F0b22339414ADeD7D5F06f9D604c7fF5b2fe3f6",
}

GMX_READER_ADDRESS = {
    "arbitrum": "0x5Ca84c34a381434786738735265b9f3FD814b824",
    "avalanche": "0xBAD04dDcc5CC284A86493aFA75D2BEb970C72216",
}

GMX_EXCHANGE_ROUTER_ADDRESS = {
    "arbitrum": "0x900173A66dbD345006C51fA35fA3aB760FcD843b",
    "avalanche": "0x2b76df209E1343da5698AF0f8757f6170162e78b",
}

# Define the paths to ABI files
eventemitter_path = base_dir / "../" / "abi" / "gmx" / "eventemitter.json"

# Read and parse the JSON ABI file
GMX_EVENT_EMITTER_ABI = json.loads(eventemitter_path.read_text())


# Event signatures for GMX contracts
EVENT_SIGNATURES = {
    "UpdateFundingRate": "0xaa58a1c124fe8c67db114d6a19c3ef5b564f4ef3bd820f71e94473e846e3bb12",
    "IncreasePosition": "0x2fe68525253654c21998f35787a8d0f361bd444120e6c65920e8f7e9e4c26930",
    "DecreasePosition": "0xca28a6b76a3f6dc9124d60540e577c6adbd1e3ba0b52e013908b9ad5f15a4464",
    "LiquidatePosition": "0x2e1f85a5194ea85aa10539a6e819c82b7244e0a61ab25bd09627a29e2f7b996b",
    "SetPrice": "0x42b65f4eb3437d54b4e320a5863c8a1c28e539af1226161b7602ef73f567da5c",
}
