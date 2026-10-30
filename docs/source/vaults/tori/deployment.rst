Refresh existing Tori metadata
------------------------------

Migration ``012-tori-staking-metadata.py`` refreshes only the existing Ethereum
strUSD row. It updates protocol identification, descriptions, fees, app link,
withdrawal timing, strategy tags and public transaction capability. Curator
attribution resolves during export. An absent target is reported without
creating a row; incompatible cached identity aborts before changes are staged.

The migration reads ``JSON_RPC_ETHEREUM`` for the current cooldown. It does
not scan historical prices or change discovery boundaries, reader state,
timestamp caches or unrelated metadata. Repeated transformation of an
unchanged row is a no-op. A successful numbered migration is skipped on retry.

Migration ``013-tori-vault-risk-and-fees.py`` is the subsequent
fee refresh for the 17 reviewed native and third-party Tori exposures. It uses
``JSON_RPC_ETHEREUM`` and recent ``JSON_RPC_MONAD`` state, preserves their
actual protocols and curators, and retains stricter existing ratings and
incident exclusions. It validates all present identities and completes live
reads before staging updates. Missing targets are reported without creation;
review them before treating the rollout as complete. Current product
risk and disclosures resolve during export; product copy is appended after
all warning checks. See :doc:`risk-and-fees` for the assessment and fee limitations. Do not edit or
rerun a successful 012 to apply this later refresh.

Run after merge
~~~~~~~~~~~~~~~

Follow ``.claude/docs/migration-scripts.md``. On the production host, inspect
``docker-compose.yml`` and its persistent mounts, update to the merged revision,
then build using ``scripts/build-container.sh``. Keep the same inspected image
through preview, apply and the subsequent export cycle.

From ``~/vault-scanner/web3-ethereum-defi``, stop persistent and competing
one-shot or cron metadata writers before both preview and apply::

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps --entrypoint vault-migrate vault-scanner-oneshot list
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true -e SCAN_PRICES=false -e UPSHIFT_SCAN_PRICES=false --entrypoint vault-migrate vault-scanner-oneshot run

The runner previews the complete pending batch. Review the reported targets
and changes, including 012's single native strUSD deployment if still pending
and 013's 17 reviewed exposures, then
apply with the same image::

    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false -e SCAN_PRICES=false -e UPSHIFT_SCAN_PRICES=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
    docker compose exec vault-scanner-looped cat /usr/src/web3-ethereum-defi/GIT_VERSION_HASH.txt

Verify the printed commit against the migration journal. The runner owns the
lifecycle guard before the writer lock, backups and atomic metadata commit;
the repair helper takes no additional locks. Keep normal cleaning, exports
and uploads enabled in the restarted scanner. Check the public strUSD listing
after its successful cycle: protocol and curator should be Tori Finance, the
app link and four strategy tags should be present, fees should show
internalised accounting with an unknown gross retained income percentage,
and withdrawal timing should match the live
cooldown. The separate Upshift ecosystem vault must remain attributed to
RockawayX. For 013, check native High risk with stricter ratings/exclusions
preserved, unchanged third-party ratings, each product's risk and fee notes,
and current per-vault fee fields.
Migration success alone does not publish these changes.

Later fee-note and source-link copy changes resolve from maintained disclosures
and protocol YAML during export. This follow-up changes no cached fee accessor,
vault database field or historical store, so it needs no additional numbered
migration. Rebuild protocol metadata and regenerate normal vault exports with
the updated code; do not rerun a successful 013 solely to publish these notes.
