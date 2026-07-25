# cSigma ABI sources

`CsigmaV2Pool.json` is the verified implementation ABI of the cSigma /
cSuperior credit pool, including the FIFO withdrawal-queue surface behind the
"Withdrawal pending" revert. It is used to model cSuperior redemption as an
asynchronous queued request rather than a synchronous ERC-4626 `redeem`.

Fetched on 2026-07-25 from the Etherscan v2 verified implementation
`0xa5b7555775a33ca79818702f63b34b14dc9aec4d` (`ContractName=CsigmaV2Pool`)
behind the ERC-1967 proxy cSuperior Quality Private Credit USDC
[0x438982ea288763370946625fd76c2508ee1fb229](https://etherscan.io/address/0x438982ea288763370946625fd76c2508ee1fb229).

`CsigmaV3Pool.json` is a separate, hand-curated synchronous interface retained
for reference; the cSuperior async adapter uses `CsigmaV2Pool.json`.
