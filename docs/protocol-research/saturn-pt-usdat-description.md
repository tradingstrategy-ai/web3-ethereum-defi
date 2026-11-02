# Saturn PT-USDat description

Reviewed on 2 November 2026. The override targets Monad (143) share token
`0x1a5b77cef5e4b6f806aeac6f6ea9213a617dc7e3`. Accountable's web URL uses the
separate loan address `0x593784188369E5e5f484025A1940cf437620C3Fe`.

## Evidence and limits

The locally cached [Accountable vault metadata](https://yield.accountable.capital/vaults/0x593784188369E5e5f484025A1940cf437620C3Fe)
identifies Hyperithm as manager. Its full strategy describes PT-USDat looping,
floating USDC borrowing costs, rolling maturities, and withdrawals through
redemption or staged Pendle sales. It estimates one to two weeks in most cases
and states the earlier of maturity or two months as a limit, but also warns
that limited Morpho USDC or Pendle PT liquidity can delay processing. The copy
preserves both statements and does not promise a deadline. Direct access to
Accountable was blocked during this review; these are cached operator claims,
not independently verified current holdings or withdrawal performance.

[Saturn's current homepage](https://saturn.credit/) describes USDat as backed
by PayPal USD through PYUSDx and distinguishes it from sUSDat's exposure to
company securities. Older Treasury-backing wording remains on Saturn's deposit
page. The maintained copy uses the current homepage, consistent with the
[official USDat repository](https://github.com/saturn-organization/saturn-dollar),
which records the migration to PYUSDX. No claim about the exact PT maturity,
current allocation, leverage ratio or current fees is added.

[Pendle's principal-token documentation](https://docs.pendle.finance/pendle-v2/ProtocolMechanics/YieldTokenization/PT)
explains redemption in the market's accounting asset, the purchase discount
and the separation from variable income and points.
[Morpho's variable-rate market documentation](https://docs.morpho.org/learn/concepts/blue/)
supports the collateral, interest, oracle and liquidation explanations.

## Run after merge

Publish `docs/source/glossary.rst` from the separate documentation repository
first. Verify the frontend routes `/glossary/pendle`, `/glossary/principal-token`,
`/glossary/usdat`, `/glossary/morpho` and `/glossary/maturity` resolve before
publishing the vault copy. Also check the existing routes `/glossary/stablecoin`, `/glossary/collateral`, `/glossary/recursive-looping`,
`/glossary/leverage`, `/glossary/liquidation`, `/glossary/oracle` and
`/glossary/liquidity`. The frontend imports the deployed Sphinx glossary;
no frontend routing change is required. No local Sphinx build was performed.

Migration `026-saturn-pt-usdat-description.py` changes only `_description` and
`_short_description` in `vault-metadata-db.pickle`, for at most one existing row
and two changed fields. Missing targets are reported without creating records.
Migration `027-saturn-pt-usdat-strategy-tags.py` separately changes only
`_strategy_tags` for the same share token: lending looping, carry trade and AMM.
The current local metadata row was inspected on 2 November 2026 and describes
recursive USDC borrowing, the PT yield/borrowing-cost spread and Pendle AMM
sales. These support the three tags; supplying loans, providing liquidity and
direct real-world lending are not established. The migration reports at most
one changed row/field, preserves other data and requires Accountable detection.

No price backfill is needed for these metadata changes. Do not reset reader state.

Follow the [production migration guide](../../.claude/docs/migration-scripts.md).
From `~/vault-scanner/web3-ethereum-defi`, inspect `docker-compose.yml` and its
persistent mounts, update to the merged revision and build the stamped image
with `scripts/build-container.sh`. Finish competing writers, then run:

```shell
source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
source ../vault-rpc.env && docker compose run --rm --no-deps --entrypoint vault-migrate vault-scanner-oneshot list
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
docker compose exec vault-scanner-looped cat /usr/src/web3-ethereum-defi/GIT_VERSION_HASH.txt
```

The runner executes all pending migrations: review the full preview before
apply. Both preview and apply acquire the lifecycle guard before the writer
lock. Preserve the same image and persistent mounts throughout maintenance.
Check the journal's commit against the running image, reported row/field counts
and backups. Confirm other metadata, prices and reader state are preserved.
The restarted scanner publishes the repaired metadata through its normal
export cycle; migration success alone does not publish the description.

Check the live vault page's headings, glossary links and short summary after
publication. Production execution and live publication checks remain pending.

## Local review

The writing-style review and independent Claude Opus 5.5 review were completed
on 2 November 2026. The review's stray glossary bullet was corrected, and the
migration expectations now explicitly normalise the maintained copy. The
existing parameterised Accountable offline test includes the new override and
checks description, summary, link and formatting without an API request.

Focused tests cover the Saturn migration, Accountable metadata and numbered
migration batch. Glossary checks validate all 12 linked slugs and the five new
unique terms against the frontend slug rules; lightweight reStructuredText and
whitespace checks pass. Ruff's module-name rule is excluded for the required
numbered migration filename, consistent with the existing migration naming.

The follow-up classification review used Claude Opus 5.5. Its error-message
mismatch finding was corrected and the focused suite passed (68 tests, one
opt-in live test skipped). The complete-batch fixture now includes Accountable
detection records, as required by the new tag migration's safety check.
