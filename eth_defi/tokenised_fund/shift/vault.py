"""Read-only ShiftVault adapter.

ShiftVault is an ERC-20 share token with a request-and-batch lifecycle, not an
ERC-4626 vault. Deposits require a preceding request and offchain executor
approval, while withdrawals require batch resolution and a request-time
timelock. This
adapter therefore exposes valuation and configuration reads only; it does not
advertise a public deposit or redemption manager.

Sources:

- `Shift address registry <https://shiftprotocol.gitbook.io/shift/resources/addresses>`__
- `ShiftVault source <https://github.com/SHIFT-NebulaLabs/shift-core-v1/blob/main/src/ShiftVault.sol>`__
- `ShiftManager source <https://github.com/SHIFT-NebulaLabs/shift-core-v1/blob/main/src/ShiftManager.sol>`__
"""

# Adapter methods intentionally mirror :class:`VaultBase` method signatures.
# ruff: noqa: FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.shift.constants import SHIFT_HOMEPAGE, SHIFT_VAULT_PRODUCTS, ShiftVaultProduct
from eth_defi.tokenised_fund.shift.descriptions import get_shift_vault_description
from eth_defi.tokenised_fund.shift.historical import ShiftVaultHistoricalReader
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.lower_case_dict import LowercaseDict

