# PassivBot reference vault

Reviewed on 2026-11-01 for
[Passivbot Canon](https://tradingstrategy.ai/vaults/passivbot-canon), native
Hypercore chain 9999, address `0x490af7d4a048a81db0f677517ed6373565b42349`.

## Evidence and attribution

The [official PassivBot README](https://github.com/enarjord/passivbot) links this
exact address as its reference long-only configuration. The project supplies
open-source trading software; other users can run independent deployments.
Curator attribution therefore uses the reviewed chain/address override and
excludes automatic vault-name matching.

The [strategy documentation](https://github.com/enarjord/passivbot/blob/master/docs/config.bot.md)
explains contrarian entries, adding to positions within exposure limits,
staggered or trailing orders and loss-taking mechanisms. The vault's running
software version and settings are not independently verified. The repository's
current defaults must not be presented as the exact live configuration.

A manual public `vaultDetails` request through
`HyperliquidVault.fetch_metadata()` succeeded on 2026-11-01 against Hyperliquid
mainnet. It returned:

- Name: `Passivbot Canon`
- Description: `Reference vault using Passivbot’s template config. Standard settings, no alterations.`
- Leader commission: `0.1`, or 10% of profits
- Closed: `false`; deposits allowed: `true`

[Hyperliquid's user-vault terms](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors-legacy)
confirm the one-day lock-up and explain profit sharing and withdrawal slippage.
The [vault on Hyperliquid](https://app.hyperliquid.xyz/vaults/0x490af7d4a048a81db0f677517ed6373565b42349)
remains the source for current positions and operating status.

The maintainer explicitly assigned `high_frequency_high_win_rate`. This is a
strategy classification, not a measured win rate or evidence of latency-based
HFT. The other maintained tags describe algorithmic, directional,
mean-reversion and orderbook market-making behaviour, alongside the
native perpetual-futures default.

The grid trading tag was removed at the maintainer's request on 2026-11-14.
Migration `073-passivbot-canon-strategy-tags.py` refreshes only this vault's
persisted strategy tags. Run it after merge using the production procedure below;
do not rerun migration 020. It inspects at most one row and changes at most one
field. No historical price backfill is needed for this tag removal.

Background reading in the public curator profile includes:

- [CoinLedger's comparison of open-source crypto trading bots](https://coinledger.io/tools/the-best-open-source-and-free-crypto-trading-bots)
- [Futuresboard's PassivBot guide](https://ecoppen.github.io/futuresboard/passivbot/), labelled as historical community guidance

## Historical coverage

The local snapshot dated 2026-10-28 records a first-seen time of
2025-08-29 16:02:48.972 UTC and a native first-seen block of zero. Its 3,594 price
rows for this vault start on 2025-09-03. The current public API's earliest
`allTime` account-value and PnL samples are both 2025-09-03 22:36:00.105 UTC.
The existing series already covers that available start date; this metadata
change does not require a historical price backfill. Earlier trading cannot be
reconstructed from these API samples alone. No local snapshot was modified.

## Migration and publication

Migration `020-passivbot-canon-metadata.py` refreshes only the full description,
summary and strategy tags of the exact native vault. It uses the same maintained
resolvers as future scanner exports. Migration
`073-passivbot-canon-strategy-tags.py` subsequently removes the grid trading
tag from persisted metadata. Curator identity is resolved during export
and requires no persisted curator-field repair.

The migration is validated on isolated test data, including preview, apply,
backup, repeat execution, missing targets and invalid classification. Production
application is a separate operator action after merge and deployment. Follow the
[numbered migration runbook](../../.claude/docs/migration-scripts.md#production-runbook);
the runner executes **all pending migrations** in order, including 073 when pending.

On the production host, update the checkout to the merged revision and inspect
`docker-compose.yml`, including its persistent state mounts. Build the stamped
image, inspect the migration list, then stop persistent and competing writers:

```shell
cd ~/vault-scanner/web3-ethereum-defi
source ../vault-rpc.env
scripts/build-container.sh
docker compose run --rm --no-deps --entrypoint vault-migrate vault-scanner-oneshot list
docker compose stop vault-scanner-looped post-scanner
docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
```

Inspect the preview before applying. Migration 020 should inspect at most one
existing row and change at most three fields; migration 073 should inspect at
most one row and change at most one field. Neither performs historical writes:

```shell
docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
docker compose exec vault-scanner-looped cat /usr/src/web3-ethereum-defi/GIT_VERSION_HASH.txt
```

Check that the image commit matches the migration journal. The restarted
scanner's normal cleaning, curator export, top-vault export and uploads publish
the changes; migration success alone does not update the website. Check the
public curator, description, reference links and strategy tags after a successful
cycle. On failure, inspect the journal and backups before retrying or restoring;
restart the services when maintenance ends without resetting historical state.
