# Contract sizes and compiler optimisation

Guard and Safe integration contracts are compiled with aggressive size optimisation
to stay within the [EIP-170 24,576-byte limit](https://eips.ethereum.org/EIPS/eip-170).

## Contract sizes

| Contract | Project | Size (bytes) | % of 24,576 limit | Margin (bytes) |
|----------|---------|-------------:|------------------:|---------------:|
| TradingStrategyModuleV0 | safe-integration | 21,352 | 86.9% | 3,224 |
| GuardV0 | guard | 21,487 | 87.4% | 3,089 |
| GmxLib | guard | 5,165 | 21.0% | 19,411 |
| HypercoreVaultLib | guard | 3,761 | 15.3% | 20,815 |
| LagoonLib | guard | 3,205 | 13.0% | 21,371 |
| CowSwapLib | guard | 2,757 | 11.2% | 21,819 |
| UniswapLib | guard | 2,448 | 10.0% | 22,128 |
| VeloraLib | guard | 2,247 | 9.1% | 22,329 |
| SimpleVaultV0 | guard | 2,201 | 9.0% | 22,375 |
| LighterLib | guard | 1,862 | 7.6% | 22,714 |
| MockCoreWriter | guard | 1,632 | 6.6% | 22,944 |
| MockCoreDepositWallet | guard | 755 | 3.1% | 23,821 |
| MockSafe | safe-integration | 737 | 3.0% | 23,839 |

`TradingStrategyModuleV0` is the critical contract — it inherits all guard logic
from `GuardV0Base` and is closest to the EIP-170 limit.

## Compiler options

Both projects share the size-oriented compiler and optimiser settings below,
but deliberately use different Solidity compilation pipelines:

```toml
solc_version = "0.8.26"
evm_version = "cancun"
bytecode_hash = "none"

optimizer = true
optimizer_runs = 1

# contracts/guard/foundry.toml
via_ir = true

# contracts/safe-integration/foundry.toml
via_ir = false
```

### Option explanations

| Option | Effect | Savings |
|--------|--------|---------|
| `optimizer_runs = 1` | Optimise for minimal deployment size over execution gas cost. Value of 1 (vs default 200) tells the compiler to prefer smaller bytecode even if function calls cost slightly more gas at runtime. | Major |
| `via_ir = true` (Guard and libraries) | Use the Yul IR pipeline, which produces smaller deployed protocol libraries. | Library-specific |
| `via_ir = false` (TradingStrategyModuleV0) | Use the legacy compiler pipeline. With `optimizer_runs=1`, this avoids verbose IR-generated dispatch and error-handling bytecode in the large inherited module. | 2,491 bytes |
| `bytecode_hash = "none"` | Removes the CBOR-encoded metadata hash appended to contract bytecode. This hash (typically ~50 bytes) encodes the compiler version and source code hash for verification. Safe to remove because metadata is available from the ABI JSON files. | ~50 bytes |
| `evm_version = "cancun"` | Enables `PUSH0` opcode (EIP-3855) which replaces `PUSH1 0x00` sequences, saving 1 byte per zero-value push. HyperEVM supports Cancun opcodes. | ~10-30 bytes |
| `solc_version = "0.8.26"` | Newer compiler versions sometimes generate tighter code through improved optimisation passes. | Incremental |

### Compiler settings comparison

TradingStrategyModuleV0 size under different compiler configurations (solc 0.8.26, cancun,
bytecode_hash=none unless noted). Configurations that exceed the 24,576-byte EIP-170 limit
are marked with a negative margin.

Note: The table below was measured before library extraction (GmxLib, UniswapLib,
HypercoreVaultLib). With all libraries extracted, TSM is ~4,400 bytes smaller
across all configurations.

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

With the current generic post-call validation implementation and
`optimizer_runs=1`, **`via_ir=false` produces 2,491 bytes smaller module bytecode**
than `via_ir=true` (21,352 vs 23,843), increasing the EIP-170 margin from 733 to
3,224 bytes. The module ABI and Forge library names are identical under both
pipelines, so it can safely link to the smaller IR-compiled protocol libraries.

This is counterintuitive because the Yul IR pipeline is designed for better
cross-function optimisation. A historical pre-library-extraction bytecode
breakdown illustrates why the legacy pipeline works better for this module:

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

### Bytecode composition (via_ir=true, runs=1, pre-library extraction)

This breakdown was measured before library extraction. After extraction,
validation logic now lives in separate libraries
(GmxLib, UniswapLib, HypercoreVaultLib, CowSwapLib, VeloraLib, LagoonLib).

| Category | Approx. bytes | % of total | Notes |
|----------|-------------:|----------:|----|
| Selector dispatcher (`_validateCallInternal`) | ~3,500 | 15% | 20+ if-else branches for protocol routing |
| GMX validation | ~3,000 | 13% | Extracted to GmxLib |
| Whitelisting functions | ~2,800 | 12% | 16 functions, each with event emissions |
| Embedded revert strings | ~2,430 | 10% | 228 string fragments across all validators |
| Uniswap V2/V3 validation | ~1,800 | 8% | Extracted to UniswapLib |
| CCTP validation | ~1,000 | 4% | Tuple unpacking, bytes32-to-address conversion |
| CowSwap delegation | ~500 | 2% | isDeployed check + library call (validation consolidated in CowSwapLib) |
| Velora delegation | ~200 | 1% | isDeployed check + library calls (validation consolidated in VeloraLib) |
| ERC-4626 validation | ~1,000 | 4% | Multiple selector variants |
| View/query functions | ~800 | 3% | 31 simple mapping reads |
| Zodiac Module, Ownable, init, ABI encoding | ~4,500 | 19% | Inherited framework code |
| Hypercore validation | ~500 | 2% | Extracted to HypercoreVaultLib |
| Other (Aave, Lagoon, constants) | ~300 | 1% | Small validators |

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
| Switch TradingStrategyModuleV0 to `via_ir=false` | 2,491 bytes | Config change; potentially higher runtime gas | Done |
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
| `GmxLib` | GMX V2 perpetuals: router/market whitelisting, multicall validation | 5,165 | `keccak256("eth_defi.gmx.v1")` |
| `HypercoreVaultLib` | Hypercore vault deposit/action validation, CoreWriter checking | 3,761 | `keccak256("eth_defi.hypercore.vault.v1")` |
| `LagoonLib` | Lagoon allowlisting and atomic gross-settlement balance validation | 3,205 | `keccak256("eth_defi.lagoon.v1")` |
| `CowSwapLib` | CowSwap order creation, GPv2Order hashing, presigning, and swap validation | 2,757 | `keccak256("eth_defi.cowswap.v1")` |
| `UniswapLib` | Uniswap V2 swap path validation, V3 exactInput/exactOutput/SwapRouter02 recipient checks | 2,448 | None (stateless) |
| `VeloraLib` | Velora (ParaSwap) swapper whitelisting, swap validation, balance-envelope verification | 2,247 | `keccak256("eth_defi.velora.v1")` |
| `LighterLib` | Lighter deposits, withdrawals and asset-index validation | 1,862 | `keccak256("eth_defi.lighter.v1")` |

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
