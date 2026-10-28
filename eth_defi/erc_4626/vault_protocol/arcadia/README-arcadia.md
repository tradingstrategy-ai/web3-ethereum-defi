# Arcadia vault descriptions

Arcadia’s [public pools API](https://api.arcadia.finance/redoc) lists lending
pools and their ERC-4626 tranches. Vault metadata matches the **tranche address
and chain ID**, rather than the parent pool address. The same contract address
can exist on multiple chains. Responses are cached per chain for 24 hours;
requests time out after 30 seconds and failed refreshes back off for five minutes.
An older valid response remains usable during an outage.

The adapter preserves provider narrative when available. API labels such as
“Arcadia USDG Pool” trigger deterministic short and long descriptions based on
the denomination token, chain and explicitly named senior or junior tranche.
During an API outage or catalogue miss, the same generator uses known onchain
fields. Missing fields are omitted rather than guessed.

Generated copy explains collateralised lending and utilisation-dependent
interest, with smart contract and bad-debt risks described in the
[lender guide](https://docs.arcadia.finance/users/lenders). It does not embed
live rates, claim capital protection or promise withdrawal liquidity. These
descriptions do not certify deposit/redemption support or change fee handling.

## Repairing existing metadata

Run from the repository using its Poetry environment. When reusing the parent
checkout's virtual environment in a worktree, prefix commands with
`PYTHONPATH=.` so imports use the worktree's code:

```shell
DRY_RUN=true poetry run python scripts/erc-4626/migrate-arcadia-vault-metadata.py
DRY_RUN=false poetry run python scripts/erc-4626/migrate-arcadia-vault-metadata.py
```

Dry run is the default. Set `VAULT_DB_PATH` for a particular metadata pickle,
or use the normal `PIPELINE_DATA_DIR`. The repair changes only missing or
label-only `_description` and `_short_description` fields on Arcadia rows.
It preserves existing narratives, creates a non-overwriting backup before an
actual write, and takes the scanner writer lock. Prices, reader state, leads,
and timestamp caches are untouched. Re-export metadata through the normal
pipeline after applying a repair. For production maintenance, inspect the
Compose configuration and preserve all mounted state as described in `AGENTS.md`.

## Live API check

The focused test performs a real, unauthenticated request and checks the exact
Robinhood USDG tranche identity without asserting changing rates:

```shell
source .local-test.env && PYTHONPATH=. RUN_ARCADIA_OFFCHAIN_METADATA_TEST=1 timeout 180 poetry run pytest tests/erc_4626/vault_protocol/test_arcadia_offchain_metadata.py -k live -q --log-cli-level=info
```

Manual verification on 2026-10-28: the command above passed against the public
Arcadia Finance API, confirming two Robinhood tranches and the exact USDG
pool/tranche relationship. No authentication token or chain RPC was required.
