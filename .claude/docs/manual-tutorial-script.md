# Manual tutorial scripts

Read this guide before creating or materially changing a manual deployment,
trading or vault-lifecycle tutorial. These scripts demonstrate real library
integration and guide an operator through it. Keep orchestration readable, reuse
library helpers, and make the result and next action clear.

Existing scripts are references, not universal templates: some have older defaults
or narrower recovery support. Check their implementation as well as their module
docstrings before copying a pattern. This guide does not imply that every example
supports every execution mode.

## Available examples

| Script | Useful reference | Supported behaviour and limitations |
| --- | --- | --- |
| [Gas and Ethereum USDC distribution](../../scripts/lifi/feed-cross-chain.py) | Prefund the deployer before multichain deployment; native gas on selected chains, opt-in Ethereum USDC transfer | `DRY_RUN=true` quotes only; live execution asks before approvals/transfers. `ETHEREUM_USDC_AMOUNT` is a source spend on every invocation, not a target balance. HyperEVM USDC is funded separately. |
| [Multichain deployment](../../scripts/lagoon/deploy-lagoon-multichain.py) | Chain configuration, deterministic Safes, funding and CCTP; numbered stages and address summaries | Live mainnet/testnet deployment; mainnet Anvil simulation with forged attestations. Does not provide the full investment/unwind lifecycle. Testnet simulation is rejected. |
| [GMX lifecycle](../../scripts/lagoon/lagoon-gmx-example.py) | Deployment → deposit → trade → withdrawal, observed position confirmation, transaction and cost summaries | Live mainnet/testnet; mainnet simulation exercises limit-order create/cancel, but skips keeper-dependent market execution. |
| [Lighter satellite deployment](../../scripts/lagoon/deploy-lagoon-lighter-multichain.py) | Prefunded Ethereum USDC, funding preflight, protected API-key backup and phased activation; no deployment-time bridge | Live mainnet and `PREFLIGHT_ONLY=true`. Rejects simulation. Fresh deployment only; rerunning does not resume an interrupted deployment. |
| [Lighter pool round trip](../../scripts/lagoon/lagoon-lighter-pool-roundtrip.py) | Saved signed operations, confirmation, secure-withdrawal identity and L1 claim | Live execution against an existing funded Safe-owned account. Does not deploy or settle Lagoon. A saved report does not make the whole script automatically resumable. |
| [Lighter trading example](../../scripts/lagoon/lagoon-lighter-trade-example.py) | Optional pinned SDK installation, amount sizing and protected deployment report | Live deployment and perpetual trading. Does not withdraw collateral or redeem Lagoon shares. |
| [Hypercore vault deposit/withdrawal](../../scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py) | Separate entry/exit actions, existing deployment bindings, two-phase Core deposit and troubleshooting | `ACTION=deposit`, `withdraw` or `both`; Anvil mocks for Core calls. Live delayed exit requires saved addresses and current amounts; this is not an automatic state-file resume implementation. |
| [Hypercore account-mode investigation](../../scripts/hyperliquid/manual-test-lagoon-account-mode-roundtrip.py) | EVM/spot/perp/escrow snapshots and diagnosing missing funds | Live investigation of account-mode transfers; does not itself demonstrate investment into a Core vault. Its owner sweep is not the pattern for ordinary Lagoon redemption. |
| [Hypercore account diagnostics](../../scripts/hyperliquid/check-hypercore-user.py) | Read-only balances, escrow and positions for troubleshooting | Inspect an address without submitting an action. |
| [CowSwap example](../../scripts/lagoon/lagoon-cowswap-example.py) and [Velora example](../../scripts/lagoon/lagoon-velora-example.py) | Route/quote checks before spending, wallet and gas context, guarded trading | Live examples. Do not assume simulation or restart support merely because other tutorials have it. |
| [Lagoon withdrawal](../../scripts/lagoon/withdraw-lagoon-vault.py) | Request redemption → valuation/settlement → claim | Existing command-line-parser interface; new scripts use environment variables unless a parser is explicitly requested. Its local-cash valuation assumes other positions have already been resolved. |

