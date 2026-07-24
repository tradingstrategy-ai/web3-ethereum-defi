"""Vault fee modes."""

import enum
import math
from dataclasses import dataclass
from numbers import Real

from eth_typing import HexAddress


class VaultFeeMode(enum.Enum):
    """How vault protocol account its fees.

    - Externalised fees: fees are deducted from the redemption amount when user withdraws.
    - Internalised fees: fees are baked into the share price (asset amount) and continuously taken from the profit.
      There are no fees on withdraw.
    """

    #: Vault fees are baked into the share price (asset amount).
    #:
    #: Fees are taken from the profit at the moment profit is made,
    #: and send to another address.
    #:
    #: Example protocols: Yearn, Harvest Finance, USDAi.
    internalised_skimming = "internalised_skimming"

    #: Vault fees are baked into the share price (asset amount).
    #:
    #: Fees are taken from the profit at the moment profit is made.
    #: and corresponding number of shares is minted to the vault owner.
    #:
    #: Example protocols: AUTO Finance
    internalised_minting = "internalised_minting"

    #: Vault fees are taken from the user explicitly at the redemption time.
    #:
    #: Example protocols: Lagoon Finance.
    externalised = "externalised"

    #: This protocol has no fees.
    feeless = "feeless"

    def is_internalised(self) -> bool:
        """Are the fees internalised in the share price?"""
        return self != VaultFeeMode.externalised


