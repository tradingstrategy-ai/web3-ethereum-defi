# gTrade metadata audit

Reviewed on 2026-10-28. The union of the public vault export and the former
strategy mappings contains 25 deployments: ten verified gTrade pools,
fourteen unconfirmed historical/staging deployments and Nova's independently
operated Conviction Pool. Nova now has its own `nova-markets` protocol and is
excluded from the 24-entry Gains metadata inventory.

## Evidence and public notes

The [official contract directory](https://docs.gains.trade/what-is-gains-network/contract-addresses)
provides exact deployment identities on Polygon, Arbitrum, Base and MegaETH.
The [gToken documentation](https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults)
explains counterparty liquidity, fees, trader PnL and the overcollateralisation
buffer. Each verified vault note links its directory, product app, withdrawal
rules and collateral-specific announcement where one was established.

Historical product announcements are labelled as such, rather than presented
as an announcement of every later deployment. The original gToken post is
dated 2022-12-07; the multi-collateral launch 2024-01-27; gGNS 2025-03-13;
and the BtcUSD announcement 2025-06-12 (announcing the next day's launch).
No separately dated gUSDm deployment announcement was verified.

The audit searched the exact addresses of the unconfirmed entries and compared
them with the official directories. Existing listing names and similar bytecode
do not verify an operator or published strategy. These entries therefore have
explicit information-gap notes, no maintained strategy tags or Gains curator
attribution, and explorer links instead of unrelated deposit apps. They have
not been declared dead, malicious or unofficial by this change. Existing manual
warnings retain priority over descriptive notes.

The curator profile distinguishes the initial community-farmed launch from
later funding. Gains Network's own announcement describes a $250,000 MATIC
trading-incentive grant on 2022-01-05. The proposed $750,000 grant is not
reported as money received. Snapshot's primary GraphQL endpoint confirmed the
June 2026 restructuring vote was closed, with 6,144,005.922949055 votes For,
zero Against and quorum 1. Approval is distinguished from completion of future
personal-capital spending or token purchases.

## Deployment inventory

| Chain | Address | Reported product | Review | Destination |
| --- | --- | --- | --- | --- |
| 137 | `0x91993f2101cc758d0deb7279d41e880f7defe827` | gDAI | Verified gTrade | [App](https://gains.trade/vaults/gDAI) |
| 137 | `0x1544e1ff1a6f6bdbfb901622c12bb352a43464fb` | gETH | Verified gTrade | [App](https://gains.trade/vaults/gETH) |
| 137 | `0x29019fe2e72e8d4d2118e8d0318bef389ffe2c81` | gUSDC | Verified gTrade | [App](https://gains.trade/vaults/gUSDC) |
| 42161 | `0xd85e038593d7a098614721eae955ec2022b9b91b` | gDAI | Verified gTrade | [App](https://gains.trade/vaults/gDAI) |
| 42161 | `0x5977a9682d7af81d347cfc338c61692163a2784c` | gETH | Verified gTrade | [App](https://gains.trade/vaults/gETH) |
| 42161 | `0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0` | gUSDC | Verified gTrade | [App](https://gains.trade/vaults/gUSDC) |
| 42161 | `0x4beef1113f968326905224d2ca272f3032a9a9f4` | gGNS | Verified gTrade | [App](https://gains.trade/vaults/gGNS) |
| 8453 | `0xad20523a7dc37babc1cc74897e4977232b3d02e5` | gUSDC | Verified gTrade | [App](https://gains.trade/vaults/gUSDC) |
| 8453 | `0xb7cb7cb7c3cd96e251c9bf8800b9631134bbadc6` | gBTCUSD | Verified gTrade | [App](https://gains.trade/vaults/gBTCUSD) |
| 4326 | `0x46344456f130e9dcdea7f98cdb0e02fb9f4ab72d` | gUSDm | Verified gTrade | [App](https://gains.trade/vaults/gUSDM) |
| 42161 | `0xfe3e29b3328026003a15bf0846846b03af86b537` | gDAI (historical deployment) | Unconfirmed | [Explorer](https://routescan.io/address/0xfe3e29b3328026003a15bf0846846b03af86b537) |
| 42161 | `0xf40808f50b8d858f3ac6d10c441bb61da4564d53` | Gains Network DAI (historical deployment) | Unconfirmed | [Explorer](https://routescan.io/address/0xf40808f50b8d858f3ac6d10c441bb61da4564d53) |
| 42161 | `0x992eb7040b66b13abea94e2621d4e61d5ce608bd` | mUSDC | Unconfirmed | [Explorer](https://routescan.io/address/0x992eb7040b66b13abea94e2621d4e61d5ce608bd) |
| 42161 | `0x9b9469a68b3d94446c4c466bf8015bdaef971cf4` | Gains Network USDC (staging) | Unconfirmed | [Explorer](https://routescan.io/address/0x9b9469a68b3d94446c4c466bf8015bdaef971cf4) |
| 42161 | `0xb7058370db10f0712eddb297bc3a58c3a2e5c3a7` | Gains Network USDC (staging) | Unconfirmed | [Explorer](https://routescan.io/address/0xb7058370db10f0712eddb297bc3a58c3a2e5c3a7) |
| 42161 | `0xfc7c19509e96415f32d80f47a08cc9ff84e7580d` | Gains Network USDC (staging) | Unconfirmed | [Explorer](https://routescan.io/address/0xfc7c19509e96415f32d80f47a08cc9ff84e7580d) |
| 56 | `0xd796a9e7e30bfc1b1a9380f501430f681c31eb78` | X-Cloud USD | Unconfirmed | [Explorer](https://routescan.io/address/0xd796a9e7e30bfc1b1a9380f501430f681c31eb78) |
| 56 | `0xfb34af2138280e13b0759fd322fe63fccc7508a6` | X-Solaris USD | Unconfirmed | [Explorer](https://routescan.io/address/0xfb34af2138280e13b0759fd322fe63fccc7508a6) |
| 56 | `0x28e1afcd2d91a7f0ea49e81192599fbe1e700169` | hsUSDT | Unconfirmed | [Explorer](https://routescan.io/address/0x28e1afcd2d91a7f0ea49e81192599fbe1e700169) |
| 56 | `0x1e98b6143a4eaf78ab63de8ea8186eec3dbe5edc` | hsUSD1 | Unconfirmed | [Explorer](https://routescan.io/address/0x1e98b6143a4eaf78ab63de8ea8186eec3dbe5edc) |
| 8453 | `0xd78bd3aef2e8aa7820fea8ffb33eddc4f13fa933` | Staked ARA Vault | Unconfirmed | [Explorer](https://routescan.io/address/0xd78bd3aef2e8aa7820fea8ffb33eddc4f13fa933) |
| 999 | `0x31297b564fb8ec52a7d84cc2dee437e0992ef2b8` | xVault | Unconfirmed | [Explorer](https://routescan.io/address/0x31297b564fb8ec52a7d84cc2dee437e0992ef2b8) |
| 80094 | `0x6a6e4ad4a5ca14b940cd6949b1a90f947ae21c19` | hsHONEY | Unconfirmed | [Explorer](https://routescan.io/address/0x6a6e4ad4a5ca14b940cd6949b1a90f947ae21c19) |
| 80094 | `0xdd560bc2c98bc3fa39fcafe256249707f9b83b3c` | hsHONEY | Unconfirmed | [Explorer](https://routescan.io/address/0xdd560bc2c98bc3fa39fcafe256249707f9b83b3c) |

Nova's `42161:0x9ca80193712f372e2ff33a00127292884f6f8598` is documented
separately in [README-Nova-Markets.md](../nova_markets/README-Nova-Markets.md).

## Live destination checks

Manual HTTP checks on 2026-10-28 returned 200 and the expected visible heading
for `/vaults/gDAI`, `/vaults/gUSDC`, `/vaults/gETH`, `/vaults/gGNS`,
`/vaults/gBTCUSD` and `/vaults/gUSDM` on `gains.trade`; `/vaults` displayed
"Vaults". Network support was checked against exact addresses in the official
directories, not inferred from the route's HTTP status. The token pages load
wallet-dependent values in the browser; disconnected zero APY is not used as a
performance observation. No deposit was submitted through a live website.

Nova's app returned 200 with title "Liquidity Pools · Nova". Its official
pool documentation links that destination and establishes Arbitrum as the
Conviction Pool's chain. The docs distinguish it from the separate HyperEVM
NLP product. The app's pool contents require client-side state/sign-in; the
complete contract lifecycle is instead tested on a fixed Anvil fork.

Arbiscan's Nova address page returned 403 to the automated fetcher, and no
Chrome tool was connected. The exact-address relationship was verified through
Nova's primary contract disclosures and real Arbitrum state, not inferred from
that blocked page. Historic Medium announcements were verified through indexed
publisher pages. No separately dated Nova pool launch post was found.

## Refresh existing metadata

**Run after merge and deployment:** repository changes do not update the
production pickle or public JSON automatically. The repair has not run on
production. Inspect the deployed `docker-compose.yml` first. The profile-only
`vault-scanner-oneshot` normally inherits a scan entrypoint, so every maintenance
command below explicitly overrides it. All commands retain the production
`~/.tradingstrategy` mount.

Run the Nova migration first as described in its README. Then use the same
stopped-scanner maintenance window for the remaining 24 reviewed deployments:

```shell
source ~/vault-scanner/vault-rpc.env && (cd ~/vault-scanner/web3-ethereum-defi && docker compose run --rm --entrypoint /bin/bash vault-scanner-oneshot -lc 'DRY_RUN=true python scripts/erc-4626/migrate-gtrade-metadata.py')
source ~/vault-scanner/vault-rpc.env && (cd ~/vault-scanner/web3-ethereum-defi && docker compose run --rm --entrypoint /bin/bash vault-scanner-oneshot -lc 'DRY_RUN=false python scripts/erc-4626/migrate-gtrade-metadata.py')
source ~/vault-scanner/vault-rpc.env && (cd ~/vault-scanner/web3-ethereum-defi && docker compose run --rm --entrypoint /bin/bash vault-scanner-oneshot -lc 'DRY_RUN=true python scripts/erc-4626/migrate-gtrade-metadata.py')
```

Expected: 24 reviewed deployments, with `present + absent = 24`. Absent
historical rows are reported and never created. Existing rows change only
`Link`, `_notes`, `_strategy_tags` and `_curator_slug`. A successful final dry
run reports `changed=0`. An apply run takes the shared writer lock, creates an
unused `.bak-gtrade-metadata` sibling backup and atomically replaces the pickle.
Unchanged runs create no backup or replacement. Conflicting protocol or
detection identities abort before writing.

No network reads or historical scans are performed by this repair. Raw prices,
cleaned prices, leads, scan boundaries, reader state and timestamp caches remain
unchanged. Continue with the metadata/JSON export and restart instructions in
the Nova README, and record the deployed commit, UTC date and outcome when the
production operator performs the run.