The [multichain narrative tutorial](../../docs/source/tutorials/lagoon-multichain.rst)
shows how to introduce architecture, prerequisites, commands, workflow and API
references. Protocol-specific context lives in the
[Hypercore guard guide](../../docs/README-Hypercore-guard.md) and
[Lighter execution guide](../../docs/README-lighter-core-execution.md).

The [three-chain Core tutorial](../../scripts/lagoon/lagoon-multichain-core-roundtrip.py)
combines Arbitrum deployment, satellite investment, saved-state status and manual
unwind. Live mode supports `deploy`, `status` and `unwind`; simulation runs `all`
on three forks with explicit Core/API doubles. It is the reference for protected
multi-stage state and final investor redemption; a simulated pass does not prove
a funded live lifecycle. Its [implementation plan](../../docs/superpowers/plans/2026-10-27-lagoon-three-chain-roundtrip.md)
records the decisions and review.

## File and documentation structure

Start the script with a Sphinx/reStructuredText module docstring containing:

1. What the script demonstrates and what a successful run returns to the operator.
2. A numbered lifecycle and, for multiple chains, a short custody/transfer sketch.
3. Prerequisites: user RPCs, required signer or API credentials, optional SDK,
   starting asset balances and native gas on each chain.
4. Execution modes, which steps use real contracts, and which require mocks or
   external execution that a fork cannot supply.
5. Required inputs separately from optional settings and their defaults.
6. Copyable execution examples, including delayed exit when applicable.
7. State location, recovery limits and symptom-based troubleshooting.
8. Links to relevant repository helpers and authoritative protocol documentation.

Follow with imports, module logger, small constants/settings, phase functions and
`main()`. Keep `main()` responsible for settings, connections, action dispatch and
resource cleanup. Use descriptive phase names that match the logs and docstring.
Put reusable protocol execution in `eth_defi`, not in a script imported by another
script. Add a small workflow module only when it improves reuse or testability;
do not introduce a general workflow engine for a manual example.

Use typed functions, `dataclass(slots=True)`, `Decimal` monetary values and token
conversion helpers. Prefer synchronous execution; an optional SDK may need a
small event-loop wrapper for construction/cleanup without turning the workflow
into asynchronous orchestration. Follow the repository's normal API docstring,
logging and formatting rules.

## Configuration and sensible defaults

Require environment inputs only when values cannot safely be defaulted or
inferred: private keys, user RPC connections, required API credentials and an
explicit existing deployment or target where necessary. Never invent RPCs or
credentials. Choose reasonable defaults for amounts, fees, paths, timeouts,
logging and chains. Overrides are optional; example commands should work after
the private environment is loaded, without restating all defaults.

Use `create_multi_provider_web3()` for the repository's space-separated RPC
fallback format. Keep keys out of commands, terminal output and committed files.
Explorer verification and decorative reports must not introduce mandatory API
credentials. Parse booleans explicitly: `SIMULATE=false` must not enable a fork.
Reject unsupported action/network combinations before doing work.

Require prefunded native gas on every selected chain and check the actual
funding source before deployment. The Lighter deployment-only example consumes
1 USDC from the deployer on Ethereum, with no hub subscription or activation
bridge. Record it as a contribution and include its collateral in initial NAV.
The investment round trip instead funds its positions through the hub subscription
and CCTP; do not replace that accounted capital with deployer donations. Its
separate satellite wallet reserves are not position capital. Cross-reference
the gas distribution script in the tutorial module docstring.

Validate funding and route feasibility before deployment, then recheck current
fees, capacity and balances before spending. Distinguish invested principal,
recoverable activation collateral, consumed fees, fee ceilings and native gas.
Display amounts from validated settings rather than stale prose or duplicated
constants. A tutorial's small default must still satisfy protocol minima.

## Execution modes

