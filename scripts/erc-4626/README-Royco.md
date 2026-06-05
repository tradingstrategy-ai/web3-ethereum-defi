# Royco vault rescan commands

Commands for rediscovering Royco vaults and rescanning the historical price
data for newly added Royco vault adapters.

Royco tranche vaults are discovered from custom events and read with a
Royco-specific historical reader. Because the vault discovery scanner is
incremental, old Royco event logs need a lead reset after deploying new Royco
event support.

## Vault ids

The first Royco senior/junior tranche vaults added with this adapter are on
Ethereum mainnet:

```shell
export ROYCO_TRANCHE_VAULT_IDS="1-0x059bc7aa5000a26aae2601cfbf060653adf8fd91,1-0x1ba515a409dd702105415cdaae439059aa0b402a"
```

Add any newly discovered Royco vaults to this comma-separated list before
running the targeted price rescan.

## 1. Back up production scanner state

Back up the metadata database, raw price parquet, and reader state before a
large production rediscovery.

```shell
mkdir -p ~/.tradingstrategy/vaults/backups

cp ~/.tradingstrategy/vaults/vault-metadata-db.pickle \
   ~/.tradingstrategy/vaults/backups/vault-metadata-db.before-royco-rescan.pickle

cp ~/.tradingstrategy/vaults/vault-prices-1h.parquet \
   ~/.tradingstrategy/vaults/backups/vault-prices-1h.before-royco-rescan.parquet

cp ~/.tradingstrategy/vaults/reader-state.pickle \
   ~/.tradingstrategy/vaults/backups/reader-state.before-royco-rescan.pickle
```

## 2. Check Royco API inventory

This does not write the vault database. It compares Royco's first-party Vault
Market API rows against the local metadata database and onchain RPC reads.

```shell
source .local-test.env && \
ACTIVE_VERIFIED_ONLY=true \
LOG_LEVEL=info \
poetry run python scripts/erc-4626/scan-royco-vaults.py
```

Use this output to identify any active verified Royco wrappers missing from the
local vault database.

## 3. Rediscover Royco event leads

Run this after adding support for Royco custom discovery events. This rescans
all configured ERC-4626 discovery events from block 1 on Ethereum and merges
the results back into the existing vault metadata database.

```shell
source .local-test.env && \
RESET_LEADS=1 \
SCAN_BACKEND=hypersync \
LOG_LEVEL=info \
JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
poetry run python scripts/erc-4626/scan-vaults.py
```

If HyperSync is unavailable, use the RPC backend. This is slower on Ethereum.

```shell
source .local-test.env && \
RESET_LEADS=1 \
SCAN_BACKEND=rpc \
LOG_LEVEL=info \
JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
poetry run python scripts/erc-4626/scan-vaults.py
```

## 4. Rescan historical prices for new Royco vaults

Use `VAULT_ID` for a targeted history rewrite. This is safe against production
data: `scan-prices.py` clears saved reader state and parquet rows only for the
listed vault ids, preserving unrelated vault history.

```shell
source .local-test.env && \
VAULT_ID="$ROYCO_TRANCHE_VAULT_IDS" \
JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
START_BLOCK=1 \
LOG_LEVEL=info \
poetry run python scripts/erc-4626/scan-prices.py
```

For a bounded smoke test, set `END_BLOCK` to a recent Ethereum block before
running the full history scan:

```shell
source .local-test.env && \
VAULT_ID="$ROYCO_TRANCHE_VAULT_IDS" \
JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
START_BLOCK=25251545 \
END_BLOCK=25261545 \
LOG_LEVEL=info \
poetry run python scripts/erc-4626/scan-prices.py
```

## 5. Verify one Royco vault history read

Use the same historical reader pipeline as `scan-prices.py` for a single vault.

```shell
source .local-test.env && \
VAULT_ID="1-0x059bc7aa5000a26aae2601cfbf060653adf8fd91" \
START_BLOCK=25251545 \
END_BLOCK=25261545 \
JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
poetry run python scripts/erc-4626/check-vault-history.py
```

Check the parquet after the price scan:

```shell
poetry run python scripts/erc-4626/check-prices-parquet.py
```

## 6. Post-process prices

After the targeted historical scan has completed, rebuild cleaned prices and
exports.

```shell
source .local-test.env && \
LOG_LEVEL=info \
poetry run python scripts/erc-4626/post-process-prices.py
```

For a local-only dry run that skips uploads:

```shell
source .local-test.env && \
SKIP_EXPORT=true \
LOG_LEVEL=info \
poetry run python scripts/erc-4626/post-process-prices.py
```

## Docker equivalents

Rediscover Ethereum Royco leads in the vault scanner container:

```shell
docker compose --profile oneshot run --rm \
  --entrypoint python \
  -e JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
  -e HYPERSYNC_API_KEY="$HYPERSYNC_API_KEY" \
  -e SCAN_BACKEND=hypersync \
  -e RESET_LEADS=true \
  -e LOG_LEVEL=info \
  vault-scanner \
  scripts/erc-4626/scan-vaults.py
```

Rescan only the new Royco vault histories:

```shell
docker compose --profile oneshot run --rm \
  --entrypoint python \
  -e JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
  -e VAULT_ID="$ROYCO_TRANCHE_VAULT_IDS" \
  -e START_BLOCK=1 \
  -e LOG_LEVEL=info \
  vault-scanner \
  scripts/erc-4626/scan-prices.py
```
