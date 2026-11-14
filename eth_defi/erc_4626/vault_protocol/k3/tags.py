"""Maintained K3 strategy classifications."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: K3 cbBTC Vault. Added: 2026-11-10.
    #: Decision material: The product documentation identifies this exact
    #: Ethereum deployment and a BTC-denominated spread trade: pledge cbBTC,
    #: borrow USDC, invest in permitted yield venues and convert net income
    #: into cbBTC. This supports carry_trade. Its explicit objective to harvest
    #: risk premia supports risk_premium_harvest. Permitted allocations include
    #: "selected Morpho and Euler markets"; supplying USDC to these markets
    #: is interpreted as lending, supporting lending as a strategy category,
    #: without asserting those positions are currently held. Borrowing once
    #: does not establish recursive looping; eligible Ethena investments do
    #: not establish vault-level perpetual-futures trading. No explicit
    #: delta-neutral mandate was established for this product.
    #: Sources:
    #: - https://docs.k3.capital/vault-infra/introduction/cbbtc-vault.html
    "0x009c02a73706a68e0ae0209235408206e4f53709": {StrategyTag.lending, StrategyTag.carry_trade, StrategyTag.risk_premium_harvest},
    #: Vault: K3 cirBTC Vault. Added: 2026-11-10.
    #: Decision material: The launch thread calls the strategy delta-neutral.
    #: USDC investments aim to offset the USDC debt while earning a spread,
    #: rather than adding directional crypto exposure. Neutrality is assessed
    #: in the deposit asset; the depositor's bitcoin/USD exposure is retained.
    #: It pledges cirBTC to borrow USDC, invests the proceeds and converts the
    #: investment income less borrowing costs into cirBTC (carry trade).
    #: The opening announcement explicitly identifies risk-premium harvesting;
    #: the borrowing and lending posts establish its income mechanism.
    #: The mandate permits lending and selected principal tokens; the launch
    #: allocation explicitly includes Aave and Morpho lending. Borrowing once
    #: to invest does not establish recursive lending looping. Permitted PT
    #: holdings do not establish an active second strategy or perpetual futures.
    #: Sources:
    #: - https://x.com/k3_capital/status/2119852350034895084
    #: - https://x.com/k3_capital/status/2119852361879556335
    #: - https://x.com/k3_capital/status/2119852385367740877
    #: - https://x.com/k3_capital/status/2119852420981506238
    #: - https://api-app.k3.capital/v1/earn/vaults
    "0x56fb80282bb565de67c196924dda0138e30323be": {StrategyTag.delta_neutral, StrategyTag.lending, StrategyTag.carry_trade, StrategyTag.risk_premium_harvest},
}
#: USDe, USDC and AUSD are deliberately unmapped: reviewed identity
#: and wrapper source establish mechanics, not the underlying investment strategy.
#: Announcement searches on 2026-11-10 did not establish matching strategies.
#: See docs/source/vaults/k3/index.rst for address-specific evidence gaps and
#: announcements rejected because they refer to other K3-curated products.