Mode chooses the execution environment; action chooses the lifecycle stage.
They are separate concepts. Document each supported combination explicitly.

| Mode | Intended behaviour |
| --- | --- |
| Live | Submit transactions to the selected real network using the supplied signer. Show which network and accounts will be used. |
| Simulation | Run on local Anvil forks with generated/funded test accounts and clearly identified mocks. No live signer or real funds required. |
| Preflight | Perform configuration, funding and read-only feasibility checks without broadcasting. This is not a simulation of transactions. |
| Status | Load a saved deployment and report observed balances, positions and pending work without submitting transactions. |
| Testnet, if supported | Submit real transactions to a test network. This is not synonymous with simulation; factories, assets and external execution may differ. |

Not every new tutorial needs every mode. A simple one-shot swap does not need
an overnight state machine. A delayed vault lifecycle does need distinct entry,
status and exit behaviour. Use `ACTION=all` or an equivalent combined action only
where the protocol permits immediate completion; default delayed live workflows
to entry and stop rather than sleeping overnight.

### Simulation example

After loading the Arbitrum and Ethereum RPC variables, this existing command
deploys on two forks and exercises CCTP with forged attestations:

```shell
CHAINS=arbitrum,ethereum SIMULATE=true poetry run python scripts/lagoon/deploy-lagoon-multichain.py
```

The `CHAINS` override keeps this example to two chains; the script's default
topology contains more chains and requires their RPCs too. For a single-chain
example with the GMX simulation limitations described above:

```shell
SIMULATE=true poetry run python scripts/lagoon/lagoon-gmx-example.py
```

Ensure every transaction-producing client, including protocol adapters, uses the
fork RPC. Close owned Anvil processes in `finally`. Core sequencers and keepers
do not observe fork deposits: mock that boundary explicitly or mark dependent
steps skipped. Recording a call alone does not simulate its cash movement. If
the test claims conservation or final redemption, debit/credit simulated balances
exactly once and check the resulting holdings.

Do not add live transaction submissions to make a simulation pass. Clearly mark
mocked results and avoid mainnet explorer links for fork-only transactions.
Ephemeral fork reports cannot resume on mainnet or after the forks have stopped;
state this rather than adding fork persistence infrastructure unnecessarily.

### Live and preflight examples

Load the deployer key and selected RPCs from the operator's private environment.
For the existing Lighter deployment script:

```shell
# Read-only checks; no contract deployment or funding transactions.
PREFLIGHT_ONLY=true poetry run python scripts/lagoon/deploy-lagoon-lighter-multichain.py

# Deploy and activate on live mainnets using the script's defaults.
poetry run python scripts/lagoon/deploy-lagoon-lighter-multichain.py
```

Ensure `PREFLIGHT_ONLY` and `SIMULATE` are not left exported from earlier work
when selecting live execution. This script currently uses a HyperEVM hub and
Ethereum satellite, requires 1 native USDC on Ethereum and native gas on every chain, and stops
after activation. Its preflight mode is not proof of pool entry/exit or recovery.

For the existing pool lifecycle, first supply its required protected deployment
report and selected pool in the private environment:

```shell
poetry run python scripts/lagoon/lagoon-lighter-pool-roundtrip.py
```

This uses an already-funded account and has no simulation flag. It is a separate
financial operation, not a continuation automatically inferred from the previous
command. Confirm the required initial account state described in its docstring.

### Multiple stages and vault delays

The existing Hypercore example illustrates separate actions. Load a deliberately
selected `HYPERCORE_VAULT`, signer and mainnet RPC first. Keep `SIMULATE` unset
for this legacy script: any non-empty value, including `false`, enables simulation.

```shell
# Entry: save the printed Lagoon vault, Safe, module and selected Core vault.
NETWORK=mainnet ACTION=deposit poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

# Exit: fill in the SAME saved deployment after its actual lock has expired.
# Set USDC_AMOUNT to a currently withdrawable whole-USDC amount supported by
# this legacy script, not blindly the original deposit after gains or losses.
NETWORK=mainnet ACTION=withdraw \
LAGOON_VAULT='<saved-lagoon-vault-address>' \
TRADING_STRATEGY_MODULE='<saved-module-address>' \
USDC_AMOUNT='<currently-withdrawable-whole-USDC>' \
poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py
```

