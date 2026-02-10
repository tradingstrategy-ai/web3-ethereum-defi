"""Velora (ParaSwap) contract addresses and constants.

See `Velora developer documentation <https://developers.velora.xyz>`__ for more details.
"""

from eth_typing import HexAddress

#: Velora API endpoint (still uses ParaSwap domain)
VELORA_API_URL = "https://api.paraswap.io"

#: Augustus Swapper v5 contract addresses per chain
#:
#: This is the main router contract that executes swaps.
#: Calldata from Velora API is executed on this contract.
VELORA_AUGUSTUS_SWAPPER: dict[int, HexAddress] = {
    1: HexAddress("0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57"),  # Ethereum
    42161: HexAddress("0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57"),  # Arbitrum
    137: HexAddress("0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57"),  # Polygon
    10: HexAddress("0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57"),  # Optimism
    43114: HexAddress("0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57"),  # Avalanche
    56: HexAddress("0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57"),  # BSC
    8453: HexAddress("0x59C7C832e96D2568bea6db468C1aAdcbbDa08A52"),  # Base
}

#: TokenTransferProxy contract addresses per chain
#:
#: IMPORTANT: Users must approve THIS contract for token spending, NOT Augustus Swapper.
#: Funds may be lost if approved to Augustus directly.
VELORA_TOKEN_TRANSFER_PROXY: dict[int, HexAddress] = {
    1: HexAddress("0x216b4b4ba9f3e719726886d34a177484278bfcae"),  # Ethereum
    42161: HexAddress("0x216B4B4Ba9F3e719726886d34a177484278Bfcae"),  # Arbitrum
    137: HexAddress("0x216b4b4ba9f3e719726886d34a177484278bfcae"),  # Polygon
    10: HexAddress("0x216B4B4Ba9F3e719726886d34a177484278Bfcae"),  # Optimism
    43114: HexAddress("0x216b4b4ba9f3e719726886d34a177484278bfcae"),  # Avalanche
    56: HexAddress("0x216b4b4ba9f3e719726886d34a177484278bfcae"),  # BSC
    8453: HexAddress("0x93aAAe79a53759cD164340E4C8766E4Db5331cD7"),  # Base
}

#: Supported chain IDs for Velora
VELORA_SUPPORTED_CHAINS = frozenset(VELORA_AUGUSTUS_SWAPPER.keys())
