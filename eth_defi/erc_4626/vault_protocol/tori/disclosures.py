"""Reviewed Tori exposure across native and third-party vaults.

This inventory was reconciled against the tracked catalogue on 29 October
2026. It identifies deployments, not name patterns: test tokens and unrelated
products must never inherit these disclosures. Protocol and curator identity
remain with the actual vault infrastructure and operator.

The shared notes explain underlying issuer risk separately from the
wrapper's own fees. Unknown rates are deliberately not replaced by zero.
Canonical product sources are linked in the notes and the accompanying
``docs/protocol-research/tori-vault-risk-and-fees.md`` evidence record.
"""

from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class ToriExposure:
    """Identify a reviewed vault and its product-specific disclosure.

    The chain/address key lives in :data:`TORI_EXPOSURES`. Token symbols are
    additional migration identity checks, rather than discovery rules.
    """

    #: Reviewed catalogue name for operator reports.
    name: str
    #: Actual infrastructure protocol; never relabel a wrapper as Tori.
    protocol: str
    #: Expected receipt-token symbol.
    symbol: str
    #: Expected deposit-asset symbol.
    asset: str
    #: Product-specific risks and fee terms, with primary sources.
    details: str


#: Shared backing and control risks inherited by every reviewed exposure.
TORI_BACKING_RISK = "[Tori's backing](https://docs.tori.finance/solution/backing) includes trading positions held with external custodians. Direct holdings can lose value through trading losses, custodian or counterparty failures, or a trUSD price below one dollar. [Privileged roles](https://docs.tori.finance/security/roles) can report losses, restrict wallets and change contract parameters; upgrades pass through a timelock. Public audits do not eliminate these risks."

#: Native staking charges and withdrawal access.
NATIVE_TERMS = "Native strUSD staking has no separate management, deposit or withdrawal charge. [Rewards](https://docs.tori.finance/products/strusd) are paid after Tori retains an undisclosed share of trading income. As at 29 October 2026, unstaking requires a seven-day cooldown followed by a claim for trUSD; the operator can change the delay. Converting trUSD back into USDC is a separate operation with its own eligibility rules and charges."

#: Upshift product terms; live fee getters take precedence over launch targets.
UPSHIFT_TERMS = "This [RockawayX-curated Upshift vault](https://www.rockawayx.com/insights/rockawayx-tori-ecosystem-vault-investment-thesis-risk-disclosure) adds vault governance, valuation and allocation risks to Tori exposure. Allocations can include liquidity pools, Pendle positions and borrowing, which introduce price, liquidation and exit-liquidity risks. Fees are configured per vault and reflected in the share price; the early pre-deposit fee waiver has ended. At Ethereum block 26299349 on 29 October 2026, the management fee was 0.2% a year, the performance fee 10%, and the standard withdrawal fee zero. The optional instant-redemption charge was 5%, subject to available liquidity. These live settings can change and differ from the launch schedule."

#: Lending risks shared by the reviewed Morpho lending vaults.
MORPHO_TERMS = "This lending vault adds collateral-price, oracle, liquidation and bad-debt risks. Withdrawals depend on unborrowed liquidity and may wait for repayment. Management and performance charges reduce the share price; rates vary by vault. See [Morpho's vault risk explanation](https://docs.morpho.org/learn/resources/risks/) and the vault's fee settings in the Morpho app."

#: Royco junior position absorbs losses before the senior position.
ROYCO_JUNIOR_TERMS = "This [Royco junior tranche](https://docs.royco.org/royco-overview) takes losses before the senior tranche and can lose its entire capital. Its income includes compensation transferred from senior holders for providing that protection. The tranche contracts and shared fee controller have verified source code and can be upgraded through Royco governance. At Ethereum block 26299721 on 29 October 2026, the [accountant](https://etherscan.io/address/0xb8D9b04e43d18Db437A4F717F1bD72dA07C50b0E#readProxyContract) had zero senior, junior and yield-share protocol-fee rates. Fees can change; the yield transferred between tranches is a separate economic cost. A match between these deployed implementations and published audit reports has not been established."

