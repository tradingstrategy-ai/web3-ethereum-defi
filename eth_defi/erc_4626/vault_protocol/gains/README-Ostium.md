# Ostium vault V1.5.0 breaking change

## Summary

On 2026-04-28 Ostium upgraded their OLP vault proxy
(`0x20D419a8e12C45f88fDA7c5760bb6923Cee27F98`) to a new implementation
that **disables the standard ERC-4626 `deposit()` and `mint()` functions**.
The upgrade replaces synchronous deposits with an async
request/settle/claim flow.

## Timeline

| Event | Arbitrum block | Timestamp (UTC) | Reference |
|---|---|---|---|
| V1.5.0 implementation deployed | — | ~2026-04-28 | [GitHub commit](https://github.com/0xOstium/smart-contracts-public/commits/main) |
| Proxy upgraded to V1.5.0 impl `0xd2619e2012a120504e043f61c8acb3ede2472bf7` | 457,238,658 | 2026-04-28 13:03:25 | [Arbiscan tx](https://arbiscan.io/tx/0x3f25d52219c7a9b2469ac3582c6664940ede80da361b987bad6cab6336619363) |
| Previous impl was | `0x7912cf084eb45e7d2a2c10dda3e98136d12043fb` | — | — |
| V1.5.0 source published on GitHub | — | 2026-05-07 | [PR #3](https://github.com/0xOstium/smart-contracts-public/commits/main) |
| Our test trade failure | — | 2026-06-05 14:24 | — |

## What changed in V1.5.0

The [V1.5.0 OstiumVault.sol](https://github.com/0xOstium/smart-contracts-public/blob/main/src/OstiumVault.sol)
overrides ERC-4626 entry points to revert unconditionally:

```solidity
error FunctionDisabled();  // selector 0xbf241488

function deposit(uint256 assets, address receiver) public override returns (uint256) {
    revert FunctionDisabled();
}

function mint(uint256 shares, address receiver) public override returns (uint256) {
    revert FunctionDisabled();
}

function withdraw(uint256, address, address) public override returns (uint256) {
    revert FunctionDisabled();
}

function redeem(uint256, address, address) public override returns (uint256) {
    revert FunctionDisabled();
}
```

Deposits now use an async flow via the
[IOstiumVault](https://github.com/0xOstium/smart-contracts-public/blob/main/src/interfaces/IOstiumVault.sol)
interface:

1. `requestDeposit(uint256 assets)` — user submits USDC, queued for next settlement
2. Settlement executes daily 5-6 pm ET Mon-Fri (batches all pending requests)
3. `claimDeposit()` — user claims OLP shares after settlement

Deposits can be cancelled before settlement via `cancelRequestDeposit()`.
Failed settlements allow reclaiming via `reclaimDeposit()`.
Deposit status is queryable via `getDepositStatus()` which returns an enum
`{NONE, PENDING, CLAIMABLE, RECLAIMABLE}`.

Withdrawals use the same pattern: `requestWithdraw` → settlement →
`claimWithdraw`, with analogous cancel/reclaim paths.

### ERC-4626 spec violation

`maxDeposit()` still returns `type(uint256).max` even though `deposit()`
always reverts. Per the ERC-4626 specification, `maxDeposit()` must
return `0` when deposits are not possible. This is a bug in V1.5.0.

## Impact on eth_defi

### Detection (fixed)

`GainsVault.fetch_deposit_closed_reason()` now probes the vault with a
static `deposit(0, address(0))` call. If this reverts with the
`FunctionDisabled()` selector (`0xbf241488`), it returns
`DEPOSIT_CLOSED_FUNCTION_DISABLED`. This ensures the vault scanner
correctly flags Ostium deposits as closed.

### Trading support (not yet implemented)

To resume trading Ostium positions, the following changes are needed
in `eth_defi`:

1. **New ABI**: Update `eth_defi/abi/gains/OstiumVault.json` with the
   V1.5.0 ABI that includes `requestDeposit`, `claimDeposit`,
   `cancelRequestDeposit`, `reclaimDeposit`, `getDepositStatus`,
   `requestWithdraw`, `claimWithdraw`, `cancelRequestWithdraw`,
   `reclaimWithdraw`, `getWithdrawStatus`, and the settlement-related
   functions.

2. **Async deposit manager**: Extend `GainsDepositManager` (or create an
   `OstiumDepositManager`) to implement the request/settle/claim cycle.
   This is conceptually similar to ERC-7540 async vaults.

3. **Multi-tick execution**: The current trade executor processes a
   deposit in a single tick (approve → deposit → done). The new async
   flow requires:
   - Tick 1: `requestDeposit(assets)`
   - Wait for daily settlement (up to 24 h)
   - Tick 2: `claimDeposit()`

   This may require changes to the trade executor's trade lifecycle to
   support pending/multi-phase trades.

4. **Guard whitelisting**: The Lagoon `TradingStrategyModuleV0` guard
   needs `requestDeposit`, `claimDeposit`, `cancelRequestDeposit`, and
   `reclaimDeposit` call sites whitelisted via a new or updated
   `whitelistERC4626()` variant. The existing whitelist only covers
   `deposit`, `withdraw`, `redeem`, and `makeWithdrawRequest`.

5. **Valuation and settlement timing**: Share price changes at
   settlement, not at deposit time. Valuation models need to account
   for pending deposits that have not yet converted to shares.

## Manual script

`scripts/erc-4626/ostium-v15.py` — combined script for status checking,
deposits, and withdrawals. All transaction-sending actions require y/n
confirmation before broadcast.

| Variable | Description |
|----------|-------------|
| `JSON_RPC_ARBITRUM` | Arbitrum RPC URL (space-separated fallback format supported) |
| `ACTION` | `status` (default), `deposit`, or `withdraw` |
| `PRIVATE_KEY` | Private key for the signing wallet (required for deposit/withdraw) |
| `VAULT_ADDRESS` | Ostium vault address (default: `0x20d419a8e12c45f88fda7c5760bb6923cee27f98`) |
| `OWNER_ADDRESS` | Address to check status for (defaults to `PRIVATE_KEY` address) |
| `AMOUNT` | USDC amount for deposit, OLP share amount for withdrawal |
| `SETTLEMENT_ID` | Settlement ID for `--claim` / `--reclaim` modes |

### Examples

    # Check vault state and owner request status
    source .local-test.env && ACTION=status poetry run python scripts/erc-4626/ostium-v15.py
    source .local-test.env && ACTION=status OWNER_ADDRESS=0x... poetry run python scripts/erc-4626/ostium-v15.py

    # Request a deposit (approve + requestDeposit)
    source .local-test.env && ACTION=deposit AMOUNT=100 poetry run python scripts/erc-4626/ostium-v15.py

    # Claim deposit after settlement
    source .local-test.env && ACTION=deposit SETTLEMENT_ID=42 poetry run python scripts/erc-4626/ostium-v15.py --claim

    # Reclaim USDC after failed settlement
    source .local-test.env && ACTION=deposit SETTLEMENT_ID=42 poetry run python scripts/erc-4626/ostium-v15.py --reclaim

    # Request a withdrawal (requestWithdraw)
    source .local-test.env && ACTION=withdraw AMOUNT=50 poetry run python scripts/erc-4626/ostium-v15.py

    # Claim withdrawal after settlement
    source .local-test.env && ACTION=withdraw SETTLEMENT_ID=42 poetry run python scripts/erc-4626/ostium-v15.py --claim

    # Reclaim OLP shares after failed withdrawal settlement
    source .local-test.env && ACTION=withdraw SETTLEMENT_ID=42 poetry run python scripts/erc-4626/ostium-v15.py --reclaim

## Reference links

- [Ostium V1.5.0 source (current)](https://github.com/0xOstium/smart-contracts-public/blob/main/src/OstiumVault.sol)
- [Ostium V1.2.3 source (pre-upgrade, referenced in our code)](https://github.com/0xOstium/smart-contracts-public/blob/da3b944623bef814285b7f418d43e6a95f4ad4b1/src/OstiumVault.sol#L243)
- [IOstiumVault interface (V1.5.0)](https://github.com/0xOstium/smart-contracts-public/blob/main/src/interfaces/IOstiumVault.sol)
- [OLP Updates blog post](https://www.ostium.com/blog/olp-updates-a-more-seamless-vault-experience-for-liquidity-providers)
- [Vault proxy on Arbiscan](https://arbiscan.io/address/0x20d419a8e12c45f88fda7c5760bb6923cee27f98)
- [V1.5.0 implementation on Arbiscan](https://arbiscan.io/address/0xd2619e2012a120504e043f61c8acb3ede2472bf7)
- [Old implementation on Arbiscan](https://arbiscan.io/address/0x7912cf084eb45e7d2a2c10dda3e98136d12043fb)
- [Ostium docs (new)](https://docs.ostium.com)
- [Ostium docs (old, deprecated)](https://ostium-labs.gitbook.io/ostium-docs)
