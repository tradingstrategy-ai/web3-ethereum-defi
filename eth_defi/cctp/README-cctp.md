# Circle CCTP V2 integration

Python integration for [Circle's Cross-Chain Transfer Protocol (CCTP) V2](https://developers.circle.com/cctp), enabling cross-chain native USDC transfers through burn-and-mint.

## How CCTP works

CCTP enables trustless USDC transfers across blockchains using a burn-and-mint mechanism:

1. **Burn (source chain)**: User calls `depositForBurn()` on `TokenMessengerV2`, which burns USDC on the source chain
2. **Attest (off-chain)**: Circle's Iris attestation service observes the burn event and produces a cryptographic signature after block finality
3. **Mint (destination chain)**: Anyone relays the signed attestation by calling `receiveMessage()` on `MessageTransmitterV2`, which mints the equivalent USDC on the destination chain

### Token address resolution

Each chain has its own native USDC contract address (e.g. Ethereum `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`, Arbitrum `0xaf88d065e77c8cC2239327C5EDb3A432268e5831`). The `burnToken` parameter in `depositForBurn()` is always the **source chain's native USDC address**. On the destination chain, `TokenMinterV2` uses a `remoteTokensToLocalTokens` mapping (managed by Circle's `TokenController`) to resolve which local token to mint. Callers do not need to specify the destination USDC address.

### CCTP V2 vs V1

This integration targets CCTP V2 exclusively. V1 is deprecated (deprecation begins July 2026). V2 adds:
- Fast Transfer mode (faster-than-finality, with small fees)
- Post-transfer hooks for composability
- Broader chain support (17+ chains)

## Contract addresses

All CCTP V2 contracts share identical addresses across EVM chains (deployed via CREATE2):

| Contract | Address |
|----------|---------|
| TokenMessengerV2 | `0x28b5a0e9C621a5BadaA536219b3a228C8168cf5d` |
| MessageTransmitterV2 | `0x81D40F21F12A8F0E3252Bccb954D722d4c464B64` |
| TokenMinterV2 | `0xfd78EE919681417d192449715b2594ab58f5D002` |

### CCTP domain IDs

CCTP uses its own domain identifiers, not EVM chain IDs:

| Domain | Chain | Chain ID |
|--------|-------|----------|
| 0 | Ethereum | 1 |
| 3 | Arbitrum | 42161 |
| 6 | Base | 8453 |
| 7 | Polygon | 137 |

## Quick start

### Direct transfer (no vault guard)

```python
from web3 import Web3
from eth_defi.cctp.transfer import prepare_deposit_for_burn, prepare_approve_for_burn

web3 = Web3(Web3.HTTPProvider("https://..."))
sender = "0x..."

# 1. Approve USDC spending by TokenMessengerV2
approve_fn = prepare_approve_for_burn(web3, amount=1_000_000)  # 1 USDC
approve_fn.transact({"from": sender})

# 2. Initiate cross-chain transfer (Ethereum -> Arbitrum)
burn_fn = prepare_deposit_for_burn(
    web3,
    amount=1_000_000,         # 1 USDC (6 decimals)
    destination_chain_id=42161,  # Arbitrum
    mint_recipient="0x...",      # Recipient on Arbitrum
)
tx_receipt = web3.eth.wait_for_transaction_receipt(
    burn_fn.transact({"from": sender})
)
```

### Polling for attestation

```python
from eth_defi.cctp.attestation import fetch_attestation
from eth_defi.cctp.constants import CCTP_DOMAIN_ETHEREUM

attestation = fetch_attestation(
    source_domain=CCTP_DOMAIN_ETHEREUM,
    transaction_hash=tx_receipt.transactionHash.hex(),
    timeout=300.0,  # 5 minutes
)
```

### Completing the transfer on destination chain

```python
from eth_defi.cctp.receive import prepare_receive_message

web3_arbitrum = Web3(Web3.HTTPProvider("https://arb1..."))

receive_fn = prepare_receive_message(
    web3_arbitrum,
    message=attestation.message,
    attestation=attestation.attestation,
)
receive_fn.transact({"from": relayer})
```

## Guard whitelisting (Lagoon vaults)

When using CCTP through a guarded Lagoon vault, the guard contract must whitelist the CCTP contracts.

### During vault deployment

```python
from eth_defi.cctp.whitelist import CCTPDeployment

cctp = CCTPDeployment.create_for_chain(
    chain_id=1,                           # Source chain (Ethereum)
    allowed_destinations=[42161, 8453],   # Arbitrum, Base
)

deploy_automated_lagoon_vault(
    ...
    cctp_deployment=cctp,
)
```

### Manual whitelisting

```python
guard.functions.whitelistCCTP(
    "0x28b5a0e9C621a5BadaA536219b3a228C8168cf5d",  # TokenMessengerV2
    "Allow CCTP",
).transact({"from": safe_address})

guard.functions.whitelistCCTPDestination(
    3,  # Arbitrum domain
    "Allow transfers to Arbitrum",
).transact({"from": safe_address})
```

### Guard validation

