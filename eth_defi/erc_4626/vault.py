"""Generic ECR-4626 vault reader implementation."""
import dataclasses
import datetime
from decimal import Decimal
from functools import cached_property
from typing import Iterable

from eth_typing import HexAddress
from fontTools.unicodedata import block
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, BlockNumberOutofRange
from web3.types import BlockIdentifier

from eth_defi.abi import get_deployed_contract, get_contract
from eth_defi.balances import fetch_erc20_balances_fallback
from eth_defi.erc_4626.core import get_deployed_erc_4626_contract, ERC4626Feature
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int, convert_uint256_bytes_to_address
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.vault.base import VaultBase, VaultSpec, VaultInfo, TradingUniverse, VaultPortfolio, VaultFlowManager, VaultHistoricalReader, VaultHistoricalRead


class ERC4626VaultInfo(VaultInfo):
    """Capture information about ERC- vault deployment."""

    #: The ERC-20 token that nominates the vault assets
    address: HexAddress

    #: The address of the underlying token used for the vault for accounting, depositing, withdrawing.
    #:
    #: Some broken vaults do not expose this, and may be None.
    #: e.g. https://arbiscan.io/address/0x9d0fbc852deccb7dcdd6cb224fa7561efda74411#code
    #:
    #: E.g. USDC.
    #:
    asset: HexAddress | None


