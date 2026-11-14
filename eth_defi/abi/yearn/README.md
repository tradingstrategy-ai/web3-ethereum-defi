# Yearn ABI provenance

`YearnV3Vault.json` is the canonical Yearn interface. Birch Hill's hook-enabled
variant is stored separately in `BirchHillV3Vault.json`; it exposes
`deposit_hook()` instead of `deposit_limit_module()`.

The Birch Hill ABI was fetched on 2026-11-06 from Etherscan v2 `getsourcecode`
for the [verified implementation](https://basescan.org/address/0xc1f484dee9a586c122d658b8afe3436c2f4290be#code)
on Base (chain 8453). The [RWA USDC vault](https://basescan.org/address/0xa5afd3a598ef000a5edd96ed65929ac7a6feea39#code)
is an EIP-1167 clone pointing to that implementation.

The implementation delegates receiver capacity to
`deposit_hook.available_deposit_limit(receiver)` and calls
`deposit_hook.post_deposit(sender, receiver, assets, shares)` atomically after
minting. Its [MorphoGateDepositHook](https://basescan.org/address/0x486aaf6bb111a837664331db65b811d5d707533e#code)
has immutable receiver and sender gate addresses. The
[WhitelistReceiveSharesGate](https://basescan.org/address/0x985b68740ab9aae2863d002ca208725c76e42fd5#code)
returns explicit account membership; the
[WhitelistSendAssetsGate](https://basescan.org/address/0xb36693bb25c542ea00e77d97c0a93dbd28749a4d#code)
requires membership or an approved intermediary. These are direct deployments,
not upgradeable proxies. Their verified source and ABI were inspected on the
same date. The vault can replace its hook, so classification reads the current
hook rather than permanently assigning permission by vault name or address.

The separate Groma Ecosystem vault at
`0xb038cf3e233b78225f6d350ac8319bec8457db4a` uses Morpho V2, with both deposit
gates disabled at Base block 54,012,303. Frontend identity checks do not change
its onchain deposit policy.
