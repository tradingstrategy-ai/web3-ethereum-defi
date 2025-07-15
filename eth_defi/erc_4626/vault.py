"""Generic ECR-4626 vault reader implementation."""

import datetime
import logging
from decimal import Decimal
from functools import cached_property
from typing import Iterable

import eth_abi
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, BlockNumberOutofRange
from web3.types import BlockIdentifier

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.balances import fetch_erc20_balances_fallback
from eth_defi.erc_4626.core import get_deployed_erc_4626_contract, ERC4626Feature
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int, convert_uint256_bytes_to_address
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult, BatchCallState
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.vault.base import VaultBase, VaultSpec, VaultInfo, TradingUniverse, VaultPortfolio, VaultFlowManager, VaultHistoricalReader, VaultHistoricalRead


logger = logging.getLogger(__name__)


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


class VaultReaderState(BatchCallState):
    """Adaptive reading frequency for vaults.

    - This class maintains the per-vault state of reading between different eth_call reads over time

    - Most vaults are uninteresting, but we do not know ahead of time which ones

    - We need 1h data for interesting vaults to make good trade decisions

    - We switch to 1h scanning if the TVL is above a threshold, otherwise we read it once per day

    .. note ::

        Due to filtering, only handles stablecoin vaults correctly at the moment.
        Lacks exchange rate support.
    """

    #: All attributes we store when we serialise the read state between runs
    SERIALISABLE_ATTRIBUTES = (
        "last_tvl",
        "first_read_at",
        "max_tvl",
        "last_call_at",
        "last_block",
        "peaked_at",
        "faded_at",
        "entry_count",
    )

    def __init__(
        self,
        vault: "ERC4626Vault",
        tvl_threshold_1d_read=Decimal(10_000),
        peaked_tvl_threshold=Decimal(200_000),
        min_tvl_threshold=Decimal(1_500),
        down_hard=0.98,
        traction_period: datetime.timedelta = datetime.timedelta(days=14),
    ):
        """
        :param vault:
            The vault we are reading historical data for
        :param tvl_threshold_1d_read:
            If the TVL is below this threshold, we will not read it more than once per day,
            otherwise hourly.
        :param down_hard:
            Stop reading the vault if the TVL is down by this percentage from the peak.
        :parm peaked_tvl_threshold:
            The TVL value we first need to reach to trigger down hard condition.
        :param min_tvl_threshold:
            If the vault never reaches this TVL, we stop reading it after the traction period.
        :param traction_period:
            How long we wait for the vault to get traction before we stop reading it.
        """
        super().__init__()
        self.vault = vault

        self.tvl_threshold_1d_read = tvl_threshold_1d_read
        self.peaked_tvl_threshold = peaked_tvl_threshold
        self.down_hard = down_hard

        #: Passed from the vault discovery reader,
        #: pass the block number as args when we know this vault popped in to the existing
        self.first_seen_at_block = vault.first_seen_at_block

        #: TVL from the last read
        self.last_tvl: Decimal = None

        #: Timestamp of the block of the first successful read of this vault.
        self.first_read_at: datetime.datetime = None

        #: Start with zero TVL
        self.max_tvl: Decimal = Decimal(0)

        #: When this vault received its last eth_call update
        self.last_call_at: datetime.datetime | None = None

        #: When this vault received its last eth_call update
        self.last_block: int | None = None

        #: Disable reading if the vault has peaked (TVL too much down) and is no longer active
        self.peaked_at: datetime.datetime = None

        #: Disable reading if the vault has never gotten any traction
        self.faded_at: datetime.datetime = None

        #: How much time after deployment we allow to get traction
        self.traction_period = traction_period

        #: Minimum TVL traction threshold to start reading the vault
        self.min_tvl_threshold = min_tvl_threshold

        #: Events read, used for testing
        self.entry_count = 0

    def save(self) -> dict:
        return {k: getattr(self, k) for k in self.SERIALISABLE_ATTRIBUTES}

    def load(self, data: dict):
        """Load the state from a dictionary."""
        for k, v in data.items():
            assert k in VaultReaderState.SERIALISABLE_ATTRIBUTES, f"Unknown key {k} in VaultReaderState.load()"
            setattr(self, k, v)

    def should_invoke(
        self,
        call: "EncodedCall",
        block_identifier: BlockIdentifier,
        timestamp: datetime.datetime,
    ) -> bool:
        if self.first_seen_at_block:
            if block_identifier < self.first_seen_at_block:
                # We do not read historical data before the first seen block
                return False

        if self.last_call_at is None:
            # First read, we always read it
            return True

        freq = self.get_frequency()

        if freq is None:
            # Further reads disabled
            return False

        refresh_needed = (timestamp - self.last_call_at) >= freq
        if refresh_needed:
            return True

        return False

    def get_frequency(self) -> datetime.timedelta | None:
        """How fast we are reading this vault."""
        if self.peaked_at or self.faded_at:
            # Disabled due to either of reasons
            return None
        elif self.last_tvl < self.tvl_threshold_1d_read:
            return datetime.timedelta(days=1)
        else:
            return datetime.timedelta(hours=1)

    def on_called(
        self,
        result: "EncodedCallResult",
        total_assets: Decimal | None = None,
    ):
        assert result.timestamp, f"EncodedCallResult {result} has no timestamp, cannot update state"

        if total_assets is None:
            assert result.revert_exception, f"EncodedCallResult {result} has no total assets, but no revert exception either"
            # Cannot read total assets from this vault for some reason as the call is failing.
            # We will mark these broken vaults with special -1 TVL value in the vault reader state.
            total_assets = -1

        timestamp = result.timestamp
        self.last_call_at = timestamp

        if self.first_read_at is None:
            self.first_read_at = timestamp

        self.last_tvl = total_assets
        self.last_call_at = timestamp
        self.last_block = result.block_identifier
        existing_max_tvl = self.max_tvl or 0
        self.max_tvl = max(existing_max_tvl, total_assets) if total_assets != -1 else total_assets

        if self.max_tvl > self.peaked_tvl_threshold:
            if self.last_tvl < self.max_tvl * Decimal(1 - self.down_hard):
                logger.info(f"{self.last_call_at}: Vault {self.vault} peaked at {self.max_tvl}, now TVL is {self.last_tvl}, no longer reading it")
                self.peaked_at = timestamp

        if self.last_call_at - self.first_read_at > self.traction_period:
            if self.max_tvl < self.min_tvl_threshold:
                logger.info(f"{self.last_call_at}:  Vault {self.vault} disabled at {self.max_tvl}, never reached min TVL {self.min_tvl_threshold}, no longer reading it")
                self.faded_at = timestamp

        self.entry_count += 1


