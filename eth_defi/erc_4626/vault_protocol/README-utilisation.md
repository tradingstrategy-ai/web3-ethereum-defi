# Utilisation and available liquidity metrics for lending vaults

This document describes the utilisation and available liquidity API for lending protocol vaults.

## Overview

Lending protocol vaults expose two key metrics:

1. **Available liquidity** - Amount of denomination token available for immediate withdrawal
2. **Utilisation percent** - Percentage of assets currently lent out (0.0 to 1.0)

## Supported protocols

| Protocol | Liquidity method | Utilisation formula |
|----------|-----------------|---------------------|
| Gearbox | `availableLiquidity()` | `totalBorrowed / (totalBorrowed + availableLiquidity)` |
| Euler EVK | `cash()` | `totalBorrows / (cash + totalBorrows)` |
| EulerEarn | `asset().balanceOf(vault)` | `(totalAssets - idle) / totalAssets` |
| Morpho V1 | `asset().balanceOf(vault)` | `(totalAssets - idle) / totalAssets` |
| Morpho V2 | `asset().balanceOf(vault)` | `(totalAssets - idle) / totalAssets` |
| IPOR | `asset().balanceOf(vault)` | `(totalAssets - idle) / totalAssets` |
| Llama Lend | `asset().balanceOf(vault)` | `(totalAssets - idle) / totalAssets` |
| Fluid | `asset().balanceOf(vault)` | `(totalAssets - idle) / totalAssets` |
| Silo | `getLiquidity()` | `getDebtAssets() / getCollateralAssets()` |

## Important limitation

**`maxRedeem(address(0))` does NOT work as a proxy for available liquidity** because:

- It requires a specific address that has already deposited shares
- For `address(0)`, `balanceOf` is always 0, so `maxRedeem` returns 0 regardless of actual liquidity
- This is documented in `can_check_redeem()` methods for each vault

## API usage

### Spot queries

```python
from eth_defi.erc_4626.classification import create_vault_instance_autodetect

vault = create_vault_instance_autodetect(web3, vault_address="0x...")

# Get available liquidity (Decimal in denomination token units)
available = vault.fetch_available_liquidity()

# Get utilisation (float between 0.0 and 1.0)
utilisation = vault.fetch_utilisation_percent()
```

### Historical data via multicall

Each lending protocol has a custom `VaultHistoricalReader` that inherits from `ERC4626HistoricalReader`:

```python
reader = vault.get_historical_reader(stateful=False)
calls = list(reader.construct_multicalls())

# Calls include utilisation-specific queries like:
# - "idle_assets" for idle assets pattern
# - "availableLiquidity" for Gearbox
# - "cash" and "totalBorrows" for Euler EVK
```

The `VaultHistoricalRead` dataclass includes:

```python
@dataclasses.dataclass(slots=True)
class VaultHistoricalRead:
    # ... other fields ...

    #: Available liquidity for immediate withdrawal.
    available_liquidity: Decimal | None = None

    #: Utilisation percentage of the lending vault.
    utilisation: Percent | None = None
```

### Identifying lending protocols

```python
from eth_defi.erc_4626.core import is_lending_protocol, LENDING_PROTOCOL_FEATURES

# Check if a vault is a lending protocol
if is_lending_protocol(vault.features):
    available = vault.fetch_available_liquidity()
    utilisation = vault.fetch_utilisation_percent()

# LENDING_PROTOCOL_FEATURES includes:
# - gearbox_like, ipor_like, euler_like, euler_earn_like
# - morpho_like, morpho_v2_like, llamma_like
# - fluid_like, silo_like
```

## Implementation pattern

### Idle assets pattern (most protocols)

Most protocols use the "idle assets" pattern where unallocated funds sit in the vault:

```python
def fetch_available_liquidity(self, block_identifier="latest") -> Decimal | None:
    denomination_token = self.denomination_token
    idle_raw = denomination_token.contract.functions.balanceOf(self.address).call(
        block_identifier=block_identifier
    )
    return denomination_token.convert_to_decimals(idle_raw)

def fetch_utilisation_percent(self, block_identifier="latest") -> Percent | None:
    total_assets = self.vault_contract.functions.totalAssets().call(
        block_identifier=block_identifier
    )
    idle = denomination_token.contract.functions.balanceOf(self.address).call(
        block_identifier=block_identifier
    )
    if total_assets == 0:
        return 0.0
    return (total_assets - idle) / total_assets
```

