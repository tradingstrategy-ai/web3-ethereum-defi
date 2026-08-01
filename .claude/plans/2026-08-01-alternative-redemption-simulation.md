# Alternative redemption simulation plan

## Goal

For a vault whose real redemption lifecycle is implemented but presently blocked
by liquidity, a request window or maturity, provide an Anvil-only alternative
that completes the real request, settlement and claim sequence. The alternative
must disclose every synthetic action and never weaken live execution.

## Scope

This repository owns manager-specific intervention drivers and their fork tests.
Trade-executor owns when to invoke them, report rendering and the final 129-vault
matrix.

The current targets are:

| Vault | Observed result | Driver |
|---|---|---|
| Pharaoh USDC (40acres, Avalanche) | `redemption_capacity_limited` | Inject the proven denomination-token shortfall and retry unchanged shares. |
| Arche USD (Yearn, Ethereum) | `redemption_zero_payout` | Diagnose the exact payout source first; add a driver only for the proven cause. |
| gTrade USDC (Gains, Base) | `redemption_window_closed` | Advance Anvil to the next valid request window, then settle and claim. |
| YieldNest RWA MAX (Ethereum) | `redemption_not_yet_matured` | Advance Anvil to maturity, then run the real lifecycle. |
| Aerodrome USDC (40acres, Base) | `transaction_reverted` | Diagnose the share-balance/reconciliation failure before adding a payout driver. |

Ember Apollo ACRED's minimum-aware amount and Upshift Sentora's accepted-asset
run are executor orchestration concerns. Eth-defi must expose the existing
minimum/asset metadata and execute their normal manager calls, but needs no
synthetic redemption driver for them.

## Contract

Add `prepare_redemption_simulation(owner, raw_shares, failure)` to
`VaultDepositManager`.

1. The base implementation raises `UnsupportedVaultSimulation`.
2. A concrete manager applies its own Anvil-only intervention and returns a
   disclosure record.
3. The caller retries the unchanged request and performs ordinary receipt
   analysis; this method never fabricates a receipt or trade success.
4. The record includes the original typed failure, intervention kind, token and
   raw amount where capital was injected, time before/after where Anvil time
   moved, privileged actor/function where used, and transaction hashes.
5. The method rejects non-Anvil providers.

Keep `force_settle()` responsible for asynchronous ticket settlement. Its result
must retain synthetic capital and any time/actor disclosure and remain terminal
only with a claimable ticket or proven positive direct payout.

## Implementation order

1. Add the base contract, disclosure schema and unsupported/non-Anvil tests.
2. Implement the 40acres liquidity driver for Pharaoh, using previewed payout
   and injecting only the shortfall into the real payout source.
3. Diagnose Arche and Aerodrome on pinned forks. Implement a manager driver
   only if the observed failure is a reproducible payout-capital shortfall.
4. Add Gains request-window advancement and settlement proof.
5. Add YieldNest maturity advancement and post-maturity redemption proof.
6. Add one pinned fork regression per implemented driver. Each proves the
   natural typed result first, then a positive real payout after intervention.

## Non-goals

- Do not bypass KYC, allow-lists, deposit-asset compatibility, or arbitrary
  paused/admin state.
- Do not mutate contract storage directly.
- Do not make fake capital available in normal execution, manual execution or a
  non-Anvil provider.
- Do not claim that an intervention success proves live vault liquidity.

## Acceptance criteria

- Every implemented driver exposes complete disclosure data and leaves the
  request, settlement and receipt analysis path real.
- Pharaoh, Gains and YieldNest have focused fork coverage of their observed
  failure plus alternative success path.
- Arche and Aerodrome have a pinned diagnosis; a driver is added only for a
  source-proven capital shortfall.
- Existing Morpho liquidity-intervention tests continue to pass unchanged.
