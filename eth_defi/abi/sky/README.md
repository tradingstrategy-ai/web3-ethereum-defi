# Sky contract ABIs

Complete verified Ethereum ABIs for the Staked USDS withdrawal-capacity reader.
The [stUSDS proxy](https://sourcify.dev/server/v2/contract/1/0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9?fields=proxyResolution)
resolves to the implementation below. The accounting, rate and auction contracts
are the immutable dependencies of that implementation.

| Filename | Source | Fetched at (UTC) | Reason to fetch |
| --- | --- | --- | --- |
| `StUsds.json` | [Sourcify, Ethereum implementation](https://sourcify.dev/server/v2/contract/1/0x7A61B7adCFD493f7CF0F86dFCECB94b72c227F22?fields=abi,compilation,sources) | 2026-11-27T16:58:04Z | Share supply, current asset conversion, stored share rate and lending market identifier |
| `Vat.json` | [Sourcify, Ethereum Vat](https://sourcify.dev/server/v2/contract/1/0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B?fields=abi,compilation,sources) | 2026-11-27T16:58:04Z | Normalised debt for the SKY-backed lending market |
| `Jug.json` | [Sourcify, Ethereum Jug](https://sourcify.dev/server/v2/contract/1/0x19c0976f590D67707E62397C87829d896Dc0f1F1?fields=abi,compilation,sources) | 2026-11-27T16:58:04Z | Interest accumulator used by withdrawals |
| `LockstakeClipper.json` | [Sourcify, Ethereum LockstakeClipper](https://sourcify.dev/server/v2/contract/1/0x836F56750517b1528B5078Cba4Ac4B94fBE4A399?fields=abi,compilation,sources) | 2026-11-27T16:58:04Z | Borrowed USDS reserved for active auctions |
