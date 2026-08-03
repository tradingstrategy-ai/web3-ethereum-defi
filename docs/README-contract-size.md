# Contract sizes and compiler optimisation

Guard and Safe integration contracts are compiled with aggressive size optimisation
to stay within the [EIP-170 24,576-byte limit](https://eips.ethereum.org/EIPS/eip-170).

## Contract sizes

| Contract | Project | Size (bytes) | % of 24,576 limit | Margin (bytes) |
|----------|---------|-------------:|------------------:|---------------:|
| TradingStrategyModuleV0 | safe-integration | 23,816 | 96.9% | 760 |
| GuardV0 | guard | 21,691 | 88.3% | 2,885 |
| GmxLib | guard | 6,153 | 25.0% | 18,423 |
| HypercoreVaultLib | guard | 3,927 | 16.0% | 20,649 |
| LagoonLib | guard | 4,928 | 20.1% | 19,648 |
| CowSwapLib | guard | 3,464 | 14.1% | 21,112 |
| UniswapLib | guard | 3,333 | 13.6% | 21,243 |
| VeloraLib | guard | 2,686 | 10.9% | 21,890 |
| SimpleVaultV0 | guard | 2,058 | 8.4% | 22,518 |
| LighterLib | guard | 2,370 | 9.6% | 22,206 |
| MockCoreWriter | guard | 2,010 | 8.2% | 22,566 |
| MockCoreDepositWallet | guard | 1,101 | 4.5% | 23,475 |
| MockSafe | safe-integration | 1,408 | 5.7% | 23,168 |

`TradingStrategyModuleV0` is the critical contract — it inherits all guard logic
from `GuardV0Base` and is closest to the EIP-170 limit.

## Compiler options

Both projects use the same size-oriented compiler and legacy pipeline:

```toml
solc_version = "0.8.26"
evm_version = "cancun"
bytecode_hash = "none"

optimizer = true
optimizer_runs = 1

# contracts/guard/foundry.toml and contracts/safe-integration/foundry.toml
via_ir = false
```

### Option explanations

| Option | Effect | Savings |
|--------|--------|---------|
| `optimizer_runs = 1` | Optimise for minimal deployment size over execution gas cost. Value of 1 (vs default 200) tells the compiler to prefer smaller bytecode even if function calls cost slightly more gas at runtime. | Major |
| `via_ir = false` | Use the legacy compiler pipeline. With `optimizer_runs=1`, it keeps both large dispatch contracts deployable with material EIP-170 margin. | Major |
| `bytecode_hash = "none"` | Removes the CBOR-encoded metadata hash appended to contract bytecode. This hash (typically ~50 bytes) encodes the compiler version and source code hash for verification. Safe to remove because metadata is available from the ABI JSON files. | ~50 bytes |
| `evm_version = "cancun"` | Enables `PUSH0` opcode (EIP-3855) which replaces `PUSH1 0x00` sequences, saving 1 byte per zero-value push. HyperEVM supports Cancun opcodes. | ~10-30 bytes |
| `solc_version = "0.8.26"` | Newer compiler versions sometimes generate tighter code through improved optimisation passes. | Incremental |

### Pipeline choice

The Yul IR pipeline can make individual protocol libraries smaller, but at
`optimizer_runs=1` it makes the two large guard dispatch contracts too large.
The legacy pipeline provides the current deployable margins above. Re-run both
size builds after changing a guard rule or compiler setting; do not optimise a
library in isolation.

### Further size reduction opportunities

If additional space is needed in future:

| Technique | Est. savings | Effort | Status |
|-----------|------------:|--------|--------|
| Extract GMX validation to `GmxLib` | ~2,121 bytes | New library with diamond storage | Done |
| Extract Uniswap V2/V3 validation to `UniswapLib` | ~1,800 bytes | New library with IGuardChecks callbacks | Done |
| Extract Hypercore validation to `HypercoreVaultLib` | ~500 bytes | Consolidated validateCall() entry point | Done |
| Consolidate CowSwap validation into `CowSwapLib` | ~550 bytes | Combined validate+create function | Done |
| Consolidate Velora validation into `VeloraLib` | ~450 bytes | Combined validate+balance function | Done |
| Error bubbling helper | ~150 bytes | Shared `_bubbleUpRevert()` in module | Done |
| Use `via_ir=false` for GuardV0 and TradingStrategyModuleV0 | Major | Config change; potentially higher runtime gas | Done |
| Shorten revert strings (e.g. "GMX:R01" codes) | ~1,000 bytes | All validators; hurts debuggability | Available |
| Extract CCTP validation to `CctpLib` | ~800 bytes | New library | Available |

## Library pattern

External Forge libraries keep protocol-specific logic outside the main contract bytecode.
They use `DELEGATECALL` via Forge's library linking mechanism, so they have access to
the calling contract's storage through diamond storage slots.

Libraries that need cross-cutting permission checks (sender, asset, receiver validation)
use the `IGuardChecks` callback interface — they call `IGuardChecks(address(this)).isAllowed*()`
which resolves to the calling contract's public view functions via a regular CALL in the
DELEGATECALL context.

| Library | Purpose | Size (bytes) | Storage slot |
|---------|---------|-------------:|-------------|
| `GmxLib` | GMX V2 perpetuals: router/market whitelisting, multicall validation | 6,153 | `keccak256("eth_defi.gmx.v1")` |
| `HypercoreVaultLib` | Hypercore vault deposit/action validation, CoreWriter checking | 3,927 | `keccak256("eth_defi.hypercore.vault.v1")` |
| `LagoonLib` | Lagoon allowlisting, atomic gross-settlement validation and cooldown state | 4,928 | `keccak256("eth_defi.lagoon.v1")` |
| `CowSwapLib` | CowSwap order creation, GPv2Order hashing, presigning, and swap validation | 3,464 | `keccak256("eth_defi.cowswap.v1")` |
| `UniswapLib` | Uniswap V2 swap path validation, V3 exactInput/exactOutput/SwapRouter02 recipient checks | 3,333 | None (stateless) |
| `VeloraLib` | Velora (ParaSwap) swapper whitelisting, swap validation, balance-envelope verification | 2,686 | `keccak256("eth_defi.velora.v1")` |
| `LighterLib` | Lighter deposits, withdrawals and asset-index validation | 2,370 | `keccak256("eth_defi.lighter.v1")` |

On chains where a library is not needed, it is linked with the zero address
(`0x0000...0000`) so the library code is never actually called and doesn't need
to be deployed.

## Code consolidation techniques

Applied to `GuardV0Base.sol` to reduce bytecode size:

- **Extract protocol libraries**: Protocol-specific validation extracted into external
  Forge libraries using `DELEGATECALL` and diamond storage. Libraries use `IGuardChecks`
  callbacks for cross-cutting permission checks (sender, asset, receiver validation).
  - `GmxLib` — GMX V2 multicall validation (~2,121 bytes saved)
  - `UniswapLib` — Uniswap V2/V3 swap path and recipient validation (~1,800 bytes saved)
  - `HypercoreVaultLib` — Hypercore deposit/action validation with consolidated
    `validateCall()` entry point (~500 bytes saved)
  - `CowSwapLib` — CowSwap order creation and GPv2Order hashing (~758 bytes saved)
  - `VeloraLib` — Velora swap validation with balance-envelope verification
- **Consolidate validation into libraries**: CowSwap and Velora swap validation
  (sender, token, receiver checks) consolidated into their respective libraries
  using `IGuardChecks` callbacks (~733 bytes saved from module).
- **Error bubbling helper**: Duplicate revert-reason assembly blocks replaced with
  shared `_bubbleUpRevert()` private function (~150 bytes saved).
- **Merge identical branches**: Four separate Lagoon settlement selector branches
  that all called `validate_lagoonSettle(target)` were merged into a single
  `if` with OR conditions.
- **Remove dead code**: `validate_ERC4626Deposit()` and `validate_cowSwapSettlement()`
  were defined but never called. Removed entirely.
- **Remove dead code (Orderly)**: Orderly stub selectors and whitelisting function
  removed entirely (protocol integration abandoned).

## Checking sizes

Build and check sizes:

```shell
make guard safe-integration
```

Then inspect the deployed bytecode in the ABI JSON files:

```python
import json
from pathlib import Path

data = json.loads(Path("eth_defi/abi/safe-integration/TradingStrategyModuleV0.json").read_text())
bc = data["deployedBytecode"]["object"]
if bc.startswith("0x"):
    bc = bc[2:]
print(f"Size: {len(bc) // 2:,} bytes (limit: 24,576)")
```

Or use Forge directly:

```shell
cd contracts/guard && forge build --sizes
cd contracts/safe-integration && forge build --sizes
```
