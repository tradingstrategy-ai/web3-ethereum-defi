# Euler vault metadata

Euler vault names, products and deprecation status are maintained offchain in
the [`euler-xyz/euler-labels`](https://github.com/euler-xyz/euler-labels)
repository. Do not infer product deprecation from ERC-4626 contract fields
alone, because the contract `name()` is not the canonical product label.

## Stream xUSD blacklist audit

Use Euler's label metadata as the primary source when checking which Euler
vaults were affected by Stream Finance xUSD illiquidity.

The method used for the Stream incident check was:

1. Enumerate all numeric chain directories in
   `https://github.com/euler-xyz/euler-labels`.
2. Fetch each chain's `products.json` and `earn-vaults.json` when present.
   For the 2026-07 audit this covered chain IDs `1`, `56`, `130`, `143`,
   `146`, `239`, `999`, `1923`, `8453`, `9745`, `42161`, `43114`, `59144`,
   `60808` and `80094`.
3. In `products.json`, select products where `deprecationReason` mentions
   Stream or insolvent Stream positions, then collect their
   `deprecatedVaults`.
4. In `earn-vaults.json`, select entries where `deprecated` is `true` and
   `deprecationReason` mentions Stream or insolvent Stream positions.
5. Join the collected `(chain_id, address)` pairs against our exported vault
   universe, for example
   `~/.tradingstrategy/vaults/downloads/vault-universe.json`.
6. Only blacklist addresses that are present in our exported vault universe and
   correspond to the affected Euler vaults. Use
   `VaultFlag.illiquid` with the Stream xUSD warning in
   `eth_defi.vault.flag.VAULT_FLAGS_AND_NOTES`.

This is an offchain metadata-based determination. It is not a direct onchain
collateral or balance analysis. Euler labels are still the canonical user-facing
source for deprecation status because they power the Euler app's product and
vault warnings. The Euler app `/api/internal/labels` endpoint returned `403`
and explicitly said `/api/internal/*` is not a public contract, so use the
public `euler-labels` repository for repeatable audits.

For the 2026-07 check, Euler labels had 29 Stream-deprecated addresses. 17 of
those addresses were present in our exported vault universe and needed manual
blacklist coverage:

| Chain | Curator | Product | Vault | Address |
| --- | --- | --- | --- | --- |
| Ethereum | Telos Consilium | TelosC Stream | USDC | `0x01864ae3c7d5f507cc4c24ca67b4cabbdda37ecd` |
| Ethereum | Telos Consilium | EulerEarn | TelosC Surge USDC | `0x49c5733d71511a78a3e12925ea832f49031c97e9` |
| Ethereum | Telos Consilium | TelosC Stream | xUSD | `0xf1ba8c5ca5ab011d06f31e64dad313d204acb9eb` |
| Plasma | unknown | Frontier Elixir | Frontier Elixir USDT0 | `0x3799251bd81925cfccf2992f10af27a4e62bf3f7` |
| Plasma | Hyperithm | EulerEarn | Hyperithm Euler USDT | `0x66be42a0bda425a8c3b3c2cf4f4cb9edfcaed21d` |
| Plasma | RE7 Labs | Re7 Labs xUSD | xUSD | `0x8adb906421f65c27155f44f1829ca1e5b024c3f6` |
| Plasma | RE7 Labs | Re7 Labs xUSD | USDT0 | `0xf675fbe777e992f5d5d84adf41161dc0f20104a6` |
| Plasma | RE7 Labs | EulerEarn | Re7 USDT0 Core | `0xa5eed1615cd883dd6883ca3a385f525e3beb4e79` |
| Plasma | Telos Consilium | TelosC Stream | xUSD | `0x138c289bb8b855cf271305c8bcf91dc31ba30194` |
| Plasma | Telos Consilium | TelosC Trevee | plUSD | `0x1ad2d433b5e95077eb2855eab854b72ea9ee9d6c` |
| Plasma | Telos Consilium | TelosC Stream | plUSD | `0x27934d4879fc28a74703726edae15f757e45a48a` |
| Plasma | Telos Consilium | TelosC Stream | USDT0 | `0x57c582346b7d49a46af3745a8278917d1c1311b8` |
| Plasma | Telos Consilium | EulerEarn | TelosC Surge | `0xa9c251f8304b1b3fc2b9e8fcae78d94eff82ac66` |
| Plasma | Telos Consilium | TelosC Trevee | USDT0 | `0xb5526491742fee67e9e0d0d8c619a95d422fd398` |
| Plasma | Telos Consilium | TelosC Stream | msUSD | `0xf90cf999de728a582e154f926876b70e93a747b7` |
| Avalanche | RE7 Labs | EulerEarn | Re7 AUSD | `0x70c329d6f06b33fa6b75e335b35168b1de84217b` |
| Avalanche | RE7 Labs | EulerEarn | Re7 USDC | `0xeaf77df5d03306bca4ee8b58b6821e6aca76309d` |

Do not blacklist unrelated TelosC products unless their Euler label metadata
also marks them as Stream-affected. In the same check, TelosC Almanak, Haven,
Reservoir and f(x) were not classified as Stream-affected by Euler labels.
