# Vault scan readiness manifest

The scanner publishes `vault-scan-manifest.json` after successful private data
export. It lets live strategies poll for new data without downloading the full
cleaned price history. The contract is defined in
[eth_defi/vault/scan_manifest.py](../eth_defi/vault/scan_manifest.py) and consumed by
`tradingstrategy/vault_scan_manifest.py` in the trading-strategy package.
The [vault script guide](../scripts/erc-4626/README-vault-scripts.md#published-price-readiness)
describes scanner and post-processing switches.

## Storage and serving contract

- Bucket: `R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME` (private).
- Keys: `{UPLOAD_PREFIX}vault-scan-manifest.json` and
  `{UPLOAD_PREFIX}cleaned-vault-prices-1h.parquet`.
- Credentials: `R2_DATA_ACCESS_KEY_ID`, `R2_DATA_SECRET_ACCESS_KEY`,
  `R2_DATA_ENDPOINT_URL`, each falling back to its `R2_VAULT_METADATA_*`
  equivalent, as in the existing private data exporter.
- Manifest metadata: `Content-Type: application/json`,
  `Cache-Control: no-store`. The authenticated frontend route
  `/vaults/datasets/download/vault-scan-manifest` must also bypass its caches;
  R2 metadata alone does not disable a CDN cache rule.
- The existing price download cache policy is unchanged. Consumers must verify
  the response's source ETag against the manifest before using the snapshot.
  ETags are opaque version identifiers, not necessarily MD5 hashes.

## Field semantics

Example receipt (illustrative, not a production observation):

```json
{
  "schema_version": 1,
  "published_at": "2026-09-22T04:10:00Z",
  "price_file": {
    "key": "cleaned-vault-prices-1h.parquet",
    "etag": "opaque-source-version"
  },
  "chains": {
    "9999": {
      "name": "Hypercore",
      "last_successful_price_scan_ended_at": "2026-09-22T04:05:00Z",
      "last_candle_at": "2026-09-22T04:00:00Z"
    }
  }
}
```

The version-1 JSON object contains `schema_version`, `published_at`,
`price_file: {key, etag}`, and `chains`, keyed by decimal chain IDs such as
`"9999"` for HyperCore. All timestamps are ISO-8601 UTC strings ending in `Z`.
`published_at` is receipt creation time after the price upload, not scan time
or confirmation that the subsequent manifest upload has completed. ETags in
JSON have no surrounding HTTP quotes. All declared fields are required; unknown
per-chain timestamps are explicit `null` values. Each chain records:

- `name`: display label only; consumers identify chains by numeric key.
- `last_successful_price_scan_ended_at`: last successful price collection time
  persisted in `vault-price-scan-state.json`. Metadata-only scans do not update
  this state. Missing historical provenance is `null`, not fabricated.
- `last_candle_at`: maximum timestamp in the uploaded cleaned price history for
  that chain, or `null` when absent. This is a bucket label, not the raw sample
  time, end of a complete daily candle, or a per-vault completeness guarantee.

Price-success callbacks currently cover EVM chains and HyperCore. Other native
protocols present in cleaned prices still appear with their candle maxima, but
their scan completion remains `null` until their collectors supply provenance.
Do not treat a native protocol's inclusion as a known completed scan.

HyperCore samples arrive roughly every four hours. Readiness therefore must
not require 24 hourly samples or an observation exactly at midnight. A fresh
chain maximum still does not prove every vault is fresh; the consumer checks
the selected data and fills historical gaps separately.

## Publication and operation

| Entry point | Effect on the receipt |
| --- | --- |
| `scan-vaults-all-chains.py` | Records successful price collection, then publishes through post-processing unless it is skipped. |
| `post-process-prices.py` | Republishes cleaned data and its receipt using existing provenance; does not advance scan completion. |
| `export-data-files.py` | Uploads data only; does not generate a new receipt. |
| Standalone native scanner or cleaner | Does not publish the all-chains receipt. |

Local provenance defaults to `$PIPELINE_DATA_DIR/vault-price-scan-state.json`,
where the data directory defaults to `~/.tradingstrategy/vaults`. This file is
not the general scheduling state `scan-cycle-state.json`. The manifest is
serialised in memory and uploaded directly; there is no required local
`vault-scan-manifest.json` output file. Do not edit provenance to make a stale
chain appear ready.

The scanner saves price-only provenance after successful collection, then runs
cleaning and private export. Only a successful export allows manifest
publication. The publisher aggregates just `chain` and `timestamp` in Arrow,
reads the uploaded object's ETag with R2 HEAD, and uploads the small receipt.
This assumes the scanner's existing single-writer pipeline lock: do not run
concurrent publishers targeting the same bucket/key.

Private export reads the pipeline data directory. A different `cleaned_path`
override is refused for manifest publication, rather than attaching local
freshness claims to a different uploaded file. An unrelated failure in the
complete data export also withholds the receipt, even if prices uploaded
successfully; this intentionally favours waiting over advertising uncertain
publication.

If export or manifest publication fails, the existing remote manifest remains;
it must not be interpreted as a new successful scan. Missing local price files
are logged and skipped; missing remote ETags and invalid configuration fail
publication. Sample-file exports are independent and happen afterwards.

Deploy the producer and authenticated frontend endpoint before enabling the
executor trigger. A `404` is a missing deployment, not evidence of stale data.
Do not run a production upload simply to test this module: it would replace the
readiness receipt used by live trading.

## Deployment readback checklist

1. Check the private bucket, credentials and literal `UPLOAD_PREFIX` agree
   with the serving Worker's object mapping. The prefix gains no automatic
   slash. Never pair a test-prefix receipt with production prices.
2. After an intentional normal scan/export, confirm the
   `publish-vault-scan-manifest` step succeeded and inspect the authenticated
   `/vaults/datasets/download/vault-scan-manifest` response. Keep API keys out
   of logs, command history and shared URLs.
3. Verify the JSON MIME type and `Cache-Control: private, no-store` on the
   served response, and that Worker/CDN rules do not override cache bypass.
   R2's own `no-store` metadata is necessary but insufficient.
4. Check chain `9999` scan and candle timestamps against the intended UTC slot;
   neither should exceed `published_at`. A newer publication time alone does
   not mean newer candles. Unknown provenance means not ready.
5. Download the price snapshot through the authenticated `vault-prices`
   endpoint and compare its strong source ETag with `price_file.etag`, removing
   HTTP quotes only. Missing/weak ETags are deployment errors. A different
   valid ETag can indicate a publication race: obtain a new receipt, never
   silently accept unmatched prices.

The HyperCore executor's policy is to poll every 15 minutes for up to eight
hours after its logical midnight slot. It requires both HyperCore timestamps
to reach that slot, then loads prices strictly before the slot. This is a
consumer scheduling policy, not a producer guarantee that all vaults have
complete daily candles. Producer deployment does not resolve executor
scheduler or interrupted-trade recovery limitations.
