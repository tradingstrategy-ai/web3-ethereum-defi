---
name: check-vault-onchain
description: Check our feature flagging against an onchain vault
---

# Check vault onchain

This script will run against a deployed smart contract and see if it detects out as one of our supported vaults. This skill will run a Python script and get you the output back.

## Required inputs

1. Smart ctonract chain and address: User gives this either a link (check the page and blockchain explorer) or directly

## Script source

Get the script source code from `scripts/erc-4626/check-vault-onchain.py

## Running the script

Clone the script and replace `spec` to be the correct chain id and vault address string.

Then run the generated script using `python` console command.

## Display output

Send all the script output back to the user, format as a table.
