# Euler ABI inventory

External ABIs added here must be complete verified files from Sourcify
(preferred) or Etherscan. Do not replace them with hand-written view subsets.
Retrieval timestamps below describe downloading the ABI, not source verification
or the original contract deployment. Missing legacy provenance is explicit.

| Filename | Source | Fetched at (UTC) | Reason to fetch |
| --- | --- | --- | --- |
| `EVault.json` | [Complete verified ABI](https://sourcify.dev/server/v2/contract/8453/0x30a9A9654804F1e5b3291a86E83EdeD7cF281618?fields=abi,proxyResolution,compilation); chain 8453, `0x30a9A9654804F1e5b3291a86E83EdeD7cF281618` | 2026-11-16T19:30:52.704626+00:00 | Read EVK cash and the full vault interface for withdrawal-capacity assessment; includes all 170 verified ABI entries. |
| `EulerEarn.json` | [Original repository import](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/43651845225d6491685722d2852510a05a2e76f7/eth_defi/abi/euler/EulerEarn.json); original verification/download endpoint unrecorded | Unrecorded (legacy import) | Existing EulerEarn vault and fee integration; retained unchanged, separate from EVK cash measurement. |

## EVault deployment and implementation

The representative Base vault is
`0x4c1aeda9b43efcf1da1d1755b18802aabe90f61e` ([verified proxy metadata](https://sourcify.dev/server/v2/contract/8453/0x4c1aeda9b43efcf1da1d1755b18802aabe90f61e?fields=abi,proxyResolution,compilation)).
Its verified contract is `BeaconProxy`; Sourcify resolves its implementation to
`0x30a9A9654804F1e5b3291a86E83EdeD7cF281618`, compiled as
`lib/euler-vault-kit/src/EVault/EVault.sol:EVault`.

`EVault.json` is the complete implementation ABI returned by Sourcify, bound to
the deployed vault address at runtime. The proxy's own three-entry plumbing ABI
cannot provide `cash()`. No functions, errors, events or tuple components were
selected, renamed or assembled locally. Source verification does not imply that
future proxy upgrades retain the same interface; the capacity integration also
checks the original Base fork block.