#: Royco senior protection is finite, not a guarantee.
ROYCO_SENIOR_TERMS = "This [Royco senior tranche](https://docs.royco.org/royco-overview) has a junior loss buffer, which can be exhausted; principal is not guaranteed. Part of senior yield pays junior holders for protection and liquidity providers for exit liquidity. The tranche contracts and shared fee controller have verified source code and can be upgraded through Royco governance. At Ethereum block 26299721 on 29 October 2026, the [accountant](https://etherscan.io/address/0xb8D9b04e43d18Db437A4F717F1bD72dA07C50b0E#readProxyContract) had zero senior, junior and yield-share protocol-fee rates. Fees can change. A match between these deployed implementations and published audit reports has not been established."

#: Pendle yield-token positions are finite claims with lock restrictions.
PENDLE_TERMS = (
    "This locked [Pendle yield-token position](https://docs.pendle.finance/pendle-v2/Introduction) represents yield and incentives until "
    "maturity, not a claim to the underlying principal. Rights to future yield expire on 26 November 2026. After maturity, only previously earned "
    "amounts can remain claimable, subject to the lock's rules. The verified deployed lock code has no separate wrapper management, performance, "
    "deposit or withdrawal charge. The underlying [Pendle yield and reward "
    "fees](https://docs.pendle.finance/pendle-v2/ProtocolMechanics/Mechanisms/Fees) were both 5% at Ethereum block 26299746 on 29 October 2026; "
    "swap costs are additional. Neither underlying yield token listed a separate onchain reward token at block 26299800. At block 26299714, both "
    "Tori locks had withdrawals paused. Upgrades were subject to Tori's 24-hour timelock; at block 26299746 their administrator Safe required "
    "three approvals. The administrator can also pause access without waiting for an upgrade. A match between these lock implementations and "
    "published audit reports has not been established."
)

#: Stake DAO LP reward fees differ from native strUSD staking fees.
STAKE_DAO_TERMS = (
    "This liquidity-pool position can lose value through a stablecoin depeg, pool imbalance, price divergence or a fault in Curve or Stake DAO "
    "contracts. Exiting the pool can incur swap costs and slippage. The vaults and their [shared fee "
    "controller](https://etherscan.io/address/0x93b4B9bd266fFA8AF68e39EDFa8cFe2A62011Ce0#readContract) have verified source code. At Ethereum "
    "block 26299708 on 29 October 2026, the controller charged 16.5% on rewards subject to its protocol fee and separately configured 0.025% "
    "harvest compensation. These charges apply to reward accounting, not deposited LP principal or a flat percentage of all returns. [Strategy "
    "entry and exit](https://docs.stakedao.org/strategyfees) have no separate fee; pool trading costs are additional. The Safe controlling these "
    "fees required one approval at block 26299746, and can change fee settings within the contract's 40% combined protocol-and-harvest rate cap. "
    "A match between this deployment and published audit reports has not been established."
)

#: Accountable product terms; do not reuse the Ethereum Upshift terms.
ACCOUNTABLE_TERMS = "The Monad ecosystem vault uses Accountable infrastructure and adds manager, valuation and withdrawal-settlement risks. [Accountable's product terms](https://app.accountable.capital/) govern its own fees and withdrawal arrangements. Management and performance charges reduce the share price. Borrower establishment or prepayment charges are separate from investor deposit or withdrawal fees."

#: Term charges reported profit, separately from its underlying allocations.
TERM_TERMS = "This [Term Finance lending vault](https://developers.term.finance/) adds borrower, collateral, auction and maturity risks. Immediate withdrawal depends on available liquidity. Its verified implementation charges a performance fee through newly minted shares and has no annual management-fee mechanism. At Ethereum block 26299700 on 29 October 2026, the [configured performance fee](https://etherscan.io/address/0x6f576e5192a14f259f7fe7347ecf63b255d7f7d1#readProxyContract) was 10% of reported profit. Charges within underlying lending allocations are separate; the strategy references the RockawayX Tori Morpho vault, which has its own fees."

