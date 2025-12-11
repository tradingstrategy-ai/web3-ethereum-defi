"""Curve LLAMMA Lending vault support."""

import datetime

from functools import cached_property
import logging

from web3.contract import Contract

from eth_typing import BlockIdentifier

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.vault.base import VaultTechnicalRisk


logger = logging.getLogger(__name__)


class LLAMMAVault(ERC4626Vault):
    """Llama vaults.

    This vault wraps LLAMMA AMM.

    LLAMMA (Lending Liquidating Automated Market Maker Algorithm) is the market-making contract that rebalances the collateral of a loan. It is an algorithm implemented into a smart contract which is responsible for liquidating and de-liquidating collateral based on market conditions through arbitrage traders. Each individual market has its own AMM containing the collateral and borrowable asset. E.g. the AMM of the ETH<>crvUSD contains of ETH and crvUSD.

    - `LLAMMA explained <https://docs.curve.finance/crvUSD/amm/>__
    - `Vault smart contract code: <https://arbiscan.io/address/0xe296ee7f83d1d95b3f7827ff1d08fe1e4cf09d8d#code>`__
    - `LLAMMA markets <https://www.curve.finance/llamalend/ethereum/markets>`__
    """

    @cached_property
    def name(self) -> str:
        """Get vault name."""
        return f"Curve LLAMMA {self.collateral_token.symbol} / {self.denomination_token.symbol}"

    def get_risk(self) -> VaultTechnicalRisk | None:
        return VaultTechnicalRisk.low

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="llamma/Vault.json",
        )

    @cached_property
    def borrowed_token(self) -> TokenDetails:
        """The token we are lending against."""
        addr = self.vault_contract.functions.borrowed_token().call()
        return fetch_erc20_details(
            self.web3,
            addr,
            cache=self.token_cache,
        )

    @cached_property
    def collateral_token(self) -> TokenDetails:
        """The token we are lending against."""
        addr = self.vault_contract.functions.collateral_token().call()
        return fetch_erc20_details(
            self.web3,
            addr,
            cache=self.token_cache,
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """AMM fee is not exposed and internalised."""
        return 0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """AMM fee is not exposed and internalised."""
        return 0

    def get_estimated_lock_up(self) -> datetime.timedelta:
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        chain_name = get_chain_name(self.chain_id).lower()
        return f"https://www.curve.finance/lend/{chain_name}/markets/{self.vault_address}/"
