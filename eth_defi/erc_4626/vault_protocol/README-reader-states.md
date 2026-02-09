# Vault reader states and warmup system

This document describes the vault reader state persistence and warmup system
used to detect and skip broken contract calls.

## Overview

Some vault contracts have methods that:
- Revert unexpectedly
- Use excessive gas (e.g., 36M gas for a single call)
- Are not implemented in certain vault types

The warmup system detects these issues before running historical price scans
and stores the results in persistent reader state files.

## How it works

### 1. Reader state persistence

Each vault has a `VaultReaderState` that tracks:
- Historical price reading metadata (TVL, share price, etc.)
- `call_status`: Map of function names to `(check_block, reverts)` tuples

The state is persisted to `~/.tradingstrategy/vaults/reader-state.pickle`.

### 2. Warmup phase

Before running historical scans, `scan-prices.py` runs the warmup via `VaultHistoricalReadMulticaller._run_warmup()`:

```python
# Inside read_historical():
if stateful:
    self._run_warmup(readers, end_block)
```

The warmup:
1. Gets each reader's supported calls via `get_warmup_calls()`
2. Checks which calls haven't been tested yet
3. Tests each untested call individually
4. Records results: `(check_block, reverts)` tuple

### 3. Skipping broken calls

During historical scanning, readers check before yielding calls:

```python
if not self.should_skip_call("maxDeposit"):
    yield EncodedCall.from_contract_call(...)
```

### 4. Examining reader states

Use the helper script to see which calls are broken:

```bash
poetry run python scripts/erc-4626/check-reader-states.py
```

Output example:
```
Loaded 1234 reader states from ~/.tradingstrategy/vaults/reader-state.pickle

Total calls checked across all vaults: 4936

Found 3 broken calls:

+----------+---------------+-------------+--------------------+
| Chain    | Vault         | Function    | Detected at Block  |
+----------+---------------+-------------+--------------------+
| Plasma   | 0xa9C251F8... | maxDeposit  | 12,345,678         |
| Plasma   | 0xa9C251F8... | totalAssets | 12,345,678         |
| Arbitrum | 0x1234...     | getLiquidity| 98,765,432         |
+----------+---------------+-------------+--------------------+
```

## Supported calls by reader type

| Reader | Base calls | Protocol-specific calls |
|--------|------------|------------------------|
| ERC4626HistoricalReader | total_assets, total_supply, convertToAssets, maxDeposit | - |
| FluidVaultHistoricalReader | (base) | idle_assets |
| SiloVaultHistoricalReader | (base) | getLiquidity, getDebtAssets, getCollateralAssets |
| GearboxVaultHistoricalReader | (base) | availableLiquidity, totalBorrowed |
| EulerVaultHistoricalReader | (base) | cash, totalBorrows, interestFee |
| EulerEarnVaultHistoricalReader | (base) | idle_assets, fee |
| IPORVaultHistoricalReader | (base) | idle_assets, getPerformanceFeeData, getManagementFeeData |

## Adding support for new calls

1. Override `get_warmup_calls()` in the reader class to yield `(function_name, callable)` pairs
2. Call `yield from super().get_warmup_calls()` to include base calls
3. Wrap the call in `construct_*_calls()` with `if not self.should_skip_call("function_name"):`

Example:
```python
class MyProtocolHistoricalReader(ERC4626HistoricalReader):
    def get_warmup_calls(self) -> Iterable[tuple[str, Callable[[], None]]]:
        yield from super().get_warmup_calls()  # Include base ERC-4626 calls
        vault_contract = self.vault.vault_contract
        yield ("myCustomCall", lambda: vault_contract.functions.myCustomCall().call())

    def construct_utilisation_calls(self) -> Iterable[EncodedCall]:
        if not self.should_skip_call("myCustomCall"):
            yield EncodedCall.from_contract_call(
                self.vault.vault_contract.functions.myCustomCall(),
                extra_data={"function": "myCustomCall", "vault": self.vault.address},
                first_block_number=self.first_block,
            )
```

## Troubleshooting

### Warmup detects too many broken calls

Check if the RPC node is healthy. Warmup uses individual calls, not multicall,
so network issues can cause false positives.

### A call was incorrectly marked as broken

Delete the reader state file and re-run the scan:
```bash
rm ~/.tradingstrategy/vaults/reader-state.pickle
```

Or manually edit using Python:
```python
import pickle
from pathlib import Path

path = Path.home() / ".tradingstrategy/vaults/reader-state.pickle"
with open(path, "rb") as f:
    states = pickle.load(f)

# Fix a specific vault
key = (9745, "0xa9C251F8304b1B3Fc2b9e8fcae78D94Eff82Ac66".lower())
if key in states:
    states[key].call_status.pop("maxDeposit", None)

with open(path, "wb") as f:
    pickle.dump(states, f)
```

## Known problematic vaults

| Chain | Vault Address | Function | Issue |
|-------|---------------|----------|-------|
| Plasma | 0xa9C251F8304b1B3Fc2b9e8fcae78D94Eff82Ac66 | maxDeposit | Uses 36M gas (entire block limit) |

## Implementation files

| File | Purpose |
|------|---------|
| `eth_defi/erc_4626/vault.py` | VaultReaderState with call_status map |
| `eth_defi/erc_4626/warmup.py` | Warmup functions |
| `eth_defi/vault/historical.py` | VaultHistoricalReadMulticaller._run_warmup() |
| `scripts/erc-4626/check-reader-states.py` | Helper script to examine states |
