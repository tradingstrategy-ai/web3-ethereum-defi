# Vault integration through the existing framework

Read this before changing EVM ERC-4626-style vault adapters, their guard
deployment or executor integration, including queued variants.
The [protocol support contract](../../eth_defi/erc_4626/README-vault-protocol-support.md)
defines required behaviour and guarded coverage. Lagoon's
`LagoonDepositManager(GenericERC7540DepositManager)` is the reference for
reusing queued execution; a different ABI needs adapter methods, not another
request/settlement framework.

## Put each responsibility in its existing layer

| Responsibility | Existing owner |
|---|---|
| Discover features and construct protocol adapters, with execution where supported | `detect_vault_features()` and `create_vault_instance()` in `eth_defi/erc_4626/classification.py` |
| Contract calls, approvals, preflights, receipt parsing and ticket fields | The protocol's `VaultDepositManager` implementation |
| Select and configure delegated guard policies | `setup_guard()` in `eth_defi/erc_4626/vault_protocol/lagoon/deployment.py` |
| Queue status, claim execution, recovery and simulation | Existing manager methods and request/ticket classes |
| Account admission, denomination and capability metadata | Existing vault accessors, scanner resolver and exports |
| Trade state, accounting and settlement retry | Trade-executor's existing routing, `TradeExecution`, state store and settlement resolver |

Before adding a helper or branch, identify the missing behaviour and its owner
in this table. Check Lagoon, Atoma and a synchronous ERC-4626 integration first.
Keep the change in that owner. Do not add a one-caller factory wrapper mainly
to give a mocked test something to call, or repeat protocol selection in CLI
commands, universe builders and simulation controllers.

## Deployment descriptors are not execution adapters

For guard configuration, callers carry an ordinary `ERC4626Vault` descriptor
with the vault's identity, token cache and known features:

```python
descriptor = ERC4626Vault(
    web3,
    spec,
    token_cache=token_cache,
    features=known_features or None,
)
```

Pass these descriptors through the existing deployment configuration to
`setup_guard()`. That function currently resolves Atoma and Upshift through
the shared factory and selects their existing guard policies. Other protocol
flags retain the generic descriptor; do not feed every detected protocol into
this path and assume the factory always returns an `ERC4626Vault` subclass.

Plain `ERC4626Vault` descriptors without features are detected inside guard
setup. Metadata-only subclasses without features are deliberately not probed
there; unsupported diagnostic adapters remain isolated. Resolve every required
policy before the first guard-policy broadcast. This does not undo deployment
transactions that happened before `setup_guard()` was called.

Actual trading must use the protocol adapter selected by the factory. A
deployment descriptor does not prove executable deposit/redemption support.
Keep separate LP-token addresses and non-standard selectors in the adapter
and shared guard configuration. Reuse existing validated selectors and token
allow-list primitives; never bypass receiver, owner or approval checks to make
a generic descriptor appear supported.

## Extend only the missing adapter behaviour

Use existing request construction, ticket serialisation, status, finish and
receipt-analysis methods. The shared hooks already cover these exceptions:

- `create_redemption_request_for_analysis()` rebuilds a mined request's parser
  without rerunning balance or admission checks that the request invalidated.
- `fetch_pending_redemption_value()` values an entitlement when ordinary
  share valuation no longer represents it; it never credits reserve cash.
- `fetch_completed_redemption_tx_hash()` finds and validates an external payout
  through the protocol's existing recovery surface.

Add another hook only for a demonstrated gap in these contracts. Keep its
default compatible with existing managers. Do not introduce another execution
ledger, duplicate ticket fields as a pricing cache, or infer executor behaviour
from a protocol-specific ticket key. A changed signature or separate share
token alone does not justify a new CLI, router or persistence mechanism.

An adapter must distinguish temporary unavailability from unsafe attribution.
Raise `VaultRequestInvariantViolation` when the observed request aggregate
cannot be assigned to its recorded ticket; consumers must halt rather than
retry it as a settlement delay. Atoma selects this error in its existing
`_unavailable()` helper. A consumed ticket still uses ordinary recovery.
Its explicit aggregate-reconciliation library methods are outside the
executor's single-request operating model and must not run automatically.

Successful receipt analyses own the evidence for realised amounts, including
a proven zero-payout redemption. Keep protocol validation in the analysis
subclass and use the executor's existing zero-proceeds accounting opt-in.
Live startup and ticks resolve all vaults once before valuation and treasury
sync, so no protocol-specific scheduling predicate or saved capability flag
is needed. Backtests keep their existing post-sync settlement order. Remaining
consumer-specific ticket and preflight exceptions belong in small policy
helpers, preserving whole-batch checks before any claim.

Account admission is per deployment. Accepted deposit inputs do not redefine
the withdrawal denomination, and a supported manager does not establish public
access. USPC remains permissioned even when a particular custody account is
approved; do not clear it for a permissionless shortlist. For Upshift, keep
the native withdrawal payout as the denomination and accepted input tokens
separate. A USDC input alone does not establish a USDC exit route.
Persisted accessor corrections follow the
[numbered migration guide](migration-scripts.md); a documentation-only change
needs no migration.

Yearn-compatible deployments can replace canonical admission selectors. Birch
Hill's Base Flagship uses `deposit_hook()` and atomic post-deposit sender checks
instead of `deposit_limit_module()`. An absent canonical selector does not prove
public access: inspect the verified implementation ABI and configured hook.
Keep its classification separate from the permissionless Morpho Ecosystem vault;
the verified addresses and ABI provenance are in `eth_defi/abi/yearn/README.md`.

## Use the denomination in both trading directions

Upshift's ordinary manager methods use the withdrawal denomination for deposits,
estimates and capacity. The executor's direct route requires that denomination
to match the funded reserve. Keep approvals, raw units, pricing and diagnostics
on that ordinary manager interface; do not add deposit-token selection wiring.

The library retains explicit `accepted_asset` deposits and
`estimate_deposit_for_asset()`. For stablecoin vaults these extra inputs are a
convenience: the vault handles their conversion into reference-asset accounting.
This feature is not needed for our executor integration. Keep the metadata's
accepted-input list accurate, but register only the denomination and LP token
needed by this integration in guard setup. Existing guard token permissions
are shared across integrations, not a per-vault deposit-token restriction.

This default-execution change does not alter persisted accessors or capability
fields, so it needs no new metadata migration. It does not remove dated claims,
permission checks, protocol previews or native payout validation.

## Check the actual entry point

Cover the shared configuration with both feature-bearing and address-only
descriptors, preserve generic handling, and prove resolution failure sends no
policy transactions. Exercise the resulting calls through real guarded custody
on the repository's shared fixed-block Anvil fork.

Test the public command branch being claimed. In trade-executor's
`lagoon-deploy-vault`, supplying `STRATEGY_FILE` selects multichain deployment;
adding `--erc-4626-vaults` to that test does not exercise the separate
single-chain explicit-address branch.
Keep this distinction in PR results. Mocked factory tests alone do not prove
the deployed guard admits the actual calls.

Trade-executor agents must also read its existing
[vault integration guide](https://github.com/tradingstrategy-ai/trade-executor/blob/master/.claude/docs/vault-deposit-redeem.md).
Live synchronous/queued execution, conservative allocation and backtest delay
are separate decisions. Reuse the existing funding classification and backtest
overrides, and document their timing limits rather than adding quote-time
protocol state.