The guard validates `depositForBurn()` calls by checking:
- **TokenMessengerV2** is whitelisted via `whitelistCCTP()`
- **Destination domain** is whitelisted via `whitelistCCTPDestination()`
- **Burn token** (USDC) is an allowed asset
- **Mint recipient** (converted from bytes32 to address) is an allowed receiver

## Security considerations

- **Destination domain whitelisting**: The guard restricts which chains USDC can be transferred to. Without this, a compromised asset manager could burn USDC to an unsupported chain.
- **Mint recipient validation**: The `mintRecipient` (bytes32) is converted to an address and checked against `allowedReceivers`. Ensures USDC is only minted to approved addresses.
- **Burn token validation**: Only whitelisted tokens (USDC) can be burned, preventing approval of arbitrary tokens to the TokenMessenger.
- **`destinationCaller`**: When set to bytes32(0), anyone can relay the attestation. For production use, consider setting this to a specific relayer address.

## Testnet

### Getting testnet tokens

**Testnet USDC** — Circle provides a [public faucet](https://faucet.circle.com/):

- **No account required**: 10 USDC per request, once every 24 hours per chain
- **Developer console** ([console.circle.com/faucet](https://console.circle.com/faucet)): 20 USDC per request, 10 requests per 24 hours (requires a free Circle developer account)

**Testnet ETH** (for gas) — use one of these faucets:

- [LearnWeb3 Arbitrum Sepolia faucet](https://learnweb3.io/faucets/arbitrum_sepolia/) — no account required
- [Alchemy Sepolia faucet](https://www.alchemy.com/faucets/ethereum-sepolia) — requires Alchemy account
- [Google Cloud Sepolia faucet](https://cloud.google.com/application/web3/faucet/ethereum/sepolia) — requires Google account

### Testnet USDC addresses

| Chain | USDC address |
|-------|-------------|
| Ethereum Sepolia | `0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238` |
| Arbitrum Sepolia | `0x75faf114eafb1BDbe2F0316DF893fd58CE46AA4d` |
| Base Sepolia | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |

### Testnet CCTP V2 contracts

Testnet contracts use **different** addresses from mainnet (still identical across all testnets via CREATE2):

| Contract | Testnet address |
|----------|----------------|
| TokenMessengerV2 | `0x8FE6B999Dc680CcFDD5Bf7EB0974218be2542DAA` |
| MessageTransmitterV2 | `0xE737e5cEBEEBa77EFE34D4aa090756590b1CE275` |
| TokenMinterV2 | `0xb43db544E2c27092c107639Ad201b3dEfAbcF192` |

### Testnet attestation API

Use the sandbox Iris API for testnet attestations:

```
https://iris-api-sandbox.circle.com
```

Example attestation poll:

```
GET https://iris-api-sandbox.circle.com/v2/messages/{sourceDomainId}?transactionHash={txHash}
```

### Manual testnet bridge walkthrough

1. Get Sepolia ETH from a faucet (for gas)
2. Get testnet USDC from [faucet.circle.com](https://faucet.circle.com/) on the source chain
3. Approve USDC to testnet TokenMessengerV2 (`0x8FE6B999Dc680CcFDD5Bf7EB0974218be2542DAA`)
4. Call `depositForBurn()` targeting the destination domain
5. Poll `https://iris-api-sandbox.circle.com/v2/messages/{domainId}?transactionHash={txHash}` until status is `complete`
6. Call `receiveMessage()` on the destination chain's MessageTransmitterV2 with the attestation

## Attestation timing

Per [Circle's block confirmation requirements](https://developers.circle.com/cctp/required-block-confirmations):

| Transfer mode | L2 chains (Arbitrum, Base) | Ethereum |
|---------------|---------------------------|----------|
| Standard (finalized) | ~65 ETH blocks (~15-19 min) | ~65 blocks (~15-19 min) |
| Fast (confirmed) | 1 block (~8 sec) | 1 block (~8 sec) |

**Testnet caveats:**

- Attestation times on Sepolia testnets can be **much longer** than mainnet. We observed 45+ minutes on Arbitrum Sepolia with no attestation completion, even with `FINALITY_THRESHOLD_FAST`.
- Both Arbitrum and Base have identical confirmation requirements per Circle's docs. The slow testnet attestation is likely caused by infrequent L2 batch posting to L1 Ethereum Sepolia, which delays Circle's Iris service from observing finality.
- The sandbox Iris API (`iris-api-sandbox.circle.com`) may also process attestations with different priority than production.

## Testing

```bash
# Fork integration tests (mainnet forks, no testnet USDC needed)
source .local-test.env && poetry run pytest tests/cctp/ -v
```

## See also

- [Circle CCTP documentation](https://developers.circle.com/cctp)
- [CCTP V2 contracts (GitHub)](https://github.com/circlefin/evm-cctp-contracts)
- [CCTP supported blockchains](https://developers.circle.com/cctp/cctp-supported-blockchains)
- [Attestation API reference](https://developers.circle.com/api-reference/cctp/all/get-attestation)
