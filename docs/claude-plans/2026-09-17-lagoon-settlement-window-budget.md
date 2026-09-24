# Lagoon settlement-window budget

Status: **implemented**. Written 2026-09-17.

## Problem

GuardV0 previously allowed one non-zero Lagoon settlement and then applied a
24-hour cooldown. That is not a 5,000-USDC daily allowance: a 1-USDC bootstrap
settlement prevented a later 20-USDC investor settlement.

## Policy

For one GuardV0/Lagoon vault pair, use a fixed cumulative window:

- The first non-zero automated settlement starts the window.
- Each settlement consumes its gross flow: Silo deposits plus vault
  redemptions. Opposite directions never net.
- Accept a settlement when `used + gross <= cap`; reject it otherwise.
- The next non-zero settlement at or after expiry starts a new window.
- Empty, rejected and direct Safe settlements do not change the accounting.
- A direct Safe governance transaction remains outside the asset-manager
  policy. It still needs the Safe's normal authorisation.

This is deliberately a fixed, not rolling, window. A full window can settle
before expiry and another full window after it. Governance must not interpret
the cap as a rolling 24-hour throughput limit.

## Implementation

- Keep the existing Lagoon balance snapshot and post-call validation. It is the
  only point where the full settlement flow is known, and a validation revert
  atomically rolls back Lagoon, Safe and Guard state.
- Store `settlementWindow`, the active `windowStartTimestamp`, and
  `settledAmountInWindow` in the existing singleton Lagoon storage.
- Preserve the original Solidity cooldown-named selectors and event signature
  for ABI compatibility, but call their values a settlement-window duration in
  source, Python and documentation. Module version 4 / `v0.6` identifies the
  new return-value semantics.
- Configure the normal cap before Lighter bootstrap. The 20-USDC Lagoon
  subscription consumes the same budget as every automated settlement. The
  1-USDC Lighter activation transfer is a direct Safe transfer, not a Lagoon
  settlement.
- Avoid bootstrap exemptions, second configuration steps and multi-vault
  accounting.

## Tests

- 1 USDC then 20 USDC succeeds in one 5,000-USDC window and records 21 USDC.
- Exact-cap settlement succeeds; the next non-zero settlement reverts without
  changing state.
- Deposit and redemption flows are added as gross flow.
- Expiry resets the effective usage; empty settlements do not create or alter a
  window.
- Direct Safe settlement bypasses only the asset-manager policy.
- A fixed Ethereum Anvil fork deploys the Lighter bootstrap path, verifies
  20-USDC subscription, 1-USDC activation, 19-USDC Safe reserve, and an
  immediate normal 20-USDC investor settlement.

## Documentation

Describe the feature as a cumulative settlement-window budget, never as a
one-settlement-per-day cooldown. Document the fixed-window boundary and the
distinction between automated Lagoon settlements and direct Safe governance.
