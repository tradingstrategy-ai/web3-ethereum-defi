"""Frankencoin Shares equity vault support.

FCS wraps FPS one to one and exposes an ERC-4626 interface denominated in
ZCHF. ``totalAssets()`` reports proportional backing equity, while
``convertToAssets()`` uses the bonding curve's marginal reference valuation,
three times backing per share. The inherited historical reader preserves
these independent values. Neither value is an executable redemption quote.

Redemption requires the wrapper to control more than two thirds of FPS votes
and to meet the underlying FPS holding-period gate. The wrapper also imposes
size-dependent redemption discounts. Public transaction support remains
uncertified; a preview alone does not establish that redemption is available.

- Mechanics: https://docs.frankencoin.com/pool-shares/fcs
- Source: https://github.com/Frankencoin-ZCHF/Frankencoin/blob/main/contracts/equity/shares/FCSMintRedeem.sol
- Deployment: https://etherscan.io/address/0xDb861830D9Ae2d1fCF99fA0cfd3973de382B0B5b
"""

# Adapter methods retain the shared VaultBase signatures for static metadata.
# ruff: noqa: ARG002, PLR6301

from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.frankencoin.constants import FRANKENCOIN_SHARES_CHAIN_ID
from eth_defi.erc_4626.vault_protocol.frankencoin.tags import STRATEGY_TAGS
from eth_defi.types import Percent
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags


