# Accountable ABI sources

`AccountableAsyncRedeemVault.json` is the Accountable vault interface used by
the adapter (synchronous deposits, operator-finalised async redemptions).

`OpenTermCompoundV1.json` is the verified implementation ABI of the Accountable
strategy contract that the vault delegates deposits to via `strategy()`. It
exposes the per-loan terms (`loan()` with `minDeposit` / `minRedeem`) and the
custom errors reverted during `onDeposit` — notably `InsufficientAmount()`
`0x5945ea56` (raised when a deposit is below the loan's `minDeposit`, distinct
from the vault-level `MIN_AMOUNT_WEI`) and `DepositNotAllowed()` `0x3d90e2a0`.

Fetched on 2026-07-25 from the Etherscan v2 (Monad, chain id 143) verified
implementation `0x647c9584072a4f1c96d5f82a7133af5642f39402`
(`ContractName=OpenTermCompoundV1`) behind the ERC-1967 proxy strategy
`0xD0943c76ee287793559c1dF82E5B2B858Dd01Ef3`, referenced by the Hyperithm Delta
Neutral vault `0x7cd231120a60f500887444a9baf5e1bd753a5e59`. Monad retains only a
moving recent historical-state window, so the Accountable fork test uses
current-head / state-relative assertions rather than a fixed historical block.
