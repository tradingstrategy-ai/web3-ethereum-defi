"""Vault status flags and notes.

Manual flags and notes for individual vaults and protocols. A bad flag, see
:py:data:`BAD_FLAGS`, hides a vault on the website and in the data exports.

Instructions for adding entries
-------------------------------

Every entry added to :py:data:`VAULT_FLAGS_AND_NOTES` must be documented with an
extensive line comment block directly above it. This applies to humans and to
AI agents alike, including the ``check-top-list-vaults`` and ``add-vault-note``
skills. A bare address, or a comment with only the vault name, is not enough.
The comment block states:

- the vault name, protocol, chain and, when known, curator;
- when the entry was added and what found the problem, e.g. the vault report
  investability check, an incident report or a user report;
- what is wrong, with the figures observed and when, e.g. utilisation,
  redeemable liquidity or the collateral's market;
- why this :class:`VaultFlag` was chosen;
- canonical source URLs for the data: the vault's page on
  ``https://tradingstrategy.ai/vaults/``, the block explorer page of the vault
  and of any token or oracle involved, the protocol's app or forum post, and
  the incident reports or announcements relied on. Prefer human-readable
  pages over API endpoints.

Use :py:attr:`VaultFlag.review_needed` for a vault we are unsure about: it
is not blacklisted, and its note tells readers the vault is under review. Its
comment block must also record each check run's decision, the date and the
model, what conflicts or is missing, and what a reviewer should check to
decide. When a later run looks at the vault again, add its finding to the same
comment block rather than a new entry.

The note message itself goes in a module-level constant with a ``#:`` comment,
so it can be shared by vaults with the same problem. See the King RSS USDC
Vault entry for an example. After changing flags, rerun the vault metadata
scan, see :class:`VaultFlag`.
"""

import enum

from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.axis.constants import AXIS_NOTES_BY_CHAIN
from eth_defi.tokenised_fund.ondo.constants import ONDO_PRODUCT_NOTES, ONDO_TOKENISED_FUND_ADDRESSES
from eth_defi.tokenised_fund.securitize.description import SECURITIZE_PRODUCT_NOTES, SECURITIZE_TOKENISED_FUND_ADDRESSES
from eth_defi.tokenised_fund.spiko.constants import EUTBL_TOKEN_ADDRESS, USTBL_TOKEN_ADDRESS
from eth_defi.vault.handwritten_metadata import MORINI_CAPITAL_VAULT_METADATA, format_handwritten_vault_note


class VaultFlag(str, enum.Enum):
    """Flags indicating the status of a vault.

    Manual vault flags are resolved through :py:meth:`eth_defi.vault.base.VaultBase.get_flags`
    during vault metadata scanning and stored in the vault metadata database as
    ``_flags``. Published vault JSON then serialises those stored values through
    ``scripts/erc-4626/vault-analysis-json.py`` /
    :py:mod:`eth_defi.vault.top_vaults_json`.

    After changing :class:`VaultFlag`, :py:data:`VAULT_DESCRIPTIVE_FLAGS`, or
    :py:data:`VAULT_FLAGS_AND_NOTES`, rerun the vault metadata scan and its
    top-vault JSON post-processing. Running only the JSON export against an old
    ``vault-metadata-db.pickle`` will keep the previously stored flags.
    """

    #: We can deposit now
    deposit = "deposit"

    #: We can redeem now
    redeem = "redeem"

    #: Vault is paused
    paused = "paused"

    #: Vault is in trading mode - we can expect vault to generate yield
    trading = "trading"

    #: Vault is not in trading mode - any deposit are unlikely to generate yield right now
    idle = "idle"

    #: Vault is illiquid
    #:
    #: E.g. Stream xUSD episode
    #:
    illiquid = "illiquid"

    #: Vault is broken
    #:
    #: Onchain metrics coming out of it do not make sense
    #:
    broken = "broken"

    #: The contract will steal your money
    malicious = "malicious"

    #: Vault has unresolved controversy based on community reports
    controversial = "controversial"

    #: Abnormal TVL
    abnormal_tvl = "abnormal_tvl"

    # Properitary trading
    proprietary_trading = "proprietary_trading"

    #: The vault supplies automated-market-maker inventory and is exposed to
    #: trader profit and loss.
    market_making = "market_making"

    #: The vault represents a pro-rata share of protocol liquidity.
    liquidity_provision = "liquidity_provision"

    #: This vault represents an underlying wrapped asset like a share
    wrapped_asset = "wrapped_asset"

    #: This vault represents shares in a legally structured tokenised fund.
    #:
    #: Fund assets, NAV calculation and investor eligibility can be managed
    #: off-chain by the fund issuer. Apply this only where the product has a
    #: legal fund structure; epoch-settled DeFi vault shares alone do not make
    #: a product a tokenised fund.
    tokenised_fund = "tokenised_fund"

    #: A detectable vault cannot be verified as an official product from a
    #: protocol's authoritative metadata. Use this flag only where the source
    #: explicitly does not endorse the vault; absence from one frontend alone
    #: is insufficient because a protocol can endorse partner products outside
    #: its primary frontend. The deployment may be unofficial or a spoof attempt.
    #: For example, the Yearn V3-compatible Coinflakes Vault V2.0 at
    #: ``0x254bd33e2f62713f893f0842c99e68f855cda315`` is absent from Yearn's
    #: frontend and is therefore ``unofficial``.
    unofficial = "unofficial"

    #: Vault has abnormal price behaviour on low TVL
    abnormal_price_on_low_tvl = "abnormal_price_on_low_tvl"

    #: This vault is a subvault used by other vaults
    subvault = "subvault"

    #: Share price is unrealistically high (> $1M), likely a broken contract
    abnormal_share_price = "abnormal_share_price"

    #: Vault reported an incorrect share price because of misleading accounting
    misleading_valuation = "misleading_valuation"

    #: Annualised volatility is unrealistically high
    abnormal_volatility = "abnormal_volatility"

    #: Tnis vault is a perp dex trading vault on Hyperliquid, Orderly, Lighter, etc.
    perp_dex_trading_vault = "perp_dex_trading_vault"

    #: The vault does not do daily NAV. It's share price has confusing equity curve, making users misjudge the vault.
    irregular_reporting = "irregular_reporting"

    #: This is for vaults with especially long redemption periods.
    long_duration = "long_duration"
    #: Morpho Blue API reports one or more RED-level warnings on this vault or its underlying markets.
    #:
    #: RED warnings include unrealised bad debt (``bad_debt_unrealized``), oracle price deviation,
    #: short timelock, and deposit-disabled. Check ``other_data["morpho_vault_flags"]`` and
    #: ``other_data["morpho_market_flags"]`` in the metrics Series for the specific flag types.
    morpho_issues = "morpho_issues"

    #: Morpho API does not return this vault by address.
    not_in_morpho_api = "not_in_morpho_api"

    #: Vault denomination stablecoin is marked as depegged.
    depegged_denomination_token = "depegged_denomination_token"

    #: A human should review this vault; it is **not** blacklisted.
    #:
    #: Use this when there is a credible concern but not enough evidence to
    #: blacklist, e.g. the vault report investability check left the vault
    #: ``uncertain``, or its AI-assisted research is not deterministic and
    #: separate runs reach different decisions. The vault stays visible on the
    #: website and in the exports; its note tells readers it is under review.
    #: Deliberately not in :py:data:`BAD_FLAGS`. Replace it with a bad flag, or
    #: remove the entry, once a human has decided.
    review_needed = "review_needed"


#: Don't touch vaults with these flags
BAD_FLAGS = {
    VaultFlag.paused,
    VaultFlag.illiquid,
    VaultFlag.broken,
    VaultFlag.malicious,
    VaultFlag.controversial,
    VaultFlag.abnormal_tvl,
    VaultFlag.unofficial,
    VaultFlag.abnormal_price_on_low_tvl,
    VaultFlag.abnormal_share_price,
    VaultFlag.misleading_valuation,
    VaultFlag.abnormal_volatility,
    VaultFlag.subvault,
    VaultFlag.irregular_reporting,
    VaultFlag.morpho_issues,
    VaultFlag.not_in_morpho_api,
    VaultFlag.depegged_denomination_token,
}


_empty_set = set()


JLTXX_FACT_SHEET_URL = "https://am.jpmorgan.com/content/dam/jpm-am-aem/americas/us/en/literature/fact-sheet/money-market/fs-ocltmm-t.pdf"

ODA_FACT_JLTXX_NOTE = f"""JPMorgan OnChain Liquidity-Token Money Market Fund (JLTXX).

- **Curator:** J.P. Morgan Asset Management / Kinexys.
- **Vault strategy:** Registered government money market fund investing in U.S. Treasury securities and overnight repurchase agreements collateralised by U.S. Treasury securities and/or cash.
- **Fee structure:** Token Class prospectus advertises 0.71% gross total annual fund operating expenses and 0.16% net total annual fund operating expenses after waivers through 2028-06-30. These are off-chain fund expenses, not ODA-FACT token contract methods.
- Equity curve and profit information for this vault are missing because they are not publicly available and proprietary to J.P. Morgan.
- **Fact sheet:** [JLTXX fact sheet]({JLTXX_FACT_SHEET_URL}).
"""

WISDOMTREE_WTGXX_NOTE = """WisdomTree Treasury Money Market Digital Fund (WTGXX).

- **Curator:** WisdomTree.
- **Token structure:** Permissioned, revocable compliance ERC-20 shares; not an ERC-4626 vault.
- **NAV source:** Historical NAV data is not publicly available. WisdomTree provides it through the permissioned DataSpan API, which requires an API key issued after approval.
- **Investor access:** Wallets must be approved by WisdomTree Connect; public deposit and redemption managers are intentionally unsupported.
- **Fund page:** [WisdomTree WTGXX](https://www.wisdomtreeconnect.com/digital-funds/money-market/wtgxx).
"""

SUPERSTATE_USTB_NOTE = """Invesco Short Duration US Government Securities Fund (USTB).

- **Curator:** Superstate.
- **Vault strategy:** Tokenised shares in a short-duration U.S. government-securities fund.
- **NAV and liquidity:** The tracked NAV/share is Superstate's issuer-published continuous price. It is not an exchange price and does not by itself guarantee redeemable USDC liquidity.
- **Eligibility:** USTB is a permissioned instrument. Transfers, subscriptions and redemptions require Superstate approval and can be paused or subject to issuer settlement conditions.
- **Fund documentation:** [Superstate USTB](https://docs.superstate.com/superstate-funds/ustb).
"""

ODA_FACT_MONY_NOTE = """My OnChain Net Yield Fund (MONY).

- **Curator:** J.P. Morgan Asset Management / Kinexys.
- **Fund access:** The Ethereum token is a permissioned FACT Diamond. Its account activation, stop-code, lock and role controls mean an ERC-20 transfer or burn interface does not establish general investor eligibility or public redemption access.
- **Valuation:** The deployed token exposes no on-chain NAV or share-price function. Supply is tracked as an on-chain diagnostic only; do not derive fund value from it.
- **Operations:** J.P. Morgan's launch announcement says the fund is powered by Kinexys Digital Assets and distributed through Morgan Money.
"""

SPIKO_USTBL_NOTE = """Spiko US T-Bills Money Market Fund (USTBL).

- **Curator:** Spiko.
- **Vault strategy:** Permissioned tokenised share in Spiko's U.S. Treasury-bill money-market fund.
- **Valuation:** Spiko's verified Chainlink-compatible Oracle publishes NAV/share; fund holdings are off-chain.
- **Dealing:** Subscriptions, transfers and redemptions require eligibility checks and issuer-operated servicing.
- **Fees:** Spiko states a 0.25% annual management fee, reflected in NAV/share.
"""

SPIKO_EUTBL_NOTE = """Spiko EU T-Bills Money Market Fund (EUTBL).

- **Curator:** Spiko.
- **Vault strategy:** Permissioned tokenised share in Spiko's Eurozone Treasury-bill money-market fund.
- **Valuation:** Spiko's verified Chainlink-compatible Oracle publishes EUR NAV/share; fund holdings are off-chain.
- **Dealing:** Subscriptions, transfers and redemptions require eligibility checks and issuer-operated servicing.
- **Fees:** Spiko states a 0.25% annual management fee, reflected in NAV/share.
"""

#: Public qualification for GMX's share-price-equivalent performance curve.
#:
#: GMX liquidity-provider shares retain exposure to every token in their
#: underlying markets, despite the single-sided USDC performance convention.
GMX_SINGLE_SIDED_USDC_NOTE = "The vault performance approximates the single-sided USDC deposit value. GMX vaults hold exposure to all underlying tokens they market make"

#: Public qualification for YieldBasis's USD redemption-value curve.
#: The note gives general readers concise return and risk guidance.
YIELD_BASIS_NOTE = """A [YieldBasis](https://yieldbasis.com/earn) yb-LP is a leveraged BTC or ETH liquidity-provider position. Its value rises and falls with the underlying asset.

yb-LP yield comes primarily from [trading fees earned by the underlying Curve pool and YieldBasis LEVAMM](https://docs.yieldbasis.com/user/protocol/fee-mechanics), after protocol fee allocations and rebalancing costs.

Performance is shown in USD using the marginal amount returned by `preview_withdraw`, so the [Temporary Redemption Discount (TRD)](https://docs.yieldbasis.com/user/protocol/fundamental-value-redemption-value-and-trd) is part of the historical equity curve. [New shares mint at fundamental PPS, while exits use redemption value](https://docs.yieldbasis.com/dev/integration/deposit-withdraw); negative TRD therefore affects an immediate exit, not entry.

The entry and exit fee fields each model a 0.10% conversion between a generic USD stablecoin and the pool's BTC or ETH token. These endpoint costs sit outside the historical equity curve and exclude price impact, gas and MEV.
"""

