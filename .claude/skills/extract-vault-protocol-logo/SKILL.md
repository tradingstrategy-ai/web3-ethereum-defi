---
name: extract-vault-protocol-logo
description: Extract a logo for vault protocol metadata
---

# Extract vault protocol logo

This skill extracts and saves a logo for vault protocol metadata stored in this repo.

# Inputs

- Vault protocol name

# Step 1: Find protocol homepage link 

Get the homepage link from the protocol-specific YAML file in `eth_defi/data/vaults`.

# Step 2: Extract the logo

Use `extract-project-logo` skill.

- Give the protocol homepage link as an input
- Save the logos to [eth_defi/data/vaults/original_logos](../../../eth_defi/data/vaults/original_logos/) folder in the project tree.
- Use filenames
    - `{protocol slug}.generic.{image file extension}` for generic logo versions
    - `{protocol slug}.light.{image file extension}` for light background theme
    - `{protocol slug}.dark.{image file extension}` for dark background theme

Don't create PNG files or any post-processing of the logos yet, just save the original logos for now.