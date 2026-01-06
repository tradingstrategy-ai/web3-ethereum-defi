---
name: extract-vault-protocol-logo
description: Extract a logo for vault protocol metadata
---

# Extract vault protocol logo

This skill extracts and saves a logo for vault protocol metadata stored in this repo.

# Inputs

- Vault protocol name

# Step 1: Find protocol homepage link 

Get the homepage link from the protocol-specific YAML file in `eth_defi/data/vaults/metadata`.

# Step 2: Extract the logo

Use `extract-project-logo` skill.

- Give the protocol homepage link as an input
- Save the logos to the folder `eth_defi/data/vaults/original_logos/{protocol slug}`
- Use filenames like
    - `{protocol slug}.generic.{image file extension}` for generic logo versions
    - `{protocol slug}.light.{image file extension}` for light background theme
    - `{protocol slug}.dark.{image file extension}` for dark background theme

Don't convert image file formats or do any image post-processing of the logos yet, just save as many as possible original logos for now.