#: Vault-specific notes and classifications that do not exclude a vault from
#: research datasets.
#:
#: Unlike :py:data:`VAULT_FLAGS_AND_NOTES`, entries here do not make the vault
#: "flagged" through :py:func:`is_flagged_vault`.
VAULT_NOTES: dict[str, str] = {
    **SECURITIZE_PRODUCT_NOTES,
    **ONDO_PRODUCT_NOTES,
    "0x09864f52b035ae22ee739dfa5c748fa080d07bd8": ODA_FACT_JLTXX_NOTE,
    "0x1fecf3d9d4fee7f2c02917a66028a48c6706c179": WISDOMTREE_WTGXX_NOTE,
    "0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e": SUPERSTATE_USTB_NOTE,
    "0x6a7c6aa2b8b8a6a891de552bdeffa87c3f53bd46": ODA_FACT_MONY_NOTE,
    USTBL_TOKEN_ADDRESS: SPIKO_USTBL_NOTE,
    EUTBL_TOKEN_ADDRESS: SPIKO_EUTBL_NOTE,
}

#: Protocol-wide descriptive notes that do not flag products as problematic.
PROTOCOL_NOTES: dict[str, str] = {
    "GMX": GMX_SINGLE_SIDED_USDC_NOTE,
    "YieldBasis": YIELD_BASIS_NOTE,
}

#: Product classification flags that are descriptive rather than exclusionary.
VAULT_DESCRIPTIVE_FLAGS: dict[str, set[VaultFlag]] = {
    **{address: {VaultFlag.tokenised_fund} for address in SECURITIZE_TOKENISED_FUND_ADDRESSES},
    **{address: {VaultFlag.tokenised_fund} for address in ONDO_TOKENISED_FUND_ADDRESSES},
    "0x09864f52b035ae22ee739dfa5c748fa080d07bd8": {VaultFlag.tokenised_fund},
    "0x1fecf3d9d4fee7f2c02917a66028a48c6706c179": {VaultFlag.tokenised_fund},
    "0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e": {VaultFlag.tokenised_fund},
    "0x6a7c6aa2b8b8a6a891de552bdeffa87c3f53bd46": {VaultFlag.tokenised_fund},
    # Centrifuge: Janus Henderson Anemoy S&P500® Fund (SPXA) USDC vault on Base.
    # https://docs.centrifuge.io/developer/protocol/deployments/
    "0x99e9092bae6d4394e54034ecb1e45441678323b9": {VaultFlag.tokenised_fund},
    USTBL_TOKEN_ADDRESS: {VaultFlag.tokenised_fund},
    EUTBL_TOKEN_ADDRESS: {VaultFlag.tokenised_fund},
}

#: Vault-specific notes which must only apply on the specified EVM chain.
#:
#: An address can be deployed on more than one chain. Keep manager-maintained
#: Morini metadata is chain-scoped so a matching address elsewhere cannot
#: inherit an unrelated strategy note.
# fmt: off
CHAIN_SCOPED_VAULT_NOTES: dict[tuple[int, str], str] = {
    (chain_id, address): format_handwritten_vault_note(metadata)
    for (chain_id, address), metadata in MORINI_CAPITAL_VAULT_METADATA.items()
} | AXIS_NOTES_BY_CHAIN
# fmt: on


def get_vault_special_flags(address: str | HexAddress, protocol_name: str | None = None) -> set[VaultFlag]:
    """Get all special vault flags.

    Vault flags can be address-specific or protocol-wide. Protocol-wide flags
    are used when all vaults under a detected protocol should inherit the same
    manual warning, such as the Summer.fi illiquid flag added after the
    2026-07-06 exploit reporting.
    """
    address = address.lower()
    flags = set(VAULT_DESCRIPTIVE_FLAGS.get(address, _empty_set))
    entry = VAULT_FLAGS_AND_NOTES.get(address)
    if entry:
        if entry[0]:
            flags.add(entry[0])

    if protocol_name:
        protocol_entry = PROTOCOL_FLAGS_AND_NOTES.get(protocol_name)
        if protocol_entry:
            if protocol_entry[0]:
                flags.add(protocol_entry[0])

    return flags or _empty_set


def get_notes(address: HexAddress | str, chain_id: int | None = None, protocol_name: str | None = None) -> str | None:
    """Get vault-specific notes.

    Notes can come from the descriptive vault or protocol notes matrices,
    special vault flags or chain-wide defaults. Descriptive notes do not make
    a vault flagged.

    :param address:
        Vault address (will be lowercased).
    :param chain_id:
        Chain ID of the vault. Used to apply chain-wide default notes
        (e.g. all Hypercore vaults get :py:data:`HYPERCORE_VAULT_NOTE`).
    """
    address = address.lower()
    if chain_id is not None:
        note = CHAIN_SCOPED_VAULT_NOTES.get((chain_id, address))
        if note:
            return note

    note = VAULT_NOTES.get(address)
    if note:
        return note

    entry = VAULT_FLAGS_AND_NOTES.get(address)
    if entry:
        return entry[1]

    if protocol_name:
        protocol_entry = PROTOCOL_FLAGS_AND_NOTES.get(protocol_name)
        if protocol_entry:
            return protocol_entry[1]

        note = PROTOCOL_NOTES.get(protocol_name)
        if note:
            return note

    # Default note for all Hypercore vaults
    from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID

    if chain_id == HYPERCORE_CHAIN_ID:
        return HYPERCORE_VAULT_NOTE

    return None


def is_flagged_vault(address: HexAddress | str, protocol_name: str | None = None) -> bool:
    """Is this vault flagged for any special reason?

    Supports both EVM (``0x``-prefixed) and non-EVM addresses (e.g. GRVT ``vlt:`` prefix).
    """
    address = address.lower()
    return VAULT_FLAGS_AND_NOTES.get(address) is not None or bool(protocol_name and PROTOCOL_FLAGS_AND_NOTES.get(protocol_name))


IRREGULAR_REPORTING = "The share price of this vault is updated too irregularly onchain. This makes it difficult to compare it against other vaults. Having no onchain transparency to the value of the vault poses a risk to users."

XUSD_MESSAGE = "Vault likely illiquid due to Stream xUSD exposure issues. You may lose all of your deposits."

HIDDEN_VAULT = "Vault not actively listed on any known website. Likely unmaintained. You may lose your deposits."

BROKEN_VAULT = "Onchain metrics coming out of this vault do not make sense and it's likely the smart contract is broken."

MALICIOUS_VAULT = "This vault is reported as malicious, and may have some sort of mechanism to steal funds."

CONTROVERSIAL_VAULT = "Based on community reports, this vault is controversial. Do not deposit, unless the issue is resolved and full transparency becomes available."

MAINST_VAULT = "Main Street Market related products were wiped out in Oct 10th event https://x.com/Main_St_Finance/status/1976972055951147194"

ABNORMAL_TVL = "The TVL on this vault is abnormal"

ABNORMAL_SHARE_PRICE = "Share price is unrealistically high, likely a broken smart contract"

MISLEADING_VALUATION = "This vault incorrectly reported its share price in the past, due to misleading accounting"

ABNORMAL_VOLATILITY = "Annualised volatility is unrealistically high, likely a low-TVL vault with very few trades"

HYPERCORE_VAULT_NOTE = "Profit and loss (PnL) results here differ from the method used on the Hyperliquid website. Instead of raw account PnL, the data is cleaned from deposit/redeem flow and reflects better the actual profitability of the underlying trading activity."

UNKNOWN_VAULT = "Vault is not known, not listed on the website of the protocol"

FOXIFY_VAULT = "Foxify offers perp DEX and funding for proprietary trades. This vault is associated with this activity, but it is not publicly described how the vault works."

PENDLE_LOOPING = "Abnormal high yield due to Pendle looping - more info here https://x.com/ssmccul/status/2006016219275501936"

ZEROLEND_SUPERFORM_WITHDRAW_ONLY = "All ZeroLend vaults on Superform are in withdraw-only mode. Support could not give an answer on why."

HLT_IRREGULAR_SHARE_PRICE = "This vault has experienced irregular share price resets (epoch resets) that distort the performance metrics and make the equity curve misleading."

LOW_TVL_ABNORMAL_PRICE = "Low-TVL vault with abnormal price behaviour"

ILLIQUID_ABNORMAL_SHARE_PRICE = "Vault likely illiquid. Share price chart has abnormal high returns while deposits are still enabled."

MISSING_IN_PROTOCOL_FRONTEND = "This vault is missing in the protocol's primary website and cannot be verified."

NOT_IN_MORPHO_API = "This vault does not appear on Morpho website."

NOT_IN_YEARN_FRONTEND = "This Yearn V3-compatible vault is not endorsed in Yearn's authoritative metadata."

TEST_VAULT = "This appears to be a test vault and should not be shown to end users."

UNKNOWN_ABNORMAL_SHARE_PRICE = "Share price chart has abnormal high returns and the vault protocol is not yet identified."

SUBVAULT = "This vault is likely not intended to be directly exposed to the end users. It may be used by other vaults as a part of the strategy mix and has erratic TVL."

PEAPODS_ILLIQUID = "Peapods vault is illiquid"

USDN_WRAPPER_ILLIQUID = "USDN Wrapper is illiquid"

SUMMER_FI_ILLIQUID = "Summer.fi vault is illiquid"

GREENHOUSE_ILLIQUID = "Greenhouse vault is illiquid"

MO_EARN_MAX_USDC_ILLIQUID = "Mo Earn Max USDC is illiquid"

CLEARSTAR_YIELD_USDC_ILLIQUID = "Clearstar Yield USDC is illiquid"

ETHEREALM_USDC_ILLIQUID = "Etherealm USDC is illiquid"

APOSTRO_USDC_FRONTIER_ILLIQUID = "Apostro USDC Frontier is illiquid"

HYUSDT0_HWHLP_ILLIQUID = "hyUSD₮0 (hwHLP) vault is illiquid"

RESOLV_ILLIQUID = "Resolv vault is illiquid"

RESOLV_USDC_ILLIQUID = "Resolv USDC vault is illiquid"

STEAKHOUSE_PRIME_AUSD_ILLIQUID = "Steakhouse Prime AUSD vault is illiquid"

LIQUITY_V2_WETH_STABILITY_POOL_ILLIQUID = "Liquity V2 WETH Stability Pool vault is illiquid"

ODINS_RESERVE_ILLIQUID = "Odins Reserve vault is illiquid"

BORROWABLE_USDC_SILOID_111_ILLIQUID = "Borrowable USDC Deposit, SiloId: 111 is illiquid"

BORROWABLE_USDC_SILOID_142_ILLIQUID = "Borrowable USDC Deposit, SiloId: 142 is illiquid"

BORROWABLE_USDC_SILOID_145_ILLIQUID = "Borrowable USDC Deposit, SiloId: 145 is illiquid"

VI_USDC_QA_G_ILLIQUID = "VI-USDC-QA_G is illiquid."

#: King RSS USDC Vault lends against a token with no market, see its entry in :py:data:`VAULT_FLAGS_AND_NOTES`
KING_RSS_UNSELLABLE_COLLATERAL = "Lends against RSS elephanToken, which has no market and a custom oracle; the reported yield cannot be realised."

#: Credifi's Euler pool lends against an unsellable credit-line token, see its entry in :py:data:`VAULT_FLAGS_AND_NOTES`
CREDIFI_UNSECURED_CREDIT_COLLATERAL = "Lends only against Credifi CREDIT, a single-holder bookkeeping token with no market priced at $1 by Credifi's own unverified oracle, so the loans are unsecured credit lines and the collateral cannot be valued or sold."

#: Euler pools left with bad debt by the Stream Finance and Elixir collapse, see their entries in :py:data:`VAULT_FLAGS_AND_NOTES`
STREAM_ELIXIR_EULER_BAD_DEBT = "Fully borrowed against deUSD, sdeUSD or USDX collateral that collapsed with Stream Finance and Elixir in November 2025; the vault has no cash and withdrawals fail."

#: A vault denominated in the collapsed Stream Finance xUSD, see its entry in :py:data:`VAULT_FLAGS_AND_NOTES`
STREAM_XUSD_DENOMINATED = "Denominated in Stream Finance xUSD, which collapsed in November 2025 and trades near $0.02; the reported TVL counts xUSD at $1 and the vault has no borrowers."

#: Under review: collateral without a liquid market, valued by its issuer; see the ``review_needed`` entries in :py:data:`VAULT_FLAGS_AND_NOTES`
REVIEW_NEEDED_OFF_MARKET_COLLATERAL = "Under review: the vault lends against collateral without a liquid DEX market, valued by its issuer's NAV or oracle. Our automated investability checks have not reached a consistent verdict, so the vault is not blacklisted. Check the collateral and the withdrawable liquidity before depositing."

#: Under review: the vault's lending pool is often fully borrowed; see the ``review_needed`` entries in :py:data:`VAULT_FLAGS_AND_NOTES`
REVIEW_NEEDED_EXIT_LIQUIDITY = "Under review: the vault's lending pool has been almost fully borrowed at times, so withdrawals may have to wait for repayments. Our automated investability checks have not reached a consistent verdict, so the vault is not blacklisted. Check the withdrawable liquidity before depositing."

#: Under review: the vault's reported protocol or TVL may be wrong; see the ``review_needed`` entries in :py:data:`VAULT_FLAGS_AND_NOTES`
REVIEW_NEEDED_DATA_QUALITY = "Under review: the reported protocol or TVL of this vault may be inaccurate. Our automated investability checks have not reached a consistent verdict, so the vault is not blacklisted. Verify the vault onchain before depositing."

#: Under review: a review started by a person rather than the investability check; see the ``review_needed`` entries in :py:data:`VAULT_FLAGS_AND_NOTES`
REVIEW_NEEDED_MANUAL = "Under review: the Trading Strategy team is reviewing this vault. It is not blacklisted; check its positions and withdrawable liquidity before depositing."


