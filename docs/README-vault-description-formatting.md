# Repair vault description formatting

Nest's [first-party CMS](https://cms.nest.credit/api/vaults?limit=100&depth=2)
has supplied the BlackOpal LiquidStone II summary with a literal `\n` between
sentences, and its full description with actual paragraph breaks. Individual
vault `description` and `short_description` values must be single-line text,
without newline characters, carriage returns or literal `\n` / `\r` escapes.
Short descriptions must contain only one sentence. When a description or a
provider-supplied summary is automatically copied into `short_description`,
retain only its first sentence and ending punctuation. This requirement does
not change protocol-level Markdown descriptions or notes. A single line here
means no embedded line breaks; the frontend may still wrap text to fit a screen.

The corrected summary is:

> Investment grade short-duration payment receivables.

Lagoon's [Coinshift USPC High Yield page](https://app.lagoon.finance/vault/1/0x09252D2C4afca9B1479efdD39fAa53DE9ff23114)
also supplies two sentences in its summary field. Its adapter now retains only
the first sentence, including for previously cached responses.

`normalise_vault_description()` flattens full descriptions, while
`normalise_vault_short_description()` also extracts the first sentence. Nest,
Lagoon and scanner output use the appropriate helper; automatically generated
Accountable summaries share the same first-sentence logic.
The public JSON exporter also applies both rules to every published vault,
including reused sticky records and native vault records that bypass the
ERC-4626 scanner.
`scripts/erc-4626/fix-vault-descriptions.py` repairs existing rows across all
protocols without fetching new data. It changes only `_description` and
`_short_description`; full descriptions retain every sentence. It preserves
notes, all other metadata and scanning state.
Rerunning an applied repair makes no further changes.

## Local inspection

Use `VAULT_DB_PATH` to inspect a copy of the metadata pickle. Dry-run mode is
the default and reports the exact before/after text with escaped line breaks:

```shell
VAULT_DB_PATH=/path/to/copy/vault-metadata-db.pickle DRY_RUN=true poetry run python scripts/erc-4626/fix-vault-descriptions.py
```

## Production rollout

Run these commands on the scanner host after the change has been merged and
the production checkout at `~/vault-scanner/web3-ethereum-defi` updated to the
merged revision. Inspect its `docker-compose.yml` before maintenance. Build
the version-stamped image through the repository wrapper:

```shell
source ~/vault-scanner/vault-rpc.env && (cd ~/vault-scanner/web3-ethereum-defi && scripts/build-container.sh)
```

Stop the persistent scanner before inspecting and applying the repair, so it
cannot overwrite the repaired pickle. The overridden entrypoint runs only the
repair script, with historical price scanning explicitly disabled. The service
retains its normal mounted `/root/.tradingstrategy` production state.

```shell
source ~/vault-scanner/vault-rpc.env && (cd ~/vault-scanner/web3-ethereum-defi && docker compose stop vault-scanner-looped)
source ~/vault-scanner/vault-rpc.env && (cd ~/vault-scanner/web3-ethereum-defi && docker compose run --rm --entrypoint /bin/bash vault-scanner-oneshot -lc 'SCAN_PRICES=false DRY_RUN=true python scripts/erc-4626/fix-vault-descriptions.py')
```

Review the reported changes, then apply and check that a second dry run reports
zero changed fields:

```shell
source ~/vault-scanner/vault-rpc.env && (cd ~/vault-scanner/web3-ethereum-defi && docker compose run --rm --entrypoint /bin/bash vault-scanner-oneshot -lc 'SCAN_PRICES=false DRY_RUN=false python scripts/erc-4626/fix-vault-descriptions.py')
source ~/vault-scanner/vault-rpc.env && (cd ~/vault-scanner/web3-ethereum-defi && docker compose run --rm --entrypoint /bin/bash vault-scanner-oneshot -lc 'SCAN_PRICES=false DRY_RUN=true python scripts/erc-4626/fix-vault-descriptions.py')
source ~/vault-scanner/vault-rpc.env && (cd ~/vault-scanner/web3-ethereum-defi && docker compose up -d vault-scanner-looped)
```

The next normal scanner post-processing pass must regenerate and upload the
public top-vaults JSON. Keep `SKIP_POST_PROCESSING` and `SKIP_TOP_VAULTS` disabled
in the production configuration. Watch the scanner logs for successful export
and upload before checking the
[Nest vault page](https://tradingstrategy.ai/vaults/nest-blackopal-liquidstone-ii-vault-6)
and [Coinshift vault page](https://tradingstrategy.ai/vaults/coinshift-leveraged-uspc).
The metadata repair alone does not refresh the public export or website caches.

If the repair fails, leave the metadata unchanged, investigate the error and
restart the scanner when maintenance ends. Do not reset reader state or rescan
historical prices to repair display text.
