# Harvest contract ABIs

| Filename | Source | Fetched at (UTC) | Reason to fetch |
| --- | --- | --- | --- |
| `VaultV2.json` | Legacy Harvest vault ABI | Unrecorded | Generic Harvest vault reads |
| `SiloVaultStrategy.json` | Legacy strategy ABI | Unrecorded | Silo strategy reads |
| `VaultV2InKind.json` | [Sourcify, Base implementation](https://repo.sourcify.dev/8453/0xc74Ba1db3140380757d1Ea21daC8C8C790c184e8), proxy `0xC777031D50F632083Be7080e51E390709062263E` | 2026-11-26T14:21Z | Verify the cash withdrawal configuration and simulate the public redemption path |
| `FortyAcresLendStrategy.json` | [Sourcify, Base implementation](https://repo.sourcify.dev/8453/0xF9dE4E53e87cc73e4F3be9f3CdcA23CFb1ed1770), proxy `0x1d59868D7767d703929393bDaB313302840f533c` | 2026-11-26T14:21Z | Resolve the strategy's lending pool, underlying asset and parent vault |