Keep the selected `HYPERCORE_VAULT` identical for both commands. The placeholders
must be replaced; they are not valid addresses or amounts. This older script
demonstrates the split, but does not supply the durable, automatic unwind contract
described below. Its manual amount input is not a model for new full-exit scripts.
Check the recorded/observed lock, not merely “one day later”; vaults can have
different delays and liquidity constraints.

For a **new** delayed lifecycle, prefer the following interface pattern. These
commands are a design template, not an existing executable file:

```shell
# Day one: defaults to deploy/invest, saves state, then exits.
poetry run python scripts/lagoon/<tutorial>.py

# Inspect at any time, without an EOA signing key.
ACTION=status poetry run python scripts/lagoon/<tutorial>.py

# Operator triggers this after the reported unlock; repeat for pending settlement.
ACTION=unwind poetry run python scripts/lagoon/<tutorial>.py

# Exercise the full flow with mocked delay in one local run.
SIMULATE=true poetry run python scripts/lagoon/<tutorial>.py
```

Do not assume repeated `unwind` is safe until restart reconciliation is implemented
and tested. Do not schedule exit automatically, wait overnight, or unwind in a
generic exception handler.

## Saved state and recovery

For a resumable tutorial, choose a default under
`~/.tradingstrategy/<tutorial-name>/<mode>/`. Keep live and simulated identities
separate. Protect directories/files with modes 0700/0600, prevent concurrent
writers, and update state atomically. Refuse to overwrite an existing deployment
on an entry command. Store generated protocol keys securely before registration;
do not persist the supplied deployer's private key.

Save the run identity, chain IDs, deployment addresses, roles, targets, amounts,
stage, observed holdings and unlock times. Save signed transaction identity and
nonce before broadcast; retain protocol operation hashes, CCTP messages and
exact withdrawal/request identifiers where applicable. Reconcile uncertain
submissions before a new mutation. Missing receipts or unchanged balances alone
do not prove an asynchronous action failed.

On restart, load the original targets and bindings rather than rediscovering or
redeploying. Verify chain/account identity and remaining gas. Derive exit amounts
from current holdings. Treat redemption, protocol withdrawal, bridge receipt and
Lagoon request/settlement/claim as separate stages. Unknown outcomes leave the
affected stage pending; no blind re-signing, duplicate burn or repeated subscription.
State exactly which interruptions require manual recovery rather than promising
universal resumability.

Use the protocol's full-exit instruction rather than submitting a quoted whole
balance. Hypercore's `vaultTransfer(vault, false, 0)` withdraws all at execution
value; observe the actual perp credit before sizing the next transfer. For
Lighter, a secure history row can remain `claimable` after an automatic L1
claim. Check the pending balance and matching Safe credit before claiming again.
The protocol guides linked above cover these cases and the dated mainnet run.

When the protocol explicitly rejects a submission, retain its original hash and
rejection evidence. An operator-confirmed recovery uses a new operation identity;
never replace the amount under an already submitted hash. A timeout or an
unchanged balance alone does not authorise such a replacement.

For Lagoon, return external capital and resolve receivables before using local
Safe cash as final NAV. Use normal share redemption, settlement and claim to the
deployer. A direct owner sweep is not an equivalent test of investor redemption.
Do not present remaining dust or a pending position as a complete return.

## Operator output and troubleshooting

- At startup, show mode/action, chain roles, deployer, funding/gas balances,
  target positions, amounts and state path in a compact table.
- During work, log numbered phases, chain, transaction identifiers and the
  condition being awaited. Use bounded polling with visible progress at least
  once a minute; use `tqdm_loggable.auto` for long iterable workloads. A successful
  EVM receipt or API submission is not automatically a confirmed protocol action.
