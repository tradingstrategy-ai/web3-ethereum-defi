---
name: extract-test-set
description: Extract raw price dataframe for a test case
---

# Extract test set from raw prices

This is a skill to extract price data from the raw prices for an isolated unit test.

## Inputs

1. Smart contract address and a blockchain as a blockchain explorer link
2. Test case name

## Relevant files

Seek metadata and Parquet information here:

- [vault database](../../../eth_defi/vault/vaultdb.py)
- [data wrangling](../../../eth_defi/research/wrangle_vault_prices.py)

## Ad-hoc script

Create an ad-hoc Python script that reads 

Script inputs

- chain id (numeric) - address tuple
- test case name

Scripts

- Extracts the price series from `DEFAULT_UNCLEANED_PRICE_DATABASE`

Script outputs

- Pytest test module with a single test case
- Related Parquet file containing price data only for this vault

## Write test case

Then the script creates test_xxx file, stores metadata there inline and creates corresponding test_xxx_price.parquet file for the test case to read. 

- Include only a single test function, do not generate excessive tests

## Run the script

- After running the script, run the generated test case