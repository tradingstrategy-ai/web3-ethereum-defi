from functools import cached_property

from eth_typing import HexAddress, BlockIdentifier
from web3 import Web3

from eth_defi.balances import fetch_erc20_balances_fallback
from eth_defi.vault.base import VaultBase, VaultSpec, VaultInfo, TradingUniverse, VaultPortfolio


class LagoonVaultInfo(VaultInfo):
    """TODO: Add Lagoon vault info query"""

    #: Address of the Safe multisig the vault is build around
    safe_address: HexAddress


class LagoonVault(VaultBase):
    """Python interface for interacting with Velvet Capital vaults."""

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
    ):
        """
        :param spec:
            Address must be Velvet portfolio address (not vault address)
        """
        assert isinstance(web3, Web3)
        assert isinstance(spec, VaultSpec)
        self.web3 = web3
        self.spec = spec

    def has_block_range_event_support(self):
        return True

    def get_flow_manager(self):
        raise NotImplementedError("Velvet does not support individual deposit/redemption events yet")

    def fetch_info(self) -> LagoonVaultInfo:
        """Read vault parameters from the chain."""
        return {
            "safe_address": self.spec.vault_address,
        }

    @cached_property
    def info(self) -> LagoonVaultInfo:
        return self.fetch_info()

    @property
    def safe_address(self) -> HexAddress:
        return self.info["safe_address"]

    @property
    def owner_address(self) -> HexAddress:
        return self.info["owner"]

    @property
    def name(self) -> str:
        return self.info["name"]

    @property
    def token_symbol(self) -> str:
        return self.info["symbol"]

    def fetch_portfolio(
        self,
        universe: TradingUniverse,
        block_identifier: BlockIdentifier | None = None,
    ) -> VaultPortfolio:
        """Read the current token balances of a vault.

        - SHould be supported by all implementations
        """

        vault_address = self.info["vaultAddress"]

        erc20_balances = fetch_erc20_balances_fallback(
            self.web3,
            vault_address,
            universe.spot_token_addresses,
            block_identifier=block_identifier,
            decimalise=True,
        )
        return VaultPortfolio(
            spot_erc20=erc20_balances,
        )
