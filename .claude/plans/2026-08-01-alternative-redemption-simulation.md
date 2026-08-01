# Alternative redemption simulations

## Goal

Expose narrowly scoped, Anvil-only manager drivers for redemption states that
have a proven protocol transition. A driver changes fork state, then the caller
must retry and analyse the real redemption lifecycle. It never establishes
production availability.

## Implemented drivers

| Manager | Typed natural refusal | Fork preparation |
|---|---|---|
| Morpho Vault V2 | `redemption_liquidity_unavailable` | Provision the observed transfer shortfall, including bounded native-unit rounding, at the payout target |
| 40acres Pharaoh USDC | `redemption_capacity_limited` | Provision direct vault USDC until the address-scoped preflight accepts the unchanged request; receipt proves mechanics only because ERC-4626 assets change |
| Gains | `redemption_window_closed` / `EndOfEpoch` | Advance to the epoch boundary and call the public `forceNewEpoch()` transition |

`VaultDepositManager.prepare_redemption_simulation()` is deliberately
unsupported by default. Its disclosure can describe token funding or a time
transition, including the protocol transaction hash.

## Rules

1. Require an Anvil provider and the exact typed refusal.
2. Do not impersonate privileged identities or write generic vault state. The
   disclosed direct ERC-20 balance write is the sole exception and makes its
   payout counterfactual.
3. Do not change the owner or requested share amount.
4. Return disclosure data; do not fabricate a success result.
5. Fail closed when the protocol transition does not make the normal request
   constructible.

## Deferred investigation

Arche zero payout, YieldNest maturity/buffer, Ember minimum amount, Sentora
asset choice, Aerodrome satellite accounting and DeTrade timeout handling each
need their own verified source path before they can have a driver.

## Verification

The focused Morpho, Pharaoh and Gains fork tests must prove the natural refusal
first, apply the driver, then verify normal receipt analysis yields a positive
redemption outcome. A liquidity-injection amount is not an economic result.