- At checkpoints and in status mode, show timestamped cash, holdings, escrow,
  pending withdrawals/transfers and unlock information. Missing data is
  unavailable, not zero. Use `tabulate` rather than printed row loops.
- On pending work or failure, print the last confirmed stage, state path and
  an exact safe next command. Distinguish pending from success and failure in
  the exit status. Quote custom paths safely. Preserve evidence before cleanup.
- At completion, show subscription, confirmed claim proceeds, realised change,
  actual fees, residuals and native costs per chain. Never call a whole wallet
  balance profit. An overnight wallet delta can include unrelated activity;
  reconcile it against recorded transfers. Keep ETH/HYPE costs separate from
  USDC results and do not add price-feed dependencies just for presentation.

Prefer a short symptom → observation → next-action troubleshooting table. For
example, “Core vault position missing” should direct the operator to spot/perp,
escrow and saved operation evidence, not automatically repeat the deposit.
An existing read-only diagnostic is:

```shell
ADDRESS='<saved-safe-address>' NETWORK=mainnet poetry run python scripts/hyperliquid/check-hypercore-user.py
```

## Reusable testing submodules

Search the reusable helpers under `eth_defi` before adding fork setup, funding,
mock contracts or settlement shortcuts to a tutorial. Do not import fixtures
from `tests` or copy a test's private setup into a script. Keep new reusable
simulation helpers in the relevant protocol's testing module; keep production
execution in its normal module. A module named `testing` is not necessarily
fork-only: inspect each function's documented behaviour and signing mode.

### Forks, funding and isolation

| Module | Use in a tutorial or its tests |
| --- | --- |
| [`eth_defi.provider.anvil`](../../eth_defi/provider/anvil.py) | `launch_anvil()`/`fork_network_anvil()` and `AnvilLaunch.close()` for a script-owned fork; `is_anvil()` to check the local environment; `set_balance()`, `fund_erc20_on_anvil()` and impersonation helpers for simulation setup; `mine()` for local time advancement. Prefer this path over deprecated `eth_defi.anvil`. |
| [`eth_defi.testing.anvil_fork_pool`](../../eth_defi/testing/anvil_fork_pool.py) | `AnvilForkPool` for reusable forks. In pytest, obtain it through the repository's `anvil_fork_pool` fixture. Its module docstring is the authoritative setup/isolation guide, including the copyable test skeleton. |
| [`eth_defi.testing.fork_blocks`](../../eth_defi/testing/fork_blocks.py) | Canonical chain `*_MIDNIGHT_BLOCK` values and `get_midnight_block()` for reproducible fork tests. Use these instead of inventing a block number. |
| [`eth_defi.testing.evm_snapshot_fixture`](../../eth_defi/testing/evm_snapshot_fixture.py) | `evm_snapshot_revert()` around mutating pytest cases that share a fork. Restore all affected chains for a multichain case. Read the pool guide rather than copying the older standalone-fork example in this helper's docstring. |
| [`eth_defi.testing.rpc_cache`](../../eth_defi/testing/rpc_cache.py) | Repository fork-cache seeding, already wired into the pytest pool setup. Preserve warm caches and close owned forks gracefully. A standalone script needing seeding can reuse `seed_default_foundry_rpc_cache()`. |
| [`eth_defi.testing.token_cache`](../../eth_defi/testing/token_cache.py) | Seeded token metadata/address caches for tests. Follow its documented session integration; avoid repeatedly probing the same tokens. Do not use historical fixture data as live balances or execution prices. |

For a standalone simulation, the script owns its forks and closes them in
`finally`. For pytest, the shared pool owns process lifetime; tests must not close
its forks. Follow the pool's canonical fixed-block/cache/xdist/isolation recipe
instead of maintaining a second recipe here. Respect documented chain exceptions,
such as Monad's limited historical state. The practical troubleshooting reference
is [`eth_defi/testing/README.md`](../../eth_defi/testing/README.md).

