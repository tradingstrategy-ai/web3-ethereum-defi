"""Read-only GMX V2 GM and GLV share-token adapter.

GM and GLV are liquidity-provider ERC-20 tokens, not ERC-4626 vaults. The
adapter supplies catalogue metadata only: a historical GMX price requires the
Reader or GlvReader valuation inputs for the relevant block and is not inferred
from deposit or withdrawal events.

See `GMX pricing documentation <https://docs.gmx.io/docs/api/gm-glv-prices/>`__.
"""

# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR6301

from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.token import USDC_NATIVE_TOKEN, TokenDetails, fetch_erc20_details
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositManager
from eth_defi.vault.lower_case_dict import LowercaseDict


class GMXVault(VaultBase):
    """Read catalogue metadata for a GMX V2 GM or GLV share token.

    The catalogue uses USDC as its displayed denomination, while GMX values
    the share in USD from pool state and oracle inputs. It is intentionally not
    admitted to the common price scanner until that valuation is reproduced
    reliably for historical blocks.
    """

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
        **_kwargs: object,
    ) -> None:
        """Create the metadata adapter for a GMX share token.

        :param web3:
            Arbitrum One or Avalanche connection.
        :param spec:
            Chain and GM or GLV share-token address.
        :param token_cache:
            Optional shared ERC-20 metadata cache.
        :param default_block_identifier:
            Default metadata block, retained for scanner compatibility.
        :param require_denomination_token:
            Retained shared-adapter compatibility option.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        if spec.chain_id not in USDC_NATIVE_TOKEN:
            raise ValueError(f"GMX catalogue has no configured USDC denomination for chain {spec.chain_id}")
        self.web3 = web3
        self.spec = spec
        self.default_block_identifier = default_block_identifier or "latest"

    @property
    def chain_id(self) -> int:
        """Return the EVM deployment chain."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the GM or GLV share-token address."""

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible share-token address."""

        return self.address

    @property
    def name(self) -> str:
        """Return the onchain share-token name.

        Catalogue synchronisation replaces this generic token name with a
        stable market-specific display name.
        """

        return self.share_token.name

    @property
    def symbol(self) -> str:
        """Return the onchain share-token symbol."""

        return self.share_token.symbol

    @property
    def short_description(self) -> str:
        """Return a compact description for the metadata row."""

        return "GMX V2 liquidity-provider share token"

    def get_notes(self) -> str:
        """Explain the displayed denomination and unavailable price curve."""

        return "USDC is the displayed denomination. GMX prices GM and GLV shares in USD using pool state and oracle inputs; this catalogue does not publish a historical price curve."

    def fetch_share_token(self) -> TokenDetails:
        """Fetch GM or GLV ERC-20 metadata.

        :return:
            Share-token details from the configured chain.
        """

        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, cache=self.token_cache, cause_diagnostics_message=f"GMX share token {self.address}")

    def fetch_denomination_token_address(self) -> HexAddress:
        """Return native USDC as the catalogue display denomination.

        :return:
            Native USDC address on the product chain.
        """

        return HexAddress(Web3.to_checksum_address(USDC_NATIVE_TOKEN[self.chain_id]))

    def fetch_denomination_token(self) -> TokenDetails:
        """Fetch native USDC metadata for the catalogue row.

        :return:
            Native USDC token details.
        """

        return fetch_erc20_details(self.web3, self.fetch_denomination_token_address(), chain_id=self.chain_id, cache=self.token_cache, cause_diagnostics_message="GMX USDC display denomination")

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch outstanding GM or GLV shares.

        :param block_identifier:
            EVM block at which to read token supply.
        :return:
            Decimal-scaled token supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject unsupported generic NAV reads.

        GMX requires pool and oracle price context for a valuation. Returning a
        plausible number from a partial source would misstate the product.

        :param block_identifier:
            Retained for the shared scanner interface.
        """

        message = "GMX share valuation requires Reader or GlvReader inputs and is not available through the generic vault adapter"
        raise NotImplementedError(message)

    def fetch_info(self) -> VaultInfo:
        """Return the catalogue identity.

        :return:
            Basic GMX share-token metadata.
        """

        return {"token": self.address, "chain_id": self.chain_id, "denomination": "USDC"}

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Export explicit denomination and valuation limitations.

        :return:
            Scanner-private GMX metadata fields.
        """

        return {
            "Denomination": "USDC",
            "_denomination_token": self.fetch_denomination_token().export(),
            "_synthetic_usd_denomination": False,
            "_nav_available": False,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no generic spot portfolio.

        :param universe:
            Unused generic token universe.
        :param block_identifier:
            Unused EVM block identifier.
        :return:
            Empty portfolio because GMX manages the pool composition.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return that generic deposit and redemption events are unsupported."""

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return that generic position distribution does not apply."""

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject generic flow accounting for asynchronous GMX requests."""

        message = "GMX liquidity requests are not supported by the generic flow adapter"
        raise NotImplementedError(message)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject generic transaction construction for GMX liquidity requests."""

        message = "GMX liquidity requests are not supported by the generic deposit adapter"
        raise NotImplementedError(message)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Reject unsupported generic historical price scanning."""

        message = "GMX historical valuation requires per-block Reader or GlvReader inputs"
        raise NotImplementedError(message)

    def get_link(self, referral: str | None = None) -> str:
        """Return the GMX liquidity application page."""

        return "https://app.gmx.io/#/pools"
