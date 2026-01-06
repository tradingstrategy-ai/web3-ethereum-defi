#!/usr/bin/env python3
"""Add Trading Strategy listing links to README.md vault protocol entries.

This script adds [Listing](URL) links to the README.md protocol table
for vault protocols.
"""

import logging
import pathlib
import yaml
from typing import Dict

logger = logging.getLogger(__name__)


def load_metadata() -> Dict[str, Dict[str, str]]:
    """Load all vault protocol metadata to get Trading Strategy URLs.

    :return:
        Dictionary mapping protocol names to their metadata
    """
    metadata_dir = pathlib.Path("eth_defi/data/vaults/metadata")
    metadata_map = {}

    for yaml_file in metadata_dir.glob("*.yaml"):
        with open(yaml_file, "r") as f:
            data = yaml.safe_load(f)

        # Use protocol name as the key
        name = data.get("name", "")
        if name:
            metadata_map[name] = data

    return metadata_map


def find_trading_strategy_url(protocol_name: str, metadata_map: Dict[str, Dict[str, str]]) -> str:
    """Find Trading Strategy URL for a protocol.

    :param protocol_name:
        Protocol name from README (e.g., 'Yearn', 'Lagoon', 'gTrade')
    :param metadata_map:
        Map of protocol names to metadata
    :return:
        Trading Strategy URL or empty string if not found
    """
    # Try direct match
    if protocol_name in metadata_map:
        url = metadata_map[protocol_name].get("links", {}).get("trading_strategy")
        if url:
            return url

    # Try case-insensitive match
    protocol_lower = protocol_name.lower()
    for name, data in metadata_map.items():
        if name.lower() == protocol_lower:
            url = data.get("links", {}).get("trading_strategy")
            if url:
                return url

    # Special mappings for protocols with different names in README
    special_mappings = {
        "gTrade": "Gains Network",
        "Ostium": "Gains Network",  # Ostium uses same protocol as Gains
        "Goat Protocol": "Goat",
        "Harvest Finance": "Harvest",
        "AUTO Finance": "Auto",
        "Umami Finance": "Umami",
        "Untangle Finance": "Untangle",
        "D2 Finance": "D2",
        "Term Finance": "Term",
        "Liquidity Royalty": "Liquidity Royalty Tranching",
        "Lagoon": "Lagoon Finance",
        "Morpho": "Morpho Blue",
        "Silo": "Silo Finance",
        "Summer": "Summer.fi",
        "C-Sigma": "cSigma Finance",
        "Sky (MakerDAO)": "Sky",
    }

    if protocol_name in special_mappings:
        mapped_name = special_mappings[protocol_name]
        if mapped_name in metadata_map:
            url = metadata_map[mapped_name].get("links", {}).get("trading_strategy")
            if url:
                return url

    return ""


def update_readme(readme_path: pathlib.Path, metadata_map: Dict[str, Dict[str, str]]) -> int:
    """Add Listing links to README.md for vault protocols.

    :param readme_path:
        Path to README.md
    :param metadata_map:
        Map of protocol names to metadata
    :return:
        Number of protocols updated
    """
    with open(readme_path, "r") as f:
        content = f.read()

    # Find all protocol entries in the table
    # Pattern: | ProtocolName | Actions | [Tutorial/API](url) |
    # We want to add [Listing](url) after the existing link

    updated_count = 0
    lines = content.split("\n")
    new_lines = []

    # Vault protocols that need listing links (ones with vault-related actions)
    vault_keywords = ["vault", "deposit", "redeem", "Savings"]

    for line in lines:
        new_line = line

        # Check if this is a table row with vault-related content
        if line.startswith("|") and any(keyword in line for keyword in vault_keywords):
            # Extract protocol name (first column)
            parts = line.split("|")
            if len(parts) >= 4:
                protocol_name = parts[1].strip()

                # Find Trading Strategy URL
                trading_strategy_url = find_trading_strategy_url(protocol_name, metadata_map)

                if trading_strategy_url:
                    # Check if Listing link already exists
                    if "Listing" not in line and trading_strategy_url not in line:
                        # Add [Listing](url) at the end of the third column (before the last |)
                        # Find the position before the final |
                        last_pipe_idx = line.rfind("|")
                        if last_pipe_idx > 0:
                            before_pipe = line[:last_pipe_idx].rstrip()
                            listing_link = f" [Listing]({trading_strategy_url})"
                            new_line = before_pipe + listing_link + " " + line[last_pipe_idx:]
                            updated_count += 1
                            logger.info(f"Added Listing link for {protocol_name}")

        new_lines.append(new_line)

    # Write back
    with open(readme_path, "w") as f:
        f.write("\n".join(new_lines))

    return updated_count


def main():
    """Main entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Load metadata to get Trading Strategy URLs
    metadata_map = load_metadata()
    logger.info(f"Loaded metadata for {len(metadata_map)} protocols")

    # Update README
    readme_path = pathlib.Path("README.md")
    updated_count = update_readme(readme_path, metadata_map)

    logger.info(f"\nSummary: Updated {updated_count} protocol entries in README.md")


if __name__ == "__main__":
    main()
