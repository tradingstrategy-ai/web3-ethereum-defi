# Shift contract ABIs

| Filename | Source | Fetched at (UTC) | Reason to fetch |
| --- | --- | --- | --- |
| ShiftVault.json | [Sourcify, Arbitrum 0x956bdd9C18B786b082fd50C52722d254f0CB6964](https://sourcify.dev/server/v2/contract/42161/0x956bdd9C18B786b082fd50C52722d254f0CB6964?fields=abi) | 2026-11-26T10:47:50Z | Verified common vault reads, including immutable feed binding and freshness |
| ShiftTvlFeed.json | [Sourcify, Arbitrum 0x4062c539D09A76D79e366F8C2a2e2F987EAD1FE0](https://sourcify.dev/server/v2/contract/42161/0x4062c539D09A76D79e366F8C2a2e2F987EAD1FE0?fields=abi), [identical Base extUSD feed ABI](https://sourcify.dev/server/v2/contract/8453/0xdBE599c184CC13D82F956fA5adAa1b18c0Ff67D3?fields=abi) | 2026-11-26T10:47:50Z | Complete feed interface for raw valuation and supply snapshots |
| ShiftTvlFeedLegacy.json | [Sourcify, Base 0x6196810Be8e6a1D0fB0Ec10c789966c88340F19b](https://sourcify.dev/server/v2/contract/8453/0x6196810Be8e6a1D0fB0Ec10c789966c88340F19b?fields=abi) | 2026-11-26T10:47:50Z | Complete legacy feed interface for ltPARA |

The three verified vault sources use immutable feed bindings and the same
supply-snapshot price calculation. [Base ltPARA source](https://sourcify.dev/server/v2/contract/8453/0xaf69Bf9ea9E0166498c0502aF5B5945980Ed1E0E?fields=sources) · [Base extUSD source](https://sourcify.dev/server/v2/contract/8453/0x4cE3ec1b7B4FFb33A0B70c64a0560A3F341AA2E1?fields=sources)
