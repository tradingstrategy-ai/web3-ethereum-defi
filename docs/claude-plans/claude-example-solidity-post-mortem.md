# Post-mortem: Incorrect oracle address in Moonwell OEV wrapper PR

This document records a Claude Code investigation session analysing a bug in
[moonwell-fi/moonwell-contracts-v2 PR #578](https://github.com/moonwell-fi/moonwell-contracts-v2/pull/578),
which activates Chainlink OEV (Oracle Extractable Value) wrappers for remaining
Moonwell lending markets on Base and Optimism.

## Summary

The PR uses the wrong oracle for cbETH on Base. It wraps `cbETHETH_ORACLE`
(cbETH/ETH exchange rate, returning ~1.12) instead of `cbETH_ORACLE`
(cbETH/USD composite price, returning ~$2,267). The Moonwell ChainlinkOracle
passes the feed value through as USD, so cbETH collateral is priced at **$1.12
instead of ~$2,267** — a ~2,000x undervaluation that would cause mass
liquidations.

## The bug

In `proposals/ChainlinkOracleConfigs.sol`, the PR configures:

```solidity
_oracleConfigs[BASE_CHAIN_ID].push(
    OracleConfig("cbETHETH_ORACLE", "cbETH", "MOONWELL_cbETH")
);
```

This wraps `cbETHETH_ORACLE` at `0x806b4Ac04501c29769051e42783cF04dCE41440b`,
which returns the **cbETH/ETH exchange rate** (~1.1225, 18 decimals).

The correct oracle is `cbETH_ORACLE` at
`0xB0Ba0C5D7DA4ec400C1C3E5ef2485134F89918C5`, which returns the **cbETH/USD
composite price** (~$2,267, 18 decimals).

## On-chain verification

Querying `getUnderlyingPrice()` on the Moonwell ChainlinkOracle
(`0xEC942bE8A8114bFD0396A5052c36027f2cA6a9d0`) on Base:

| Token  | Price      | Correct? |
|--------|------------|----------|
| WETH   | $2,019.36  | Yes      |
| cbETH  | **$1.12**  | **No — should be ~$2,267** |
| rETH   | $2,338.03  | Yes      |
| wstETH | $2,475.87  | Yes      |
| weETH  | $2,195.66  | Yes      |

## Why the mistake was made

The PR excludes composite oracles because they don't support `latestRound()`,
which is required by the OEV wrapper. The excluded oracles all have `COMPOSITE`
in their name:

- `CHAINLINK_WSTETH_STETH_COMPOSITE_ORACLE`
- `CHAINLINK_RETH_ETH_COMPOSITE_ORACLE`
- `CHAINLINK_WEETH_USD_COMPOSITE_ORACLE`
- `CHAINLINK_wrsETH_COMPOSITE_ORACLE`
- `CHAINLINK_LBTC_BTC_COMPOSITE_ORACLE`

`cbETHETH_ORACLE` does **not** have `COMPOSITE` in its name, so it was
classified as a non-composite oracle and included. On-chain verification
confirms this classification logic:

```
cbETHETH_ORACLE (ETH rate):     latestRound() = 18446744073709552675 — SUPPORTED
cbETH_ORACLE (USD composite):   latestRound() — FAILED (execution reverted)
```

The author pattern-matched on naming convention (`COMPOSITE` → exclude) rather
than verifying what the oracle actually returns. `cbETHETH_ORACLE` supports
`latestRound()` but returns ETH-denominated data, not USD.

**This is most likely an AI-generated mistake.** The superficial name-based
classification (presence/absence of the word "COMPOSITE") is a textbook LLM
pattern-matching error. A human familiar with the oracle naming convention would
recognise that `cbETHETH` means "cbETH priced in ETH". The PR also has
automated Copilot review comments, and the changes are mechanical (systematically
uncomment everything not labelled COMPOSITE).

## Why the test suite didn't catch it

The proposal's `validate()` function in `mip-x43.sol` checks plumbing, never
prices:

| Validator                          | What it checks                                    |
|------------------------------------|---------------------------------------------------|
| `_validateFeedsPointToWrappers`    | feed address == wrapper address                   |
| `_validateCoreWrappersConstructor` | owner, feeRecipient, priceFeed addr, round params |
| `_validateDeprecatedWrappers`      | old != new, old != 0x0                            |
| `_validateMTokensWhitelisted`      | mToken whitelisted on fee redeemer                |
| `_validateExistingWrapperFees`     | fee == 3000 bps                                   |
| `_validateMorphoWrappersState`     | `answer > 0` (Morpho only, not core wrappers)     |

Every check asks *"is the wrapper wired correctly?"* No check asks *"does the
price make sense?"*

Even the Morpho validator's basic `answer > 0` check would pass (1.12 > 0),
and core wrappers have no price check at all.

### The missing test

A simple before/after price comparison would have caught this instantly:

```solidity
uint256 priceBefore = oracle.getUnderlyingPrice(mToken);
// ... setFeed to wrapper ...
uint256 priceAfter = oracle.getUnderlyingPrice(mToken);
assertApproxEqRel(priceAfter, priceBefore, 0.01e18, "Price deviation > 1%");
```

$1.12 vs $2,267 is a 99.95% deviation.

## Correct fix

cbETH should be excluded from this PR alongside the other composite oracles,
with a comment explaining why:

```solidity
// cbETH_ORACLE doesn't support latestRound(), deferred to follow-up
// _oracleConfigs[BASE_CHAIN_ID].push(
//     OracleConfig("cbETH_ORACLE", "cbETH", "MOONWELL_cbETH")
// );
```

## Severity

Critical. If deployed, cbETH collateral would be valued at $1.12 instead of
~$2,267, causing:

- Mass liquidation of existing cbETH positions
- Ability for attackers to borrow against nearly-free cbETH collateral
- Potential protocol insolvency

## Investigation method

This bug was found by Claude Code (claude-opus-4-6) through:

1. Reading the full PR diff via `gh pr diff`
2. Identifying the two cbETH oracles on Base (`cbETHETH_ORACLE` vs `cbETH_ORACLE`)
3. Querying both oracles on-chain via JSON-RPC to compare return values
4. Calling `getUnderlyingPrice()` on the Moonwell ChainlinkOracle to confirm
   the cbETH price was $1.12
5. Verifying `latestRound()` support to explain why the wrong oracle was chosen
6. Reviewing the test suite to understand why validation missed the bug
