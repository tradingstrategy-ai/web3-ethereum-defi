"""Test rendering a sparkline for a single vault.

- Open the result in a browser
"""

import base64
import os
import tempfile
import webbrowser

from eth_defi.vault.base import VaultSpec
from eth_defi.research.sparkline import extract_vault_price_data, render_sparkline_simple, export_sparkline_as_svg, render_sparkline_gradient, export_sparkline_as_png
from eth_defi.vault.vaultdb import VaultDatabase, read_default_vault_prices


def display_png_in_browser(title: str, png_bytes: bytes):
    """Display PNG bytes in the default web browser.

    :param png_bytes: PNG image as bytes
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
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
        f.write(html_content)
        temp_path = f.name

    # Open in browser
    webbrowser.open(f"file://{temp_path}")


def display_svg_in_browser(title: str, svg_bytes: bytes):
    """Display SVG bytes in the default web browser.

    :param title: The title for the HTML page.
    :param svg_bytes: SVG image as bytes.
    """
    # Encode SVG bytes as base64
    base64_svg = base64.b64encode(svg_bytes).decode("utf-8")

    # Create HTML with embedded image
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sparkline Chart for {title}</title>
    </head>
    <body bgcolor="#000000">
        <img src="data:image/svg+xml;base64,{base64_svg}" />
    </body>
    </html>
    """

    # Write to temporary file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
        f.write(html_content)
        temp_path = f.name

    # Open in browser
    webbrowser.open(f"file://{temp_path}")


def main():
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

    # fig = render_sparkline(
    #    vault_prices_df,
    #    width=100,
    #    height=25,
    # )

    # Twitter Summary CArd
    # https://developer.x.com/en/docs/x-for-websites/cards/overview/summary-card-with-large-image
    fig = render_sparkline_gradient(
        vault_prices_df,
    )

    svg_bytes = export_sparkline_as_svg(
        fig,
    )

    png_bytes = export_sparkline_as_png(
        fig,
    )

    display_png_in_browser(
        f"Vault {vault['Name']}: {vault_id}",
        png_bytes,
    )

    # Special filename for unit testing
    object_name = f"test-{spec.as_string_id()}.png"

    bucket_name = os.environ.get("R2_SPARKLINE_BUCKET_NAME")
    account_id = os.environ.get("R2_SPARKLINE_ACCOUNT_ID")
    access_key_id = os.environ.get("R2_SPARKLINE_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_SPARKLINE_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_SPARKLINE_ENDPOINT_URL")

    if bucket_name:
        from eth_defi.research.sparkline import upload_to_r2_compressed

        print(f"Uploading sparkline to R2 bucket '{bucket_name}' as '{object_name}', access key is {access_key_id}, account is {account_id}")

        upload_to_r2_compressed(
            payload=svg_bytes,
            bucket_name=bucket_name,
            object_name=object_name,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            content_type="image/svg+xml",
        )
        print(f"Uploaded sparkline to R2 bucket '{bucket_name}' as '{object_name}'")
    else:
        print(f"R2_SPARKLINE_BUCKET_NAME not set, skipping upload")


if __name__ == "__main__":
    main()
