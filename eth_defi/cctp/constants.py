"""Circle CCTP V2 constants.

Cross-Chain Transfer Protocol V2 deployment addresses and domain mappings.

CCTP enables burn-and-mint USDC transfers across chains:

1. Source chain: call :func:`depositForBurn` on TokenMessengerV2 to burn USDC
2. Circle's Iris attestation service signs the burn event
3. Destination chain: call :func:`receiveMessage` on MessageTransmitterV2 to mint USDC

The ``burnToken`` parameter is always the source chain's native USDC address.
On the destination chain, ``TokenMinterV2`` uses a ``remoteTokensToLocalTokens``
mapping (managed by Circle's ``TokenController``) to resolve which local token
to mint. The caller does not need to specify the destination USDC address.

All CCTP V2 contracts share the same address across all EVM chains (deployed via CREATE2).

- `CCTP V2 documentation <https://developers.circle.com/cctp>`_
- `EVM contract addresses <https://developers.circle.com/cctp/evm-smart-contracts>`_
- `Circle CCTP GitHub <https://github.com/circlefin/evm-cctp-contracts>`_
"""

from eth_typing import HexAddress


#: CCTP V2 TokenMessengerV2 - entry point for cross-chain USDC transfers.
#: Same address on all EVM chains via CREATE2.
TOKEN_MESSENGER_V2: HexAddress = HexAddress("0x28b5a0e9C621a5BadaA536219b3a228C8168cf5d")

#: CCTP V2 MessageTransmitterV2 - handles message passing and attestation verification.
#: Same address on all EVM chains via CREATE2.
MESSAGE_TRANSMITTER_V2: HexAddress = HexAddress("0x81D40F21F12A8F0E3252Bccb954D722d4c464B64")

#: CCTP V2 TokenMinterV2 - executes burning/minting of USDC.
#: Same address on all EVM chains via CREATE2.
TOKEN_MINTER_V2: HexAddress = HexAddress("0xfd78EE919681417d192449715b2594ab58f5D002")

#: CCTP domain ID for Ethereum mainnet
CCTP_DOMAIN_ETHEREUM = 0

#: CCTP domain ID for Arbitrum One
CCTP_DOMAIN_ARBITRUM = 3

#: CCTP domain ID for Base
CCTP_DOMAIN_BASE = 6

#: CCTP domain ID for Polygon PoS
CCTP_DOMAIN_POLYGON = 7

#: Mapping from EVM chain ID to CCTP domain ID.
#:
#: CCTP uses its own domain identifiers, not EVM chain IDs.
CHAIN_ID_TO_CCTP_DOMAIN: dict[int, int] = {
    1: CCTP_DOMAIN_ETHEREUM,
    42161: CCTP_DOMAIN_ARBITRUM,
    8453: CCTP_DOMAIN_BASE,
    137: CCTP_DOMAIN_POLYGON,
}

#: Reverse mapping from CCTP domain to EVM chain ID.
CCTP_DOMAIN_TO_CHAIN_ID: dict[int, int] = {v: k for k, v in CHAIN_ID_TO_CCTP_DOMAIN.items()}

#: Mapping from CCTP domain ID to human-readable chain name.
CCTP_DOMAIN_NAMES: dict[int, str] = {
    CCTP_DOMAIN_ETHEREUM: "Ethereum",
    CCTP_DOMAIN_ARBITRUM: "Arbitrum",
    CCTP_DOMAIN_BASE: "Base",
    CCTP_DOMAIN_POLYGON: "Polygon",
}

#: Circle Iris attestation API base URL (mainnet).
IRIS_API_BASE_URL = "https://iris-api.circle.com"

#: Minimum finality threshold for standard (finalized) transfers.
FINALITY_THRESHOLD_STANDARD = 2000

#: Minimum finality threshold for fast (confirmed) transfers.
#: Uses lower block confirmation, may incur fees.
FINALITY_THRESHOLD_FAST = 1000
