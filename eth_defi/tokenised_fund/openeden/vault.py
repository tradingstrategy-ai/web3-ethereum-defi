"""OpenEden TBILL adapter using the issuer's documented NAV oracle."""

# ruff: noqa: ARG002, PLR6301

from functools import cached_property

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.openeden.constants import OPENEDEN_CHAIN_ID, OPENEDEN_TBILL_ADDRESS, OPENEDEN_TBILL_DENOMINATION_TOKEN_ADDRESS, OPENEDEN_TBILL_FIRST_SEEN_AT_BLOCK, OPENEDEN_TBILL_ORACLE_FIRST_SEEN_AT_BLOCK, OPENEDEN_TBILL_PRICE_ORACLE_ADDRESS
from eth_defi.tokenised_fund.usyc.vault import _USYC_ORACLE_ABI, USYCVault
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.types import Percent
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData


class OpenEdenVault(USYCVault):
    """Read TBILL supply and NAV from OpenEden's published price oracle."""

    def __init__(self, web3: Web3, spec: VaultSpec, **kwargs) -> None:
        """Create the reviewed TBILL adapter.

        :param web3: Ethereum Web3 connection.
        :param spec: TBILL share-token identity.
        :param kwargs: Shared vault-factory keyword arguments.
        :return: ``None``.
        """

        if spec.chain_id != OPENEDEN_CHAIN_ID or spec.vault_address.lower() != OPENEDEN_TBILL_ADDRESS:
            raise ValueError(f"Unsupported OpenEden product: chain={spec.chain_id}, token={spec.vault_address}")
        TokenisedFundVault.__init__(self, token_cache=kwargs.get("token_cache"), require_denomination_token=kwargs.get("require_denomination_token", False))
        self.web3 = web3
        self.spec = spec
        self.features = kwargs.get("features") or {ERC4626Feature.openeden_like}
        self.first_seen_at_block = OPENEDEN_TBILL_FIRST_SEEN_AT_BLOCK
        self.oracle_first_seen_at_block = OPENEDEN_TBILL_ORACLE_FIRST_SEEN_AT_BLOCK

    @property
    def address(self) -> HexAddress:
        """Return the TBILL ERC-20 token address."""

        return HexAddress(Web3.to_checksum_address(OPENEDEN_TBILL_ADDRESS))

    def fetch_denomination_token_address(self) -> HexAddress:
        """Return OpenEden TBILL's reviewed USDC subscription asset.

        :return:
            Native Ethereum USDC address.
        """

        return HexAddress(Web3.to_checksum_address(OPENEDEN_TBILL_DENOMINATION_TOKEN_ADDRESS))

    def fetch_denomination_token(self) -> TokenDetails:
        """Fetch OpenEden TBILL's USDC denomination metadata.

        Keeping this implementation local avoids coupling OpenEden metadata to
        the separate USYC product merely because the adapters share oracle
        mechanics.

        :return:
            Native Ethereum USDC token details.
        """

        return fetch_erc20_details(
            self.web3,
            self.fetch_denomination_token_address(),
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"OpenEden TBILL denomination token for vault {self.address}",
        )

    @cached_property
    def price_oracle_contract(self) -> Contract:
        """Return OpenEden's documented Chainlink-compatible TBILL oracle."""

        return self.web3.eth.contract(address=Web3.to_checksum_address(OPENEDEN_TBILL_PRICE_ORACLE_ADDRESS), abi=_USYC_ORACLE_ABI)

    @property
    def description(self) -> str:
        """Return the issuer's plain-language TBILL description."""

        return "Permissioned tokenised shares in OpenEden's short-dated U.S. Treasury bill fund."

    @property
    def short_description(self) -> str:
        """Return a compact TBILL listing description."""

        return "Permissioned tokenised U.S. Treasury bill fund"

    @property
    def manager_name(self) -> str:
        """Return the issuer and platform name."""

        return "OpenEden"

    def fetch_deposit_closed_reason(self) -> str:
        """Explain TBILL's permissioned subscription flow.

        :return: Issuer restriction explanation.
        """

        return "TBILL subscriptions and redemptions require OpenEden onboarding and approved investor wallets"

    def fetch_redemption_closed_reason(self) -> str:
        """Explain TBILL's permissioned redemption flow.

        :return: Issuer restriction explanation.
        """

        return self.fetch_deposit_closed_reason()

    def get_management_fee(self, block_identifier) -> Percent | None:
        """Return no separately verified on-chain management fee.

        :param block_identifier: Ignored block identifier.
        :return: ``None``.
        """

        return None

    def get_performance_fee(self, block_identifier) -> Percent | None:
        """Return no separately verified on-chain performance fee.

        :param block_identifier: Ignored block identifier.
        :return: ``None``.
        """

        return None

    def get_deposit_fee(self, block_identifier) -> Percent | None:
        """Return no separately verified public subscription fee.

        :param block_identifier: Ignored block identifier.
        :return: ``None``.
        """

        return None

    def get_withdraw_fee(self, block_identifier) -> Percent | None:
        """Return no separately verified public redemption fee.

        :param block_identifier: Ignored block identifier.
        :return: ``None``.
        """

        return None

    def get_fee_data(self) -> FeeData:
        """Return unknown fee data instead of inheriting USYC fee assumptions.

        :return: Broken fee-data sentinel.
        """

        return BROKEN_FEE_DATA

    def fetch_info(self) -> dict[str, object]:
        """Export TBILL's actual price-oracle metadata.

        :return: Scanner-compatible TBILL metadata mapping.
        """

        return {"token": self.address, "chain_id": self.chain_id, "denomination_token": self.fetch_denomination_token_address(), "price_oracle": HexAddress(Web3.to_checksum_address(OPENEDEN_TBILL_PRICE_ORACLE_ADDRESS)), "nav_source": "openeden_tbill_price_oracle_latestRoundData"}

    def get_link(self, referral: str | None = None) -> str:
        """Return OpenEden's official TBILL product page.

        :param referral: Ignored referral parameter.
        :return: Product page URL.
        """

        return "https://openeden.com/tbill"

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Export OpenEden's oracle and restricted-flow diagnostics.

        :return: Scanner private columns.
        """

        return {"_nav_source": "openeden_tbill_price_oracle_latestRoundData", "_nav_estimated": False, "_openeden_price_oracle": HexAddress(Web3.to_checksum_address(OPENEDEN_TBILL_PRICE_ORACLE_ADDRESS)), "_deposit_closed_reason": self.fetch_deposit_closed_reason(), "_redemption_closed_reason": self.fetch_redemption_closed_reason(), "_curator_slug": "openeden"}
