# Vault reader states and warmup system

This document describes the vault reader state persistence and warmup system
used to detect and skip broken contract calls.

## Overview

Some vault contracts have methods that:
- Revert unexpectedly
- Use excessive gas (e.g., 36M gas for a single call)
- Are not implemented in certain vault types

The optional warmup helper records per-call outcomes in persistent reader state.
The common historical scanner currently **does not run warmup automatically**:
its `_run_warmup()` invocation is disabled. An absent outcome means unchecked,
not healthy. Individual readers handle unavailable contract methods during scans.

## How it works

### 1. Reader state persistence

Each vault has a [`VaultReaderState`](../vault.py) that tracks:
- Historical price reading metadata (TVL, share price, etc.)
- `call_status`: Map of function names to `(check_block, reverts)` tuples
- Latest and highest estimated USD TVL, from which the live price-row policy
  derives eligibility (enter at $1,500; leave below $1,000)

The all-chain scanner stores serialised state dictionaries keyed by `VaultSpec`
in `~/.tradingstrategy/vaults/vault-reader-state-1h.pickle`, or the directory
selected by `PIPELINE_DATA_DIR`. The single-chain price script uses
`reader-state.pickle` by default and accepts `READER_STATE_DATABASE`.

## TVL limits and row freshness

The reader converts denomination-token TVL using its existing estimated USD
exchange rate or a protocol override. Unknown conversions do not certify USD
TVL. Peaked and faded vaults poll weekly. Other vaults at $10,000 or more poll
hourly, those from $1,000 to $10,000 daily, and tiny vaults weekly after an
initial two-week daily sampling period. The live
scanner retains a genuine unchanged row early enough to target a maximum
14-day source-observation age for qualified vaults. A successful scan reports
overdue vaults when no current valid source observation can be obtained. The
Parquet `written_at` field only records publication time and does not refresh
the source `timestamp`. Without a new TVL read, the audit uses the last
observed TVL for eligibility; it cannot establish the vault's current TVL.

### 2. Warmup phase

`VaultHistoricalReadMulticaller._run_warmup()` remains available for explicit
checks, but its invocation inside `read_historical()` is commented out.
Enabling it broadly would add individual RPC reads for untested calls; assess
that cost and transient-failure handling before changing the scanner.

When explicitly invoked, warmup:
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

Use the read-only helper to report **recorded** failures. Its default is the
all-chain state file; set `READER_STATE_PATH` for a single-chain or legacy file.
Both current dictionary states and legacy reader objects are supported:

```bash
poetry run python scripts/erc-4626/check-reader-states.py
```

The report includes full vault addresses, recorded check blocks and counts by
chain. No recorded failures does not certify unchecked calls. The script makes
no RPC requests and does not modify reader progress.

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

Preserve the reader-state file. Losing it can restart a historical scan from
deployment and replace existing price rows; old Monad values may be impossible
to reconstruct. Back up the state using the normal pipeline backup process and
stop all writers before an address-scoped repair under the `scan-pipeline` lock.
Remove only the affected entry in that vault's `call_status` mapping, preserving
its block cursor and all other state. Publish the repaired pickle atomically.
Current states are dictionaries; older files may contain reader objects.

A targeted current-state check should establish whether the method actually
reverts before repairing an old outcome. Do not automatically treat a transport
failure as a deterministic contract failure.

## Known problematic vaults

| Chain | Vault address | Function | Issue |
|-------|---------------|----------|-------|
| Plasma | 0xa9C251F8304b1B3Fc2b9e8fcae78D94Eff82Ac66 | maxDeposit | Uses 36M gas (entire block limit) |

## Implementation files

| File | Purpose |
|------|---------|
| `eth_defi/erc_4626/vault.py` | VaultReaderState with call_status map |
| `eth_defi/erc_4626/warmup.py` | Warmup functions |
| `eth_defi/vault/historical.py` | VaultHistoricalReadMulticaller._run_warmup() |
| `scripts/erc-4626/check-reader-states.py` | Helper script to examine states |
