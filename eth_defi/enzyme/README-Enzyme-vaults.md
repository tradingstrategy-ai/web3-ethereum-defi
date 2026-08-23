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

### Blue descriptions

Vault share-token contracts provide a name but no manager-authored strategy
text. Enzyme's authenticated
[GetVault API](https://sdk.enzyme.finance/api/endpoints/vault/) is the
authoritative source for Blue listing metadata:

- API ``tagline`` becomes the short description.
- API ``description`` becomes the long description.
- An empty successful response means that the manager has supplied no public
  copy. The scanner then retains its generic Blue fallback text.

The scheduled scanner never makes a per-vault API request. Instead,
``scripts/enzyme/migrate-offchain-metadata.py`` creates the versioned cache at
``~/.tradingstrategy/cache/enzyme/vault-metadata.json`` and updates the local
vault database in one transaction. Adapters read that cache without a token.

Create a token in the Enzyme application, store it only in the operator's
secret environment, and run the migration serially. The provider can return
``429`` with ``Retry-After``; do not increase concurrency to work around it.

```shell
source .local-test.env
DRY_RUN=true MAX_WORKERS=1 poetry run python scripts/enzyme/migrate-offchain-metadata.py
DRY_RUN=false MAX_WORKERS=1 poetry run python scripts/enzyme/migrate-offchain-metadata.py
```

The variable must be named ``ENZYME_BLUE_API_TOKEN``. Never commit it, print
it, or put it in a command line.

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

## Running migrations in the scanner container

``docker-compose.yml`` passes ``ENZYME_BLUE_API_TOKEN`` only to
``vault-scanner-oneshot``. The token must be exported in the environment that
launches Compose; it is not baked into the image or persisted in the repository.
Stop the looped scanner before modifying its shared metadata state.

```shell
source ~/vault-scanner/vault-rpc.env
cd ~/vault-scanner/web3-ethereum-defi
docker compose stop vault-scanner-looped
docker compose --profile oneshot run --rm --entrypoint /bin/bash vault-scanner-oneshot \
  -lc 'DRY_RUN=false MAX_WORKERS=1 poetry run python scripts/enzyme/migrate-offchain-metadata.py'
docker compose start vault-scanner-looped
```

The container mounts ``~/.tradingstrategy`` as persistent scanner state. Do
not run the migration in an unmounted container, remove its metadata pickle or
delete the timestamp caches while doing metadata maintenance.