### Protocol-specific methods

Some protocols expose dedicated methods:

**Gearbox:**
```python
available = gearbox_contract.functions.availableLiquidity().call()
total_borrowed = gearbox_contract.functions.totalBorrowed().call()
utilisation = total_borrowed / (total_borrowed + available)
```

**Euler EVK:**
```python
cash = euler_contract.functions.cash().call()
total_borrows = euler_contract.functions.totalBorrows().call()
utilisation = total_borrows / (cash + total_borrows)
```

**Silo:**
```python
liquidity = silo_contract.functions.getLiquidity().call()
debt = silo_contract.functions.getDebtAssets().call()
collateral = silo_contract.functions.getCollateralAssets().call()
utilisation = debt / collateral
```

## Historical reader classes

| Protocol | Reader class |
|----------|--------------|
| Gearbox | `GearboxVaultHistoricalReader` |
| Euler EVK | `EulerVaultHistoricalReader` |
| EulerEarn | `EulerEarnVaultHistoricalReader` |
| Morpho V1 | `MorphoV1VaultHistoricalReader` |
| Morpho V2 | `MorphoV2VaultHistoricalReader` |
| IPOR | `IPORVaultHistoricalReader` |
| Llama Lend | `LlamaLendVaultHistoricalReader` |
| Fluid | `FluidVaultHistoricalReader` |
| Silo | `SiloVaultHistoricalReader` |

All readers inherit from `ERC4626HistoricalReader` and implement:

- `construct_multicalls()` - yields core ERC-4626 calls plus utilisation-specific calls
- `construct_utilisation_calls()` - yields protocol-specific utilisation calls
- `process_utilisation_result()` - decodes utilisation from multicall results
- `process_result()` - returns `VaultHistoricalRead` with `available_liquidity` and `utilisation` fields

## Testing the utilisation pipeline

### Unit tests

Each protocol has a test file that validates the utilisation API:

| Protocol | Test file | Chain | RPC variable |
|----------|-----------|-------|--------------|
| Fluid | `tests/erc_4626/vault_protocol/test_fluid.py` | Plasma | `JSON_RPC_PLASMA` |
| Silo | `tests/erc_4626/vault_protocol/test_silo.py` | Arbitrum | `JSON_RPC_ARBITRUM` |
| Gearbox | `tests/erc_4626/vault_protocol/test_gearbox.py` | Ethereum | `JSON_RPC_ETHEREUM` |
| IPOR | `tests/erc_4626/vault_protocol/test_ipor.py` | Ethereum | `JSON_RPC_ETHEREUM` |
| Morpho V2 | `tests/erc_4626/vault_protocol/test_morpho_v2.py` | Arbitrum | `JSON_RPC_ARBITRUM` |

Run tests with:

```bash
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_fluid.py -v
```

### Integration testing with vault scanner

To verify the full pipeline collects utilisation metrics:

```bash
# Scan a single chain with lending protocols
source .local-test.env && \
  TEST_CHAINS=Plasma SCAN_PRICES=true MAX_WORKERS=10 \
  poetry run python scripts/erc-4626/scan-vaults-all-chains.py

# Verify metadata contains utilisation
python -c "
import pickle
from pathlib import Path
vault_db_path = Path.home() / '.tradingstrategy/vaults/vault-metadata-db.pickle'
with open(vault_db_path, 'rb') as f:
    vault_db = pickle.load(f)
lending = [(s, r) for s, r in vault_db.items() if r.get('_available_liquidity')]
print(f'Found {len(lending)} lending vaults with utilisation data')
"
```

### Chains with lending protocol support

| Chain | Chain ID | Lending protocols |
|-------|----------|-------------------|
| Ethereum | 1 | Gearbox, Euler, EulerEarn, Morpho V1, IPOR, Llama Lend |
| Arbitrum | 42161 | Morpho V2, Gearbox, Silo |
| Base | 8453 | Morpho V1, IPOR, Fluid, Silo |
| Plasma | 9745 | Fluid |
