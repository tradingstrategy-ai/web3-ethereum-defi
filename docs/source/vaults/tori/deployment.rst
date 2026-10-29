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
and changes, including this migration's single native strUSD deployment, then
apply with the same image::

    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false -e SCAN_PRICES=false -e UPSHIFT_SCAN_PRICES=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
    docker compose exec vault-scanner-looped cat /usr/src/web3-ethereum-defi/GIT_VERSION_HASH.txt

Verify the printed commit against the migration journal. The runner owns the
lifecycle guard before the writer lock, backups and atomic metadata commit;
the repair helper takes no additional locks. Keep normal cleaning, exports
and uploads enabled in the restarted scanner. Check the public strUSD listing
after its successful cycle: protocol and curator should be Tori Finance, the
app link and four strategy tags should be present, fees should preserve the
unknown retained income share, and withdrawal timing should match the live
cooldown. The separate Upshift ecosystem vault must remain attributed to
RockawayX. Migration success alone does not publish these changes.
