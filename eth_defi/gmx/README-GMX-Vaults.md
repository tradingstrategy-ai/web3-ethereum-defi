# GMX V2 liquidity catalogue

This integration lists GMX V2 GM market tokens and GLV liquidity-vault tokens
on Arbitrum One and Avalanche in the shared vault metadata database.

GM and GLV are ERC-20 liquidity-provider shares, not ERC-4626 vaults. GMX
prices each share in USD from current pool state, capped trader PnL, total
supply and oracle prices. The [GMX pricing documentation](https://docs.gmx.io/docs/api/gm-glv-prices/)
requires the Reader or GlvReader inputs for an exact valuation at a particular
block. A deposit/withdrawal event does not supply a complete historical price
series, so this integration deliberately does not add GMX products to the
common historical price scanner.

## Catalogue fields

The synchroniser gets product identity and enabled status from GMX Reader
contracts. It writes descriptive, stable names such as `GMX Market
[WETH-USDC] (Arbitrum, 0x...)` and `GMX Liquidity Vault [WETH-USDC]
(Avalanche, 0x...)`. The chain and full product-token address make each name
unique even when markets share the same backing tokens.

The website display denomination is USDC. This is a catalogue convention for
comparison; it does not mean every pool accepts USDC, is solely backed by USDC,
or has an onchain USDC NAV. GMX's own GM and GLV valuations remain USD values.

## Operations

Use the metadata script to add or refresh current products. It does not modify
historical price data or reader state.

```shell
source ~/vault-scanner/vault-rpc.env
cd ~/vault-scanner/web3-ethereum-defi
docker compose run --rm --entrypoint /bin/bash vault-scanner-oneshot -lc 'PYTHONPATH=. UPSHIFT_SCAN_PRICES=false python scripts/erc-4626/seed-gmx-vaults.py'
```

Use `DRY_RUN=true` first to inspect the catalogue without writing it. GMX
liquidity deposits and withdrawals are asynchronous protocol requests and are
not exposed through the generic vault transaction adapter.
