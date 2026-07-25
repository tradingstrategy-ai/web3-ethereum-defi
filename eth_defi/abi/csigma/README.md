# cSigma ABI sources

`CsigmaV2Pool.json` is the verified implementation ABI of the cSigma /
cSuperior credit pool, including the FIFO withdrawal-queue surface behind the
"Withdrawal pending" revert. It is bound to the cSuperior proxy so the adapter
can inspect its queue gate and decode its custom errors. The pool has no
onchain per-lender request, claim or ticket surface: eth-defi models it as a
synchronous, reserve-limited ERC-4626 redemption and refuses any redemption
that cannot complete in full immediately. The offchain withdrawal manager may
partially service queued lenders, but that partial-fill lifecycle is outside
this adapter and trade-executor's current full-fill contract.

Fetched on 2026-07-25 from the Etherscan v2 verified implementation
`0xa5b7555775a33ca79818702f63b34b14dc9aec4d` (`ContractName=CsigmaV2Pool`)
behind the ERC-1967 proxy cSuperior Quality Private Credit USDC
[0x438982ea288763370946625fd76c2508ee1fb229](https://etherscan.io/address/0x438982ea288763370946625fd76c2508ee1fb229).

`CsigmaV3Pool.json` is a separate, hand-curated synchronous interface retained
for reference; the cSuperior async adapter uses `CsigmaV2Pool.json`.
