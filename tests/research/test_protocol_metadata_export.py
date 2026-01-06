"""Test vault protocol metadata export to R2."""

import os

import pytest
import requests

from eth_defi.vault.protocol_metadata import process_and_upload_protocol_metadata


@pytest.mark.skipif(os.environ.get("R2_VAULT_METADATA_BUCKET_NAME") is None, reason="R2_VAULT_METADATA_BUCKET_NAME not set")
def test_upload_euler_protocol_metadata():
    """Test uploading Euler protocol metadata and logo to R2.

    - Uploads metadata JSON and light.png logo with test- prefix
    - If R2_VAULT_METADATA_PUBLIC_URL is set, downloads files back to verify upload succeeded
    """

    bucket_name = os.environ.get("R2_VAULT_METADATA_BUCKET_NAME")
    access_key_id = os.environ.get("R2_VAULT_METADATA_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_VAULT_METADATA_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_VAULT_METADATA_ENDPOINT_URL")
    public_url = os.environ.get("R2_VAULT_METADATA_PUBLIC_URL")

    assert public_url is not None, "R2_VAULT_METADATA_PUBLIC_URL must be set to verify upload"

    slug = "euler"
    key_prefix = "test-"

    metadata = process_and_upload_protocol_metadata(
        slug=slug,
        bucket_name=bucket_name,
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        public_url=public_url,
        key_prefix=key_prefix,
    )

    # Verify returned metadata structure
    assert metadata["name"] == "Euler"
    assert metadata["slug"] == "euler"
    public_url_normalised = public_url.rstrip("/")
    assert metadata["logos"]["light"] == f"{public_url_normalised}/{slug}/light.png"
    assert metadata["logos"]["dark"] is None  # Euler only has light.png

    # Download and verify files if R2_VAULT_METADATA_PUBLIC_URL is configured
    if os.environ.get("R2_VAULT_METADATA_PUBLIC_URL"):
        # Download and verify metadata JSON
        metadata_url = f"{public_url}/{key_prefix}{slug}/metadata.json"
        response = requests.get(metadata_url)
        assert response.status_code == 200, f"Failed to fetch {metadata_url}: {response.status_code}"
        downloaded_metadata = response.json()
        assert downloaded_metadata["name"] == "Euler"
        assert downloaded_metadata["slug"] == "euler"

        # Download and verify light logo
        logo_url = f"{public_url}/{key_prefix}{slug}/light.png"
        response = requests.get(logo_url)
        assert response.status_code == 200, f"Failed to fetch {logo_url}: {response.status_code}"
        assert response.headers.get("Content-Type") == "image/png"
        assert len(response.content) > 0
