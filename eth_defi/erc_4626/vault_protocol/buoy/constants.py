"""Reviewed Buoy deployments on HyperEVM.

Sources and accounting verification are recorded in ``eth_defi/abi/buoy/README.md``.
Unrecognised clones must not inherit the reviewed implementations' semantics.
"""

from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3

#: Buoy is a small protocol focused on prop trading, so keep thresholds low for now.
BUOY_MIN_TVL_USD = Decimal(500)

BUOY_CHAIN_ID = 999
BUOY_ROUTER = HexAddress("0x4f4e4e94b94d40600b30100a7f8e5e312f59738e")
BUOY_FACTORY = HexAddress("0x8c5e0ef4efbf57016d1b55d37e035f115d65a0a5")
#: Historical factories remain here even after Router retirement.
BUOY_FACTORIES = (BUOY_FACTORY,)
#: Clone implementation -> canonical interface; v1.1 is our intermediate-generation label.
BUOY_IMPLEMENTATIONS = {
    "0x27cd383395f366be39b5a38a08dc98775d665bb0": "BuoyVaultV1.json",
    "0x3fe85a627dcc8ff6896b1376beda7b1493f2c538": "BuoyVaultV1_1.json",
    "0xe412625761b8256832a018765dcf2ef8e7b0397b": "BuoyVaultV2.json",
    "0x47d0e7aca4246da0334f97d6840cf8559d771663": "BuoyVaultV3.json",
}
BUOY_V3_IMPLEMENTATION = "0x47d0e7aca4246da0334f97d6840cf8559d771663"
#: Both reviewed factory creation signatures, including the v3 policy fields.
BUOY_CREATED_TOPICS = tuple(
    Web3.to_hex(Web3.keccak(text=signature))
    for signature in (
        "VaultCreated(address,address,string,string,uint16)",
        "VaultCreated(address,address,string,string,uint16,uint16,uint256,bool)",
    )
)
