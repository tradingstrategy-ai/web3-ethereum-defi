# Vault scan readiness manifest

The scanner publishes `vault-scan-manifest.json` after successful private data
export. It lets live strategies poll for new data without downloading the full
cleaned price history. The contract is defined in
`eth_defi/vault/scan_manifest.py` and consumed by
`tradingstrategy/vault_scan_manifest.py` in the trading-strategy package.

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

The version-1 JSON object contains `schema_version`, `published_at`,
`price_file: {key, etag}`, and `chains`, keyed by decimal chain IDs such as
`"9999"` for HyperCore. All timestamps are ISO-8601 UTC strings ending in `Z`.
Each chain records:

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
