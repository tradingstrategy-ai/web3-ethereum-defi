# Atoma execution validation

The adapter extends the existing Atoma reader for the two Arbitrum deployments
listed in the [ABI provenance](../eth_defi/abi/atoma/README.md). It implements
synchronous deposits and asynchronous request, operator settlement and claim,
with persistence and Guard/Safe routing. The original requirement is recorded
in [PR #1691](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1691).

## Running the focused checks

Load the private environment through the repository's unchanged
`.local-test.env`. `JSON_RPC_ARBITRUM` is required for fork and live RPC tests;
`HYPERSYNC_API_KEY` is also required for the indexed history test. RPC fallback
URLs are passed to `create_multi_provider_web3()`.

```shell
source .local-test.env && PYTHONPATH="$PWD" timeout 180 poetry run pytest \
  tests/erc_4626/vault_protocol/test_atoma_deposit_redeem.py \
  tests/erc_4626/vault_protocol/test_atoma.py \
  tests/erc_4626/test_atoma_events.py tests/test_revert_data.py \
  tests/vault/test_deposit_redeem.py tests/erc_4626/test_deposit_probe.py -q

source .local-test.env && PYTHONPATH="$PWD" timeout 600 poetry run pytest \
  tests/guard/test_guard_atoma.py tests/guard/test_guard_atoma_policy.py \
  tests/erc_4626/vault_protocol/test_atoma_integration.py \
  -q --log-cli-level=info

make guard safe-integration
```

The current lifecycle requires block **519150162**. The canonical Arbitrum
block **487039644** predates that implementation; generic Guard rejection tests
and legacy reader tests use the canonical block. Neither test forks at latest
or changes the global chain constant. Both use the shared Anvil pool and
matching xdist groups. The repository includes a warmed current-block cache
with 37 accounts and 2,084 storage slots. Custody deployment is shared across
the session and protocol changes are reverted between tests.

## Recorded evidence

Manual local runs on **2026-10-28** used the supplied authenticated Arbitrum
archive RPC configuration (Goldsky with dRPC and Alchemy fallbacks),
`arbitrum.hypersync.xyz` with supplied credentials, and Anvil 1.8.6. No credentials
are included here. These are local results, not CI results or live funded
transactions. The commands above passed 92 Python unit/regression cases and
16 fork/live integration cases.

- Both Index and RWA completed a 100 USDC deposit, locked-share rejection,
  withdrawal request, JSON ticket reconstruction, operator settlement and claim
  through **SimpleVault and Safe/TradingStrategyModule**. The asset manager was
  distinct from governance. Funding used an actual USDC transfer on Anvil.
- Index minted **90,183,127,089,591** raw shares and paid **99,500,000** raw USDC,
  with **500,000** retained as the exit fee. RWA minted **92,043,718,995,111** raw
  shares and paid **99,500,000** raw USDC, with **499,999** retained as the exit
  fee. RWA's gross claim was **99,999,999** because of integer rounding.
- Isolated fork scenarios exercised partial locks, paused exits, duplicate and
  concurrent epochs, inclusion across an epoch boundary, schedule changes,
  insufficient idle assets, and owner-reported losses producing half-value and
  zero payouts. Loss/liquidity scenarios use the actual administrative APIs;
  they are controlled scenarios, not claims about an observed protocol loss.
- Authenticated live RPC reads confirmed both pinned implementations and
  configuration. Bounded Hypersync reads recovered the Index request at block
  **486733515** and its legacy claim at **492170597**. The actual payout was
  **507.202587 USDC**, with **2.548756 USDC** transferred separately to the operator
  as that implementation's exit fee. Completed-claim lookup recovered the same
  transaction after offline ticket reconstruction.
- Synthetic receipts and mocked indexed histories cover corrupted persistence,
  missing contribution evidence, indexer lag, aggregate supersession, unknown
  implementations, incorrect receipt identities and transport errors. These
  tests are bookkeeping evidence; the fork and live checks establish provider
  and contract integration separately.
- `make guard safe-integration` succeeded. Deployed bytecode is **22,173 bytes**
  for GuardV0 and **24,259 bytes** for TradingStrategyModuleV0, both below
  EIP-170's 24,576-byte limit. The module has **317 bytes** remaining.
- The existing Guard Solidity regression contract also passed all **11** tests:
  `source .local-test.env && timeout 180 forge test --root contracts/guard --match-path test/GuardVaultProtocol.t.sol`.

An owner-wide diagnostic scans every epoch, including old unclaimed requests.
On a cold local fork, RWA's approximately 1,559 keys took about 24 seconds;
Index has approximately 453 keys at the same stage. After read-only cache
warming, the complete RWA lifecycle test took about 4 seconds. This is a
diagnostic API; routine polling reads a single owner/epoch ticket.

## Activation and recovery requirements

Executor accounting and backtest settlement scheduling remain separate work.
Consumers must persist tickets and broadcast hashes, atomically supersede
component tickets after aggregate reconciliation, account for zero payouts,
and distinguish queued shares, settled claims and realised cash. Completed-claim
lookup searches only indexed blocks and defers an absent result if the indexer
lags the chain; absence from an incomplete archive is not a failed claim.
A settlement burn is not a cash receipt, and a settled claim can remain
illiquid or suffer a haircut. Claim events already report net proceeds; do not
charge the exit fee twice.

Use the specialised `whitelistAtoma()` policy. Deployment configuration, the
existing-vault whitelist script and the deposit probe select it for Atoma.
Existing broad ERC-4626 permissions are additive and must be removed, or the
Guard/module replaced, before relying on the narrow Atoma policy. No share
transfer/approval, direct redemption, settlement or administrative permission
is added by this whitelist.

Only the two documented implementations are recognised for receipt decoding,
and only the current one is permitted for execution. Other historical
implementations exist, including `0xfe1630da561e82fefb56e37796faee917331215f`.
Their requests retain evidence with reconciliation required; their flow
receipts are returned as structured failures rather than assigned accounting
values. A mined upgrade ambiguity requires a new reviewed decoder, not a
transaction retry.

When opening the eventual implementation PR, add the repository-required real
integration result comment: the exact command above, its outcome and run date,
the redacted providers, and the distinction between manual live reads, fork
transactions and synthetic tests. This work does not authorise a production
transaction, push or PR creation.

## Independent review

The implementation and final cleanup were independently reviewed with Claude
CLI Opus 5.5 (`claude-opus-5-5`) on 2026-10-28 through `clean-up-for-pr`.
The completed reviews returned **no blocking findings**. The cleanup simplified
the fixed historical request/claim test into direct assertions; its five
integration checks passed against the supplied providers. Formatting and
`git diff --check` passed; Sphinx was not built.
