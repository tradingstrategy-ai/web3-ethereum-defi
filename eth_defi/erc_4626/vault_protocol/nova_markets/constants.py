"""Reviewed Nova Markets Conviction Pool identity and public notes.

Primary sources: https://docs.nova.markets/#/contracts and
https://docs.nova.markets/#/conviction-pool. Reviewed 2026-10-28.
"""

from eth_typing import HexAddress

#: Arbitrum One, where Nova deploys its Gains-fork Conviction Pool.
NOVA_MARKETS_CHAIN_ID = 42161
#: Exact pool address published in Nova's contract documentation.
NOVA_POOL_ADDRESS: HexAddress = "0x9ca80193712f372e2ff33a00127292884f6f8598"
#: Official pool application, linked by the current Nova documentation.
NOVA_POOL_APP = "https://app.nova.markets/earn/liquidity-pools"
#: Exact contract identity and administrative permissions.
NOVA_CONTRACTS = "https://docs.nova.markets/#/contracts"
#: Pool strategy, rewards and withdrawal lifecycle.
NOVA_POOL_DOCUMENTATION = "https://docs.nova.markets/#/conviction-pool"
#: Public strategy note, independent of the separate HyperEVM NLP product.
NOVA_POOL_NOTES = """**Strategy:** The [Nova Conviction Pool](https://docs.nova.markets/#/conviction-pool) accepts USDC on Arbitrum and supplies pooled counterparty liquidity to Nova's perpetual markets. Nova documents income from 15% of opening and closing trading fees, borrowing fees, liquidation proceeds and trader losses; profitable traders are paid from the pool. Deposits also accrue Conviction Points, which are not a guaranteed cash return. Fees and trader PnL affect the share price and capital is at risk.

**Operator and withdrawals:** [Nova's contract disclosures](https://docs.nova.markets/#/contracts) identify this exact address as a Gains Network fork controlled by Nova Markets, rather than a Gains Network-operated vault. This is separate from Nova's Lagoon-based NLP market-making vault on HyperEVM. Withdrawals require a request and subsequent claim after one to three three-day epochs, depending on collateralisation. Nova controls administrative and upgrade permissions.

**Depositor fees:** Direct pool deposits and redemptions have no management, performance, entry or exit fee under the implementation reviewed on 28 October 2026. Trading income and PnL affect the share value. Nova's separate [platform-funding flow](https://docs.nova.markets/#/get-started) documents a flat 0.20 USDC charge for funding a trading balance; this is outside the direct vault fee schedule.

**Audit and centralisation:** Nova links audits of upstream Gains contracts; no Nova-specific deployment audit report or attributable public development repository was verified as of 28 October 2026. Its 2-of-3 governance multisig controls timelocked pool upgrades and trading-code changes, including a ten-hour emergency route for the trading contract. The 14-day pool-upgrade delay does not apply to every governance action. Market parameters and some oracle changes have no delay, and [Nova's risk disclosures](https://docs.nova.markets/risks.md) identify operated keepers and oracles.

**App and announcements:** Use [Nova's liquidity pools](https://app.nova.markets/earn/liquidity-pools). The current documentation verifies the product and address; a separately dated launch announcement for this pool was not verified in the 28 October 2026 review."""