#: Curvance collateral and optional borrowing risks.
CURVANCE_TERMS = "This [Curvance vault](https://docs.curvance.com/) lends deposited strUSD to borrowers. Lenders can lose money through unpaid debt, failed liquidations or oracle and contract faults. Withdrawals depend on available unborrowed strUSD. Borrowing against a deposit also introduces interest costs and liquidation risk. Underlying strUSD redemption still follows Tori's withdrawal rules. Its deployed contract has verified source code. At Monad block 117664073 on 29 October 2026, [the configured interest charge](https://monadscan.com/address/0xbde156e035e5501378d07399c404b4d1d5c86ae8#readContract) was 10% of borrower interest, collected by minting protocol shares, which reduces lenders' interest income. It is not a 10% charge on the underlying strUSD rewards. No separate annual management, deposit or withdrawal charge was found in this implementation. A match between this deployment and published audit reports has not been established."

#: Exact reviewed inventory, 29 October 2026. Sources and exclusions are recorded
#: in ``docs/protocol-research/tori-vault-risk-and-fees.md``. No name matching.
TORI_EXPOSURES: dict[tuple[int, HexAddress], ToriExposure] = {
    (1, "0x280839980a7ed0d7717f64125fe241012e5f5815"): ToriExposure("Staked trUSD", "Tori Finance", "strUSD", "trUSD", NATIVE_TERMS),
    (1, "0xcd69123b3fbbfc666e1f6a501da27b564c00de54"): ToriExposure("Tori Ecosystem Vault", "Upshift", "etrUSD", "trUSD", UPSHIFT_TERMS),
    (1, "0x3bd9adae6643ddcddd02746b8b60075e56df9478"): ToriExposure("RockawayX Tori Ecosystem", "Morpho", "roxTORI", "USDC", MORPHO_TERMS),
    (1, "0x3b0cecd83bb9ee016587cbe61bea89e429da7a09"): ToriExposure("Junior Staked trUSD", "Royco", "jrStrUSD", "strUSD", ROYCO_JUNIOR_TERMS),
    (1, "0xfd43913ee196743e14c54c037b0a96023796f23c"): ToriExposure("Senior Staked trUSD", "Royco", "srStrUSD", "strUSD", ROYCO_SENIOR_TERMS),
    (1, "0x10b2487173d9f7087c8bdce9e70fd135212a8d7d"): ToriExposure("Locked Pendle YT trUSD", "ERC-4626", "lYT-trUSD", "YT-trUSD-26NOV2026", PENDLE_TERMS + " Cash yield earned while YT is held by the trUSD lock accrues to the lock itself. Holders have no onchain distribution path to receive it; the administrator can transfer non-YT funds out. Any separate manual distribution has not been verified."),
    (1, "0x6569c554cbf71dfcc32aec48b0edd2582f5c4a96"): ToriExposure("Locked Pendle YT strUSD", "ERC-4626", "lYT-strUSD", "YT-strUSD-26NOV2026", PENDLE_TERMS + " The strUSD lock lets holders claim booked yield even while YT withdrawals are paused, unless the administrator has also paused all access. Its administrator can reopen withdrawals without waiting for the timelock."),
    (1, "0x6324cd92203bb218079f2f44eb3893bd1ebea9a4"): ToriExposure("Stake DAO frxUSD/trUSD Vault", "ERC-4626", "sd-frx-trUSD-vault", "frx-trUSD", STAKE_DAO_TERMS),
    (1, "0xcab1d2a2973763bed06e782b65a43fb3cfe27f13"): ToriExposure("Stake DAO trUSD/USDC Vault", "ERC-4626", "sd-trUSD-USDC-vault", "trUSD-USDC", STAKE_DAO_TERMS),
    (1, "0xa9163d9a3d69c3a43074c65a32c2b4d7aa585c34"): ToriExposure("Stake DAO strUSD/trUSD Vault", "ERC-4626", "sd-str-trUSD-vault", "str-trUSD", STAKE_DAO_TERMS),
    (1, "0x6f576e5192a14f259f7fe7347ecf63b255d7f7d1"): ToriExposure("RockawayX Tori USDC", "Term Finance", "tsvRockXToriUSDC", "USDC", TERM_TERMS),
    (1, "0xef6955d886fce26d87753da45192ea50f59dc91c"): ToriExposure("Tenor strUSD/USDC Collateral Vault", "Morpho", "strUSD-USDC-collat", "USDC", MORPHO_TERMS),
    (1, "0x12a3b3fa107c4847bf214c29e6573cb8d2e7a879"): ToriExposure("Tenor strUSD/USDC Lending Vault", "Morpho", "strUSD-USDC-lend", "USDC", MORPHO_TERMS),
    (1, "0xadfcfff962d7e22f15ada7fa089848f7672724c8"): ToriExposure("USDC/strUSD Market Vault", "Morpho", "USDCstrUSDMV", "USDC", MORPHO_TERMS),
    (143, "0x9e98bda72ced55e562bed1dcaf2b123e95eb181e"): ToriExposure("Tori Ecosystem Monad", "Accountable", "metrUSD", "AUSD", ACCOUNTABLE_TERMS),
    (143, "0x790e4f829fe865d432c4ea83b8b15f8871415b6c"): ToriExposure("SharpByte AUSD Tori Ecosystem", "Morpho", "sbAUSD-Tori-Mon", "AUSD", MORPHO_TERMS),
    (143, "0xbde156e035e5501378d07399c404b4d1d5c86ae8"): ToriExposure("Curvance Staked trUSD", "Curvance", "cstrUSD", "strUSD", CURVANCE_TERMS),
}

