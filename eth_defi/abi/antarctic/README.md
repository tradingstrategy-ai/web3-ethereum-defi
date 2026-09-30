# Antarctic manager interfaces

Verified standalone contracts on Arbitrum One (42161), fetched on 2026-09-30. Neither reviewed manager is a proxy; these are their deployed interfaces, including non-indexed settlement amounts and the lowercase `removeLiquidityCooldown()` getter.

- [AMLP manager](https://arbiscan.io/address/0x98a6aEE58699e4f4E13D8d8d0800e4e9cbBcf8dD#code): `AMLPManager.json`.
- [AHLP manager](https://arbiscan.io/address/0xc5F9d4b9f68CAAA869317Baa09a233b22940bd9f#code): `AHLPManager.json`.

Fetched through the Etherscan v2 verified contract ABI API. Creation transaction hashes and Hypersync-verified blocks/timestamps are recorded in the deployment registry. Do not substitute the frontend ABI: its uppercase cooldown getter is stale.
