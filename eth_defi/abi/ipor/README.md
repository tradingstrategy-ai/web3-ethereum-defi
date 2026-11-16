# IPOR ABI inventory

New external ABIs must be complete verified arrays from Sourcify (preferred)
or Etherscan. Missing provenance for the older files is recorded as unrecorded;
their historical Git import date is not presented as an ABI fetch timestamp.

| Filename | Source | Fetched at (UTC) | Reason to fetch |
| --- | --- | --- | --- |
| `WithdrawManager.json` | [Complete verified ABI](https://etherscan.io/address/0x040f1f8005E6D2B364aD649132dCD2576Ecc6aCb#code); chain 1, `0x040f1f8005E6D2B364aD649132dCD2576Ecc6aCb` | 2026-11-16T19:32:21.264423+00:00 | Read getSharesToRelease() and retain the full verified withdrawal-manager interface; all 50 entries retained, including events and errors. |
| `PlasmaVaultBase.json` | [Original repository import](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/592236725d30e8d7deb9ec0cfcdff7890b7d850c/eth_defi/abi/ipor/PlasmaVaultBase.json); original verification/download endpoint unrecorded | Unrecorded (legacy import) | Existing PlasmaVault state, fuse configuration and vault integration; retained unchanged. |
| `AccessManager.json` | [Original repository import](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/592236725d30e8d7deb9ec0cfcdff7890b7d850c/eth_defi/abi/ipor/AccessManager.json); original verification/download endpoint unrecorded | Unrecorded (legacy import) | Existing IPOR access-management integration; retained unchanged. |

## Withdrawal manager source

The representative Ethereum PlasmaVault stcUSD is
`0xef53663bb775a51181f04d590f88fc38d6bd5751`. At inspection block 26,429,685,
its manager was `0x040f1f8005E6D2B364aD649132dCD2576Ecc6aCb`, resolved from the
legacy WithdrawManager storage slot
`0xb37e8684757599da669b8aea811ee2b3693b2582d2c730fab3f4965fa2ec3e11`.
The reader also supports the corrected slot
`0x465d2ff0062318fe6f4c7e9ac78cfcd70bc86a1d992722875ef83a9770513100`.

Sourcify returned no verification for this manager. Etherscan's v2 API returned
its full verified `WithdrawManager` ABI and reported `Proxy=0`: it is a direct
deployment, so no proxy implementation ABI is substituted. The complete
`getabi` result was checked against the ABI included in `getsourcecode`; the
arrays matched. Authenticated API URLs and keys are not stored here; the public
verified-code page is linked in the inventory.

Released withdrawal claims reserve assets that are unavailable to new instant
withdrawals. Capacity subtracts the assets corresponding to
`getSharesToRelease()` from idle cash when the instant-withdrawal fuse list is
empty. The complete ABI supplies decoding; it does not establish capacity for
other fuse routes.

## Legacy references

These links were already present before the inventory was added and do not
establish the original download endpoint or date for either legacy ABI:

- https://github.com/IPOR-Labs/ipor-fusion/tree/main
- https://basescan.org/address/0x187937aab9b2d57D606D0C3fB98816301fcE0d1f#code
- https://basescan.org/address/0x0d877Dc7C8Fa3aD980DfDb18B48eC9F8768359C4#readProxyContract
- https://www.codeslaw.app/contracts/base/0x12e9b15ad32faeb1a02f5ddd99254309faf5f2f8?tab=abi
- Example vault: https://app.ipor.io/fusion/base/0x45aa96f0b3188d47a1dafdbefce1db6b37f58216