class ERC4626HistoricalReader(VaultHistoricalReader):
    """Support reading historical vault data.

    - Share price (returns), supply, NAV
    - For performance fees etc. there are no standards so you need to subclass this for
      each protocol
    """

    def __init__(self, vault: "ERC4626Vault"):
        super().__init__(vault)

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Get the onchain calls that are needed to read the share price."""
        yield from self.construct_core_erc_4626_multicall()

    def construct_core_erc_4626_multicall(self) -> Iterable[EncodedCall]:
        """Polling endpoints defined in ERC-4626 spec.

        Does not include fees.
        """

        # TODO: use asset / supply as it is more reliable
        if self.vault.denomination_token is not None:
            # amount = self.vault.denomination_token.convert_to_raw(Decimal(1))
            # share_price_call = EncodedCall.from_contract_call(
            #     self.vault.vault_contract.functions.convertToShares(amount),
            #     extra_data = {
            #         "function": "share_price",
            #         "vault": self.vault.address,
            #         "amount": amount,
            #         "denomination_token": self.vault.denomination_token.symbol,
            #         "decimals": self.vault.denomination_token.decimals,
            #     },
            #     first_block_number=self.first_block,
            # )
            # yield share_price_call
            pass

        total_assets = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.totalAssets(),
            extra_data = {
                "function": "total_assets",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield total_assets

        total_supply = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.totalSupply(),
            extra_data = {
                "function": "total_supply",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield total_supply

    def process_core_erc_4626_result(
        self,
        call_by_name: dict[str, EncodedCallResult],
    ) -> tuple:
        """Decode common ERC-4626 calls."""

        errors = []

        # Not generated with denomination token is busted
        # assert "share_price" in call_by_name, f"share_price call missing for {self.vault}, we got {list(call_by_name.items())}"
        assert "total_supply" in call_by_name, f"total_supply call missing for {self.vault}, we got {list(call_by_name.items())}"
        assert "total_assets" in call_by_name, f"total_assets call missing for {self.vault}, we got {list(call_by_name.items())}"

        if call_by_name["total_supply"].success:
            raw_total_supply = convert_int256_bytes_to_int(call_by_name["total_supply"].result)
            total_supply = self.vault.share_token.convert_to_decimals(raw_total_supply)
        else:
            errors.append("total_supply call failed")
            total_supply = None

        if self.vault.denomination_token is not None and call_by_name["total_assets"].success:
            raw_total_assets = convert_int256_bytes_to_int(call_by_name["total_assets"].result)
            total_assets = self.vault.denomination_token.convert_to_decimals(raw_total_assets)
        else:
            errors.append("total_assets call failed")
            total_assets = None

        if total_assets == 0:
            errors.append(f"total_assets zero: {call_by_name['total_assets']}")

        if total_supply == 0:
            errors.append(f"total_supply zero: {call_by_name['total_supply']}")

        if total_supply and total_assets:
            share_price = Decimal(total_assets) / Decimal(total_supply)
        else:
            share_price = None

        return share_price, total_supply, total_assets, (errors or None)

    def dictify_multicall_results(
        self,
        block_number: int,
        call_results: list[EncodedCallResult],
        allow_failure=True,
    ) -> dict[str, EncodedCallResult]:
        """Convert batch of multicalls made for this vault to more digestible dict.

        - Assert that all multicalls succeed

        :return:
            Dictionary where each multicall is keyed by its ``EncodedCall.extra_data["function"]``
        """
        call_by_name = {r.call.extra_data["function"]: r for r in call_results}

        # Check that all multicalls succeed for this vault
        if not allow_failure:
            for result in call_by_name.values():
                assert result.success, f"Multicall failed at block {block_number:,}: {result.call} for vault {self.vault}\nDebug info for Tenderly: {result.call.get_debug_info()}"

        return call_by_name

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:

        call_by_name = self.dictify_multicall_results(block_number, call_results)
        assert all(c.block_identifier == block_number for c in call_by_name.values()), "Sanity check for call block numbering"

        # Decode common variables
        share_price, total_supply, total_assets, errors = self.process_core_erc_4626_result(call_by_name)

        # Subclass
        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=None,
            management_fee=None,
            errors=errors or None,
        )


class ERC4626Vault(VaultBase):
    """ERC-4626 vault adapter

    Handle vault operations:

    - Metadata
    - Deposit and redeem from the vault
    - Vault historical price reader
    - Also partial support for ERC-7575 extensions

    More info:

    - `Find the interface here <https://github.com/OpenZeppelin/openzeppelin-contracts/blob/master/contracts/token/ERC20/extensions/ERC4626.sol>`__
    - `EIP-7575 <https://eips.ethereum.org/EIPS/eip-7575>`__
    """

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
    ):
        """
        :param web3:
            Connection we bind this instance to

        :param spec:
            Chain, address tuple

        :param token_cache:
            Cache used with :py:meth:`fetch_erc20_details` to avoid multiple calls to the same token.

            Reduces the number of RPC calls when scanning multiple vaults.

        :param features:
            Pass vault feature flags along, externally detected.
        """

        if type(features) == set:
            assert len(features) >= 1, "If given, the vault features set should contain at least one feature"

        super().__init__(token_cache=token_cache)
        self.web3 = web3
        self.spec = spec
        self.features = features

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.spec}>"

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
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
        )

    @property
    def underlying_token(self) -> TokenDetails:
        """Alias for :py:meth:`denomination_token`"""
        return self.denomination_token

    def fetch_denomination_token_address(self) -> HexAddress | None:
        try:
            asset = self.vault_contract.functions.asset().call()
            return asset
        except (ValueError, BadFunctionCallOutput):
            pass
        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        token_address = self.fetch_denomination_token_address()
        # eth_defi.token.TokenDetailError: Token 0x4C36388bE6F416A29C8d8Eee81C771cE6bE14B18 missing symbol
        if token_address:
            return fetch_erc20_details(
                self.web3,
                token_address,
                chain_id=self.spec.chain_id,
                raise_on_error=False,
                cause_diagnostics_message=f"Vault {self.__class__.__name__} {self.address} denominating token lookup",
                cache=self.token_cache,
            )
        else:
            return None

    def fetch_share_token(self) -> TokenDetails:
        """Get share token of this vault.

        - Vault itself (ERC-4626)
        - share() accessor (ERc-7575)
        """
        erc_7575 = False
        try:
            # ERC-7575
            erc_7575_call = EncodedCall.from_keccak_signature(
                address=self.vault_address,
                signature=Web3.keccak(text="share()")[0:4],
                function="share",
                data=b"",
                extra_data=None,
            )

            result = erc_7575_call.call(self.web3, block_identifier="latest")
            if len(result) == 32:
                erc_7575 = True
                share_token_address = convert_uint256_bytes_to_address(result)
            else:
                # Could not read ERC4626Vault 0x0271353E642708517A07985eA6276944A708dDd1 (set()):
                share_token_address = self.vault_address

        except (ValueError, BadFunctionCallOutput) as e:
            parsed_error = str(e)
            # Mantle
            # Could not read ERC4626Vault 0x32F6D2c91FF3C3d2f1fC2cCAb4Afcf2b6ecF24Ef (set()): {'message': 'out of gas', 'code': -32000}
            # Hyperliquid
            # ValueError: Call failed: 400 Client Error: Bad Request for url: https://lb.drpc.org/ogrpc?network=hyperliquid&dkey=AiWA4TvYpkijvapnvFlyx_WBfO5CICoR76hArr3WfgV4
            if not (("execution reverted" in parsed_error) or ("out of gas" in parsed_error) or ("Bad Request" in parsed_error)):
                raise

            share_token_address = self.vault_address

        # eth_defi.token.TokenDetailError: Token 0xDb7869Ffb1E46DD86746eA7403fa2Bb5Caf7FA46 missing symbol
        return fetch_erc20_details(
            self.web3,
            share_token_address,
            raise_on_error=False,
            chain_id=self.spec.chain_id,
            cache=self.token_cache,
            cause_diagnostics_message=f"Share token for vault {self.address}, ERC-7575 is {erc_7575}",
        )

    def fetch_vault_info(self) -> ERC4626VaultInfo:
        """Get all information we can extract from the vault smart contracts."""
        vault = self.vault_contract
        #roles_tuple = vault.functions.getRolesStorage().call()
        #whitelistManager, feeReceiver, safe, feeRegistry, valuationManager = roles_tuple
        try:
            asset = vault.functions.asset().call()
        except ValueError as e:
            asset = None

        return {
            "address": vault.address,
            "asset": asset,
        }

    def fetch_total_assets(self, block_identifier: BlockIdentifier) -> Decimal | None:
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
        if self.underlying_token is not None:
            return self.underlying_token.convert_to_decimals(raw_amount)
        return None

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
        assert isinstance(block_identifier, (int, str)), f"Block identifier should be int or str, got {type(block_identifier)}"
        try:
            raw_amount = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        except BlockNumberOutofRange as e:
            raise RuntimeError(f"Cannot fetch total supply for block number: {block_identifier} for vault {self}") from e
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

    def get_historical_reader(self) -> VaultHistoricalReader:
        return ERC4626HistoricalReader(self)