class FrankencoinSharesVault(ERC4626Vault):
    """Read the Ethereum FCS equity and governance product.

    The underlying reserve bears protocol losses and receives net income.
    FCS therefore has different accounting and exit conditions from svZCHF.
    See https://docs.frankencoin.com/pool-shares for the holder economics.
    """

    @property
    def name(self) -> str:
        return "Frankencoin Shares"

    @property
    def manager_name(self) -> str:
        return "Frankencoin"

    @property
    def short_description(self) -> str:
        return "Loss-bearing equity in Frankencoin's reserve pool, earning net revenue from collateralised ZCHF borrowing against crypto and tokenised real-world assets."

    @property
    def description(self) -> str:
        return (
            "[Frankencoin Shares](https://docs.frankencoin.com/pool-shares) represent loss-bearing equity participation in the reserve behind the ZCHF Swiss franc stablecoin. "
            "Each FCS wraps one underlying FPS equity share and confers time-weighted governance votes. Net income increases the shared backing capital, while expenses and losses reduce it.\n\n"
            "The underlying revenue streams, expenses and collateral exposure are:\n\n"
            "- **Borrowing income:** Borrowers lock collateral and mint ZCHF. The [position terms](https://docs.frankencoin.com/positions/open) charge interest up front for the remaining term; "
            "newer positions use the global borrowing rate plus a position-specific risk premium. The fee accrues to equity, while retained minter reserves are accounted separately.\n"
            "- **Position proposal fees:** The [MintingHub contract](https://github.com/Frankencoin-ZCHF/Frankencoin/blob/8b4c4ab67bb361b91d58c474b87f4608fc4c0566/contracts/minting/MintingHub.sol) "
            "collects a 1,000 ZCHF fee into equity when a new position is proposed. Cloning an existing position does not incur another opening fee.\n"
            "- **Minting module application fees:** The amount forwarded to ZCHF increases equity. The [MinterGovernance contract](https://github.com/Frankencoin-ZCHF/Frankencoin/blob/8b4c4ab67bb361b91d58c474b87f4608fc4c0566/contracts/equity/shares/MinterGovernance.sol) "
            "can retain part of an application payment to refill its enforcement reward pool, so the entire payment need not become shareholder income.\n"
            "- **Liquidation gains and losses:** [Reserve settlement](https://docs.frankencoin.com/reserve) releases assigned minter reserves and settles collateral sale proceeds, debt and challenger rewards. "
            "The net result can increase equity or consume its capital; some excess proceeds can also go to the borrower.\n"
            "- **Share entry and exit costs:** The [Equity contract](https://github.com/Frankencoin-ZCHF/Frankencoin/blob/8b4c4ab67bb361b91d58c474b87f4608fc4c0566/contracts/equity/Equity.sol) "
            "applies a 0.3% entry fee and a nominal 0.3% share-based exit fee, retaining backing for remaining shareholders. "
            "The [FCS accounting contract](https://github.com/Frankencoin-ZCHF/Frankencoin/blob/8b4c4ab67bb361b91d58c474b87f4608fc4c0566/contracts/equity/shares/FCSMintRedeem.sol) "
            "returns its additional redemption discount to Equity. Share subscriptions themselves are capital contributions, rather than operating revenue.\n"
            "- **Savings expense:** [Savings interest](https://docs.frankencoin.com/savings) is paid from equity and reduces the income available to FCS holders. "
            "Referral payments split the same gross interest between savers and referrers.\n"
            "- **Lending and real-world-asset exposure:** The [collateral catalogue](https://app.frankencoin.com/monitoring/collateral) includes crypto assets and tokenised gold such as "
            "[physically backed PAXG](https://www.paxos.com/pax-gold). FCS has indirect exposure to these borrowing activities through FPS equity; collateral remains in borrower positions.\n\n"
            "[Protocol redemption](https://docs.frankencoin.com/pool-shares/fcs) depends on the wrapper's voting power and holding duration and applies fees and size-dependent discounts. "
            "Secondary-market prices and liquidity can differ from the protocol's reference valuation. FCS does not promise a fixed yield or a separate cash distribution."
        )

    @cached_property
    def vault_contract(self) -> Contract:
        """Bind the verified FCS interface to the share-token address.

        The deployment is a non-proxy contract; the ABI includes its inherited
        ERC-4626 and governance methods.

        :return: Deployed FCS contract using the shared ABI loader.
        """
        return get_deployed_contract(self.web3, "frankencoin/FCS.json", self.address)

    @cached_property
    def equity_contract(self) -> Contract:
        """Bind the underlying FPS contract selected by FCS.

        ``FPS1()`` identifies the reserve whose voting and holding-period
        state controls ZCHF exits.

        :return: Deployed underlying Equity contract.
        """
        address = self.vault_contract.functions.FPS1().call(block_identifier=self._get_block_identifier())
        return get_deployed_contract(self.web3, "frankencoin/Equity.json", address)

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return equity and underlying lending and collateral classifications.

        Same-address contracts on another chain and unreviewed addresses have
        no maintained classification. Return a copy to protect the mapping.

        :return: Strategy tags, or ``None`` for an unreviewed deployment.
        """
        if self.chain_id != FRANKENCOIN_SHARES_CHAIN_ID:
            return None
        return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)

    def fetch_share_price(self, block_identifier: BlockIdentifier) -> Decimal:
        """Fetch the bonding curve reference price in ZCHF.

        The inherited ratio of backing to supply is not the FCS reference
        valuation. Use the contract conversion, matching the historical
        reader, and retain ``totalAssets()`` independently as backing TVL.
        See https://docs.frankencoin.com/pool-shares/fcs.

        :param block_identifier: Block at which to read the reference price.
        :return: ZCHF reference value of one FCS, before transaction costs.
        """
        one_share = self.share_token.convert_to_raw(Decimal(1))
        raw_amount = self.vault_contract.functions.convertToAssets(one_share).call(block_identifier=block_identifier)
        return self.denomination_token.convert_to_decimals(raw_amount)

    def get_fee_mode(self) -> VaultFeeMode:
        """Describe the entry and exit costs deducted during transactions.

        The reference price excludes these transaction costs, so they must
        not inherit the savings product's fee accounting.

        :return: Externalised transaction fees.
        """
        return VaultFeeMode.externalised

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return the absence of a recurring management fee.

        The common base requires each adapter to declare this rate explicitly.

        :param block_identifier: Unused block retained for the common interface.
        :return: Zero management fee.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return the absence of a performance fee on FCS appreciation.

        Equity changes reflect protocol results and capital flows. There is no
        manager charging a percentage of those changes.

        :param block_identifier: Unused block retained for the common interface.
        :return: Zero performance fee.
        """
        return 0.0

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return the underlying curve's entry fee.

        ``Equity.calculateShares()`` excludes 0.3% of the ZCHF investment
        before calculating the shares minted.

        :param block_identifier: Unused block retained for the common interface.
        :return: Entry fee fraction, ``0.003``.
        """
        return 0.003

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return the underlying curve's nominal redemption fee.

        Equity reduces redeemed shares by 0.3% before applying its curve.
        FCS additionally applies the size- and activity-dependent discount,
        which is outside this single fee field.

        :param block_identifier: Unused block retained for the common interface.
        :return: Nominal exit fee fraction, ``0.003``.
        """
        return 0.003

    def has_custom_fees(self) -> bool:
        """Flag the additional variable redemption discount.

        A single withdrawal-fee rate cannot express the discount curve and
        its seven-day recent-redemption decay.

        :return: ``True`` because standard fee fields are incomplete.
        """
        return True

    def fetch_redemption_closed_reason(self) -> str | None:
        """Explain the wrapper's current ZCHF redemption restriction.

        Query the actual gates rather than zero-address ``maxRedeem()``,
        which returns zero even for an open vault with no zero-address shares.

        :return: Closure reason, or ``None`` when both gates are satisfied.
        """
        block = self._get_block_identifier()
        if not self.vault_contract.functions.isBinding().call(block_identifier=block):
            return "FCS does not control more than two thirds of underlying FPS voting power"
        if not self.equity_contract.functions.canRedeem(self.address).call(block_identifier=block):
            return "The FCS wrapper has not met the underlying FPS average holding-period requirement"
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Link to the protocol's equity investment interface.

        This is the FCS product page, separate from the savings interface.

        :param referral: Unused referral argument retained for compatibility.
        :return: Frankencoin equity application URL.
        """
        return "https://app.frankencoin.com/equity"

    def get_notes(self) -> str:
        """Describe the valuation and liquidity limitations in scan metadata.

        These notes distinguish backing capital from reference valuation and
        executable exits without assigning a fixed personal redemption wait.

        :return: Product-specific accounting and redemption notes.
        """
        return "The reference share price is three times backing equity per share; TVL reports only proportional backing equity. Redemption quotes include curve effects, fees and variable discounts and can be non-zero while redemption is disabled. The 90-day FPS condition applies to the wrapper, not each FCS holder. See https://docs.frankencoin.com/pool-shares/fcs."

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Label reference-price and backing-TVL observations explicitly.

        Both values come from contract state but represent different economic
        quantities. This hook adds private metadata without changing Parquet.

        :return: Scanner metadata describing the two valuation fields.
        """
        return {"_share_price_type": "bonding_curve_reference", "_tvl_type": "proportional_equity"}