For example, initialise a test wallet's USDC balance on an already-created fork
using token conversions rather than embedding storage-slot logic:

```python
from decimal import Decimal

from eth_defi.provider.anvil import fund_erc20_on_anvil, is_anvil

# web3, usdc and test_wallet belong to the local simulation setup.
assert simulate and is_anvil(web3)
fund_erc20_on_anvil(
    web3,
    usdc.address,
    test_wallet.address,
    usdc.convert_to_raw(Decimal("25")),
)
```

`fund_erc20_on_anvil()` **sets** the address's balance; it does not add a transfer
or model a deposit. Use it to seed initial simulation capital, not to patch a
missing investment return and then claim conservation. `create_fork_funded_wallet()`
is an existing whale-funded convenience helper, but check its implementation:
it currently transfers the holder's full balance rather than enforcing its
`usdc_amount` argument as an exact budget. Prefer explicit balance setup when the
tutorial tests a small funding envelope.

### Protocol helpers and what they prove

| Module | Helpers and appropriate use | Boundary to preserve |
| --- | --- | --- |
| [`eth_defi.cctp.testing`](../../eth_defi/cctp/testing.py) | `replace_attester_on_fork()`, `craft_cctp_message()`, `forge_attestation()` simulate attestation so real forked `receiveMessage()` can mint. Existing CCTP bridge helpers also expose `simulate` and test-attester inputs. | This substitutes for Circle attestation, not the source burn. For a round trip, tie message identity/amount/recipient to the recorded burn, account for fees, and configure each destination fork, including the hub on return. |
| [`eth_defi.hyperliquid.testing`](../../eth_defi/hyperliquid/testing.py) | `setup_anvil_hypercore_mocks()` installs mock CoreWriter/CoreDepositWallet code at system addresses; individual deployment helpers are also available. | These mocks record calls; they do not execute a Core sequencer or move USDC. Model the offchain balance/position transition explicitly if testing cash conservation, and consume each action once. |
| [`eth_defi.lighter.testing`](../../eth_defi/lighter/testing.py) | `register_lighter_account_on_anvil()` supplies fork registration; `credit_lighter_pending_balance_on_anvil()` supplies settled claimable collateral. `deploy_lighter_simple_vault()` is useful for isolated guard tests. | Registration and pending-balance storage overrides replace sequencer/proof results. Pair them with explicit fake API state; keep real L1 deposit/claim paths where those are under test. A SimpleVault guard fixture does not replace a Lagoon/Safe lifecycle deployment. |
| [`eth_defi.erc_4626.vault_protocol.lagoon.testing`](../../eth_defi/erc_4626/vault_protocol/lagoon/testing.py) | `redeem_vault_shares()` requests full redemption and supports a real `HotWallet`; `force_lagoon_settle()` is for fork tests using supplied/unlocked manager identities. | Redemption request is only phase one: settlement and final claim remain separate. Do not use forced settlement to bypass the guarded manager path the tutorial intends to prove. Use the canonical module, not deprecated `eth_defi.lagoon.testing`. |
| [`eth_defi.gmx.testing`](../../eth_defi/gmx/testing/__init__.py) | `oracle` supplies mock-oracle setup; `keeper` supplies keeper impersonation/order execution; `fork_provider` supplies Anvil/Tenderly overrides; `constants` supplies network defaults. | These can support explicit simulated fills, but do not mean a live keeper runs on a fork. The existing GMX tutorial skips market fills; adopting these helpers is a deliberate extension with its own assertions. |
| [`eth_defi.testing.lagoon_roundtrip`](../../eth_defi/testing/lagoon_roundtrip.py) | Local Core balance ledger, Lighter API/signer double and fee-aware test attestations for the three-chain tutorial. | Reuses the production workflow and real forked custody calls. Native signatures, sequencers, investment pricing and proof settlement remain simulated; recorded gains are declared test inputs. |
| [`eth_defi.testing.gmx_lagoon`](../../eth_defi/testing/gmx_lagoon.py) | `create_lagoon_gmx_fork_env()`, cached deployment and isolation helpers reuse expensive Lagoon/Safe/GMX test baselines. | Callers own fork lifetime and snapshot isolation. Recreate affected Python adapters/caches as documented; EVM revert does not rewind Python state. |
| [`eth_defi.erc_4626.vault_protocol.gains.testing`](../../eth_defi/erc_4626/vault_protocol/gains/testing.py) | `force_next_gains_epoch()`, `force_ostium_v15_settlement()`, `setup_ostium_simulation()` and `simulate_ostium_v15_cycle()` demonstrate delayed epoch/settlement simulation. See the [Ostium manual script](../../scripts/erc-4626/ostium-v15.py). | Time advancement and manager/oracle forcing are local substitutes. Live execution must wait for actual protocol settlement; advancing EVM time alone cannot settle an external sequencer. |