#: Different vault fee extraction methods by different protocols
#:
#: See :py:func:`eth_defi.erc_4626.core.get_vault_protocol_name` for the names list.
#:
VAULT_PROTOCOL_FEE_MATRIX = {
    "Euler": VaultFeeMode.internalised_skimming,
    "Morpho": VaultFeeMode.internalised_skimming,
    "Enzyme": VaultFeeMode.internalised_skimming,
    # ``get_vault_protocol_name()`` returns this display name for
    # ``lagoon_like`` vaults. Keep the historical short matrix key as a
    # compatibility alias for callers of ``get_vault_fee_mode()``.
    "Lagoon Finance": VaultFeeMode.externalised,
    "Lagoon": VaultFeeMode.externalised,
    # T3tris fees are represented as fee shares. Entry/exit fees reduce the
    # user's net deposit/redeem, while management and high-water-mark
    # performance fees accrue as shares for the fee recipient.
    # https://github.com/t3tris-finance/mdoc-t3tris/blob/main/docs/en/02-liquidity-providers/05-understanding-fees.md
    "T3tris": VaultFeeMode.internalised_minting,
    # Bulla Factoring's V2.1 reconciliation adds only LP net interest to its
    # capital account; administrator fees and invoice-specific underwriting
    # spreads accrue separately before ERC-4626 share value is calculated.
    # The protocol fee is also reserved when the invoice is funded, not at LP
    # deposit or redemption. Thus a holder receives a fees-net share value:
    # internalised skimming. The native Bulla adapter exposes the individual
    # components. Source: https://arbiscan.io/address/0xc099773267308D8e9E805f47EABf9ab13bBc9e37#code
    "Bulla Network": VaultFeeMode.internalised_skimming,
    # Kinexys ODA-FACT JLTXX prospectus expenses are reflected in fund returns, not as
    # explicit on-chain deposit/withdrawal fees.
    "Kinexys": VaultFeeMode.internalised_skimming,
    # Midas NAV/share is published net of product-level expenses through the
    # Midas oracle pipeline. Instant issuance/redemption fees are read
    # separately from product vault contracts.
    "Midas": VaultFeeMode.internalised_skimming,
    # Fund fees are internalised in NAV; request fees are read from AoABTManager.
    "Asseto": VaultFeeMode.internalised_skimming,
    # Benji token contracts do not expose a fund fee schedule.
    "Franklin Templeton": None,
    # Product fee schedules are not published by the reviewed CMTAT token contracts.
    "Libeara": None,
    # USTBL's disclosed management cost is reflected in issuer-published NAV.
    "Spiko": VaultFeeMode.internalised_skimming,
    # FILQ token contracts do not expose product-level fees.
    "Sygnum": None,
    # Theo does not publish a universal product-level thBILL fee schedule.
    "Theo": None,
    # DSToken contracts do not expose product-level fund fees.
    "Securitize": None,
    # USDY and OUSG reflect issuer fund expenses in the published NAV. OUSG's
    # documented management fee is surfaced per product by the Ondo adapter.
    "Ondo": VaultFeeMode.internalised_skimming,
    # USYC subscriptions and redemptions charge Teller fees; the product also
    # discloses a performance fee that is reflected in the NAV/share price.
    "Circle USYC": VaultFeeMode.externalised,
    # WTGXX's published annual expense ratio is reflected in the fund NAV.
    "WisdomTree": VaultFeeMode.internalised_skimming,
    # USTB product-level fees are governed by fund documents, not exposed by
    # the permissioned token contract.
    "Superstate": None,
    # wstGBP applies mint and redemption spreads through mintcost() and
    # burncost(), reducing the user's issued shares or redeemed assets.
    "wstGBP": VaultFeeMode.externalised,
    # Vault Street's 0.5% protocol fee accrues daily and is deducted from the
    # primeUSD vault. The product page lists a 0% performance fee.
    # https://app.vaultstreet.com/
    "Vault Street": VaultFeeMode.internalised_skimming,
    "Velvet Capital": VaultFeeMode.internalised_skimming,
    "Umami": VaultFeeMode.externalised,
    # Unverified contracts, no open source repo
    # https://arbiscan.io/address/0xd15a07a4150b0c057912fe883f7ad22b97161591#code
    "Peapods": None,
    "Ostium": VaultFeeMode.feeless,
    "Gains": VaultFeeMode.feeless,
    "KiloEx": None,
    # Kiln combines a fixed asset-denominated deposit fee with a reward fee
    # collected by minting shares. This mixed model has no single enum value.
    # Per-vault values are read by KilnVault.
    "Kiln": None,
    "Domination Finance": VaultFeeMode.feeless,
    "Plutus": VaultFeeMode.internalised_skimming,
    "Harvest Finance": VaultFeeMode.internalised_skimming,
    "D2 Finance": VaultFeeMode.internalised_skimming,
    "Untangle Finance": VaultFeeMode.externalised,
    "Yearn": VaultFeeMode.internalised_skimming,
    "Goat Protocol": VaultFeeMode.internalised_skimming,
    "USDai": VaultFeeMode.internalised_skimming,
    "AUTO Finance": VaultFeeMode.internalised_minting,
    "NashPoint": VaultFeeMode.internalised_skimming,
    "Llama Lend": VaultFeeMode.internalised_skimming,
    "Summer.fi": VaultFeeMode.internalised_minting,
    "Silo Finance": VaultFeeMode.internalised_minting,
    "Sky": VaultFeeMode.feeless,
    "cSigma Finance": VaultFeeMode.feeless,
    # 3Jane - credit money market with no explicit deposit/withdrawal/redemption fees;
    # suppliers earn the net borrower interest, taken via the interest spread and
    # internalised in the share price (https://docs.3jane.xyz/usd3-susd3/suppliers)
    "3Jane": VaultFeeMode.internalised_skimming,
    "Ethena": VaultFeeMode.feeless,
    "Term Finance": VaultFeeMode.internalised_skimming,
    "Royco": None,
    "ETH Strategy": VaultFeeMode.feeless,
    # Yuzu Money has no performance fee, uses yield-smoothing mechanism instead
    # https://yuzu-money.gitbook.io/yuzu-money/faq-1/performance-fee
    "Yuzu Money": VaultFeeMode.feeless,
    # Altura has minimal exit fees (0.01%) on instant withdrawals only, no management/performance fees
    "Altura": VaultFeeMode.feeless,
    # Gearbox has fees internalised in share price via APY spread between borrower and lender rates
    "Gearbox": VaultFeeMode.internalised_skimming,
    "Mainstreet Finance": None,
    "YieldFi": None,
    "Resolv": VaultFeeMode.feeless,
    # Curvance uses interest-based fees (up to 60% of borrower interest) which are internalised
    # Depositors don't pay explicit fees - the protocol earns from borrower interest spread
    "Curvance": VaultFeeMode.internalised_skimming,
    # Singularity Finance fees are internalised in the share price via minting shares
    "Singularity Finance": VaultFeeMode.internalised_minting,
    "Brink": None,
    # Accountable fees are internalised in the share price
    "Accountable": VaultFeeMode.internalised_skimming,
    "YieldNest": None,
    # Secured Finance charges protocol trading fees on the underlying fixed-rate lending actions.
    # These costs are incurred at trade execution time instead of as explicit vault deposit/withdraw fees.
    "Secured Finance": VaultFeeMode.internalised_skimming,
    # Dolomite fees are internalised through interest rate spreads
    "Dolomite": VaultFeeMode.internalised_skimming,
    # HypurrFi fees are internalised in the share price
    "HypurrFi": VaultFeeMode.internalised_skimming,
    # Fluid fToken fees are internalised through the exchange price mechanism (interest accrual)
    "Fluid": VaultFeeMode.internalised_skimming,
    # USDX Money sUSDX - yield is distributed through value appreciation (internalised)
    # Management and performance fees are both 0%
    "USDX Money": VaultFeeMode.internalised_skimming,
    # NaraUSD+ does not publish a universal management or performance fee schedule.
    "Nara": None,
    # Hyperlend WHLP - 10% performance fee on yield, internalised in share price
    "Hyperlend": VaultFeeMode.internalised_skimming,
    # Sentiment SuperPools - fees taken from interest earned
    "Sentiment": VaultFeeMode.internalised_skimming,
    # infiniFi - fees are internalised via epoch-based reward distribution
    "infiniFi": None,
    # Renalta - unverified contract, fee mode unknown
    "Renalta": None,
    # Avant - no explicit fees, yield distributed through rewards vesting
    "Avant": VaultFeeMode.feeless,
    # aarnâ - fee mode unknown
    "aarnâ": None,
    # Aera - TVL fees and Yearn TokenizedStrategy performance fees are reflected in share price/totalAssets()
    "Aera": VaultFeeMode.internalised_skimming,
    # Yo - has deposit and withdrawal fees, externalised
    "Yo": None,
    # Frax - Fraxlend's per-pair protocol share of interest is read on-chain and
    # internalised by minting fee shares, diluting lenders.
    # FraxStakingVault overrides this protocol default because reviewed sFRAX/sfrxUSD
    # staking deployments have no explicit vault fees.
    "Frax": VaultFeeMode.internalised_minting,
    # Hyperdrive - fee mode unknown (unverified contracts)
    "Hyperdrive": None,
    # BaseVol - fee mode unknown
    "BaseVol": None,
    # sBOLD - yield accrues through stability pool rewards, no external fees
    "sBOLD": VaultFeeMode.internalised_skimming,
    # Hyperliquid native vaults - leader commission is taken from PnL, share price already reflects it
    "Hyperliquid": VaultFeeMode.internalised_skimming,
    # Ember - management and performance fees are embedded in the vault rate updates (internalised)
    # https://learn.ember.so/ember-protocol/core-concepts
    "Ember": VaultFeeMode.internalised_skimming,
    # GRVT native vaults - management fees (0-4%) are internalised via daily share minting,
    # but performance fees (0-40%) are charged at redemption (externalised) and NOT reflected
    # in the share price. We use externalised because the share price is gross of perf fees.
    # Per-vault fee percentages are fetched from the public GraphQL API at edge.grvt.io/query.
    # https://help.grvt.io/en/articles/11424466-grvt-strategies-core-concepts
    # https://help.grvt.io/en/articles/11640733-strategy-setup-guide-how-to-configure-fees-redemptions-and-rewards-on-grvt
    "GRVT": VaultFeeMode.externalised,
    # Lighter pools - operator fee is a performance fee taken from PnL,
    # already reflected in share prices (internalised skimming).
    # Per-pool operator fees range from 0% (LLP) to variable amounts for user pools.
    "Lighter": VaultFeeMode.internalised_skimming,
    # Hibachi - all vault-level fees are zero (management, performance, deposit, withdrawal)
    # Platform charges trading taker fees and deposit/withdrawal fees at exchange level
    "Hibachi": VaultFeeMode.feeless,
    # ApeX exposes raw fee-like fields, but their units and application are
    # not documented authoritatively by the public vault API.
    "ApeX": None,
    # Liquid Royalty has no management/performance fees, but 20% early withdrawal penalty within 7-day cooldown
    "Liquid Royalty": VaultFeeMode.feeless,
    # Inverse Finance sDOLA - no explicit fees, yield via DBR auction mechanism
    "Inverse Finance": VaultFeeMode.feeless,
    # 40acres - lender premium (20% of weekly veNFT rewards) is transferred as USDC directly to the vault,
    # increasing totalAssets() and share price. No entry/exit fees. epochRewardsLocked() vests over 7-day epoch.
    # https://github.com/40-Acres/loan-contracts/blob/main/src/LoanV2.sol
    "40acres": VaultFeeMode.internalised_skimming,
    # IPOR Fusion - management fees (up to 5%/yr on AUM) and performance fees (up to 50%, high-water mark)
    # are both collected by minting new vault shares to FeeAccount contracts, which the FeeManager then
    # distributes between IPOR DAO and atomist-defined recipients. From an investor's perspective fees
    # are internalised in the share price through dilution — there is no fee charged at redemption.
    # https://deepwiki.com/IPOR-Labs/ipor-fusion/6.2-fee-distribution
    "IPOR Fusion": VaultFeeMode.internalised_minting,
    # ForgeYields - 20% daily performance fee internalised into the share price via pps updates
    "ForgeYields": VaultFeeMode.internalised_skimming,
    # CrystalClear - 20% performance fee charged at redemption (externalised)
    # Fee is deducted from withdrawal proceeds via WithdrawalClaimed event
    "CrystalClear": VaultFeeMode.externalised,
    # Aave - v4 Tokenization Spoke has no explicit deposit/withdrawal or performance fee.
    # Suppliers earn the Hub interest spread; Aave protocol revenue is taken as a reserve
    # factor on borrow interest, so yield is internalised in the Hub-derived share price.
    "Aave": VaultFeeMode.internalised_skimming,
    # Frankencoin svZCHF vault yield accrues in the savings module share price.
    # Optional per-account referral fees can skim up to 25% of earned interest,
    # but there are no protocol-wide deposit, withdrawal, management or performance fees.
    "Frankencoin": VaultFeeMode.internalised_skimming,
    # Mellow Core Vaults - FeeManager charges deposit, redeem, performance and
    # time-based protocol fees in vault shares. Performance/protocol fees are
    # minted as shares, while deposit/redeem fees reduce user share amounts.
    "Mellow": VaultFeeMode.internalised_minting,
    # Atoma has mixed fees. The 20% high-water-mark performance fee is minted as
    # operator shares when NAV rises, so it is internalised through dilution. The
    # separate 0.5% withdrawal fee is still exposed through FeeData.withdraw and
    # preserved by FeeData.get_net_fees().
    "Atoma": VaultFeeMode.internalised_minting,
}