#: Protocol-wide flags and notes.
#:
#: Unlike :py:data:`VAULT_FLAGS_AND_NOTES`, these entries apply to all vaults
#: under a protocol name returned by vault detection.
#:
#: Summer.fi incident context: CryptoBriefing reported on 2026-07-06 that
#: Blockaid flagged an active exploit against Summer.fi, with approx. $6M DAI
#: drained from Ethereum contracts including
#: 0x98C49e13bf99D7CAd8069faa2A370933EC9EcF17.
#:
#: Source: https://cryptobriefing.com/blockaid-detects-6m-exploit-summer-fi/
PROTOCOL_FLAGS_AND_NOTES: dict[str, tuple[VaultFlag | None, str]] = {
    "Summer.fi": (VaultFlag.illiquid, SUMMER_FI_ILLIQUID),
}

YIELDNEST_YNRWAX = """ynRWAx: Tokenized Australian residential real estate credit earning 11% APY, allocated to mortgage-backed loans on verified house-and-land developments. Made safe in collaboration with a fully licensed and insured fund manager, [Kimber Capital](https://kimbercapital.au/) (AFS Licence No. 425278).

Fees: 0%.

Fixed Maturity Date: 15 Oct, 2026.

Although the vault has long lock up matching the duration of the underlying real-world asset instrument, [the share token can be traded against the secondary liquidity available at Curve DEX](https://www.curve.finance/dex/ethereum/pools/factory-stable-ng-650/swap).
"""

ETH_STRATEGY_ESPN = """ESPN (ETH Strategy Perpetual Note) lends USDS to ETH Strategy, but instead of receiving interest, ESPN receives a long-dated ETH call option. To extract yield from this long-dated call option, ESPN systematically sells shorter-dated call options on [Derive](https://www.derive.xyz/). The symmetry between the long-dated convertibles acquired and short-dated calls sold keeps the strategy balanced in USD terms.

Third-party comment:

> They do not have a redemption queue in place (yet), that's one of the things on the roadmap they're promising since months and nothing is happening.
>
> I have read the docs and I know that they're using options on Derive, but in the last few months at least a part of those must have been expired, and they propably rolled them over to new ones without satisfying redemptions. If that isn't scammy behaviour, then I don't know...

[Discussion about the ESPN vault](https://x.com/TradingProtocol/status/2011043276283900198).
"""

LIQUID_ROYALTY_NOTE = "Early withdrawal within 7-day cooldown incurs a 20% liquidation penalty on the unstaked amount. Initiate cooldown and wait 7 days to withdraw without penalty."

INVERSE_SDOLA_FLASH_LOAN_EXPLOIT = "An attacker used ~$30M in flash loans to inflate the sDOLA exchange rate (from ~1.188 to ~1.358 per DOLA) by donating assets directly into the vault. This triggered faulty oracle pricing on LlamaLend, force-liquidating ~27 user positions (DOLA-backed leveraged longs) and letting the attacker profit ~$240K in liquidation rewards."

LIGHTER_LLP_STAKING = """LLP (Lighter Liquidity Provider) is the protocol's community-owned market-making pool providing liquidity and handling liquidations on Lighter DEX.

Depositing into LLP requires staking LIT tokens at a 1:10 ratio (1 LIT staked per 10 USDC deposited). Staking ≥100 LIT waives withdrawal and transfer fees. If staked LIT does not fully cover the deposit, up to 3% or 100 USDC of the uncovered amount is returned daily to the user's balance.

Withdrawal cooldown is 5 minutes. Operator fee is 0%."""

LIGHTER_ROBINHOOD_LLP_INSURANCE = """Lighter Liquidity Provider (LLP) on Robinhood Chain is the USDG-denominated protocol insurance fund for Robinhood Wallet perpetual futures.

During partial liquidations, the protocol can send a liquidation fee of up to 1% to LLP. During full liquidations, LLP can take over the remaining positions. The pool therefore carries market-making and liquidation risk and is not equivalent to holding USDG directly.

Robinhood's public documentation does not state that the Ethereum LLP's LIT staking access rule applies to this deployment, so the Ethereum USDC/LIT requirements are intentionally not repeated here.

[Robinhood Wallet perpetual futures](https://robinhood.com/us/en/support/articles/robinhood-wallet-perpetual-futures/)."""

GRVT_GLP_DEPOSIT_LIMITS = """GLP deposit limits are tied to your lifetime trading volume on GRVT. Each tier sets a maximum percentage of account equity and an absolute USDT cap:

| Trading volume | % of equity | Max USDT |
|---|---|---|
| $0–$10k | 10% | $10k |
| $10k–$1m | 20% | $20k |
| $1m–$10m | 30% | $50k |
| $10m–$100m | 40% | $100k |
| $100m–$250m | 50% | $250k |
| $250m+ | 60% | $500k |

The system checks your total equity meets the minimum for your tier when you attempt withdrawals, transfers, or redemption cancellations — if it does not, the action is blocked. Redemptions take 2–7 days.

[More details on the GLP programme](https://help.grvt.io/en/articles/12760192-grvt-liquidity-provider-glp).
"""

