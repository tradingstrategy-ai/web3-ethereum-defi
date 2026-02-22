# Contract sizes and compiler optimisation

Guard and Safe integration contracts are compiled with aggressive size optimisation
to stay within the [EIP-170 24,576-byte limit](https://eips.ethereum.org/EIPS/eip-170).

## Contract sizes

| Contract | Project | Size (bytes) | % of 24,576 limit | Margin (bytes) |
|----------|---------|-------------:|------------------:|---------------:|
| TradingStrategyModuleV0 | safe-integration | 21,522 | 87.6% | 3,054 |
| GuardV0 | guard | 19,072 | 77.6% | 5,504 |
| GmxLib | guard | 4,364 | 17.8% | 20,212 |
| HypercoreVaultLib | guard | 2,434 | 9.9% | 22,142 |
| SimpleVaultV0 | guard | 2,202 | 9.0% | 22,374 |
| CowSwapLib | guard | 1,895 | 7.7% | 22,681 |
| MockCoreWriter | guard | 1,632 | 6.6% | 22,944 |
| MockCoreDepositWallet | guard | 755 | 3.1% | 23,821 |
| MockSafe | safe-integration | 737 | 3.0% | 23,839 |

`TradingStrategyModuleV0` is the critical contract — it inherits all guard logic
from `GuardV0Base` and is closest to the EIP-170 limit.

## Compiler options

Both `contracts/guard/foundry.toml` and `contracts/safe-integration/foundry.toml`
use identical optimisation settings:

```toml
solc_version = "0.8.26"
evm_version = "cancun"
bytecode_hash = "none"

optimizer = true
optimizer_runs = 1
via_ir = true
```

### Option explanations

| Option | Effect | Savings |
|--------|--------|---------|
| `optimizer_runs = 1` | Optimise for minimal deployment size over execution gas cost. Value of 1 (vs default 200) tells the compiler to prefer smaller bytecode even if function calls cost slightly more gas at runtime. | Major |
| `via_ir = true` | Use the Yul IR pipeline for compilation. Enables better cross-function optimisation and dead code elimination compared to the legacy pipeline. See [via_ir analysis](#via_ir-analysis) below for why this setting is counterproductive for this contract. | See below |
| `bytecode_hash = "none"` | Removes the CBOR-encoded metadata hash appended to contract bytecode. This hash (typically ~50 bytes) encodes the compiler version and source code hash for verification. Safe to remove because metadata is available from the ABI JSON files. | ~50 bytes |
| `evm_version = "cancun"` | Enables `PUSH0` opcode (EIP-3855) which replaces `PUSH1 0x00` sequences, saving 1 byte per zero-value push. HyperEVM supports Cancun opcodes. | ~10-30 bytes |
| `solc_version = "0.8.26"` | Newer compiler versions sometimes generate tighter code through improved optimisation passes. | Incremental |

### Compiler settings comparison

TradingStrategyModuleV0 size under different compiler configurations (solc 0.8.26, cancun,
bytecode_hash=none unless noted). Configurations that exceed the 24,576-byte EIP-170 limit
are marked with a negative margin.

Note: The table below was measured before `GmxLib` extraction. With GmxLib extracted,
TSM is ~2,121 bytes smaller across all configurations.

| optimizer_runs | via_ir | TSM size (bytes) | Margin | GuardV0 | CowSwapLib | HypercoreVaultLib |
|---------------:|--------|----------------:|-------:|--------:|-----------:|------------------:|
| 1 | true | 23,643 | 933 | 21,150 | 1,895 | 2,434 |
| **1** | **false** | **22,106** | **2,470** | **19,632** | **2,027** | **2,497** |
| 200 | true | 23,851 | 725 | 21,198 | 1,934 | 2,512 |
| 200 | false | 22,618 | 1,958 | 20,053 | 2,024 | 2,751 |
| 1,000 | true | 23,058 | 1,518 | 20,216 | 2,077 | 2,846 |
| 1,000 | false | 25,730 | -1,154 | 23,441 | 2,143 | 2,930 |
| 10,000 | true | 26,221 | -1,645 | 22,989 | 2,470 | 3,448 |
| 10,000 | false | 26,576 | -2,000 | 24,649 | 2,492 | 3,576 |

Other settings tested:

| Variation | TSM size (bytes) | Margin |
|-----------|----------------:|-------:|
| bytecode_hash = "ipfs" (vs "none") | 23,684 | 892 |
| evm_version = "shanghai" (vs "cancun") | 23,676 | 900 |

### via_ir analysis

With `optimizer_runs=1`, **`via_ir=false` produces 1,537 bytes smaller bytecode** than
`via_ir=true` (22,106 vs 23,643). This is counterintuitive — the Yul IR pipeline is
designed for better cross-function optimisation. The reason lies in bytecode composition:

| Metric | via_ir=true | via_ir=false | Delta |
|--------|------------|-------------|------:|
| Total size | 23,643 | 22,106 | +1,537 |
| Opcode bytes | 14,653 | 14,071 | +582 |
| Push data bytes | 8,990 | 8,035 | +955 |
| Embedded string bytes | 2,430 | 1,520 | +910 |
| Embedded string count | 228 | 118 | +110 |
| REVERT instructions | 54 | 198 | -144 |
| PUSH0 count | 375 | 445 | -70 |
| PUSH1 count | 2,328 | 1,835 | +493 |
| PUSH2 count | 1,801 | 1,572 | +229 |

The IR pipeline generates more verbose error handling — nearly double the embedded string
bytes (2,430 vs 1,520). It uses fewer REVERT opcodes (54 vs 198) but more push data for
ABI-encoded revert strings. The legacy pipeline uses compact revert patterns with more
REVERT instructions and less string data.

This contract has characteristics that work against the IR pipeline's strengths:

- **83 public functions** — the IR pipeline creates more elaborate dispatch code
- **Large if-else dispatcher** with 20+ branches — IR cannot collapse this effectively
- **Many small validation functions** — inlining them may increase total size vs subroutines
- **Abundant revert strings** — IR generates fuller ABI-encoded revert data

With higher `optimizer_runs`, via_ir starts winning: at runs=1,000, via_ir=true is 23,058
vs via_ir=false at 25,730. The IR pipeline's cross-function optimisation helps when
optimising for execution gas. But at runs=1 (deployment size focus), the legacy pipeline wins.

### Bytecode composition (via_ir=true, runs=1, pre-GmxLib extraction)

This breakdown was measured before GMX extraction to GmxLib. After extraction,
TSM is 21,522 bytes; GMX validation now lives in the separate GmxLib (4,364 bytes).

| Category | Approx. bytes | % of total | Notes |
|----------|-------------:|----------:|----|
| Selector dispatcher (`_validateCallInternal`) | ~3,500 | 15% | 20+ if-else branches for protocol routing |
| GMX validation | ~3,000 | 13% | Now extracted to GmxLib |
| Whitelisting functions | ~2,800 | 12% | 16 functions, each with event emissions |
| Embedded revert strings | ~2,430 | 10% | 228 string fragments across all validators |
| Uniswap V2/V3 validation | ~1,800 | 8% | Multi-hop path validation loop |
| Velora swap validation | ~1,200 | 5% | Pre/post balance checks, event |
| CCTP validation | ~1,000 | 4% | Tuple unpacking, bytes32-to-address conversion |
| CowSwap delegation | ~1,000 | 4% | Library call + argument validation |
| ERC-4626 validation | ~1,000 | 4% | Multiple selector variants |
| View/query functions | ~800 | 3% | 31 simple mapping reads |
| Zodiac Module, Ownable, init, ABI encoding | ~4,500 | 19% | Inherited framework code |
| Other (Aave, Hypercore delegation, Lagoon, constants) | ~500 | 2% | Small validators |

### Further size reduction opportunities

If additional space is needed in future:

| Technique | Est. savings | Effort | Status |
|-----------|------------:|--------|--------|
| Extract GMX validation to `GmxLib` | ~2,121 bytes | New library with diamond storage | Done |
| Switch to `via_ir=false` | ~1,537 bytes | Config change only; slightly higher runtime gas | Available |
| Shorten revert strings (e.g. "GMX:R01" codes) | ~1,000 bytes | All validators; hurts debuggability | Available |
| Extract CCTP validation to `CctpLib` | ~800 bytes | New library | Available |
| Extract Velora validation to `VeloraLib` | ~1,000 bytes | New library | Available |

## Library pattern

External Forge libraries keep protocol-specific logic outside the main contract bytecode.
They use `DELEGATECALL` via Forge's library linking mechanism, so they have access to
the calling contract's storage through diamond storage slots.

| Library | Purpose | Size (bytes) | Storage slot |
|---------|---------|-------------:|-------------|
| `GmxLib` | GMX V2 perpetuals: router/market whitelisting, multicall validation | 4,364 | `keccak256("eth_defi.gmx.v1")` |
| `HypercoreVaultLib` | Hypercore vault validation and CoreWriter action checking | 2,434 | `keccak256("eth_defi.hypercore.vault.v1")` |
| `CowSwapLib` | CowSwap order creation, GPv2Order hashing, and presigning | 1,895 | `keccak256("eth_defi.cowswap.v1")` |

On chains where a library is not needed, it is linked with the zero address
(`0x0000...0000`) so the library code is never actually called and doesn't need
to be deployed.

## Code consolidation techniques

Applied to `GuardV0Base.sol` to reduce bytecode size:

- **Extract libraries**: CowSwap order creation and GPv2Order hashing (758 bytes saved)
  moved to `CowSwapLib`. Hypercore vault validation moved to `HypercoreVaultLib`.
  GMX V2 validation moved to `GmxLib` (2,121 bytes saved).
- **Merge identical branches**: Four separate Lagoon settlement selector branches
  that all called `validate_lagoonSettle(target)` were merged into a single
  `if` with OR conditions.
- **Remove dead code**: `validate_ERC4626Deposit()` and `validate_cowSwapSettlement()`
  were defined but never called. Removed entirely.
- **Inline empty stubs**: Three empty Orderly validator functions with only TODO
  comments were inlined as no-ops in the dispatcher.

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
