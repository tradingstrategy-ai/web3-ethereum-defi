"""Vault fee modes."""

import enum
from dataclasses import dataclass

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
    "Lagoon": VaultFeeMode.externalised,
    "Velvet Capital": VaultFeeMode.internalised_skimming,
    "Umami": VaultFeeMode.externalised,
    # Unverified contracts, no open source repo
    # https://arbiscan.io/address/0xd15a07a4150b0c057912fe883f7ad22b97161591#code
    "Peapods": None,
    "Ostium": VaultFeeMode.feeless,
    "Gains": VaultFeeMode.feeless,
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
    # Dolomite fees are internalised through interest rate spreads
    "Dolomite": VaultFeeMode.internalised_skimming,
    # HypurrFi fees are internalised in the share price
    "HypurrFi": VaultFeeMode.internalised_skimming,
    # Fluid fToken fees are internalised through the exchange price mechanism (interest accrual)
    "Fluid": VaultFeeMode.internalised_skimming,
    # USDX Money sUSDX - yield is distributed through value appreciation (internalised)
    # Management and performance fees are both 0%
    "USDX Money": VaultFeeMode.internalised_skimming,
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
    # Yo - has deposit and withdrawal fees, externalised
    "Yo": None,
    # Frax - Fraxlend takes 10% of interest revenue as protocol fee, internalised in share price
    "Frax": VaultFeeMode.internalised_skimming,
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
    # GRVT native vaults - management and performance fees vary per vault (0-4% mgmt, 0-40% perf)
    # Fees are embedded in the LP token price
    # https://help.grvt.io/en/articles/11424466-grvt-strategies-core-concepts
    "GRVT": VaultFeeMode.internalised_skimming,
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
                management=0,
                performance=0,
                deposit=0.0,
                withdraw=0.0,
            )
        else:
            return self


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
    return VAULT_PROTOCOL_FEE_MATRIX.get(vault_protocol_name)