#: Vault-specific flags and notes.
#:
#: Most entries identify vaults that should not be used. Some entries, such as
#: tokenised funds, are descriptive classifications and are not in
#: :py:data:`BAD_FLAGS`.
#:
#: Make sure address is lowercased
VAULT_FLAGS_AND_NOTES: dict[str, tuple[VaultFlag | None, str]] = {
    # Tulipa USDC
    "0xce0b790ae0d8cf91e01f3fb69025e14569b574f3": (VaultFlag.misleading_valuation, MISLEADING_VALUATION),
    # Borrowable USDC Deposit, SiloId: 127
    "0x2433d6ac11193b4695d9ca73530de93c538ad18a": (VaultFlag.illiquid, XUSD_MESSAGE),
    # https://tradingstrategy.ai/vaults/borrowable-xusd-deposit-siloid-112
    "0x172a687c397e315dbe56ed78ab347d7743d0d4fa": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Llama Lend IBTC / crvUSD
    "0xe296ee7f83d1d95b3f7827ff1d08fe1e4cf09d8d": (VaultFlag.illiquid, HIDDEN_VAULT),
    # Silo Finance Borrowable USDC Deposit in ARB Silo
    "0xb739ae19620f7ecb4fb84727f205453aa5bc1ad2": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable scUSD Deposit, SiloId: 125
    "0x0ab02dd08c1555d1a20c76a6ea30e3e36f3e06d4": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable frxUSD Deposit, SiloId: 37
    "0xda14a41dbda731f03a94cb722191639dd22b35b2": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Exposure to Elixir
    "0x94643e86aa5e38ddac6c7791c1297f4e40cd96c1": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Exposure to xUSD - Silos
    "0x3014ed70b39be395e1a5eb8ab4c4b8a5378e6522": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x1de3ba67da79a81bc0c3922689c98550e4bd9bc2": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x672b77f0538b53dc117c9ddfeb7377a678d321a6": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0xe0fc62e685e2b3183b4b88b1fe674cfec55a63f7": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x9c4d4800b489d217724155399cd64d07eae603f3": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0xa1627a0e1d0ebca9326d2219b84df0c600bed4b1": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Harvest: USDC Vault (0xACB7)
    "0xacb7432a4bb15402ce2afe0a7c9d5b738604f6f9": (VaultFlag.subvault, SUBVAULT),
    "0x1320382143d98a80a0b247148a42dd2aa33d9c2d": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0xed9777944a2fb32504a410d23f246463b3f40908": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x61ffbead1d4dc9ffba35eb16fd6cadee9b37b2aa": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x8399c8fc273bd165c346af74a02e65f10e4fd78f": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0xac69cfe6bb269cebf8ab4764d7e678c3658b99f2": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x55555815a5595991c3a0ff119b59aef6c8b55555": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x36e2aa296e798ca6262dc5fad5f5660e638d5402": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x27968d36b937dcb26f33902fa489e5b228b104be": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x6030ad53d90ec2fb67f3805794dbb3fa5fd6eb64": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x7184bea7743ccfbe390f9cd830095a13ef867941": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x2f5dc399b1e31f9808d1ef1256917abd2447c74f": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable USDC Deposit, SiloId: 55, Sonic
    "0x4935fadb17df859667cc4f7bfe6a8cb24f86f8d0": (VaultFlag.illiquid, XUSD_MESSAGE),
    # EVK Vault eUSDC-1, Sonic
    "0x9ccf74e64922d8a48b87aa4200b7c27b2b1d860a": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Frontier Yala USDC
    "0x481d4909d7ca2eb27c4975f08dce07dbef0d3fa7": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Frontier mMEV USDC
    "0x98281466abcf48eaad8c6e22dedd18a3426a93b4": (VaultFlag.illiquid, XUSD_MESSAGE),
    # AvantgardeUSDC Core
    "0x5b56f90340dbaa6a8693dadb141d620f0e154fe6": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable USDC Deposit, SiloId: 23
    "0x5954ce6671d97d24b782920ddcdbb4b1e63ab2de": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable scUSD Deposit, SiloId: 118
    "0xb1412442aa998950f2f652667d5eba35fe66e43f": (VaultFlag.illiquid, XUSD_MESSAGE),
    # MEV Capital scUSD
    "0xb38d431e932fea77d1df0ae0dfe4400c97e597b8": (VaultFlag.illiquid, XUSD_MESSAGE),
    # MEV Capital USDC
    "0x196f3c7443e940911ee2bb88e019fd71400349d9": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable USDC Deposit, SiloId: 170
    "0x7786dba2a1f7a4b0b7abf0962c449154c4f2b8ac": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable USDC Deposit, SiloId: 105
    "0x4f55e28d36b30a638c3aa1d5cbf9c4ccb3831506": (VaultFlag.illiquid, ILLIQUID_ABNORMAL_SHARE_PRICE),
    # Borrowable USDC Deposit, SiloId: 111 (Sonic)
    "0xae79b0d94e1c53cd2e8160899b8d58ec138d341f": (VaultFlag.illiquid, BORROWABLE_USDC_SILOID_111_ILLIQUID),
    # 40avax-USDC-VAULT on Avalanche
    "0xbed7c02887efd6b5eb9a547ac1a4d5e582791647": (VaultFlag.abnormal_share_price, UNKNOWN_ABNORMAL_SHARE_PRICE),
    # XAU Aplha Vault on Arbitrum
    "0x5424293637cc59ad7580ad1cac46e28d4801a587": (VaultFlag.abnormal_share_price, UNKNOWN_ABNORMAL_SHARE_PRICE),
    # HaUSDC Share - Test on Hyperliquid
    "0x7db7bcd6746f4dcfa2fdcdd80c1c313cc371f166": (VaultFlag.unofficial, TEST_VAULT),
    "0x25b4dc5f96312c7083a58d80d8ecad6ecddbbdfb": (VaultFlag.unofficial, TEST_VAULT),
    # Valamor aUSDC/USDC/USDT etc.
    # https://x.com/VarlamoreCap/status/1986290754688541003
    "0x3d7b0c3997e48fa3fc96cd057d1fb4e5f891835b": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0xf6f87073cf8929c206a77b0694619dc776f89885": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x2ba39e5388ac6c702cb29aea78d52aa66832f1ee": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x4dc1ce9b9f9ef00c144bfad305f16c62293dc0e8": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x6c09bfdc1df45d6c4ff78dc9f1c13af29eb335d4": (VaultFlag.illiquid, XUSD_MESSAGE),
    "0x9a1bf5365edbb99c2c61ca6d9ffad0b705acfc6f": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Euler TelosC Stream/Trevee/Surge blacklist audit.
    #
    # Method:
    # 1. Fetch Euler labels from:
    #    - https://github.com/euler-xyz/euler-labels/blob/master/1/products.json
    #    - https://github.com/euler-xyz/euler-labels/blob/master/1/earn-vaults.json
    #    - https://github.com/euler-xyz/euler-labels/blob/master/9745/products.json
    #    - https://github.com/euler-xyz/euler-labels/blob/master/9745/earn-vaults.json
    # 2. Select TelosC products and EulerEarn entries whose deprecationReason
    #    mentions Stream or insolvent Stream positions.
    # 3. Join the resulting (chain_id, address) pairs against our exported
    #    vault universe at ~/.tradingstrategy/vaults/downloads/vault-universe.json.
    #
    # Initial TelosC result:
    # - These 10 exported TelosC vaults matched the Stream-insolvent Euler labels.
    # - TelosC Almanak, Haven, Reservoir and f(x) did not match Stream-affected
    #   Euler label metadata and were not blacklisted in this pass.
    #
    # Ethereum, TelosC Stream, USDC.
    "0x01864ae3c7d5f507cc4c24ca67b4cabbdda37ecd": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Ethereum, TelosC Surge USDC, USDC, EulerEarn.
    "0x49c5733d71511a78a3e12925ea832f49031c97e9": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Ethereum, TelosC Stream, xUSD.
    "0xf1ba8c5ca5ab011d06f31e64dad313d204acb9eb": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Plasma, TelosC Stream, xUSD.
    "0x138c289bb8b855cf271305c8bcf91dc31ba30194": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Plasma, TelosC Trevee, plUSD.
    "0x1ad2d433b5e95077eb2855eab854b72ea9ee9d6c": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Plasma, TelosC Stream, plUSD.
    "0x27934d4879fc28a74703726edae15f757e45a48a": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Plasma, TelosC Stream, USDT0.
    "0x57c582346b7d49a46af3745a8278917d1c1311b8": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Plasma, TelosC Surge, USDT0, EulerEarn.
    "0xa9c251f8304b1b3fc2b9e8fcae78d94eff82ac66": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Plasma, TelosC Trevee, USDT0.
    "0xb5526491742fee67e9e0d0d8c619a95d422fd398": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Plasma, TelosC Stream, msUSD.
    "0xf90cf999de728a582e154f926876b70e93a747b7": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Full Euler-label Stream audit.
    #
    # Method:
    # 1. Enumerate all numeric chain directories in euler-xyz/euler-labels.
    # 2. Select every products.json product and earn-vaults.json entry whose
    #    deprecationReason mentions Stream.
    # 3. Join the resulting 29 Euler-label addresses against our exported vault
    #    universe at ~/.tradingstrategy/vaults/downloads/vault-universe.json.
    #
    # Result:
    # - 17 Stream-deprecated Euler-label addresses existed in our exported vault
    #   universe.
    # - 10 TelosC matches are listed above.
    # - These 7 non-TelosC matches were still unblacklisted and are now flagged.
    # - The app's /api/internal/labels endpoint returned 403 and says it is not a
    #   public contract; use the public euler-labels repository as the source.
    #
    # Plasma, Frontier Elixir, USDT0, no curator slug in our vault universe.
    "0x3799251bd81925cfccf2992f10af27a4e62bf3f7": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Plasma, Hyperithm Euler USDT, USDT0, Hyperithm, EulerEarn.
    "0x66be42a0bda425a8c3b3c2cf4f4cb9edfcaed21d": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Plasma, Re7 Labs xUSD, xUSD, Re7 Labs.
    "0x8adb906421f65c27155f44f1829ca1e5b024c3f6": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Plasma, Re7 Labs xUSD, USDT0, Re7 Labs.
    "0xf675fbe777e992f5d5d84adf41161dc0f20104a6": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Plasma, Re7 USDT0 Core, USDT0, Re7 Labs, EulerEarn.
    "0xa5eed1615cd883dd6883ca3a385f525e3beb4e79": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Avalanche, Re7 AUSD, AUSD, Re7 Labs, EulerEarn.
    "0x70c329d6f06b33fa6b75e335b35168b1de84217b": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Avalanche, Re7 USDC, USDC, Re7 Labs, EulerEarn.
    "0xeaf77df5d03306bca4ee8b58b6821e6aca76309d": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Euler Re7
    "0xaba9d2d4b6b93c3dc8976d8eb0690cca56431fe4": (VaultFlag.illiquid, XUSD_MESSAGE),
    # K3
    "0xe1a62fdcc6666847d5ea752634e45e134b2f824b": (VaultFlag.unofficial, MISSING_IN_PROTOCOL_FRONTEND),
    # Excellion USDC Vault
    "0xb8a14b03900828f863aedd9dd905363863bc31f4": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Spectra ERC4626 Wrapper: MEV USDC
    "0x92fbb58342164546325602588599b05802c69bbe": (VaultFlag.illiquid, XUSD_MESSAGE),
    # USDN Wrapper (Spectra on Ethereum)
    "0x06a491e3efee37eb191d0434f54be6e42509f9d3": (VaultFlag.illiquid, USDN_WRAPPER_ILLIQUID),
    # Greenhouse USD ghUSDC
    # https://x.com/Main_St_Finance/status/1976972055951147194
    "0xf6bc16b79c469b94cdd25f3e2334dd4fee47a581": (VaultFlag.illiquid, MAINST_VAULT),
    # Aarna atvPTmax
    # atvPTmax
    "0xd24e4a98b5fd90ff21a9cc5e2c1254de8084cd81": (VaultFlag.broken, BROKEN_VAULT),
    "0x9deb2b3593eb4e1838b233d386a9358448f753e3": (VaultFlag.broken, BROKEN_VAULT),
    "0x332e81368daec705612ff06b3a80b10ae1e5f110": (VaultFlag.broken, BROKEN_VAULT),
    # Yearn USDC to USDS Depositor strategy
    "0x39c0aec5738ed939876245224afc7e09c8480a52": (VaultFlag.broken, BROKEN_VAULT),
    # Peapods broken 42?
    "0x4b5c90dc6bc08a10a24487726e614e9d148362e1": (VaultFlag.broken, BROKEN_VAULT),
    # Mithras
    "0x391b3f70e254d582588b27e97e48d1cfcdf0be7e": (VaultFlag.broken, BROKEN_VAULT),
    # BlueChip USDC Vault (Prime)
    "0x3f604074f3f12ff70c29e6bcc9232c707dc4d970": (VaultFlag.broken, BROKEN_VAULT),
    # Peapods 14
    "0xc2810eb57526df869049fbf4c541791a3255d24c": (VaultFlag.broken, BROKEN_VAULT),
    # Pendle
    "0xd6e094faf9585757f879067ce79c7f6b3c8e4fb0": (VaultFlag.broken, BROKEN_VAULT),
    "0x64fcfd84109768136a687ed9614a9d0b8c6910e2": (VaultFlag.broken, BROKEN_VAULT),
    "0xd87598dd895de1b7fb2ba6af91b152f26baf7bee": (VaultFlag.broken, BROKEN_VAULT),
    # Yield optimizer vault
    "0x3bb60eca398f480f4b7756600c04309de486232e": (VaultFlag.broken, BROKEN_VAULT),
    # Malicious Euler vault?
    # EVK Vault eUSDC-8 on Sonic
    "0x683dbc88b371ae48962b56e36e5a0c34e3ad4caf": (VaultFlag.malicious, MALICIOUS_VAULT),
    # Broken vault?
    # http://localhost:5173/vaults/stablecoins/iusd
    "0x36585e7ae4b8a422135618a2c113b8b516067e7a": (VaultFlag.broken, BROKEN_VAULT),
    # Broken vault?
    # Upshift Edge USDC
    "0xeaa3b922e9febca37d1c02d2142a59595094c605": (VaultFlag.broken, BROKEN_VAULT),
    # Velvet USD coin
    "0xe83522f0882493844c48add97ef03281040e3d2d": (VaultFlag.broken, BROKEN_VAULT),
    # Abnormal TVLs
    "0x10019c629aa7c51e3853286b1c7894b17c257e00": (VaultFlag.abnormal_tvl, BROKEN_VAULT),
    "0x21b92610c69c889b6ca972a973f637e9f10885b3": (VaultFlag.abnormal_tvl, ABNORMAL_TVL),
    "0x8bce54605f56f2f711d9b60bdf2433aae8a14aa5": (VaultFlag.abnormal_tvl, ABNORMAL_TVL),
    "0xbcf722b41ff6f2f932721582680ed0116292cc28": (VaultFlag.abnormal_tvl, ABNORMAL_TVL),
    # USDC BaseInvaders
    "0xd1468af648565f11393e4033cb0cd270b62495c9": (VaultFlag.abnormal_tvl, UNKNOWN_VAULT),
    # Peapods Interest Bearing USDC - 17
    "0xeee75954eded526ef98a0cecc027beee4586315e": (VaultFlag.broken, BROKEN_VAULT),
    # Pendle yield vault
    "0x8977aafd34323fa046f51f3c913a30caa7dd17db": (VaultFlag.broken, BROKEN_VAULT),
    # Foxify vault
    "0x3ccff8c929b497c1ff96592b8ff592b45963e732": (VaultFlag.proprietary_trading, FOXIFY_VAULT),
    # KUSDT
    # http://localhost:5173/vaults/gtrade-kusdt
    # No idea what's this - unverified
    "0x4f04cb32688ea1954e53c85b846597881ebe9582": (VaultFlag.broken, BROKEN_VAULT),
    # Steakhouse High Yield USDT0 on Arbitrum
    # https://tradingstrategy.ai/vaults/steakhouse-high-yield-usdt0
    "0x4739e2c293bdcd835829aa7c5d7fbdee93565d1a": (None, PENDLE_LOOPING),
    # Static RWA ZeroLend USDC
    "0x942bed98560e9b2aa0d4ec76bbda7a7e55f6b2d6": (VaultFlag.illiquid, ZEROLEND_SUPERFORM_WITHDRAW_ONLY),
    # Euler MEV Capital USDC
    "0xa446938b0204aa4055cdfed68ddf0e0d1bab3e9e": (VaultFlag.unofficial, MISSING_IN_PROTOCOL_FRONTEND),
    # Rezerve USDC
    "0xc42d337861878baa4dc820d9e6b6c667c2b57e8a": (VaultFlag.unofficial, MISSING_IN_PROTOCOL_FRONTEND),
    # Rezerve.money USD "USDR 2029 Bond" on Ethereum - unverified Rezerve product.
    # Its denomination token reuses the USDR ticker (shared with the defunct Tangible
    # and StablR USDR), so it is no longer caught by the USDR depeg blacklist now that
    # USDR is matched by contract only. Keep it suppressed via a manual flag.
    "0x3839a0dd920463eb5d8231efe4d8c5edc44145ec": (VaultFlag.unofficial, MISSING_IN_PROTOCOL_FRONTEND),
    # YieldNest ynRWAx vault on Ethereum - fixed maturity date 15 Oct 2026
    "0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8": (None, YIELDNEST_YNRWAX),
    # Supply USDC on ZeroLend RWA Market
    "0x887d57a509070a0843c6418eb5cffc090dcbbe95": (VaultFlag.illiquid, ZEROLEND_SUPERFORM_WITHDRAW_ONLY),
    # Re7 USDC (Euler on Sonic)
    "0xf75ae954d30217b4ee70dbfb33f04162aa3cf260": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # Mainstreet Liquidity Vault (Euler on Sonic)
    "0x5b63bd1574d40d98c6967047f0323cc5d4895775": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # Braindead Digital USDC (Euler on Sonic)
    "0x3710b212b39477df2deaadcf16ef56c384a3d142": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # ymevUSDC (Yearn on Avalanche)
    "0x7aca67a6856bf532a7b2dea9b20253f08bc9a85a": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # Hemi Clearstar USDC.e
    "0x05c2e246156d37b39a825a25dd08d5589e3fd883": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # https://tradingstrategy.ai/vaults/lusd-coin-2
    "0x0ddb1ea478f8ef0e22c7706d2903a41e94b1299b": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # https://tradingstrategy.ai/vaults/ltether-usd-4
    "0x4c8e1656e042a206eef7e8fcff99bac667e4623e": (VaultFlag.abnormal_price_on_low_tvl, LOW_TVL_ABNORMAL_PRICE),
    # Harvest: USDC Vault (0x0F6d)
    "0x0f6d1d626fd6284c6c1c1345f30996b89b879689": (VaultFlag.subvault, SUBVAULT),
    # Morpho OEV-boosted USDC Compounder
    "0x888239ffa9a0613f9142c808aa9f7d1948a14f75": (VaultFlag.subvault, SUBVAULT),
    # Morpho Gauntlet USDC Prime Compounder
    "0x694e47afd14a64661a04eee674fb331bcdef3737": (VaultFlag.subvault, SUBVAULT),
    # Morpho Gauntlet USDC Prime Compounder
    "0x694e47afd14a64661a04eee674fb331bcdef3737": (VaultFlag.subvault, SUBVAULT),
    # Morpho Gauntlet USDC Prime Compounder
    "0x8092c20351cf4048b464df2144dc8a4dd49ce71d": (VaultFlag.subvault, SUBVAULT),
    # Aave V3 USDS Lender
    "0xd144eaff17b0308a5154444907781382398aac61": (VaultFlag.subvault, SUBVAULT),
    # AaveV3 USDC.e Lender
    "0x85968bf0f1f110c707fef10a59f80118f349c058": (VaultFlag.subvault, SUBVAULT),
    # Curve Boosted crvUSD-sfrxUSD Lender
    "0xf91a9a1c782a1c11b627f6e576d92c7d72cdd4af": (VaultFlag.subvault, SUBVAULT),
    # Curve Boosted crvUSD-sUSDe Lender
    "0x6abbda8243f4bf130a97beae759a6e91522520b9": (VaultFlag.subvault, SUBVAULT),
    # dgnHYPE (D2 Finance on Arbitrum)
    "0x64167cd42859f64cff2aa4b63c3175ccef9659dd": (VaultFlag.subvault, SUBVAULT),
    # Convex crvUSD-sfrxUSD Lender
    "0x7a26c6c1628c86788526efb81f37a2ffac243a98": (VaultFlag.subvault, SUBVAULT),
    # USDC Fluid Lender
    "0x00c8a649c9837523ebb406ceb17a6378ab5c74cf": (VaultFlag.subvault, SUBVAULT),
    # ETH Strategy Perpetual Note (Ethereum)
    "0xb250c9e0f7be4cff13f94374c993ac445a1385fe": (VaultFlag.long_duration, ETH_STRATEGY_ESPN),
    # Apostro aprUSDC (Sonic)
    "0xcca902f2d3d265151f123d8ce8fdac38ba9745ed": (VaultFlag.unofficial, MISSING_IN_PROTOCOL_FRONTEND),
    # Apostro USDC Frontier (Euler on Ethereum)
    "0xed9278c5188f37670b33ef3b00729e38260cd5d5": (VaultFlag.illiquid, APOSTRO_USDC_FRONTIER_ILLIQUID),
    # Blue Chip USDC Vault (Prime) on Ethereum - Morpho
    "0x74847d0d124ce5c89ca8f4e7547aecd09e86b2e0": (VaultFlag.unofficial, MISSING_IN_PROTOCOL_FRONTEND),
    # VaultMorpho on Ethereum - Morpho
    "0x21ed44c18c926c60092b1b2985e2c999421a5a69": (VaultFlag.unofficial, MISSING_IN_PROTOCOL_FRONTEND),
    # Borrowable USDC Deposit, SiloId: 125 on Avalanche
    "0xe0345f66318f482acccd67244a921c7fdc410957": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable USDC Deposit, SiloId: 142 on Avalanche
    "0x606fe9a70338e798a292ca22c1f28c829f24048e": (VaultFlag.illiquid, BORROWABLE_USDC_SILOID_142_ILLIQUID),
    # Borrowable USDC Deposit, SiloId: 145 on Arbitrum
    "0xdc1ab820c92735e7a5e48f10fa3d8424ec47a93e": (VaultFlag.illiquid, BORROWABLE_USDC_SILOID_145_ILLIQUID),
    # Peapods Interest Bearing USDC - 22 (Arbitrum)
    "0x0319c82013cf676661f7bde576c6731869a93fc0": (VaultFlag.illiquid, PEAPODS_ILLIQUID),
    # Peapods Interest Bearing USDC - 38 (Sonic)
    "0x87caed1e19da46098e710b69cae33e74c146bacd": (VaultFlag.illiquid, PEAPODS_ILLIQUID),
    # Peapods Interest Bearing USDC - 38 (Arbitrum)
    "0x5eb03d0fcfd3860be03b81a1ab3d46db3315202a": (VaultFlag.illiquid, PEAPODS_ILLIQUID),
    # Peapods Interest Bearing USDC - 6 (Arbitrum)
    "0x3a87cf9af4d21778dad1ce7d0bf053f4b8f2631f": (VaultFlag.illiquid, PEAPODS_ILLIQUID),
    # Curve Boosted crvUSD-fxSAVE Lender (Yearn on Ethereum)
    "0x5103d3ee6d599984609daaadd3a439152cc0c392": (VaultFlag.subvault, SUBVAULT),
    # Convex crvUSD-fxSAVE Lender (Yearn on Ethereum)
    "0x6c7150b9eb23ee563b28905791ad5b6c9cb6b21a": (VaultFlag.subvault, SUBVAULT),
    # Grvt Liquidity Provider (GLP)
    "vlt:34dtzyg6lhkgm49je5aabi9tebw": (None, GRVT_GLP_DEPOSIT_LIMITS),
    # Lighter Liquidity Provider (LLP) — requires LIT token staking for deposits
    "lighter-pool-281474976710654": (None, LIGHTER_LLP_STAKING),
    # Lighter LLP on Robinhood Chain. For now this has a separate note because
    # the deployment uses USDG and Robinhood's documentation does not state
    # that Ethereum LLP's USDC/LIT staking requirements apply here.
    "lighter-pool-robinhood-281474976710654": (None, LIGHTER_ROBINHOOD_LLP_INSURANCE),
    # Morpho Yearn Morpho Vault 1 Compounder (Base)
    "0xf115c134c23c7a05fbd489a8be3116ebf54b0d9f": (VaultFlag.subvault, SUBVAULT),
    # Morpho Moonwell Flagship USDC Compounder (Base)
    "0xd5428b889621eee8060fc105aa0ab0fa2e344468": (VaultFlag.subvault, SUBVAULT),
    # Morpho Zircuit Finance USDC on Base Compounder
    "0x049e8aab2d3ca187e47d74cf8171ad266f18643e": (VaultFlag.subvault, SUBVAULT),
    # Tulipa Capital USDT0
    "0xaf293898269ac7f366d0e05052b5fdfee8c8052c": (VaultFlag.irregular_reporting, IRREGULAR_REPORTING),
    # Hashfire launch fund.
    # No source code.
    "0xfb7cef5cfdba99bd1f7c0350575980470dad3e6f": (VaultFlag.broken, BROKEN_VAULT),
    # YieldNest USDC Flex Strategy - ynRWAx - SPV1
    "0xf6e1443e3f70724cec8c0a779c7c35a8dcda928b": (VaultFlag.subvault, SUBVAULT),
    # Morpho Yearn OG USDC Compounder 2
    "0x0e297de4005883c757c9f09fdf7cf1363c20e626": (VaultFlag.subvault, SUBVAULT),
    # USDC To sUSDS Depositor (Yearn on Ethereum)
    "0xda2f1b3cba732d779cff56f0cf9d3bc8aea6cd8d": (VaultFlag.subvault, SUBVAULT),
    # DAI to USDS Depositor
    "0xaedf7d5f3112552e110e5f9d08c9997adce0b78d": (VaultFlag.subvault, SUBVAULT),
    # Morpho Gauntlet USDT Prime Compounder
    "0x6d2981ff9b8d7edbb7604de7a65bac8694ac849f": (VaultFlag.subvault, SUBVAULT),
    # Morpho Gauntlet AUSD Vault Compounder
    "0xf7ede5332c6b4a235be4aa3c019222cfe72e984f": (VaultFlag.subvault, SUBVAULT),
    # Morpho Steakhouse Prime AUSD Compounder
    "0xc1ec6d26902949bf6cbb0c9859dbead1e87fb243": (VaultFlag.subvault, SUBVAULT),
    # AUSD yVault (Yearn on Katana)
    "0x93fec6639717b6215a48e5a72a162c50dcc40d68": (VaultFlag.subvault, SUBVAULT),
    # Hyperliquidity Trader (HLT) - irregular share price action due to epoch resets
    "0x5a733b25a17dc0f26b862ca9e32b439801b1a8c7": (VaultFlag.abnormal_share_price, HLT_IRREGULAR_SHARE_PRICE),
    # Secured Finance JPYC Lender
    "0x6f6046e59501e484152d46045ba5eecf1cab8935": (VaultFlag.irregular_reporting, IRREGULAR_REPORTING),
    # JPYC (Yearn)
    "0x7a6e3635694952dc00f6ba4d4ad1a7b892028789": (VaultFlag.irregular_reporting, IRREGULAR_REPORTING),
    # Liquid Royalty - ALAR SailOut Royalty vault on Berachain
    "0x09cea16a2563c2d7d807c86f5b8da760389b5915": (None, LIQUID_ROYALTY_NOTE),
    # Liquid Royalty - Senior Vault Master on Berachain
    "0xc38421e5577250eba177bc5bc832e747bea13ee0": (None, LIQUID_ROYALTY_NOTE),
    # sUSDS Lender (Yearn on Ethereum)
    "0x3f2de801629116a83b9734bb72012a554e01cfc1": (VaultFlag.subvault, SUBVAULT),
    # Spark USDS Compounder (Yearn on Ethereum)
    "0xc9f01b5c6048b064e6d925d1c2d7206d4feef8a3": (VaultFlag.subvault, SUBVAULT),
    # Spark USDC Lender (Yearn on Ethereum)
    "0x25f893276544d86a82b1ce407182836f45cb6673": (VaultFlag.subvault, SUBVAULT),
    # Inverse Finance sDOLA vault on Ethereum
    "0xb45ad160634c528cc3d2926d9807104fa3157305": (None, INVERSE_SDOLA_FLASH_LOAN_EXPLOIT),
    # Vault Shares (vUSDC) on Ethereum - unidentified protocol
    # The deployer of this contract was interacting with suspicious token on Ethereum
    "0xaf68a0f0d2d4f82e671578ae6dd6a99de0e84cc6": (VaultFlag.malicious, MALICIOUS_VAULT),
    # Borrowable USDC Deposit, SiloId: 138
    "0x20abecf84ce707c3650b4e8afcf7ea1e22bbcd0c": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable USDC Deposit, SiloId: 127 (Ethereum)
    "0xce6ab1c71981e79cd30052c521c162674251018a": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Teller USDC (Yearn on Base)
    "0x19f233b2953275196e6343f17b76da098c478e21": (VaultFlag.unofficial, MISSING_IN_PROTOCOL_FRONTEND),
    # Borrowable USDC Deposit, SiloId: 27 (Sonic)
    "0x7e88ae5e50474a48dea4c42a634aa7485e7caa62": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Summer.fi USDC (Sonic)
    "0xf06bedaf951aaff253acaa05e391adfbdd6bfbe0": (VaultFlag.illiquid, SUMMER_FI_ILLIQUID),
    # Greenhouse scUSD (Sonic)
    "0x61e175f91f017987c421e0731d6baa0594eca6eb": (VaultFlag.illiquid, GREENHOUSE_ILLIQUID),
    # Mo Earn Max USDC (Morpho on Base)
    "0x3094b241aade60f91f1c82b0628a10d9501462f9": (VaultFlag.illiquid, MO_EARN_MAX_USDC_ILLIQUID),
    # Clearstar Yield USDC (Morpho on Ethereum)
    "0xfa17f7aadbfac2c5d3c8125555404c1ae17df853": (VaultFlag.illiquid, CLEARSTAR_YIELD_USDC_ILLIQUID),
    # Liquity V2 WETH Stability Pool (Ethereum)
    "0xc5e7d3f76a03006540f17668a0267c668ffb5b75": (VaultFlag.illiquid, LIQUITY_V2_WETH_STABILITY_POOL_ILLIQUID),
    # Steakhouse Prime AUSD (Morpho on Katana)
    "0x82c4c641ccc38719ae1f0fbd16a64808d838fdfd": (VaultFlag.illiquid, STEAKHOUSE_PRIME_AUSD_ILLIQUID),
    # Borrowable USDC Deposit, SiloId: 149 (Arbitrum)
    "0xa9a4bd976dbcfc2b89f554467ac85e2c758e2618": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Borrowable USDC Deposit, SiloId: 20 (Sonic)
    "0x322e1d5384aa4ed66aeca770b95686271de61dc3": (VaultFlag.illiquid, XUSD_MESSAGE),
    # Etherealm USDC V1 (Morpho on Ethereum)
    "0x7193794ec82f527efb618ac50c078d348ecba4b6": (VaultFlag.illiquid, ETHEREALM_USDC_ILLIQUID),
    # Etherealm USDC V2 (Morpho on Ethereum)
    "0xb7305d968ecd8a23a13ec01927e3f9588c7653b5": (VaultFlag.illiquid, ETHEREALM_USDC_ILLIQUID),
    # Borrowable USDC Deposit, SiloId: 178 (Ethereum)
    "0x93c8201c35666f9af8b3b943bad67b42ad0159a1": (VaultFlag.illiquid, XUSD_MESSAGE),
    # hyUSD₮0 (hwHLP) - 11 (Hyperliquid)
    "0x2c910f67dbf81099e6f8e126e7265d7595dc20ad": (VaultFlag.illiquid, HYUSDT0_HWHLP_ILLIQUID),
    # Resolv (Euler on Ethereum)
    "0xcbc9b61177444a793b85442d3a953b90f6170b7d": (VaultFlag.illiquid, RESOLV_ILLIQUID),
    # Resolv USDC (Ethereum)
    "0xf0795c47fa58d00f5f77f4d5c01f31ee891e21b4": (VaultFlag.illiquid, RESOLV_USDC_ILLIQUID),
    # Odins Reserve (Centrifuge on Arbitrum)
    "0xeae33e9f53fc405f834d4678e6c07a2523b2126e": (VaultFlag.illiquid, ODINS_RESERVE_ILLIQUID),
    # Mainstreet USDC (msUSDC, Morpho on Ethereum)
    "0xe3ba8f17fe581dd473e6699cfad04502998a57c7": (VaultFlag.malicious, MALICIOUS_VAULT),
    # Mainstreet USDC (msUSDC, Morpho on Ethereum)
    "0xad755c6c31515aef8d2f830767d846774f7e9ea9": (VaultFlag.malicious, MALICIOUS_VAULT),
    # Staked msUSD, Ethereum
    "0x890a5122aa1da30fec4286de7904ff808f0bd74a": (None, MAINST_VAULT),
    # Staked msUSD, Sonic
    "0xc7990369da608c2f4903715e3bd22f2970536c29": (None, MAINST_VAULT),
    # Altura Vault Tokens (AVLT) on Hyperliquid
    "0xd0ee0cf300dfb598270cd7f4d0c6e0d8f6e13f29": (VaultFlag.controversial, CONTROVERSIAL_VAULT),
    # VI-USDC-QA_G (Euler on Sonic)
    "0xd80c3e98c9093b41645c07c2b6d956136f89559b": (VaultFlag.illiquid, VI_USDC_QA_G_ILLIQUID),
    # King RSS USDC Vault (Morpho V1 on Base)
    #
    # Added 2026-09-26 by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``); its decisions
    # file records the evidence.
    #
    # The vault lends about 95% of its assets to one Morpho Blue market against RSS
    # "elephanToken" (0x7a305D07B537359cf468eAea9bb176E5308bC337), which has no DEX pairs and
    # is priced by a custom oracle; the market is 100% borrowed. The Morpho API lists the
    # vault as unlisted with deposits disabled and a short timelock. The reported yield cannot
    # be realised, hence misleading_valuation.
    #
    # - https://tradingstrategy.ai/vaults/king-rss-usdc-vault
    # - https://basescan.org/address/0xf80c0529bd94c773844e459853cd91b9263dd525
    # - Collateral without DEX pairs: https://dexscreener.com/base/0x7a305D07B537359cf468eAea9bb176E5308bC337
    # - Morpho app: https://app.morpho.org/base/vault/0xf80c0529bd94c773844e459853cd91b9263dd525
    "0xf80c0529bd94c773844e459853cd91b9263dd525": (VaultFlag.misleading_valuation, KING_RSS_UNSELLABLE_COLLATERAL),
    # EVK Vault eUSDC-77 (Credifi, Euler EVK on Base)
    #
    # Added 2026-09-30 by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``); its decisions
    # file records the evidence.
    #
    # The pool's only collateral is Credifi CREDIT
    # (0x57aE2FEb25B2251bdD87183Ac53F85d30072C2e2), a bookkeeping token with a single holder
    # and no market, priced at $1 by an unverified oracle
    # (0x6a63a0F0eF5045cdDE9Ed93995255e60fB8A3265) that the vault's governor deployed. The
    # loans are unsecured, reputation-based credit lines of up to $3,000 USDC, and the pool
    # was 100% borrowed with no cash on 2026-09-30 (20,295.68 USDC lent at Base block
    # 51986384). The collateral cannot be valued or sold, hence misleading_valuation.
    #
    # - https://tradingstrategy.ai/vaults/evk-vault-eusdc-77-2
    # - https://base.blockscout.com/address/0xffAABC0bfbCEc0355129E7653C6F1923eC533B66
    # - Collateral token: https://base.blockscout.com/token/0x57aE2FEb25B2251bdD87183Ac53F85d30072C2e2
    # - Oracle: https://base.blockscout.com/address/0x6a63a0F0eF5045cdDE9Ed93995255e60fB8A3265
    # - Collateral without DEX pairs: https://dexscreener.com/base/0x57aE2FEb25B2251bdD87183Ac53F85d30072C2e2
    # - Credifi product: https://credi.fi/
    "0xffaabc0bfbcec0355129e7653c6f1923ec533b66": (VaultFlag.misleading_valuation, CREDIFI_UNSECURED_CREDIT_COLLATERAL),
    # Re7 Labs Cluster AUSD (Euler EVK, Re7 Labs cluster on Avalanche)
    #
    # Added 2026-09-26 by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``); its decisions
    # file records the evidence.
    #
    # The pool accepts Elixir deUSD and sdeUSD as collateral. These collapsed with Stream
    # Finance and Elixir in November 2025, and the pool has been fully borrowed since, with no
    # or almost no cash for withdrawals and a share price that has barely moved for three
    # months (observed in the September 2026 check runs). Depositors cannot exit, hence
    # illiquid.
    #
    # - https://tradingstrategy.ai/vaults/0x2137568666f12fc5a026f5430ae7194f1c1362ab
    # - https://snowscan.xyz/address/0x2137568666f12fc5a026f5430ae7194f1c1362ab
    # - Elixir sunsets deUSD after the Stream Finance unwind: https://www.theblock.co/post/377961/elixir-sunsets-deusd-synthetic-stablecoin-following-stream-finance-unwinding-aims-full-redemptions
    # - Stream and Elixir contagion case study: https://pharos.watch/learn/case-studies/stream-elixir-contagion-2025/
    # - Elixir USDC recovery portal for lenders: https://www.bankless.com/read/news/elixir-launches-usdc-recovery-portal-for-lenders-impacted-by-stream-insolvency
    "0x2137568666f12fc5a026f5430ae7194f1c1362ab": (VaultFlag.illiquid, STREAM_ELIXIR_EULER_BAD_DEBT),
    # Re7 Labs Cluster USDC (Euler EVK, Re7 Labs cluster on Avalanche)
    #
    # Added 2026-09-26 by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``); its decisions
    # file records the evidence.
    #
    # The pool accepts Elixir deUSD and sdeUSD as collateral. These collapsed with Stream
    # Finance and Elixir in November 2025, and the pool has been fully borrowed since, with no
    # or almost no cash for withdrawals and a share price that has barely moved for three
    # months (observed in the September 2026 check runs). Depositors cannot exit, hence
    # illiquid.
    #
    # Depositors report withdrawals failing with E_InsufficientCash on the Euler forum.
    #
    # - https://tradingstrategy.ai/vaults/0x39de0f00189306062d79edec6dca5bb6bfd108f9
    # - https://snowscan.xyz/address/0x39de0f00189306062d79edec6dca5bb6bfd108f9
    # - Stuck funds on the Euler forum: https://forum.euler.finance/t/re7-labs-cluster-usdc-stuck-funds-e-insufficientcash-avalanche/1760
    # - Elixir sunsets deUSD after the Stream Finance unwind: https://www.theblock.co/post/377961/elixir-sunsets-deusd-synthetic-stablecoin-following-stream-finance-unwinding-aims-full-redemptions
    # - Stream and Elixir contagion case study: https://pharos.watch/learn/case-studies/stream-elixir-contagion-2025/
    # - Elixir USDC recovery portal for lenders: https://www.bankless.com/read/news/elixir-launches-usdc-recovery-portal-for-lenders-impacted-by-stream-insolvency
    "0x39de0f00189306062d79edec6dca5bb6bfd108f9": (VaultFlag.illiquid, STREAM_ELIXIR_EULER_BAD_DEBT),
    # Re7 Labs Cluster deUSD (Euler EVK, Re7 Labs cluster on Avalanche)
    #
    # Added 2026-09-26 by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``); its decisions
    # file records the evidence.
    #
    # The pool accepts Elixir deUSD and sdeUSD, and lends deUSD itself, which Elixir sunset as
    # collateral. These collapsed with Stream Finance and Elixir in November 2025, and the
    # pool has been fully borrowed since, with no or almost no cash for withdrawals and a
    # share price that has barely moved for three months (observed in the September 2026 check
    # runs). Depositors cannot exit, hence illiquid.
    #
    # - https://tradingstrategy.ai/vaults/0xa45189636c04388adbb4d865100dd155e55682ec
    # - https://snowscan.xyz/address/0xa45189636c04388adbb4d865100dd155e55682ec
    # - Elixir sunsets deUSD after the Stream Finance unwind: https://www.theblock.co/post/377961/elixir-sunsets-deusd-synthetic-stablecoin-following-stream-finance-unwinding-aims-full-redemptions
    # - Stream and Elixir contagion case study: https://pharos.watch/learn/case-studies/stream-elixir-contagion-2025/
    # - Elixir USDC recovery portal for lenders: https://www.bankless.com/read/news/elixir-launches-usdc-recovery-portal-for-lenders-impacted-by-stream-insolvency
    "0xa45189636c04388adbb4d865100dd155e55682ec": (VaultFlag.illiquid, STREAM_ELIXIR_EULER_BAD_DEBT),
    # Keyring zkVerified Cluster USDC (Euler EVK on Avalanche)
    #
    # Added 2026-09-26 by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``); its decisions
    # file records the evidence.
    #
    # The pool accepts Elixir sdeUSD as collateral. These collapsed with Stream Finance and
    # Elixir in November 2025, and the pool has been fully borrowed since, with no or almost
    # no cash for withdrawals and a share price that has barely moved for three months
    # (observed in the September 2026 check runs). Depositors cannot exit, hence illiquid.
    #
    # The pool's interest rate was set to zero, so lenders earn nothing while their funds are
    # locked.
    #
    # - https://tradingstrategy.ai/vaults/keyring-zkverified-cluster
    # - https://snowscan.xyz/address/0x8f23da78e3f31ab5deb75dc3282198bed630ffde
    # - Keyring cluster launch: https://thedefiant.io/news/defi/keyring-brings-zero-knowledge-id-layer-to-defi-vaults-on-avalanche
    # - Elixir sunsets deUSD after the Stream Finance unwind: https://www.theblock.co/post/377961/elixir-sunsets-deusd-synthetic-stablecoin-following-stream-finance-unwinding-aims-full-redemptions
    # - Stream and Elixir contagion case study: https://pharos.watch/learn/case-studies/stream-elixir-contagion-2025/
    # - Elixir USDC recovery portal for lenders: https://www.bankless.com/read/news/elixir-launches-usdc-recovery-portal-for-lenders-impacted-by-stream-insolvency
    "0x8f23da78e3f31ab5deb75dc3282198bed630ffde": (VaultFlag.illiquid, STREAM_ELIXIR_EULER_BAD_DEBT),
    # Re7 Labs Cluster USD1 (Euler EVK, Re7 Labs cluster on BNB Chain)
    #
    # Added 2026-09-26 by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``); its decisions
    # file records the evidence.
    #
    # The pool accepts Stables Labs USDX and sUSDX as collateral. These collapsed with Stream
    # Finance and Elixir in November 2025, and the pool has been fully borrowed since, with no
    # or almost no cash for withdrawals and a share price that has barely moved for three
    # months (observed in the September 2026 check runs). Depositors cannot exit, hence
    # illiquid.
    #
    # - https://tradingstrategy.ai/vaults/0xc41f2ba7102e9f9f2d603eb951f955ae205ed272
    # - https://bscscan.com/address/0xc41f2ba7102e9f9f2d603eb951f955ae205ed272
    # - Stables Labs USDX collapse analysis: https://beosin.com/resources/analysis-of-the-stables-labs-usdx-collapse-incident-and-fund-flow-tracing
    # - Elixir sunsets deUSD after the Stream Finance unwind: https://www.theblock.co/post/377961/elixir-sunsets-deusd-synthetic-stablecoin-following-stream-finance-unwinding-aims-full-redemptions
    "0xc41f2ba7102e9f9f2d603eb951f955ae205ed272": (VaultFlag.illiquid, STREAM_ELIXIR_EULER_BAD_DEBT),
    # Re7 Labs Cluster USDT (Euler EVK, Re7 Labs cluster on BNB Chain)
    #
    # Added 2026-09-26 by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``); its decisions
    # file records the evidence.
    #
    # The pool accepts Stables Labs USDX and sUSDX as collateral. These collapsed with Stream
    # Finance and Elixir in November 2025, and the pool has been fully borrowed since, with no
    # or almost no cash for withdrawals and a share price that has barely moved for three
    # months (observed in the September 2026 check runs). Depositors cannot exit, hence
    # illiquid.
    #
    # - https://tradingstrategy.ai/vaults/0x69a93dbab609266af96f05658b2e22d020de2e19
    # - https://bscscan.com/address/0x69a93dbab609266af96f05658b2e22d020de2e19
    # - Stables Labs USDX collapse analysis: https://beosin.com/resources/analysis-of-the-stables-labs-usdx-collapse-incident-and-fund-flow-tracing
    # - Elixir sunsets deUSD after the Stream Finance unwind: https://www.theblock.co/post/377961/elixir-sunsets-deusd-synthetic-stablecoin-following-stream-finance-unwinding-aims-full-redemptions
    "0x69a93dbab609266af96f05658b2e22d020de2e19": (VaultFlag.illiquid, STREAM_ELIXIR_EULER_BAD_DEBT),
    # MEV Capital Sonic Cluster (Euler EVK on Sonic)
    #
    # Added 2026-09-26 by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``); its decisions
    # file records the evidence.
    #
    # The vault is denominated in Stream Finance xUSD
    # (0x6202b9f02e30e5e1c62cc01e4305450e5d83b926), which collapsed in November 2025 and
    # traded near $0.02 in September 2026. The vault holds only idle xUSD with no borrowers
    # and earns nothing, while its TVL counts xUSD at $1, hence depegged_denomination_token.
    #
    # - https://tradingstrategy.ai/vaults/mev-capital-sonic-cluster-16
    # - https://sonicscan.org/address/0xdebdab749330bb976fd10dc52f9a452aaf029028
    # - xUSD price: https://www.geckoterminal.com/sonic/tokens/0x6202b9f02e30e5e1c62cc01e4305450e5d83b926
    # - Elixir sunsets deUSD after the Stream Finance unwind: https://www.theblock.co/post/377961/elixir-sunsets-deusd-synthetic-stablecoin-following-stream-finance-unwinding-aims-full-redemptions
    "0xdebdab749330bb976fd10dc52f9a452aaf029028": (VaultFlag.depegged_denomination_token, STREAM_XUSD_DENOMINATED),
    #
    # Review needed: vaults the 2026-09-30 investability checks could not decide
    # consistently. VaultFlag.review_needed does not blacklist; see its docstring.
    #
    # AlphaGrowth Base RWA (Euler EVK pool on Base, curator AlphaGrowth)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: keep, medium confidence. About 22% of assets was
    # redeemable. Almost all borrowing, about $452k, is against Re Protocol reUSD (about
    # $507k posted), a reinsurance-backed token with no DEX market but a primary-market
    # NAV and a quarterly redemption queue; a small part is against wrapped tokenised SPY
    # (wtSPYM, about $11.5k).
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: uncertain, low confidence. The pool accepts
    # reUSD and wrapped tokenised stocks with no DEX liquidity; 25% redeemable, but the
    # collateral pricing could not be verified.
    #
    # To decide: whether reUSD's NAV feed and redemption queue make it acceptable
    # collateral, and whether liquidations can work without a DEX market. TVL about $594k,
    # 1M return about 15%.
    #
    # - https://tradingstrategy.ai/vaults/alphagrowth-base-rwa
    # - https://basescan.org/address/0x4c1aeda9b43efcf1da1d1755b18802aabe90f61e
    # - Euler app: https://app.euler.finance/lend/0x4c1aeda9b43efcf1da1d1755b18802aabe90f61e?network=8453
    # - reUSD: https://pharos.watch/stablecoin/reusd-re-protocol/
    # - wtSPYM markets: https://dexscreener.com/base/0x31c2c14134e6e3b7ef9478297f199331133fc2d8
    "0x4c1aeda9b43efcf1da1d1755b18802aabe90f61e": (VaultFlag.review_needed, REVIEW_NEEDED_OFF_MARKET_COLLATERAL),
    # NetNet Credit (Morpho V2 on Robinhood Chain, curator NetNet)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: uncertain, medium confidence. 34% of assets is
    # lent against wsNET, the curator's own rebasing token, priced by NetNet's bespoke
    # LoopbackOracle, with about $277k of USDG-side DEX liquidity. The tokenised stock
    # markets are 100% borrowed and only about 11% of assets could be withdrawn. The vault
    # is unlisted on Morpho and run by a 1-of-1 Safe, although real borrowers pay the
    # yield and a treasury-backed price floor covers the wsNET debt.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: uncertain, low confidence. Unlisted vault
    # lending against tokenised stocks with unknown oracles in 100% borrowed markets, and
    # against wsNET; only 7% withdrawable.
    #
    # To decide: whether the curator's own wsNET collateral and 1-of-1 curator Safe are
    # acceptable. TVL about $1.21M, 1M return about 14.5%.
    #
    # - https://tradingstrategy.ai/vaults/netnet-credit
    # - https://robinhoodchain.blockscout.com/address/0x99347d5f70d3838763f6bddcf80304c8aa953b57
    # - wsNET oracle: https://robinhoodchain.blockscout.com/address/0xCDE9599059f8Ae6D6B9F33A0aF7877827ec75F16
    # - Curator Safe: https://robinhoodchain.blockscout.com/address/0x3Bb7A23316f82C0e984fA2E784846d8928a35f42
    # - NetNet credit docs: https://docs.netnet.capital/credit
    # - Robinhood stock tokens: https://docs.robinhood.com/chain/stock-tokens/
    "0x99347d5f70d3838763f6bddcf80304c8aa953b57": (VaultFlag.review_needed, REVIEW_NEEDED_OFF_MARKET_COLLATERAL),
    # Liquity Hub (Euler Earn on Ethereum, curator K3 Capital)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``).
    #
    # Sonnet 5.5 run, 2026-09-30 19:45 UTC: uncertain, low confidence. Only 0.014% of
    # the $135k assets is redeemable (about $19) and the pool's median utilisation over
    # 30 days is 99.997%. The pool accepts K3's own sBOLD, BOLD and expired Pendle
    # PT-sBOLD tokens (the PTs have $0 DEX liquidity, BOLD has about $10M); the protocol
    # is real, but no served withdrawals could be verified.
    #
    # To decide: whether the pool is permanently fully borrowed (exclude for exit
    # liquidity) or only tight. TVL about $135k.
    #
    # - https://tradingstrategy.ai/vaults/liquity-hub-3
    # - https://etherscan.io/address/0xc6137bc1378c2396051e06417704d31615f77cb9
    # - Euler app: https://app.euler.finance/lend/0xc6137BC1378c2396051e06417704d31615F77Cb9?network=1
    # - sBOLD: https://liquity.org/blog/sbold---the-on-chain-defi-savings-account
    "0xc6137bc1378c2396051e06417704d31615f77cb9": (VaultFlag.review_needed, REVIEW_NEEDED_EXIT_LIQUIDITY),
    # JPEG Trading x Tenbin RWAs (Euler EVK pool on Ethereum, curator JPEG Trading)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``).
    #
    # Sonnet 5.5 run, 2026-09-30 19:45 UTC: uncertain, low confidence. The pool lends
    # only against Tenbin tGLD, which has about $2.7k of DEX liquidity, and has 0%
    # redeemable liquidity and 100% median utilisation over 30 days. The sibling Euler
    # Earn vault 0x018b86a8... is already under review for the same pool.
    #
    # To decide: whether tGLD has a working primary-market redemption and whether the
    # pool is permanently fully borrowed. TVL about $152k.
    #
    # - https://tradingstrategy.ai/vaults/jpeg-trading-x-tenbin-rwas-2
    # - https://etherscan.io/address/0xb57320b253363bf749d5ce6e66592fdc74cce6f7
    # - Euler app: https://app.euler.finance/lend/0xb57320b253363bf749D5CE6e66592FDC74cce6f7?network=1
    # - tGLD: https://pharos.watch/stablecoin/tgld-tenbin/
    "0xb57320b253363bf749d5ce6e66592fdc74cce6f7": (VaultFlag.review_needed, REVIEW_NEEDED_EXIT_LIQUIDITY),
    # JPEG Trading x Tenbin RWAs (Euler Earn on Ethereum, curator JPEG Trading)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: exclude, no_exit_liquidity, medium confidence.
    # 97% of assets sits in a Tenbin tGLD Euler pool
    # (0xb57320b253363bf749d5ce6e66592fdc74cce6f7) that had been 100% borrowed for 30
    # days, so only about 2.6% (about $4k) could be withdrawn. tGLD is a pre-launch
    # synthetic gold token with fewer than 100 holders, KYC-gated redemption and about
    # $2.7k of DEX liquidity.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: uncertain, low confidence. Same figures, 97%
    # in the tGLD pool at about 100% utilisation, 2.6% redeemable.
    #
    # Not the same vault as the other JPEG Trading x Tenbin RWAs Euler pool that both runs
    # excluded. To decide: whether the tGLD pool is permanently stuck or only temporarily
    # fully borrowed. TVL about $156k.
    #
    # Sonnet 5.5 run, 2026-09-30 19:45 UTC (second review of the same day): uncertain, low
    # confidence. Facts unchanged: 97% of assets in the tGLD pool, 2.6% redeemable, 0%
    # idle over 14 days, 100% utilisation median; tGLD has $2.7k of DEX liquidity. No
    # served withdrawals could be verified, and the sibling pool 0xb57320b2... shows the
    # same pattern.
    #
    # - https://tradingstrategy.ai/vaults/jpeg-trading-x-tenbin-rwas-3
    # - https://etherscan.io/address/0x018b86a893f57a632f90c4a8308353ac938adc01
    # - Euler app: https://app.euler.finance/earn/0x018b86a893f57a632f90c4a8308353ac938adc01?network=1
    # - tGLD: https://pharos.watch/stablecoin/tgld-tenbin/
    # - Underlying tGLD pool: https://etherscan.io/address/0xb57320b253363bf749d5ce6e66592fdc74cce6f7
    "0x018b86a893f57a632f90c4a8308353ac938adc01": (VaultFlag.review_needed, REVIEW_NEEDED_EXIT_LIQUIDITY),
    # Edge UltraYield USDC (Morpho on Base, curator UltraYield)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: keep, medium confidence. 82% of assets is lent
    # against Bedrock uniBTC, thin on Base but with millions of dollars of DEX liquidity
    # on Ethereum and Optimism, priced by a MorphoChainlinkOracleV2 from a uniBTC/BTC rate
    # and Chainlink BTC/USD; about 26% withdrawable.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: uncertain, low confidence. 83% lent against
    # uniBTC with only $334 of DEX liquidity on Base; 25% redeemable, listed on Morpho.
    #
    # The runs disagree on whether uniBTC liquidity on other chains counts. To decide:
    # whether liquidations on Base can work with uniBTC's Base liquidity. TVL about $426k.
    #
    # - https://tradingstrategy.ai/vaults/edge-ultrayield-usdc-3
    # - https://basescan.org/address/0x5435bc53f2c61298167cdb11cdf0db2bfa259ca0
    # - Morpho app: https://app.morpho.org/base/vault/0x5435bc53f2c61298167cdb11cdf0db2bfa259ca0
    # - uniBTC markets: https://dexscreener.com/search?q=uniBTC
    "0x5435bc53f2c61298167cdb11cdf0db2bfa259ca0": (VaultFlag.review_needed, REVIEW_NEEDED_OFF_MARKET_COLLATERAL),
    # RockawayX PT Yield (labelled Euler Earn, on BNB Chain, curator RockawayX)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: uncertain, medium confidence. The vault is a
    # Lista DAO Moolah vault, not Euler Earn, so its protocol label and Euler app link are
    # wrong. Onchain it held $1.59M USDT with 99.5% in a PT-sUSDai-15OCT2026 market, 87%
    # utilised, about 13% of assets redeemable; it looks investable once relabelled.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: uncertain, low confidence. The Euler Earn
    # probe failed, so redeemable liquidity was unknown.
    #
    # To decide: fix the protocol classification (Lista Moolah), then review the PT-sUSDai
    # market. TVL about $1.74M.
    #
    # Sonnet 5.5 run, 2026-09-30 19:45 UTC (second review of the same day): uncertain, low
    # confidence. The Euler Earn probe still fails on withdrawQueue(uint256) decoding, so
    # redeemable liquidity is unknown; 14-day idle share 0% and utilisation 100%. Fix the
    # protocol classification first.
    #
    # - https://tradingstrategy.ai/vaults/rockawayx-pt-yield
    # - https://bscscan.com/address/0xb5a30e1fa2cf3c8dea882124b3ab5a47a27c5dd2
    # - Lista DAO lending: https://lista.org/lending
    "0xb5a30e1fa2cf3c8dea882124b3ab5a47a27c5dd2": (VaultFlag.review_needed, REVIEW_NEEDED_DATA_QUALITY),
    # Clearstar Yield (Euler EVK pool on HyperEVM, curator Clearstar Labs)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: keep, medium confidence. About 3% of assets was
    # redeemable, idle cash reached 29% within the last 14 days, and the pool accepts
    # mostly liquid HYPE, BTC and ETH collateral.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: uncertain, low confidence. Redeemable
    # liquidity was 0.003% at the time, although idle cash reached 29% within 14 days;
    # median utilisation 87%.
    #
    # To decide: whether the near-zero redeemable liquidity is temporary. HypurrFi Earn
    # USDC allocates to this pool. TVL about $310k.
    #
    # Sonnet 5.5 run, 2026-09-30 19:45 UTC (second review of the same day): uncertain, low
    # confidence. Redeemable liquidity 0.0025% of assets, idle cash up to 29% within 14
    # days, median utilisation 87%. Collateral accepted by the pool is mostly liquid HYPE
    # assets, but several accepted tokens (PT-kHYPE, hwHYPE, sUSN, FXRP, syzUSD) have no
    # DEX liquidity. Whether withdrawals were served could not be verified.
    #
    # - https://tradingstrategy.ai/vaults/clearstar-yield-6
    # - https://hyperevmscan.io/address/0xf9bb65e113418292d1a3555515fbd64637a0be18
    # - Euler app: https://app.euler.finance/lend/0xf9bb65e113418292d1a3555515fbd64637a0be18?network=999
    "0xf9bb65e113418292d1a3555515fbd64637a0be18": (VaultFlag.review_needed, REVIEW_NEEDED_EXIT_LIQUIDITY),
    # Hyperithm USDC Degen, renamed Hyperithm USDC Apex (Morpho on Ethereum, curator Hyperithm)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: keep, medium confidence. Listed by Morpho
    # without warnings. It lends against the curator's own Midas mHyperBTC and mHYPER
    # tokenised funds, which have no DEX pools but are priced by primary-market NAV
    # through a verified MetaOracleDeviationTimelock and a Chainlink-style oracle; 21%
    # redeemable.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: uncertain, medium confidence. All assets lent
    # against Midas tokens issued for the curator, with $0 DEX liquidity and 87-90%
    # utilisation; the editor should decide.
    #
    # Sonnet 5.5 run, 2026-09-30 19:50 UTC: uncertain, medium confidence. Facts unchanged:
    # 80% lent against mHyperBTC and 20% against mHYPER, both $0 DEX liquidity, markets
    # 87-90% utilised, 20.9% redeemable. Morpho still lists the vault without warnings.
    # Still undecided whether NAV-priced issuer tokens are acceptable collateral.
    #
    # To decide: whether lending against the curator's own NAV-priced funds is acceptable.
    # TVL about $1.55M.
    #
    # - https://tradingstrategy.ai/vaults/hyperithm-usdc-degen
    # - https://etherscan.io/address/0x777791c4d6dc2ce140d00d2828a7c93503c67777
    # - Morpho app: https://app.morpho.org/ethereum/vault/0x777791c4d6dc2ce140d00d2828a7c93503c67777
    # - mHYPER: https://app.rwa.xyz/assets/mHYPER
    # - Hyperithm NAV update: https://phemex.com/news/article/hyperithm-updates-midas-vaults-nav-confirms-no-drawdowns-75413
    "0x777791c4d6dc2ce140d00d2828a7c93503c67777": (VaultFlag.review_needed, REVIEW_NEEDED_OFF_MARKET_COLLATERAL),
    # YieldNest Max Vaults (Euler EVK pool on Ethereum, curator YieldNest)
    #
    # Added 2026-09-30 by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``) as
    # review_needed. Sonnet 5.5 run, 2026-09-30 19:50 UTC: uncertain, medium confidence.
    # The pool has had 100% utilisation for the last 30 days and redeemable liquidity is
    # 0.00% of its $1.2M assets (idle about $0.000001). Its collateral are YieldNest
    # ynRWAx and ynUSDx, fixed-maturity vaults (ynRWAx matures 15 October 2026) with a
    # Curve secondary market. YieldNest documents instant or queued withdrawals, but no
    # withdrawals served by this pool could be confirmed, so a new depositor may have to
    # wait for borrowers to repay.
    #
    # To decide: whether the borrowers repay at maturity and whether recent withdrawals were served.
    #
    # - https://tradingstrategy.ai/vaults/yieldnest-max-vaults
    # - https://etherscan.io/address/0x7fab04ff2717d9a6b71a51c56c29697179597d40
    # - Euler app: https://app.euler.finance/lend/0x7fab04ff2717d9a6b71a51c56c29697179597d40?network=1
    # - ynRWAx: https://tradingstrategy.ai/vaults/yieldnest-rwa-max
    "0x7fab04ff2717d9a6b71a51c56c29697179597d40": (VaultFlag.review_needed, REVIEW_NEEDED_EXIT_LIQUIDITY),
    # K3 Isolated syzUSD-USDT0 (Euler EVK pool on Monad, curator K3 Capital)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: keep, medium confidence. The pool lends against
    # Yuzu Money's syzUSD, a yield-bearing stablecoin of about $66M market cap with a
    # primary redemption path and a Balancer pool on Monad; about 8% ($0.55M) redeemable.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: uncertain, medium confidence. 100% lent
    # against syzUSD with $0 DEX liquidity on Monad; price source and redemption path not
    # verified.
    #
    # To decide: syzUSD's oracle and redemption path. Monad keeps only recent state, so
    # check the current state. TVL about $6.74M.
    #
    # - https://tradingstrategy.ai/vaults/k3-isolated-syzusd-usdt0
    # - https://monadscan.com/address/0xcf450973dee1ee41cb708496bf73f34324180035
    # - Euler app: https://app.euler.finance/lend/0xcf450973dee1ee41cb708496bf73f34324180035?network=143
    # - syzUSD markets: https://dexscreener.com/monad/0x484be0540aD49f351eaa04eeB35dF0f937D4E73f
    # - syzUSD: https://www.coingecko.com/en/coins/staked-yuzu-usd
    # - Yuzu docs: https://yuzu-money.gitbook.io/yuzu-money/yuzu-alpha/staked-yzusd-syzusd
    "0xcf450973dee1ee41cb708496bf73f34324180035": (VaultFlag.review_needed, REVIEW_NEEDED_OFF_MARKET_COLLATERAL),
    # Clearstar Reactor (Euler EVK pool on Monad, curator Clearstar Labs)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: keep, medium confidence. The pool lends against
    # FXRP, Flare's FAssets XRP token priced from XRP and bridged to Monad, so its missing
    # Monad DEX pools are not a valuation problem; about 7% ($0.42M) redeemable, idle cash
    # up to 32% in the last 14 days.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: uncertain, medium confidence. 100% exposed to
    # FXRP with $0 Monad DEX liquidity; price source not verified.
    #
    # To decide: FXRP's oracle on Monad and whether liquidations can work without Monad
    # DEX liquidity. TVL about $5.8M.
    #
    # - https://tradingstrategy.ai/vaults/clearstar-reactor
    # - https://monadscan.com/address/0x1905eddf5943ef6c92ccf1469bd40fc2cb4a77b0
    # - Euler app: https://app.euler.finance/lend/0x1905eddf5943ef6c92ccf1469bd40fc2cb4a77b0?network=143
    # - FXRP markets: https://dexscreener.com/monad/0xCE6170EA245dC8D1f275A710a062b70f125F0110
    # - FXRP: https://flare.network/news/earnxrp-launches-on-flare-the-first-xrp-denominated-yield-product
    "0x1905eddf5943ef6c92ccf1469bd40fc2cb4a77b0": (VaultFlag.review_needed, REVIEW_NEEDED_OFF_MARKET_COLLATERAL),
    # Clearstar Earn USDC (Euler Earn on Monad, curator Clearstar Labs)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: keep, medium confidence. The Earn vault
    # allocates to Clearstar Reactor, which lends against FXRP, Flare's FAssets XRP token
    # priced from XRP and bridged to Monad, so its missing Monad DEX pools are not a
    # valuation problem; about 7% ($0.42M) redeemable, idle cash up to 32% in the last 14
    # days.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: uncertain, medium confidence. 100% exposed to
    # FXRP with $0 Monad DEX liquidity; price source not verified.
    #
    # To decide: FXRP's oracle on Monad and whether liquidations can work without Monad
    # DEX liquidity. TVL about $5.8M.
    #
    # - https://tradingstrategy.ai/vaults/clearstar-earn-usdc
    # - https://monadscan.com/address/0xe1bca19baa63894d374578320551633320436523
    # - Euler app: https://app.euler.finance/earn/0xe1bca19baa63894d374578320551633320436523?network=143
    # - FXRP markets: https://dexscreener.com/monad/0xCE6170EA245dC8D1f275A710a062b70f125F0110
    # - FXRP: https://flare.network/news/earnxrp-launches-on-flare-the-first-xrp-denominated-yield-product
    "0xe1bca19baa63894d374578320551633320436523": (VaultFlag.review_needed, REVIEW_NEEDED_OFF_MARKET_COLLATERAL),
    # Clearstar OpenEden Hybond (Euler EVK pool on Ethereum, curator Clearstar Labs)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: keep, medium confidence. The pool lends against
    # OpenEden HYBOND, a tokenised BNY short-dated high-yield bond fund with a
    # primary-market NAV and T+4 redemption; about 6.6% redeemable, idle cash up to 26% in
    # the last 14 days.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: uncertain, medium confidence. 100% lent
    # against HYBOND with $0 DEX liquidity; primary-market NAV assumed but not verified;
    # 6.8% redeemable.
    #
    # To decide: whether HYBOND's NAV and redemption make it acceptable collateral. TVL
    # about $2.85M.
    #
    # - https://tradingstrategy.ai/vaults/clearstar-openeden-hybond
    # - https://etherscan.io/address/0xf26c68e6d26f725858e7cc353ee30e43adf0b732
    # - Euler app: https://app.euler.finance/lend/0xf26c68e6d26f725858e7cc353ee30e43adf0b732?network=1
    # - HYBOND docs: https://docs.openeden.com/hybond/introduction
    # - HYBOND announcement: https://openeden.com/news/openeden-bny-hybond-tokenized-high-yield-bond-fund/
    "0xf26c68e6d26f725858e7cc353ee30e43adf0b732": (VaultFlag.review_needed, REVIEW_NEEDED_OFF_MARKET_COLLATERAL),
    # Alpha USDC Forex V2 (Morpho V2 on Ethereum, curator AlphaPing)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: uncertain, medium confidence. Unlisted vault
    # lending all its assets against Morini's carry and basis trade tokens, which have no
    # DEX market and an issuer-pushed NAV; about $80 of instant liquidity, so exits need a
    # penalised force-deallocation. The curator AlphaPing's Alpha USDC Delta V2 vault lost
    # $18M on collapsed collateral in June 2026.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: exclude, no_exit_liquidity, medium confidence.
    # Only $81 withdrawable out of $1.06M, lending against leveraged carry-trade strategy
    # tokens with no DEX pairs.
    #
    # To decide: blacklist as illiquid, or accept Morini's NAV-priced tokens. TVL about
    # $1.06M.
    #
    # Sonnet 5.5 run, 2026-09-30 19:45 UTC (second review of the same day): uncertain,
    # medium confidence. Morpho API reports $81 of vault liquidity on $1.06M, unlisted
    # vault. About 72% ($771k) is lent against Morini CarryTradeUSDTRYLeverage and 28%
    # ($293k) against Morini StockMarketTRBasisTrade, both vault-share tokens of Morini
    # Capital (Piku Finance) with no DEX pairs, priced through a Chainlink-adapter feed
    # proxy (Morpho oracle contracts are the standard MorphoChainlinkOracleV2, but the
    # feeds are issuer-operated). The collateral is a real, documented product rather than
    # a scam, so not blacklisted; to decide whether a depositor can exit without a
    # penalised force-deallocation.
    #
    # - https://tradingstrategy.ai/vaults/alpha-usdc-forex-v2
    # - https://etherscan.io/address/0x153bd1abe60104bd46aa05a27fa12d1346d64a57
    # - Morpho app: https://app.morpho.org/ethereum/vault/0x153bd1abe60104bd46aa05a27fa12d1346d64a57
    # - Morini carry trade token markets: https://dexscreener.com/ethereum/0x2bf11d2E04Bc40daa95c24B8b90EC4F5c57Dd326
    # - Morini Capital: https://morini.capital/
    # - AlphaPing Delta V2 loss: https://finance.yahoo.com/markets/crypto/articles/morpho-blue-vault-faces-18m-071900390.html
    "0x153bd1abe60104bd46aa05a27fa12d1346d64a57": (VaultFlag.review_needed, REVIEW_NEEDED_OFF_MARKET_COLLATERAL),
    # HypurrFi Earn USDC (Euler Earn on HyperEVM, curator HypurrFi)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: keep, medium confidence. About 3% of assets was
    # redeemable through its underlying Euler pool (Clearstar Yield), which accepts mostly
    # liquid HYPE, BTC and ETH collateral and had up to 29% idle cash within 14 days.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: exclude, no_exit_liquidity, medium confidence.
    # Redeemable liquidity 0.003% of assets at about 100% utilisation for 30 days, with no
    # idle cash.
    #
    # The runs read the same pool differently. To decide: whether its illiquidity is
    # temporary. TVL about $254k.
    #
    # Sonnet 5.5 run, 2026-09-30 19:45 UTC (second review of the same day): uncertain, low
    # confidence. Redeemable liquidity 0.003% of assets with 0% idle over 14 days and 100%
    # utilisation median; the vault allocates to the Clearstar Yield pool above, so the
    # verdicts must match.
    #
    # - https://tradingstrategy.ai/vaults/hypurrfi-earn-usdc
    # - https://hyperevmscan.io/address/0xf868a2b30854fe13e26f7ab7a92609ccb6b9c0e1
    # - Euler app: https://app.euler.finance/earn/0xf868a2b30854fe13e26f7ab7a92609ccb6b9c0e1?network=999
    # - Underlying pool: https://tradingstrategy.ai/vaults/clearstar-yield-6
    "0xf868a2b30854fe13e26f7ab7a92609ccb6b9c0e1": (VaultFlag.review_needed, REVIEW_NEEDED_EXIT_LIQUIDITY),
    # Steakhouse PaoTech JPYC (Morpho V2 on Polygon, curator Steakhouse Financial)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: exclude, misleading valuation, high confidence.
    # The reported TVL counts JPYC, a Japanese yen stablecoin trading near $0.0063, at $1:
    # about 6.8M JPYC was reported as $6.8M although the vault held about $43k, and after
    # most deposits were withdrawn on 28-29 September about $10k, all idle.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: keep, medium confidence. Fully liquid
    # according to the Morpho API; the JPYC valuation was not examined.
    #
    # To decide: check how the export values JPYC-denominated vaults; if it counts JPYC at
    # $1, fix the valuation rather than flag the vault. Reported TVL about $1.63M.
    #
    # Sonnet 5.5 run, 2026-09-30 19:45 UTC (second review of the same day): uncertain,
    # medium confidence. The Morpho API now reports total assets of $10.3k, fully liquid,
    # against $1.63M in our export, which confirms the export overstates TVL (JPYC counted
    # at $1). The vault itself looks safe; the valuation needs fixing.
    #
    # - https://tradingstrategy.ai/vaults/steakhouse-paotech-jpyc-3
    # - https://polygonscan.com/address/0xbeef0f82e269760429be6255fa00821b7e4b592a
    # - Morpho app: https://app.morpho.org/polygon/vault/0xbeef0f82e269760429be6255fa00821b7e4b592a
    "0xbeef0f82e269760429be6255fa00821b7e4b592a": (VaultFlag.review_needed, REVIEW_NEEDED_DATA_QUALITY),
    # 9Summits Piku Ecosystem USDC (Morpho V2 on Ethereum, curator 9Summits)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: uncertain, medium confidence. All assets are
    # lent against Morini CarryTradeUSDTRYLeverage, a Midas token for an offchain Turkish
    # lira carry trade with no DEX market, priced by a NAV that a single issuer key
    # pushes; exits depend on Morini funding about $2.5M of pending redemptions, although
    # past redemptions were paid at that price and about 62% could be withdrawn.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: keep, medium confidence. The Morpho API
    # reports withdrawable liquidity and the vault is listed.
    #
    # To decide: whether Morini's issuer-priced carry trade token is acceptable
    # collateral. TVL about $619k.
    #
    # - https://tradingstrategy.ai/vaults/9summits-piku-ecosystem-usdc-2
    # - https://etherscan.io/address/0xc30c60de46dec551b96326cbd05592c9245773ef
    # - Morpho app: https://app.morpho.org/ethereum/vault/0xc30c60de46dec551b96326cbd05592c9245773ef
    # - Morini carry trade token markets: https://dexscreener.com/ethereum/0x2bf11d2E04Bc40daa95c24B8b90EC4F5c57Dd326
    # - Morini Capital: https://morini.capital/
    "0xc30c60de46dec551b96326cbd05592c9245773ef": (VaultFlag.review_needed, REVIEW_NEEDED_OFF_MARKET_COLLATERAL),
    # InfiniFi Markets (Euler Earn on Ethereum, curator infiniFi)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: uncertain, medium confidence. 90% of assets sits
    # in a 99.99% utilised Euler pool that lends only against liUSD-4w, infiniFi's own
    # locked token with no DEX market, priced by infiniFi's own accounting oracle and slow
    # to liquidate, so only the idle 9% could be withdrawn; no known exploit or depeg.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: keep, low confidence. The curator's own vault
    # with 9% redeemable; no scam evidence found.
    #
    # To decide: whether lending against the curator's own locked token is acceptable. TVL
    # about $104k.
    #
    # - https://tradingstrategy.ai/vaults/infinifi-markets
    # - https://etherscan.io/address/0xb4a2fc3adaf3bfa8fcba2a6fdaa200de106b8825
    # - Euler app: https://app.euler.finance/earn/0xb4a2fc3adaf3bfa8fcba2a6fdaa200de106b8825?network=1
    # - How infiniFi works: https://hindenrank.com/blog/how-does-infinifi-work
    "0xb4a2fc3adaf3bfa8fcba2a6fdaa200de106b8825": (VaultFlag.review_needed, REVIEW_NEEDED_OFF_MARKET_COLLATERAL),
    # Morini USDC Emerging Yield (Morpho V2 on Ethereum, curated by Morini)
    #
    # Added 2026-09-30 as review_needed by the vault report investability check
    # (``eth_defi.vault_report.vault_checks``, skill ``check-top-list-vaults``). Two runs
    # on data a few hours apart disagreed or could not decide, so a human must review the
    # vault before it is blacklisted or cleared.
    #
    # Opus 5.5 run, 2026-09-30 09:53 UTC: uncertain, medium confidence. Unlisted vault
    # curated by a single EOA that appears to be Morini itself, lending about 70% of its
    # assets against Morini's own carry and basis trade tokens with no DEX market and an
    # issuer-pushed NAV; past redemptions were paid at that price and about 42% could be
    # withdrawn.
    #
    # Sonnet 5.5 run, 2026-09-30 16:22 UTC: keep, low confidence. Unlisted Morpho V2
    # vault, 42% of assets withdrawable according to the Morpho API.
    #
    # To decide: whether the issuer lending against its own NAV-priced tokens is
    # acceptable. TVL about $399k.
    #
    # - https://tradingstrategy.ai/vaults/morini-usdc-emerging-yield
    # - https://etherscan.io/address/0x58e0f0b81576f23c5f002d949b2bb11a5d2714d6
    # - Morpho app: https://app.morpho.org/ethereum/vault/0x58e0f0b81576f23c5f002d949b2bb11a5d2714d6
    # - Morini carry trade token markets: https://dexscreener.com/ethereum/0x2bf11d2E04Bc40daa95c24B8b90EC4F5c57Dd326
    # - Morini Capital: https://morini.capital/
    "0x58e0f0b81576f23c5f002d949b2bb11a5d2714d6": (VaultFlag.review_needed, REVIEW_NEEDED_OFF_MARKET_COLLATERAL),
}

for addr in VAULT_FLAGS_AND_NOTES.keys():
    assert addr.lower() == addr, f"Vault address must be lowercased: {addr}"