#: Minimal stable, no-argument ShiftVault read ABI. The selectors are defined
#: by Shift's published Solidity source and avoid embedding the full contract ABI.
SHIFT_VAULT_READ_ABI = [
    {"inputs": [], "name": "baseToken", "outputs": [{"internalType": "contract ERC20", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "tvlFeed", "outputs": [{"internalType": "contract IShiftTvlFeed", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getSharePrice", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "maintenanceFeePerSecond18pt", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "performanceFee18pt", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "timelock", "outputs": [{"internalType": "uint32", "name": "", "type": "uint32"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "whitelistEnabled", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "paused", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
]

#: Minimal Shift TVL feed interface used for its immutable valuation scale.
SHIFT_TVL_FEED_READ_ABI = [
    {"inputs": [], "name": "decimals", "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
]


class ShiftVaultInfo(VaultInfo, total=False):
    """Shift-specific configuration exported with vault metadata."""

    #: Underlying ERC-20 token used for deposits and withdrawals.
    base_token: HexAddress

    #: Current withdrawal timelock in seconds.
    timelock_seconds: int

    #: Whether the contract has its allow-list gate enabled.
    whitelist_enabled: bool


class ShiftVault(TokenisedFundVault):
    """Read a published ShiftVault ERC-20 share contract.

    ShiftVault calculates a share price from its TVL feed and mints fee shares
    to the fee collector. The public interface cannot complete a deposit or a
    withdrawal without the executor's request approval and batch resolution,
    so transaction support remains fail-closed until the complete lifecycle is
    implemented and tested.
    """

    whitelist_notes = "Shift can enable a contract-level investor allow-list; individual membership is not publicly readable."

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
    ) -> None:
        """Create an address-scoped ShiftVault reader.

        :param web3:
            EVM JSON-RPC connection for the reviewed deployment.
        :param spec:
            Chain and ShiftVault share-token address.
        :param token_cache:
            Shared ERC-20 metadata cache.
        :param features:
            Shared classification flags, expected to include ``shift_like``.
        :param default_block_identifier:
            Optional default metadata block retained for factory compatibility.
        :param require_denomination_token:
            Whether an unavailable base token is a hard error.
        :raise RuntimeError:
            If the chain/address is not in Shift's reviewed registry.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        key = (spec.chain_id, HexAddress(spec.vault_address.lower()))
        try:
            self.product: ShiftVaultProduct = SHIFT_VAULT_PRODUCTS[key]
        except KeyError as error:
            message = f"Unsupported ShiftVault: chain={spec.chain_id}, vault={spec.vault_address}"
            raise RuntimeError(message) from error
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.shift_like}
        self.default_block_identifier = default_block_identifier

    @property
    def chain_id(self) -> int:
        """Return the ShiftVault EVM chain id."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the checksummed ShiftVault share-token address."""

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible vault identifier."""

        return self.address

    @cached_property
    def shift_contract(self) -> Contract:
        """Return the minimal ShiftVault read interface bound to this vault."""

        return self.web3.eth.contract(address=self.address, abi=SHIFT_VAULT_READ_ABI)

    @cached_property
    def tvl_feed_decimals(self) -> int:
        """Read the immutable TVL feed's decimal precision.

        ShiftVault's ``getSharePrice()`` result is scaled to this precision,
        which need not equal the deposit token's decimals.

        :return:
            Decimal places used by ``getSharePrice()``.
        """

        feed_address = self.shift_contract.functions.tvlFeed().call()
        feed = self.web3.eth.contract(address=feed_address, abi=SHIFT_TVL_FEED_READ_ABI)
        return int(feed.functions.decimals().call())

    @property
    def name(self) -> str:
        """Return the onchain share-token name with registry fallback."""

        return self.share_token.name or self.product.product_name

    @property
    def symbol(self) -> str:
        """Return the onchain ShiftVault share-token symbol."""

        return self.share_token.symbol

    @property
    def description(self) -> str:
        """Return the curated strategy and lifecycle description."""

        description = get_shift_vault_description(self.chain_id, self.address)
        assert description is not None, f"Missing curated ShiftVault description for {self.spec}"
        return description.long_description

    @property
    def short_description(self) -> str:
        """Return the curated product-specific one-line description."""

        description = get_shift_vault_description(self.chain_id, self.address)
        assert description is not None, f"Missing curated ShiftVault description for {self.spec}"
        return description.short_description

    @property
    def manager_name(self) -> str:
        """Return Shift as the published vault operator."""

        return "Shift"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the ERC-20 share-token address.

        :param block_identifier:
            Retained for vault scanner compatibility.
        :return:
            This ShiftVault's address.
        """

        del block_identifier
        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch ERC-20 share-token metadata.

        :return:
            ShiftVault ERC-20 share-token details.
        """

        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"ShiftVault share token for {self.address}")

    def fetch_denomination_token_address(self) -> HexAddress:
        """Read the base ERC-20 used by ShiftVault.

        :return:
            Underlying deposit and withdrawal token address.
        """

        return HexAddress(Web3.to_checksum_address(self.shift_contract.functions.baseToken().call()))

    def fetch_denomination_token(self) -> TokenDetails:
        """Fetch the underlying base ERC-20 metadata.

        :return:
            ShiftVault base-token details.
        """

        address = self.fetch_denomination_token_address()
        return fetch_erc20_details(self.web3, address, chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"ShiftVault base token for {self.address}")

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read the latest TVL-feed share price in base-token units.

        Shift returns zero when the TVL feed is stale. The value otherwise uses
        the TVL feed's decimal precision, as documented in ``ShiftVault``.

        :param block_identifier:
            Block number or tag at which to read the share price.
        :return:
            Base-token amount represented by one human-readable share.
        """

        raw_price = int(self.shift_contract.functions.getSharePrice().call(block_identifier=block_identifier))
        return Decimal(raw_price) / Decimal(10**self.tvl_feed_decimals)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read the outstanding ERC-20 share supply.

        :param block_identifier:
            Block number or tag at which to read the supply.
        :return:
            Human-readable share supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Calculate TVL from the TVL-feed share price and ERC-20 supply.

        :param block_identifier:
            Block number or tag at which to calculate TVL.
        :return:
            Total assets in base-token units.
        """

        return self.fetch_share_price(block_identifier) * self.fetch_total_supply(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return the ShiftVault TVL in base-token units.

        :param block_identifier:
            Block number or tag at which to calculate NAV.
        :return:
            Total assets in base-token units.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> ShiftVaultInfo:
        """Read public ShiftVault configuration required by scanner consumers.

        :return:
            Base token and public lifecycle configuration.
        """

        return ShiftVaultInfo(
            base_token=self.fetch_denomination_token_address(),
            timelock_seconds=int(self.shift_contract.functions.timelock().call()),
            whitelist_enabled=bool(self.shift_contract.functions.whitelistEnabled().call()),
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return Shift configuration that explains price and flow availability.

        :return:
            Private scanner fields for the TVL-feed valuation and unsupported flow.
        """

        return {
            "_notes": self.get_notes(),
            "_deposit_closed_reason": self.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": self.fetch_redemption_closed_reason(),
            "_nav_source": "shift_getSharePrice",
            "_nav_estimated": False,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no inferred ERC-20 portfolio.

        Shift deposits are transferred to an executor address, rather than held
        in the share contract. The publicly listed executor is insufficient to
        reconstruct its cross-chain strategy positions safely.

        :param universe:
            Ignored because no complete portfolio is exposed.
        :param block_identifier:
            Ignored because no complete portfolio is exposed.
        :return:
            Empty portfolio.
        """

        del universe, block_identifier
        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether the request-and-batch event reader is implemented.

        :return:
            ``False`` until the complete Shift lifecycle reader exists.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether adapter-level position distribution is implemented.

        :return:
            ``False`` because executor positions are not reconstructed.
        """

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unimplemented Shift request-and-batch flow accounting.

        :raise NotImplementedError:
            Always, until executor and cross-chain settlement flow accounting is implemented.
        """

        message = "ShiftVault request-and-batch flow accounting is not implemented"
        raise NotImplementedError(message)

    def fetch_deposit_closed_reason(self) -> str:
        """Explain why public deposit transactions are not advertised.

        :return:
            Stable explanation of the unsupported request/approval lifecycle.
        """

        if self.shift_contract.functions.paused().call():
            return "Shift deposits are paused"
        if self.shift_contract.functions.whitelistEnabled().call():
            return "Shift deposit allow-list is enabled"
        return "Shift deposit requests require executor approval; no public deposit manager is implemented"

    def fetch_redemption_closed_reason(self) -> str:
        """Explain why public redemption transactions are not advertised.

        :return:
            Stable explanation of the unsupported batch/timelock lifecycle.
        """

        if self.shift_contract.functions.paused().call():
            return "Shift withdrawals are paused"
        return "Shift withdrawals require executor batch resolution and a timelock; no public redemption manager is implemented"

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Return the ShiftVault share-price historical reader.

        :param stateful:
            Whether adaptive reader state should be retained.
        :return:
            ShiftVault reader using ``getSharePrice()`` and ``totalSupply()``.
        """

        return ShiftVaultHistoricalReader(self, stateful=stateful)

    def get_fee_data(self) -> FeeData:
        """Return Shift's live fee-share configuration.

        :return:
            Annual management and performance fees minted as share dilution.
        """

        return FeeData(
            fee_mode=VaultFeeMode.internalised_minting,
            management=self.get_management_fee("latest"),
            performance=self.get_performance_fee("latest"),
            deposit=None,
            withdraw=None,
        )

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Read the annualised maintenance fee.

        ``maintenanceFeePerSecond18pt`` is stored as an 18-decimal fractional
        rate per second and is minted to the fee collector as shares.

        :param block_identifier:
            Block number or tag at which to read the fee.
        :return:
            Annual management fee as a fractional percent.
        """

        raw_per_second = int(self.shift_contract.functions.maintenanceFeePerSecond18pt().call(block_identifier=block_identifier))
        return float(Decimal(raw_per_second) * Decimal(datetime.timedelta(days=365).total_seconds()) / Decimal(10**18))

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Read the performance fee rate.

        :param block_identifier:
            Block number or tag at which to read the fee.
        :return:
            Performance fee as a fractional percent.
        """

        raw_fee = int(self.shift_contract.functions.performanceFee18pt().call(block_identifier=block_identifier))
        return float(Decimal(raw_fee) / Decimal(10**18))

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Read the configured withdrawal timelock.

        :return:
            Current withdrawal timelock, measured from a withdrawal request.
        """

        return datetime.timedelta(seconds=int(self.shift_contract.functions.timelock().call()))

    def is_account_whitelisted(self, address: HexAddress) -> bool:
        """Reject an unavailable individual allow-list lookup.

        Shift's published implementation stores this mapping internally without
        a public getter, so no safe account-level read exists.

        :param address:
            Address whose membership was requested.
        :raise NotImplementedError:
            Always.
        """

        del address
        message = "ShiftVault does not expose public account allow-list membership"
        raise NotImplementedError(message)

    def get_link(self, referral: str | None = None) -> str:
        """Return Shift's public application.

        :param referral:
            Ignored because Shift does not publish a referral route.
        :return:
            Shift application URL.
        """

        del referral
        return SHIFT_HOMEPAGE
