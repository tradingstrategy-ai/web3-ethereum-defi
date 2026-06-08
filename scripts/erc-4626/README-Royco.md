# Royco vault rescan

Run these commands after adding Royco vault support to rediscover old Royco
events and rebuild historical prices for Royco API vaults.

## 1. Back up state

```shell
mkdir -p ~/.tradingstrategy/vaults/backups

cp ~/.tradingstrategy/vaults/vault-metadata-db.pickle \
   ~/.tradingstrategy/vaults/backups/vault-metadata-db.before-royco-rescan.pickle
cp ~/.tradingstrategy/vaults/vault-prices-1h.parquet \
   ~/.tradingstrategy/vaults/backups/vault-prices-1h.before-royco-rescan.parquet
cp ~/.tradingstrategy/vaults/reader-state.pickle \
   ~/.tradingstrategy/vaults/backups/reader-state.before-royco-rescan.pickle
```

## 2. Rediscover Royco leads

```shell
source .local-test.env && \
RESET_LEADS=1 \
SCAN_BACKEND=hypersync \
LOG_LEVEL=info \
JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
poetry run python scripts/erc-4626/scan-vaults.py
```

## 3. Build Royco API vault ids

This builds one Ethereum `VAULT_ID` list from active verified Royco API rows.
`scan-prices.py` scans one RPC chain at a time, so run one id list per chain.

```shell
source .local-test.env && \
export ROYCO_API_VAULT_IDS="$(
  ROYCO_CHAIN_ID="${ROYCO_CHAIN_ID:-1}" poetry run python - <<'PY'
import os

from eth_defi.erc_4626.vault_protocol.royco.offchain_metadata import fetch_royco_vaults

chain_id = int(os.environ.get("ROYCO_CHAIN_ID", "1"))
metadata = fetch_royco_vaults(api_key=os.environ.get("ROYCO_API_KEY", "ROYCO_DEMO"))
vault_ids = sorted(
    f"{entry['chain_id']}-{entry['vault_address']}"
    for entry in metadata.values()
    if entry["chain_id"] == chain_id
    and entry["is_verified"]
    and (entry["is_active"] is None or entry["is_active"])
)
print(",".join(vault_ids))
PY
)" && \
test -n "$ROYCO_API_VAULT_IDS" && \
echo "$ROYCO_API_VAULT_IDS" | tr ',' '\n'
```

## 4. Rescan Royco history

This clears reader state and parquet rows only for the listed vault ids.

```shell
source .local-test.env && \
VAULT_ID="$ROYCO_API_VAULT_IDS" \
JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
START_BLOCK=1 \
LOG_LEVEL=info \
poetry run python scripts/erc-4626/scan-prices.py
```

## 5. Verify and post-process

```shell
source .local-test.env && \
VAULT_ID="1-0x059bc7aa5000a26aae2601cfbf060653adf8fd91" \
START_BLOCK=25251545 \
END_BLOCK=25261545 \
JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
poetry run python scripts/erc-4626/check-vault-history.py

poetry run python scripts/erc-4626/check-prices-parquet.py

source .local-test.env && \
LOG_LEVEL=info \
poetry run python scripts/erc-4626/post-process-prices.py
```

## Fixing corrupted Royco tranche price data

Before the `RoycoTrancheHistoricalReader` was deployed, the generic ERC-4626
reader decoded only the first word of the Royco `AssetClaims(stAssets,
jtAssets, nav)` tuple, producing wrong `total_assets` and `share_price` values.
The first word (`stAssets`) can look plausibly small yet still be incorrect — the
correct value is `claims.nav`.

### Background

- The fix is in `eth_defi/erc_4626/vault.py`: the generic reader now rejects
  return payloads longer than 32 bytes with an error instead of misinterpreting
  them.
- The correct `RoycoTrancheHistoricalReader` (in
  `eth_defi/erc_4626/vault_protocol/royco/vault.py`) decodes the full tuple
  and uses `nav` for both TVL and share price.
- Existing corrupted rows in the price parquet must be purged and rescanned
  with the correct reader.

### Step 1: purge corrupted data

The purge script removes **all** historical rows for Royco tranche vaults
(identified via vault metadata DB) and clears their reader states. It uses the
same `UNCLEANED_PRICE_DATABASE` / `READER_STATE_DATABASE` env vars as
`scan-prices.py`.

Dry run first to see what would be removed:

```shell
source .local-test.env && \
DRY_RUN=true \
poetry run python scripts/erc-4626/purge-royco-tranche-data.py
```

Then run for real:

```shell
source .local-test.env && \
poetry run python scripts/erc-4626/purge-royco-tranche-data.py
```

The script creates backups (`*.bak-royco-purge`) and outputs per-chain rescan
commands.

### Step 2: rescan affected vaults

The normal scanner picks `start_block` from the max `last_block` of all reader
states on the chain. After purging only the Royco tranche states, other vault
states push `start_block` near head. Use `VAULT_ID` + `START_BLOCK=1` to force
a targeted rescan from each vault's first block.

The purge script outputs the exact commands, but the pattern is:

```shell
source .local-test.env && \
VAULT_ID="1-0x059bc7aa5000a26aae2601cfbf060653adf8fd91,1-0x1ba515a409dd702105415cdaae439059aa0b402a" \
START_BLOCK=1 \
JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
LOG_LEVEL=info \
poetry run python scripts/erc-4626/scan-prices.py
```

### Step 3: post-process

After the rescan populates new rows with correct values:

```shell
source .local-test.env && \
LOG_LEVEL=info \
poetry run python scripts/erc-4626/post-process-prices.py
```
