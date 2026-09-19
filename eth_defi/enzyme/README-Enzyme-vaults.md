# Enzyme vaults

This package supports the two separate Enzyme vault architectures: Enzyme Blue
and Enzyme Onyx. Both issue ERC-20 investor shares, but neither should be
treated as a generic ERC-4626 vault. The shared scanner preserves their
architecture-specific discovery, accounting and metadata rules.

## Enzyme Blue

Blue is Enzyme's VaultProxy/ComptrollerProxy architecture. The VaultProxy is
the investor share token; the paired ComptrollerProxy controls fund accounting,
assets and investor actions. Blue discovery follows the reviewed Dispatcher
`VaultProxyDeployed` events on Ethereum, Polygon, Base and Arbitrum.

The adapter reads current gross asset value, share supply and fee configuration.
Historical reads derive gross share value from GAV and outstanding shares. The
currently exported management fee is the user-facing current rate and the
protocol fee is also exported separately; consumers must not add the latter a
second time. Exact historical net share value needs the release-aware
FundValueCalculatorRouter and is not yet exported.

Blue deposit permission comes from current PolicyManager configuration. The
``ALLOWED_DEPOSIT_RECIPIENTS`` policy makes a vault ``whitelisted``; without
that policy it is ``permissionless`` for wallet identity. This does not
guarantee that every deposit will succeed, because other fund policies and
approvals can still apply.

### Blue descriptions and manager contacts

Vault share-token contracts provide a name but no manager-authored strategy
text. Enzyme's vault-detail application currently exposes Blue profile data
through an undocumented public GraphQL ``vaultProfile`` query. The query is
not a stable external API contract, so its use is isolated to the metadata
migration and must be reviewed when the app changes. It provides:

- ``tagline`` becomes the short description.
- ``description`` becomes the long description.
- manager biography, free-form contact information, public email, Telegram,
  X/Twitter and website fields are retained in the Enzyme metadata cache.
- a manager identifier uses X/Twitter, Telegram or a non-generic email local
  part when supplied, and otherwise falls back to the website domain.
- An empty successful response means that the manager has supplied no public
  copy. The scanner leaves that description field empty.

The scheduled scanner never makes a per-vault app-profile request. Instead,
``scripts/enzyme/migrate-offchain-metadata.py`` creates the versioned cache at
``~/.tradingstrategy/cache/enzyme/vault-metadata.json`` and updates the local
vault database after all profiles have been collected. In apply mode it
checkpoints successful app-profile replies in
``enzyme-offchain-metadata-state.json`` beside the metadata pickle, then
resumes only the missing replies after an interruption. The checkpoint is
deleted after both cache and database writes complete; set
``ENZYME_METADATA_STATE_PATH`` to use another location. Adapters read the
published cache without contacting the app backend.

Adapters retain compatibility with cache version one during an upgrade, so the
scanner preserves existing descriptions before this migration completes. The
migration does not reuse that older cache: it refreshes every Blue profile and
publishes the contact fields in cache version two.

The migration reads every discovered Blue vault regardless of its NAV or
denomination, because contact metadata is independent of asset value. The
exact retired generated fallback fields are cleared locally for every Blue row,
so older databases cannot keep invented copy. After a complete cache/database
update, later runs reuse the cache. Set ``ENZYME_METADATA_REFRESH=true`` to
fetch every Blue vault again. Requests are strictly serial in batches of at
most five; stop if the undocumented backend changes its schema or response
behaviour.
``ENZYME_REQUEST_INTERVAL_SECONDS`` defaults to one second between request
batches, protecting the endpoint from rate limiting during the full catalogue
refresh.

```shell
source .local-test.env
DRY_RUN=true poetry run python scripts/enzyme/migrate-offchain-metadata.py
DRY_RUN=false poetry run python scripts/enzyme/migrate-offchain-metadata.py
```

## Enzyme Onyx

Onyx is a separate modular architecture. Current support covers official Base
SharesFactory deployments, whose standalone Shares token represents the
investor's interest in a vehicle. Discovery follows the factory's
``ProxyDeployed`` events.

Onyx Shares are ERC-20 tokens rather than ERC-4626 vaults. The adapter reads
stored share price and share supply, then reports total value as their product
in the declared value asset. A named asset such as ``USD`` is an accounting
unit, not proof of a USD-denominated token balance or a current one-dollar
exchange rate. For scanner compatibility, reviewed named values use a canonical
Base reporting token: USD maps to USDC, BTC to cbBTC and EUR to EURC. Deposit
code must inspect the active handler instead of relying on this convention.

Onyx deposit permission is reconstructed from active deposit-handler events
and checked at the fixed metadata block with batched Multicall reads. The
adapter supports discovery, metadata and historical accounting, but not
generic deposits, redemptions, flow accounting or portfolio composition: all
depend on the active handler configuration.

### Onyx descriptions

Enzyme does not document a public API for manager-entered Onyx taglines or
descriptions. Do not scrape a signed-in interface or call an undocumented
endpoint. Every refreshed Onyx row therefore has:

- no short description; and
- the long description ``Description is not publicly available``.

Run the current-metadata migration once after upgrading an existing database
so its Onyx rows receive this explicit marker:

```shell
DRY_RUN=true poetry run python scripts/enzyme/migrate-current-metadata.py
MAX_WORKERS=8 poetry run python scripts/enzyme/migrate-current-metadata.py
```

This migration uses configured RPC and Hypersync credentials, preserves price
history, and stores a resumable checkpoint alongside the local vault database.

To refresh every Enzyme vault's complete current investor-facing fee schedule
after a fee-reader update, run the dedicated current-fee migration. It treats a
confirmed disabled fee component as zero, but leaves failed or unavailable
reads as unknown. It does not change historical fee series or price history:

```shell
DRY_RUN=true poetry run python scripts/enzyme/migrate-enzyme-fees.py
MAX_WORKERS=8 poetry run python scripts/enzyme/migrate-enzyme-fees.py
```

Blue's ``Mgmt fee`` includes protocol access. ``Protocol fee`` is exported
separately, so consumers can calculate the manager-only rate as ``Mgmt fee -
Protocol fee``. ``migrate-blue-fees.py`` remains available for a targeted
Blue-only repair, but is not sufficient for an all-Enzyme fee-reader change.

## Running migrations in the scanner container

Stop the looped scanner before modifying its shared metadata state.

```shell
source ~/vault-scanner/vault-rpc.env
cd ~/vault-scanner/web3-ethereum-defi
docker compose stop vault-scanner-looped
docker compose --profile oneshot run --rm --entrypoint /bin/bash vault-scanner-oneshot \
  -c 'DRY_RUN=false poetry run python scripts/enzyme/migrate-offchain-metadata.py'
docker compose start vault-scanner-looped
```

The container mounts ``~/.tradingstrategy`` as persistent scanner state. Do
not run the migration in an unmounted container, remove its metadata pickle or
delete the timestamp caches while doing metadata maintenance.
