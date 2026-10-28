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

Migration `006-arcadia-vault-metadata.py` fills missing or label-only descriptions on existing Arcadia rows, retaining meaningful narratives. An empty Arcadia inventory is an explicit no-op.

Use the [numbered migration guide](../../../../.claude/docs/migration-scripts.md) for preview, apply, backups and
recovery. The former standalone command is retired and its format is legacy.
Use a new numbered migration for later repairs. Production must stop the
daemon before previews as well as writes and use the host launcher. Restart
the same inspected image afterwards for normal cleaning, exports and uploads.
No historical backfill or reader-state reset is needed.

## Live API check

The focused test performs a real, unauthenticated request and checks the exact
Robinhood USDG tranche identity without asserting changing rates:

```shell
source .local-test.env && PYTHONPATH=. RUN_ARCADIA_OFFCHAIN_METADATA_TEST=1 timeout 180 poetry run pytest tests/erc_4626/vault_protocol/test_arcadia_offchain_metadata.py -k live -q --log-cli-level=info
```

Manual verification on 2026-10-28: the command above passed against the public
Arcadia Finance API, confirming two Robinhood tranches and the exact USDG
pool/tranche relationship. No authentication token or chain RPC was required.
