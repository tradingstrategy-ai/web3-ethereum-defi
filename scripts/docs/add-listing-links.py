#!/usr/bin/env python3
"""Add Trading Strategy listing links to vault protocol documentation.

This script adds a "Listing" link to all vault protocol documentation files
that points to the protocol's page on Trading Strategy website.
"""

import logging
import pathlib
import yaml
from typing import Dict

logger = logging.getLogger(__name__)


def load_metadata() -> Dict[str, Dict[str, str]]:
    """Load all vault protocol metadata to get Trading Strategy URLs.

    :return:
        Dictionary mapping YAML filenames (without extension) to their data
    """
    metadata_dir = pathlib.Path("eth_defi/data/vaults/metadata")
    metadata_map = {}

    for yaml_file in metadata_dir.glob("*.yaml"):
        with open(yaml_file, "r") as f:
            data = yaml.safe_load(f)

        # Use YAML filename (without extension) as the key
        key = yaml_file.stem
        metadata_map[key] = data

    return metadata_map


def find_trading_strategy_url(protocol_dir: str, metadata_map: Dict[str, Dict[str, str]]) -> str:
    """Find Trading Strategy URL for a protocol directory.

    :param protocol_dir:
        Directory name (e.g., 'gains', 'lagoon_finance')
    :param metadata_map:
        Map of YAML filenames to metadata
    :return:
        Trading Strategy URL or empty string if not found
    """
    # Try direct match with hyphens
    yaml_key = protocol_dir.replace("_", "-")
    if yaml_key in metadata_map:
        url = metadata_map[yaml_key].get("links", {}).get("trading_strategy")
        if url:
            return url

    # Try matching by slug
    for yaml_data in metadata_map.values():
        slug = yaml_data.get("slug", "")
        if slug.replace("-", "_") == protocol_dir:
            url = yaml_data.get("links", {}).get("trading_strategy")
            if url:
                return url

    # Try fuzzy matching: check if any YAML filename starts with the protocol_dir
    # e.g., 'gains' directory matches 'gains-network.yaml'
    protocol_with_hyphen = protocol_dir.replace("_", "-")
    for yaml_key, yaml_data in metadata_map.items():
        if yaml_key.startswith(protocol_with_hyphen + "-"):
            url = yaml_data.get("links", {}).get("trading_strategy")
            if url:
                return url

    return ""


def update_rst_file(rst_path: pathlib.Path, trading_strategy_url: str) -> bool:
    """Add Listing link to a vault protocol .rst documentation file.

    :param rst_path:
        Path to the .rst file to update
    :param trading_strategy_url:
        Trading Strategy URL for this protocol
    :return:
        True if file was updated, False if link already exists
    """
    with open(rst_path, "r") as f:
        content = f.read()

    # Check if Listing link already exists
    if "Listing <" in content or trading_strategy_url in content:
        logger.info(f"Listing link already exists in {rst_path.name}")
        return False

    # Find the Links section
    if "Links\n~~~~~" not in content:
        logger.warning(f"No Links section found in {rst_path}")
        return False

    # Split content to find where to insert the new link
    lines = content.split("\n")

    # Find the line after the Links header
    insert_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "Links":
            # Skip the ~~~~~ line
            if i + 1 < len(lines) and "~" in lines[i + 1]:
                # Insert after the blank line following ~~~~~
                insert_idx = i + 3
                break

    if insert_idx is None:
        logger.warning(f"Could not find insertion point in {rst_path}")
        return False

    # Insert the Listing link as the first link
    listing_line = f"- `Listing <{trading_strategy_url}>`__"
    lines.insert(insert_idx, listing_line)

    # Write back
    with open(rst_path, "w") as f:
        f.write("\n".join(lines))

    logger.info(f"Added Listing link to {rst_path.name}")
    return True


def main():
    """Main entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Load metadata to get Trading Strategy URLs
    metadata_map = load_metadata()
    logger.info(f"Loaded metadata for {len(metadata_map)} protocols")

    # Find all vault protocol documentation files
    docs_dir = pathlib.Path("docs/source/vaults")
    updated_count = 0
    skipped_count = 0

    for rst_file in sorted(docs_dir.glob("*/index.rst")):
        protocol_dir = rst_file.parent.name

        trading_strategy_url = find_trading_strategy_url(protocol_dir, metadata_map)

        if not trading_strategy_url:
            logger.warning(f"No Trading Strategy URL found for {protocol_dir}")
            skipped_count += 1
            continue

        if update_rst_file(rst_file, trading_strategy_url):
            updated_count += 1
        else:
            skipped_count += 1

    logger.info(f"\nSummary: Updated {updated_count} files, skipped {skipped_count} files")


if __name__ == "__main__":
    main()