Import `fund_lagoon_vault()` from its production
[`funding` module](../../eth_defi/erc_4626/vault_protocol/lagoon/funding.py), even
though Lagoon's testing module re-exports it. That helper runs normal subscription,
settlement and share claim. For restart-sensitive tutorials, check whether a
convenience helper exposes each transaction boundary before adopting it: a
multi-transaction helper without a pre-broadcast observer is not automatically
safe to resume by calling it again.

More specialised examples include
[`eth_defi.testing.antarctic`](../../eth_defi/testing/antarctic.py), which supplies
recorded RPC/Hypersync transports for deterministic adapter tests, and
[`eth_defi.feed.testing`](../../eth_defi/feed/testing.py), which constructs feed
fixtures. Use them only for their matching integration; they are not general
financial-lifecycle simulation backends.

### Wiring simulation without duplicating the workflow

1. Create/obtain the local forks and verify `is_anvil()` before impersonation,
   storage changes, mock-code installation or forced settlement. Do not assume
   every helper enforces this itself. Read `eth_defi/abi/README.md` before using
   contract ABIs or mock bytecode; reuse compiled artefacts instead of inline ABIs.
2. Seed initial test capital, install only the required protocol overrides and
   supply deterministic external API responses. Keep these changes inside the
   simulation setup/boundary, not scattered through live execution functions.
3. Run the same deployment, guarded calls, subscription and redemption phases as
   live mode. Invoke mock settlement only at the matching confirmed transition;
   do not credit a claim merely because a withdrawal was submitted.
4. Assert real EVM receipts and observed cash/holdings after each phase. Label
   which outcomes came from mocks. A storage-written balance is not evidence of
   successful native signing, proof generation, price formation or live execution.
5. For restart tests, reset/reconstruct Python state as well as restoring EVM
   snapshots: wallet nonces, fake API ledgers, executors and state files do not
   roll back with the chain. Use isolated temporary report paths in pytest; never
   let a test overwrite an operator's default live state directory.

## Documentation and verification

Keep required inputs, defaults and commands consistent with the implementation.
Older examples contain stale amounts, bare `python` invocations and permissive
boolean parsing; correct these when touching the relevant code rather than
propagating them. Do not broaden a new tutorial into unrelated legacy cleanup.

Add narrative documentation or cross-reference the script from the relevant
tutorial. Prefer `literalinclude` for source rather than a duplicate copy. New
public library modules need API stubs and toctree entries. Do not build Sphinx
locally.

Test meaningful funding/confirmation/recovery boundaries and mode isolation.
Use the repository's shared fixed-block Anvil fork pattern for pytest coverage;
read `eth_defi/testing/anvil_fork_pool.py` first. Run focused tests through
`.local-test.env` and Poetry, and format changed Python with Ruff. Never edit
`.local-test.env`.

Simulation proves only the exercised contract and mocked boundaries. A real
external-integration result must identify the exact command, date, redacted
environment and observed outcome in the PR comment. A delayed workflow may need
multiple manual runs. Distinguish “implemented”, “simulated” and “verified live”;
writing a financial tutorial does not itself authorise running it with real funds.
