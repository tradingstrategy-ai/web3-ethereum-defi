"""Export Ethereum-only sample vault data files to Cloudflare R2.

Generates free-download sample versions of the cleaned parquet and
top-vaults JSON filtered to Ethereum mainnet (chain_id=1) only.

These sample files are uploaded to the primary (public) R2 bucket only,
NOT to the alternative (private) bucket.
"""

import json
import logging
import os
from pathlib import Path

import pandas as pd

from eth_defi.cloudflare_r2 import create_r2_client, upload_file_to_r2
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)

#: Only include Ethereum mainnet in the sample files
ETHEREUM_CHAIN_ID = 1


def generate_sample_parquet(cleaned_path: Path, output_path: Path) -> int:
    """Filter the cleaned vault prices parquet to Ethereum only.

    Reads the full multi-chain cleaned parquet and writes an
    Ethereum-only subset for free download.

    :param cleaned_path:
        Path to the full ``cleaned-vault-prices-1h.parquet``.

    :param output_path:
        Destination path for the sample parquet.

    :return:
        Number of rows in the sample.

    :raise FileNotFoundError:
        If the source parquet does not exist.

    :raise ValueError:
        If the filtered result has zero Ethereum rows.
    """
    if not cleaned_path.exists():
        raise FileNotFoundError(f"Cleaned parquet not found: {cleaned_path}")

    df = pd.read_parquet(cleaned_path)
    sample_df = df[df["chain"] == ETHEREUM_CHAIN_ID]

    if len(sample_df) == 0:
        raise ValueError(f"No Ethereum (chain_id={ETHEREUM_CHAIN_ID}) rows found in {cleaned_path}")

    sample_df.to_parquet(output_path, compression="zstd")
    logger.info(
        "Generated sample parquet: %d Ethereum rows out of %d total -> %s",
        len(sample_df),
        len(df),
        output_path,
    )
    return len(sample_df)


def generate_sample_json(json_path: Path, output_path: Path) -> int:
    """Filter the top-vaults JSON to Ethereum only.

    Reads the full multi-chain top-vaults JSON and writes an
    Ethereum-only subset for free download.

    :param json_path:
        Path to the full ``top_vaults_by_chain.json``.

    :param output_path:
        Destination path for the sample JSON.

    :return:
        Number of vaults in the sample.

    :raise FileNotFoundError:
        If the source JSON does not exist.

    :raise ValueError:
        If the filtered result has zero Ethereum vaults.
    """
    if not json_path.exists():
        raise FileNotFoundError(f"Top vaults JSON not found: {json_path}")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    filtered_vaults = [v for v in data["vaults"] if v.get("chain_id") == ETHEREUM_CHAIN_ID]

    if len(filtered_vaults) == 0:
        raise ValueError(f"No Ethereum (chain_id={ETHEREUM_CHAIN_ID}) vaults found in {json_path}")

    sample_data = {
        "generated_at": data["generated_at"],
        "vaults": filtered_vaults,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sample_data, f, indent=2, ensure_ascii=False, allow_nan=False)

    logger.info(
        "Generated sample JSON: %d Ethereum vaults out of %d total -> %s",
        len(filtered_vaults),
        len(data["vaults"]),
        output_path,
    )
    return len(filtered_vaults)


def export_sample_files_to_r2(
    skip_parquet_sample: bool = False,
    skip_json_sample: bool = False,
) -> None:
    """Generate and upload Ethereum-only sample data files.

    Produces ``vault-historical.sample.parquet`` and
    ``vault-metadata.sample.json`` filtered to Ethereum
    mainnet (chain_id=1) only, then uploads them to the primary
    (public) R2 bucket.

    :param skip_parquet_sample:
        Skip the parquet sample generation (e.g. when
        the cleaned parquet was not generated this run).

    :param skip_json_sample:
        Skip the JSON sample generation (e.g. when the
        top-vaults JSON was not generated this run).
    """
    if skip_parquet_sample and skip_json_sample:
        logger.info("No sample files eligible — both parquet and JSON samples skipped")
        return

    # R2 credentials — same env vars as export-data-files.py
    bucket_name = os.environ.get("R2_DATA_BUCKET_NAME") or os.environ.get("R2_VAULT_METADATA_BUCKET_NAME")
    access_key_id = os.environ.get("R2_DATA_ACCESS_KEY_ID") or os.environ.get("R2_VAULT_METADATA_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_DATA_SECRET_ACCESS_KEY") or os.environ.get("R2_VAULT_METADATA_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_DATA_ENDPOINT_URL") or os.environ.get("R2_VAULT_METADATA_ENDPOINT_URL")
    public_url = os.environ.get("R2_DATA_PUBLIC_URL") or os.environ.get("R2_VAULT_METADATA_PUBLIC_URL")
    upload_prefix = os.environ.get("UPLOAD_PREFIX", "")

    assert bucket_name, "R2_DATA_BUCKET_NAME (or R2_VAULT_METADATA_BUCKET_NAME) environment variable is required"

    base_path = get_pipeline_data_dir()

    # Generate sample files
    sample_files: list[Path] = []

    if not skip_parquet_sample:
        cleaned_path = base_path / "cleaned-vault-prices-1h.parquet"
        parquet_sample_path = base_path / "vault-historical.sample.parquet"
        row_count = generate_sample_parquet(cleaned_path, parquet_sample_path)
        sample_files.append(parquet_sample_path)
        print(f"  Parquet sample: {row_count:,} Ethereum rows")
    else:
        logger.info("Skipping parquet sample generation")

    if not skip_json_sample:
        json_path = base_path / "top_vaults_by_chain.json"
        json_sample_path = base_path / "vault-metadata.sample.json"
        vault_count = generate_sample_json(json_path, json_sample_path)
        sample_files.append(json_sample_path)
        print(f"  JSON sample: {vault_count:,} Ethereum vaults")
    else:
        logger.info("Skipping JSON sample generation")

    if not sample_files:
        return

    # Upload to primary (public) bucket only — deliberately skip alternative bucket
    print("\nExporting sample files to R2 (public bucket only)")
    print(f"  Bucket: {bucket_name}")
    print(f"  Endpoint: {endpoint_url}")
    print(f"  Key prefix: '{upload_prefix}'" if upload_prefix else "  Key prefix: (none)")

    s3_client = create_r2_client(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )

    for file_path in sample_files:
        s3_key = f"{upload_prefix}{file_path.name}"
        uploaded = upload_file_to_r2(
            s3_client=s3_client,
            file_path=file_path,
            bucket_name=bucket_name,
            object_name=s3_key,
            skip_if_current=True,
        )
        if uploaded:
            logger.info("Uploaded %s to s3://%s/%s", file_path, bucket_name, s3_key)
        else:
            logger.info("Skipped unchanged %s for s3://%s/%s", file_path, bucket_name, s3_key)

        if public_url and uploaded:
            final_url = f"{public_url.rstrip('/')}/{s3_key}"
            print(f"  -> {final_url}")
