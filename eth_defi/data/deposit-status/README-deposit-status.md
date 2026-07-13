# Vault deposit-status artefact

`vault-deposit-status.json` is a version-controlled snapshot of guarded
Anvil-fork deposit probes. It is package data, like ABI files: consumers can
read the evidence shipped with a release without depending on an operator's
home-directory state.

Each row records a vault, its manager capability, the outcome, and any relevant
failure details. Every successful current result includes the positive integer
Anvil fork block used for the attempt. A legacy success without this evidence
is automatically invalidated on the next refresh. The file never contains
private keys, upstream transaction hashes, or transaction hashes from
temporary Anvil chains.

## Refreshing the snapshot

Refresh this file deliberately from a repository checkout. The probe defaults
to this path, so do not set `VAULT_DEPOSIT_STATUS_PATH` for the release refresh.

```shell
source .local-test.env
SIMULATE=true \
VAULT_SELECTION=all_protocols \
CHAIN_ID=42161 \
MAX_VAULTS=5 \
ALLOW_UNCERTIFIED_CANDIDATES=true \
CONFIRM_ALL=true \
DEPOSIT_AMOUNT=10 \
poetry run python scripts/erc-4626/probe-vault-deposits.py
```

The sweep can take a long time. For an interactive runner, prefer bounded
protocol batches and resume the same artefact:

```shell
source .local-test.env
SIMULATE=true \
VAULT_SELECTION=protocol \
PROTOCOL=Morpho \
CHAIN_ID=8453 \
MAX_VAULTS=5 \
CONFIRM_ALL=true \
DEPOSIT_AMOUNT=10 \
poetry run python scripts/erc-4626/probe-vault-deposits.py
```

Review the resulting JSON and its Git diff before committing. `success` means
the configured `SimpleVaultV0` completed a guarded deposit on the ephemeral
fork. For a synchronous manager, it also completed a guarded redemption and
received denomination tokens back. Asynchronous redemption is recorded as not
exercised because it needs a later protocol settlement. A success is not a
live-chain transaction. `funding_error` and `rpc_error` are infrastructure
findings, not protocol incompatibility. `reverted` means a guarded deposit or
synchronous redemption transaction was attempted and did not succeed.

Before committing a refreshed artefact, verify that no successful current row
has a missing or non-integer `fork_block_number`. The writer rejects such new
successes; this separate review also catches hand-edited or externally supplied
files.

The `max_deposit_guidance` field and the terminal `maxDeposit guidance` column
are informational only. ERC-4626 permits conservative `maxDeposit` values, and
Morpho V2 intentionally returns zero. Depositability is decided solely by the
guarded transaction result. A saved success is historical adapter evidence; a
production caller must still execute a current-state preflight or handle a
live transaction revert.

For an experimental run that must not change the package artefact, provide an
explicit `VAULT_DEPOSIT_STATUS_PATH` outside this directory. Do not copy an
unreviewed home-directory status file into the package.
