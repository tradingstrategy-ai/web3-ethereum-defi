"""Test rendering a sparkline for a single file."""
import base64
import os
import tempfile
import webbrowser

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.sparkline import render_sparkline_as_png
from eth_defi.vault.vaultdb import VaultDatabase, read_default_vault_prices


def display_png_in_browser(title: str, png_bytes: bytes):
    """Display PNG bytes in the default web browser.

    :param png_bytes: PNG image as bytes
    """
    # Encode PNG bytes as base64
    base64_png = base64.b64encode(png_bytes).decode('utf-8')

    # Create HTML with embedded image
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sparkline Chart for {title}</title>
    </head>
    <body>
        <img src="data:image/png;base64,{base64_png}" />
    </body>
    </html>
    """

    # Write to temporary file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
        f.write(html_content)
        temp_path = f.name

    # Open in browser
    webbrowser.open(f'file://{temp_path}')


def main():

    vault_db = VaultDatabase.read()
    prices_df = read_default_vault_prices()

    # plHEDGE on Arbitrum
    vault_id = os.environ.get("VAULT_ID", "42161-0x58BfC95a864e18E8F3041D2FCD3418f48393fE6A")

    spec = VaultSpec.from_id(vault_id)
    vault = vault_db.rows.get(spec)

    assert vault is not None, f"Vault not found in metadata: {vault_id}"

    png_bytes = render_sparkline_as_png(
        prices_df=prices_df,
        width=512,
        height=128,
    )

    display_png_in_browser(
        f"Vault {vault['Name']}: {vault_id}",
        png_bytes,
    )


if __name__ == "__main__":
    main()