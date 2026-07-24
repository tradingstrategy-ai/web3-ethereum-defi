"""Read-only Centrifuge ``Tranche`` tokenised-fund adapter.

This adapter intentionally does not use :class:`ERC4626Vault`: a Centrifuge
``Tranche`` is a permissioned ERC-20 share token, not the linked vault used for
subscriptions and redemptions. See `Tranche.sol <https://github.com/centrifuge/liquidity-pools/blob/main/src/token/Tranche.sol>`__
and the `Centrifuge share-token documentation <https://docs.centrifuge.io/user/concepts/share-tokens/>`__.
"""

# Adapter classes intentionally mirror :class:`VaultBase` method signatures.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.centrifuge.constants import CENTRIFUGE_TRANCHE_PRODUCTS, CentrifugeTrancheProduct
from eth_defi.tokenised_fund.centrifuge.historical import CentrifugeTrancheHistoricalReader
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict

#: Minimal read ABI from Centrifuge's verified ``Tranche.sol`` source.
TRANCHE_READ_ABI = [
    {
        "inputs": [],
        "name": "hook",
        "outputs": [{"internalType": "contract IHook", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
        "name": "vault",
        "outputs": [{"internalType": "contract IAsyncVault", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

#: Never advertise token-level operations as public fund dealing.
CENTRIFUGE_TRANCHE_BLOCKED_FLOW_REASON = "Centrifuge Tranche token is not the subscription/redemption vault; public dealing requires the linked permissioned pool route"

#: Explicit unavailable-price reason exposed in scanner metadata and history.
CENTRIFUGE_TRANCHE_NAV_UNAVAILABLE = "Centrifuge Tranche token does not expose NAV/share; linked pool valuation is not configured"


class CentrifugeTrancheVaultInfo(VaultInfo, total=False):
    """Centrifuge Tranche scan metadata."""

    token: HexAddress
    chain_id: int
    compliance_hook: HexAddress
    nav_source: str


class CentrifugeTrancheVault(TokenisedFundVault):
    """Read-only adapter for direct Centrifuge permissioned share tokens.

    It reads ERC-20 metadata and the live compliance hook address. It neither
    infers NAV from supply nor exposes a deposit manager, because mint/burn are
    authorised accounting operations and ``vault(asset)`` points elsewhere.
    """

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
    ):
        """Create an address-scoped Tranche token adapter.

        :param web3:
            Web3 connection for the token's chain.
        :param spec:
            Chain and direct Tranche token address.
        :param token_cache:
            Shared ERC-20 metadata cache.
        :param features:
            Classification features, expected to contain
            :attr:`ERC4626Feature.centrifuge_tranche_like`.
        :param default_block_identifier:
            Optional default block for reads.
        :param require_denomination_token:
            Retained for the shared adapter signature; no denomination token is
            exposed by the Tranche token.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.centrifuge_tranche_like}
        self.default_block_identifier = default_block_identifier
        key = (spec.chain_id, HexAddress(spec.vault_address.lower()))
        try:
            self.product: CentrifugeTrancheProduct = CENTRIFUGE_TRANCHE_PRODUCTS[key]
        except KeyError as error:
            raise RuntimeError(f"Unsupported Centrifuge Tranche token: chain={spec.chain_id}, token={spec.vault_address}") from error

    @property
    def chain_id(self) -> int:
        """Return the product's EVM chain id."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the direct Tranche ERC-20 token address."""

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible identifier for this share token."""

        return self.address

    @property
    def name(self) -> str:
        """Return on-chain name with the reviewed product name fallback."""

        return self.share_token.name or self.product.product_name

    @property
    def symbol(self) -> str:
        """Return on-chain symbol with the reviewed product symbol fallback."""

        return self.share_token.symbol or self.product.symbol

    @property
    def description(self) -> str:
        """Return the product-level description."""

        return "Permissioned Centrifuge Tranche shares in a short-duration U.S. Treasury fund."

    @property
    def short_description(self) -> str:
        """Return a concise token structure description."""

        return "Short-duration U.S. Treasury-bill strategy"

    @property
    def manager_name(self) -> str:
        """Return the verified fund sub-investment manager."""

        return self.product.manager_name

    def _get_block_identifier(self) -> BlockIdentifier:
        """Resolve the configured block identifier.

        :return:
            The configured default or ``latest``.
        """

        return self.default_block_identifier or "latest"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the direct Tranche token address.

        :param block_identifier:
            Accepted for shared scanner compatibility.
        :return:
            Tranche token address.
        """

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch direct Tranche ERC-20 metadata.

        :return:
            Token details for JTRSY.
        """

        return fetch_erc20_details(
            self.web3,
            self.address,
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Centrifuge Tranche share token for vault {self.address}",
        )

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return no denomination token for the direct share token.

        JTRSY has separate linked ERC-7540 vaults for USDC and USDS on
        Ethereum. The direct Tranche token therefore has no unique asset that
        can truthfully occupy the shared single-denomination field.

        See `Centrifuge deployment documentation
        <https://docs.centrifuge.io/developer/protocol/deployments/>`__.

        :return:
            Always ``None``: subscription assets belong to linked vaults.
        """

        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no denomination token metadata.

        :return:
            Always ``None``.
        """

        return None

    def fetch_compliance_hook(self, block_identifier: BlockIdentifier | None = None) -> HexAddress:
        """Read the live transfer-compliance hook.

        :param block_identifier:
            Block at which to read the configured hook.
        :return:
            Checksum hook address, including the zero address when disabled.
        """

        contract = self.web3.eth.contract(address=self.address, abi=TRANCHE_READ_ABI)
        hook = contract.functions.hook().call(block_identifier=block_identifier or self._get_block_identifier())
        return HexAddress(Web3.to_checksum_address(hook))

    def fetch_linked_vault(self, asset: HexAddress, block_identifier: BlockIdentifier | None = None) -> HexAddress:
        """Read the vault associated with a specific subscription asset.

        This low-level relationship does not certify that the returned vault is
        publicly accessible or that it accepts a caller. It must not be used as
        a replacement for a tested Centrifuge subscription/redemption manager.

        :param asset:
            Asset address whose linked vault is queried.
        :param block_identifier:
            Block at which to read the association.
        :return:
            Checksum linked-vault address, possibly the zero address.
        """

        contract = self.web3.eth.contract(address=self.address, abi=TRANCHE_READ_ABI)
        vault = contract.functions.vault(Web3.to_checksum_address(asset)).call(block_identifier=block_identifier or self._get_block_identifier())
        return HexAddress(Web3.to_checksum_address(vault))

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject NAV estimation from the direct share-token surface.

        :param block_identifier:
            Requested block, retained for API compatibility.
        :raises NotImplementedError:
            Always: the Tranche token exposes no valuation method.
        """

        raise NotImplementedError(CENTRIFUGE_TRANCHE_NAV_UNAVAILABLE)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch the human-readable outstanding share supply.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            Direct Tranche ERC-20 supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject TVL calculation without an authoritative NAV.

        :param block_identifier:
            Requested block, retained for API compatibility.
        :raises NotImplementedError:
            Always: supply is not NAV.
        """

        raise NotImplementedError(CENTRIFUGE_TRANCHE_NAV_UNAVAILABLE)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject direct-token NAV calculation.

        :param block_identifier:
            Requested block, retained for API compatibility.
        :raises NotImplementedError:
            Always: valuation belongs to the linked pool route.
        """

        raise NotImplementedError(CENTRIFUGE_TRANCHE_NAV_UNAVAILABLE)

    def fetch_info(self) -> CentrifugeTrancheVaultInfo:
        """Return direct-token scan metadata.

        :return:
            Token, chain, hook and valuation-source details.
        """

        return CentrifugeTrancheVaultInfo(
            token=self.address,
            chain_id=self.chain_id,
            compliance_hook=self.fetch_compliance_hook(),
            nav_source="unconfigured_linked_centrifuge_pool",
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return explicit diagnostics for the deliberately unpriced token.

        :return:
            Scanner extra fields which prevent accidental public-flow claims.
        """

        return {
            "Denomination": None,
            "_denomination_token": None,
            "_notes": self.get_notes(),
            "_deposit_closed_reason": self.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": self.fetch_redemption_closed_reason(),
            "_nav_source": "unconfigured_linked_centrifuge_pool",
            "_nav_estimated": False,
            "_synthetic_usd_denomination": False,
            "_compliance_hook": self.fetch_compliance_hook(),
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no token-contract portfolio.

        :param universe:
            Ignored because fund assets are not token holdings.
        :param block_identifier:
            Ignored because fund assets are not token holdings.
        :return:
            Empty spot portfolio.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether public flow accounting is supported.

        :return:
            ``False`` because token mint/burn is authorised accounting.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether token deposits distribute to on-chain positions.

        :return:
            ``False`` because the Tranche token is not the fund vault.
        """

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unimplemented direct-token flow accounting.

        :raises NotImplementedError:
            Always, because the token is not the dealing vault.
        """

        message = "Centrifuge Tranche token flow accounting is not implemented"
        raise NotImplementedError(message)

    def fetch_deposit_closed_reason(self) -> str:
        """Explain why public subscriptions remain unavailable.

        :return:
            Direct-token flow safety reason.
        """

        return CENTRIFUGE_TRANCHE_BLOCKED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str:
        """Explain why public redemptions remain unavailable.

        :return:
            Direct-token flow safety reason.
        """

        return CENTRIFUGE_TRANCHE_BLOCKED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create the supply-only history reader.

        :param stateful:
            Requested reader state behaviour.
        :return:
            Historical reader that leaves NAV and TVL unavailable.
        """

        return CentrifugeTrancheHistoricalReader(self, stateful=stateful)

    def get_fee_data(self) -> FeeData:
        """Return unavailable fund-fee data.

        :return:
            Broken fee data because the share token has no fund fee surface.
        """

        return BROKEN_FEE_DATA

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no token-level management fee.

        :param block_identifier:
            Ignored because fees are not token contract fields.
        :return:
            ``None``.
        """

        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no token-level performance fee.

        :param block_identifier:
            Ignored because fees are not token contract fields.
        :return:
            ``None``.
        """

        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Return no inferred lock-up.

        :return:
            ``None`` because dealing terms belong to the linked vault.
        """

        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the official JTRSY fund page.

        :param referral:
            Ignored.
        :return:
            Product homepage.
        """

        return self.product.homepage
