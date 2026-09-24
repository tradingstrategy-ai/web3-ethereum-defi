"""Render one production-style vault sparkline and open its PNG in a browser."""

import base64
import os
import tempfile
import webbrowser

from eth_defi.research.sparkline import export_sparkline_as_png, extract_vault_price_data, prepare_sparkline_data, render_sparkline_gradient, upload_to_r2_compressed
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, read_default_vault_prices


def display_png_in_browser(title: str, png_bytes: bytes) -> None:
    """Display PNG bytes in the default web browser.

    :param title:
        Browser page title.
    :param png_bytes:
        PNG image bytes.
    :return:
        None.
    """
    # Encode PNG bytes as base64
    base64_png = base64.b64encode(png_bytes).decode("utf-8")

    # Create HTML with embedded image
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sparkline Chart for {title}</title>
    </head>
    <body bgcolor="#888888">
        <img src="data:image/png;base64,{base64_png}" />
    </body>
    </html>
    """

    # Write to temporary file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", encoding="utf-8", delete=False) as f:
        f.write(html_content)
        temp_path = f.name

    # Open in browser
    webbrowser.open(f"file://{temp_path}")


def main() -> None:
    """Render the configured vault using the publication preparation policy."""
    vault_db = VaultDatabase.read()
    prices_df = read_default_vault_prices()

    # plHEDGE on Arbitrum
    vault_id = os.environ.get("VAULT_ID", "42161-0x58BfC95a864e18E8F3041D2FCD3418f48393fE6A")

    spec = VaultSpec.parse_string(vault_id)
    vault = vault_db.rows.get(spec)

    assert vault is not None, f"Vault not found in metadata: {vault_id}"

    vault_prices_df = extract_vault_price_data(
        spec=spec,
        prices_df=prices_df,
    )

    sparkline_data = prepare_sparkline_data(vault_prices_df[["share_price", "total_assets"]])
    assert sparkline_data is not None, f"Vault needs at least 14 days of finite share-price history: {vault_id}"

    fig = render_sparkline_gradient(
        sparkline_data.prices_df,
        width=300,
        height=300,
        x_axis_range=(sparkline_data.start_at, sparkline_data.end_at),
    )
    png_bytes = export_sparkline_as_png(fig)

    display_png_in_browser(
        f"Vault {vault['Name']}: {vault_id}",
        png_bytes,
    )

    # Special filename for unit testing
    object_name = f"test-{spec.as_string_id()}.png"

    bucket_name = os.environ.get("R2_SPARKLINE_BUCKET_NAME")
    access_key_id = os.environ.get("R2_SPARKLINE_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_SPARKLINE_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_SPARKLINE_ENDPOINT_URL")

    if bucket_name:
        assert access_key_id, "R2_SPARKLINE_ACCESS_KEY_ID is required for upload"
        assert secret_access_key, "R2_SPARKLINE_SECRET_ACCESS_KEY is required for upload"
        assert endpoint_url, "R2_SPARKLINE_ENDPOINT_URL is required for upload"
        print(f"Uploading sparkline to R2 bucket '{bucket_name}' as '{object_name}'")

        upload_to_r2_compressed(
            payload=png_bytes,
            bucket_name=bucket_name,
            object_name=object_name,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            content_type="image/png",
        )
        print(f"Uploaded sparkline to R2 bucket '{bucket_name}' as '{object_name}'")
    else:
        print("R2_SPARKLINE_BUCKET_NAME not set, skipping upload")


if __name__ == "__main__":
    main()