class ERC4626HistoricalReader(VaultHistoricalReader):
    """A reader that reads the historcal state of one specific vaults.

    - Generate a list of multicall instances that is needed to capture the vault state in a specific block height
    - All calls share the same state object which we use to track disabling reads for inactive vaults
    - Share price (returns), supply, NAV
    - For performance fees etc. there are no standards so you need to subclass this for
      each protocol
    - All calls for this reader share the same
    """

    def __init__(self, vault: "ERC4626Vault", stateful: bool):
        super().__init__(vault)
        if stateful:
            self.reader_state = VaultReaderState(vault)
        else:
            # Stateful reading cannot be used in unordered multiprocess reads
            self.reader_state = None

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
            extra_data={
                "function": "total_assets",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield total_assets

        total_supply = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.totalSupply(),
            extra_data={
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

        share_token = self.vault.share_token
        if call_by_name["total_supply"].success and share_token is not None:
            raw_total_supply = convert_int256_bytes_to_int(call_by_name["total_supply"].result)
            total_supply = self.vault.share_token.convert_to_decimals(raw_total_supply)
        else:
            errors.append("total_supply call failed")
            total_supply = None

        total_assets_call_result = call_by_name.get("total_assets")
        if self.vault.denomination_token is not None and total_assets_call_result.success:
            raw_total_assets = convert_int256_bytes_to_int(total_assets_call_result.result)
            total_assets = self.vault.denomination_token.convert_to_decimals(raw_total_assets)

            # Handle dealing with the adaptive frequency
            state = total_assets_call_result.state
            if state:
                state.on_called(total_assets_call_result, total_assets)

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

        # Sanity check that all calls are from the same block
        if not all(c.block_identifier == block_number for c in call_by_name.values()):
            msg = "Mismatch of block numbers in multicall results:\n"
            for c in call_by_name.values():
                msg += f"{c.call.func_name} has block number {c.block_identifier:,}, expected {block_number:,}\n"
            raise AssertionError(msg)

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

    def is_valid(self) -> bool:
        """Check if this vault is valid.

        - Call a known smart contract function to verify the function exists
        """
        denomination_token = self.fetch_denomination_token_address()
        return denomination_token is not None

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

    @cached_property
    def erc_7540(self) -> bool:
        """Is this ERC-7540 vault with asynchronous deposits.

        - For example ``previewDeposit()`` function and other functions will revert
        """
        try:
            # isOperator() function is only part of 7545 ABI and will revert is missing
            double_address = eth_abi.encode(["address", "address"], [ZERO_ADDRESS_STR, ZERO_ADDRESS_STR])
            erc_7540_call = EncodedCall.from_keccak_signature(
                address=self.address,
                signature=Web3.keccak(text="isOperator(address,address)")[0:4],
                function="isOperator",
                data=double_address,
                extra_data=None,
            )
            erc_7540_call.call(self.web3, block_identifier="latest")
            return True
        except (ValueError, BadFunctionCallOutput):
            return False

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

            result = erc_7575_call.call(
                self.web3,
                block_identifier="latest",
                ignore_error=True,
                attempts=0,
            )
            if len(result) == 32:
                erc_7575 = True
                share_token_address = convert_uint256_bytes_to_address(result)
            else:
                # Could not read ERC4626Vault 0x0271353E642708517A07985eA6276944A708dDd1 (set()):
                share_token_address = self.vault_address

        except (ValueError, BadFunctionCallOutput) as e:
            parsed_error = str(e)
            # Try to figure out broken ERC-4626 contract and have all conditions
            # to gracefully handle failed erc_7575_call()
            # Mantle
            # Could not read ERC4626Vault 0x32F6D2c91FF3C3d2f1fC2cCAb4Afcf2b6ecF24Ef (set()): {'message': 'out of gas', 'code': -32000}
            # Hyperliquid
            # ValueError: Call failed: 400 Client Error: Bad Request for url: https://lb.drpc.org/ogrpc?network=hyperliquid&dkey=AiWA4TvYpkijvapnvFlyx_WBfO5CICoR76hArr3WfgV4
            if not (("execution reverted" in parsed_error) or ("out of gas" in parsed_error) or ("Bad Request" in parsed_error) or ("VM execution error" in parsed_error)):
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
        # roles_tuple = vault.functions.getRolesStorage().call()
        # whitelistManager, feeReceiver, safe, feeRegistry, valuationManager = roles_tuple
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
            assert vault.fetch_total_assets(block_identifier=test_block_number) == Decimal("1437072.77357")
            assert vault.fetch_total_supply(block_identifier=test_block_number) == Decimal("1390401.22652875")

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

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return ERC4626HistoricalReader(self, stateful=stateful)