#: Borrowers' collateral risk is distinct from directly holding Tori tokens.
TORI_LENDING_RISK = "These loans have Tori-related collateral exposure. Losses, custody failures or a depeg can reduce what lenders recover if collateral cannot be sold for enough to repay the debt. Lenders earn borrower interest; they do not receive native strUSD rewards or pay Tori's retained income share as a direct fee. See [Tori's risk disclosures](https://docs.tori.finance/resources/risks)."

#: Income retention affects the strUSD component, not plain trUSD or lenders.
STRUSD_INCOME = " Where this position holds strUSD or its yield, [rewards](https://docs.tori.finance/products/strusd) already reflect Tori's undisclosed share of trading income, separately from the product's own charges."

#: Direct strUSD exposure reviewed from receipt and underlying token identity.
STRUSD_EXPOSURE_ADDRESSES = {
    "0xcd69123b3fbbfc666e1f6a501da27b564c00de54",
    "0x3b0cecd83bb9ee016587cbe61bea89e429da7a09",
    "0xfd43913ee196743e14c54c037b0a96023796f23c",
    "0x6569c554cbf71dfcc32aec48b0edd2582f5c4a96",
    "0xa9163d9a3d69c3a43074c65a32c2b4d7aa585c34",
    "0xbde156e035e5501378d07399c404b4d1d5c86ae8",
}

#: Appended only after incident, Morpho and abnormal-value checks finish.
TORI_EXPOSURE_NOTES = {key: f"{TORI_LENDING_RISK if exposure.protocol in {'Morpho', 'Term Finance'} else TORI_BACKING_RISK} {exposure.details}" + (STRUSD_INCOME if key[1] in STRUSD_EXPOSURE_ADDRESSES else "") for key, exposure in TORI_EXPOSURES.items()}


def append_tori_disclosure(notes: str | None, address: HexAddress | str, chain_id: int) -> str | None:
    """Append a sourced disclosure after the final warning has been selected.

    Keep disclosures out of the manual warning resolver so they cannot hide
    protocol-generated incident explanations. Exact chain/address matching
    avoids attaching another product's terms to a same-address deployment.

    :param notes: Final warning, strategy and scan-cycle text.
    :param address: Vault address, normalised for reviewed inventory lookup.
    :param chain_id: Actual deployment chain.
    :return: Existing text followed by the disclosure, or unchanged text.
    """
    disclosure = TORI_EXPOSURE_NOTES.get((chain_id, address.lower()))
    if not disclosure or (notes and disclosure in notes):
        return notes
    return f"{notes} {disclosure}" if notes else disclosure