@dataclass
class FeeData:
    """Track vault fee parameters

    - Offer methods to calculate gross/net fees based on the vault fee mode
    - `None` means fee unknown: protocol not recognized, or fee data not available

    **How fees are presented**:

    - **Gross fees** are what vaults track internally. They are not exposed to an investor,
      and only useful for internal profit calculations of the vault. Gross fees have
      already been deducted when the vault share price is updated.

    - **Net fees** are deduced at a redemption. A vault investor receives less than the value of their shares back.

    - For comparing the profitability of vaults, you need to reduce the net fees of an investment period
      from the vault share price.

    - Common vault fee mechanisms implementations are: externalised (net fees, deducted from an investor at a redemption),
       skimming (redirected from profits at the time of trade) and minting (new shares minted to the vault owner at the time of trade).
    """

    #: Determines is the vault share price is fees-net or fees-gross
    fee_mode: VaultFeeMode | None

    #: Fee for this class
    management: float | None

    #: Fee for this class
    performance: float | None

    #: Fee for this class
    deposit: float | None

    #: Fee for this class
    withdraw: float | None

    def __post_init__(self) -> None:
        """Validate and normalise fee values at the metadata ingestion boundary.

        Vault return calculations operate on floats. In particular, allowing a
        :class:`decimal.Decimal` into this dataclass causes a later
        ``Decimal * float`` failure after the metadata has been persisted in
        the vault database. Accept real numeric values so existing integer
        zeroes and NumPy floats remain supported, then retain all known fees
        as Python floats.

        :raises AssertionError:
            If a fee is not a real number or ``None``.
        :raises ValueError:
            If a fee is not finite or outside the inclusive ``[0, 1]`` range.
        """
        for field_name in ("management", "performance", "deposit", "withdraw"):
            fee = getattr(self, field_name)
            assert fee is None or (isinstance(fee, Real) and not isinstance(fee, bool)), f"FeeData.{field_name} must be a real number or None, got {type(fee)}"
            if fee is not None:
                normalised_fee = float(fee)
                if not math.isfinite(normalised_fee) or not 0 <= normalised_fee <= 1:
                    raise ValueError(f"FeeData.{field_name} must be between 0 and 1 inclusive, got {fee}")
                setattr(self, field_name, normalised_fee)

    @property
    def internalised(self) -> bool | None:
        if self.fee_mode is None:
            return None

        return self.fee_mode.is_internalised() if self.fee_mode else None

    def get_net_fees(self) -> "FeeData":
        """Get net fees paid by the user on deposit/withdraw.

        - Determined by the vault fee mode
        """
        if self.internalised:
            return FeeData(
                fee_mode=self.fee_mode,
                management=0.0,
                performance=0.0,
                deposit=self.deposit,
                withdraw=self.withdraw,
            )
        else:
            return self

    def can_calculate_investor_net_performance(self) -> bool:
        """Check whether fee data is sufficient for investor net-return calculations.

        Net return calculations require a known fee mode and every fee that can
        affect an investor's deposit-to-redemption return.  In particular,
        unknown values must not be treated as zero fees.

        :return:
            ``True`` when the fee mode and all investor-facing fees are known.
        """
        return self.fee_mode is not None and all(
            fee is not None
            for fee in (
                self.management,
                self.performance,
                self.deposit,
                self.withdraw,
            )
        )


#: Could not read fee data from the smart contract / unsupported protocol
BROKEN_FEE_DATA = FeeData(
    fee_mode=None,
    management=None,
    performance=None,
    deposit=None,
    withdraw=None,
)


def get_vault_fee_mode(vault_protocol_name: str, address: HexAddress | str) -> VaultFeeMode | None:
    """Get vault fee mode by protocol name.

    :return:
        None if unknown
    """
    del address

    return VAULT_PROTOCOL_FEE_MATRIX.get(vault_protocol_name)
