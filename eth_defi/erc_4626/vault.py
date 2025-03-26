"""Generic ECR-4626 vault reader implementation."""
import datetime
from decimal import Decimal
from functools import cached_property
from typing import Iterable

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.types import BlockIdentifier

from eth_defi.abi import get_deployed_contract
from eth_defi.balances import fetch_erc20_balances_fallback
from eth_defi.event_reader.multicall_batcher import MulticallWrapper, EncodedCall, EncodedCallResult
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.vault.base import VaultBase, VaultSpec, VaultInfo, TradingUniverse, VaultPortfolio, VaultFlowManager, VaultSharePriceReader, VaultHistoricalReader, VaultHistoricalRead


class ERC4626VaultInfo(VaultInfo):
    """Capture information about ERC- vault deployment."""

    #: The ERC-20 token that nominates the vault assets
    address: HexAddress

    #: The address of the underlying token used for the vault for accounting, depositing, withdrawing.
    #:
    #: E.g. USDC.
    #:
    asset: HexAddress


class ERC4626SharePriceReader(MulticallWrapper):

    def __init__(self):
        pass


class ERC4626HistoricalReader(VaultHistoricalReader):
    """Support reading historical vault share prices.

    - Allows to construct historical returns
    """

    def __init__(self, vault: "ERC4626Vault"):
        self.vault = vault

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Get the onchain calls that are needed to read the share price."""
        amount = self.vault.denomination_token.convert_to_raw(Decimal(1))
        share_price_call = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.convertToShares(amount),
            extra_data = {
                "function": "share_price",
                "vault": self.vault.address,
            }
        )

        yield share_price_call

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:

        call_by_name = {r.call.extra_data["function"] for r in call_results}
        raw_share_price = call_by_name["share_price"]

        return VaultHistoricalRead(
            vault_address=self.address,
            block_number=block_number,
            timestamp=timestamp,
            share_price=raw_share_price,
            total_assets=0,
            total_supply=0,
            performance_fee=0,
            management_fee=0,
        )


class ERC4626Vault(VaultBase):
    """ERC-4626 vault adapter

    - Metadata
    - Deposit and redeem from the vault
    - Vault price reader

    - `Find the interface here <https://github.com/OpenZeppelin/openzeppelin-contracts/blob/master/contracts/token/ERC20/extensions/ERC4626.sol>`__
    """

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
    ):
        self.web3 = web3
        self.spec = spec

    @property
    def chain_id(self) -> int:
        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Get the vault smart contract address."""
        return self.vault_address

    @cached_property
    def vault_address(self) -> HexAddress:
        return Web3.to_checksum_address(self.spec.vault_address)

    @property
    def name(self) -> str:
        return self.share_token.name

    @property
    def symbol(self) -> str:
        return self.share_token.symbol

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_contract(
            self.web3,
            "lagoon/IERC4626.json",
            self.spec.vault_address,
        )

    @property
    def underlying_token(self) -> TokenDetails:
        """Alias for :py:meth:`denomination_token`"""
        return self.denomination_token

    def fetch_denomination_token(self) -> TokenDetails:
        token_address = self.info["asset"]
        return fetch_erc20_details(self.web3, token_address, chain_id=self.spec.chain_id)

    def fetch_share_token(self) -> TokenDetails:
        token_address = self.info["address"]
        return fetch_erc20_details(self.web3, token_address, chain_id=self.spec.chain_id)

    def fetch_vault_info(self) -> dict:
        """Get all information we can extract from the vault smart contracts."""
        vault = self.vault_contract
        #roles_tuple = vault.functions.getRolesStorage().call()
        #whitelistManager, feeReceiver, safe, feeRegistry, valuationManager = roles_tuple
        asset = vault.functions.asset().call()
        return {
            "address": vault.address,
            "asset": asset,
        }

    def fetch_total_assets(self, block_identifier: BlockIdentifier) -> Decimal:
        """What is the total NAV of the vault.

        Example:

        .. code-block:: python

            assert vault.denomination_token.symbol == "USDC"
            assert vault.share_token.symbol == "ipUSDCfusion"
            assert vault.fetch_total_assets(block_identifier=test_block_number) == Decimal('1437072.77357')
            assert vault.fetch_total_supply(block_identifier=test_block_number) == Decimal('1390401.22652875')


        :param block_identifier:
            Block number to read.

            Use `web3.eth.block_number` for the last block.

        :return:
            The vault value in underlyinh token
        """
        raw_amount = self.vault_contract.functions.totalAssets().call(block_identifier=block_identifier)
        return self.underlying_token.convert_to_decimals(raw_amount)

    def fetch_total_supply(self, block_identifier: BlockIdentifier) -> Decimal:
        """What is the current outstanding shares.

        Example:

        .. code-block: python

            assert vault.denomination_token.symbol == "USDC"
            assert vault.share_token.symbol == "ipUSDCfusion"
            assert vault.fetch_total_assets(block_identifier=test_block_number) == Decimal('1437072.77357')
            assert vault.fetch_total_supply(block_identifier=test_block_number) == Decimal('1390401.22652875')

        :param block_identifier:
            Block number to read.

            Use `web3.eth.block_number` for the last block.

        :return:
            The vault value in underlyinh token
        """
        raw_amount = self.vault_contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_amount)

    def fetch_share_price(self, block_identifier: BlockIdentifier) -> Decimal:
        """Get the current share price.

        :return:
            The share price in underlying token.

            If supply is zero return zero.
        """

        #     function _convertToAssets(
        #         uint256 shares,
        #         uint40 requestId,
        #         Math.Rounding rounding
        #     ) internal view returns (uint256) {
        #         ERC7540Storage storage $ = _getERC7540Storage();
        #
        #         // cache
        #         uint40 settleId = $.epochs[requestId].settleId;
        #
        #         uint256 _totalAssets = $.settles[settleId].totalAssets + 1;
        #         uint256 _totalSupply = $.settles[settleId].totalSupply + 10 ** _decimalsOffset();
        #
        #         return shares.mulDiv(_totalAssets, _totalSupply, rounding);
        #     }
        total_assets = self.fetch_total_assets(block_identifier)
        total_supply = self.fetch_total_supply(block_identifier)
        if total_supply == 0:
            return Decimal(0)
        return total_assets / self.fetch_total_supply(block_identifier)

    def fetch_portfolio(
        self,
        universe: TradingUniverse,
        block_identifier: BlockIdentifier | None = None,
    ) -> VaultPortfolio:
        erc20_balances = fetch_erc20_balances_fallback(
            self.web3,
            self.safe_address,
            universe.spot_token_addresses,
            block_identifier=block_identifier,
            decimalise=True,
        )
        return VaultPortfolio(
            spot_erc20=erc20_balances,
        )

    def fetch_info(self) -> ERC4626VaultInfo:
        """Use :py:meth:`info` property for cached access.

        :return:
            See :py:class:`LagoonVaultInfo`
        """
        vault_info = self.fetch_vault_info()
        return vault_info

    def fetch_nav(self, block_identifier=None) -> Decimal:
        """Fetch the most recent onchain NAV value.

        - In the case of Lagoon, this is the last value written in the contract with
          `updateNewTotalAssets()` and ` settleDeposit()`

        - TODO: `updateNewTotalAssets()` there is no way to read pending asset update on chain

        :return:
            Vault NAV, denominated in :py:meth:`denomination_token`
        """
        token = self.denomination_token
        raw_amount = self.vault_contract.functions.totalAssets().call(block_identifier=block_identifier)
        return token.convert_to_decimals(raw_amount)

    def get_flow_manager(self) -> VaultFlowManager:
        return NotImplementedError()

    def has_block_range_event_support(self):
        raise NotImplementedError()

    def has_deposit_distribution_to_all_positions(self):
        raise NotImplementedError()
