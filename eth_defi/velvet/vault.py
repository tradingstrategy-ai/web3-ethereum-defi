"""Velvet Capital vault adapter.

- Wrap Velvet Capital vaults to our vault adapter framework

- See :py:class:`eth_defi.velvet.vault.VelvetVault` for getting started

Notes:

- Velvet Capital API URLs
    - Swagger API https://eventsapi.velvetdao.xyz/swagge
    - Vault metadata https://api.velvet.capital/api/v3/portfolio/0xbdd3897d59843220927f0915aa943ddfa1214703r

"""
from functools import cached_property

import requests
from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.balances import fetch_erc20_balances_fallback
from eth_defi.token import fetch_erc20_details
from eth_defi.vault.base import VaultBase, VaultInfo, VaultSpec, TradingUniverse, VaultPortfolio
from eth_defi.velvet.deposit import deposit_to_velvet
from eth_defi.velvet.enso import swap_with_velvet_and_enso
from eth_defi.velvet.redeem import redeem_from_velvet_velvet

#: Signing API URL
DEFAULT_VELVET_API_URL = "https://eventsapi.velvetdao.xyz/api/v3"


class VelvetVaultInfo(VaultInfo):
    """Velvet Capital vault deployment info.

    - Fetched over proprietary API server
    """
    portfolioId: str
    portfolio: str  # Ethereum address
    name: str
    symbol: str
    public: bool
    initialized: bool
    confirmed: bool
    tokenExclusionManager: str  # Ethereum address
    rebalancing: str  # Ethereum address
    owner: str  # Ethereum address
    assetManagementConfig: str  # Ethereum address
    accessController: str  # Ethereum address
    feeModule: str  # Ethereum address
    vaultAddress: str  # Ethereum address
    gnosisModule: str  # Ethereum address
    whitelistedUsers: list[str]
    whitelistedTokens: list[str]
    whitelistAccessGrantedUsers: list[str]
    assetManagerAccessGrantedUsers: list[str]
    chainID: int
    chainName: str
    txnHash: str
    isDeleted: bool
    createdAt: str  # ISO 8601 datetime string
    updatedAt: str  # ISO 8601 datetime string
    creatorName: str
    description: str
    avatar: str  # URL


class VelvetVault(VaultBase):
    """Python interface for interacting with Velvet Capital vaults."""

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        api_url: str = DEFAULT_VELVET_API_URL,
    ):
        """
        :param spec:
            Address must be Velvet portfolio address (not vault address)
        """
        assert isinstance(web3, Web3)
        assert isinstance(spec, VaultSpec)
        self.web3 = web3
        self.api_url = api_url
        self.session = requests.Session()
        self.spec = spec

    def has_block_range_event_support(self):
        return False

    def has_deposit_distribution_to_all_positions(self):
        return True

    def get_flow_manager(self):
        raise NotImplementedError("Velvet does not support individual deposit/redemption events yet")

    def fetch_info(self) -> VelvetVaultInfo:
        """Read vault parameters from the chain."""
        url = f"https://api.velvet.capital/api/v3/portfolio/{self.spec.vault_address}"
        data = self.session.get(url).json()
        return data["data"]

    @cached_property
    def info(self) -> VelvetVaultInfo:
        return self.fetch_info()

    @property
    def vault_address(self) -> HexAddress:
        return self.info["vaultAddress"]

    @property
    def owner_address(self) -> HexAddress:
        return self.info["owner"]

    @property
    def portfolio_address(self) -> HexAddress:
        return self.info["portfolio"]

    @property
    def rebalance_address(self) -> HexAddress:
        return self.info["rebalancing"]

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

    def prepare_swap_with_enso(
        self,
        token_in: HexAddress | str,
        token_out: HexAddress | str,
        swap_amount: int,
        slippage: float,
        remaining_tokens: set,
        swap_all=False,
        from_: HexAddress | str | None = None,
    ) -> dict:
        """Prepare a swap transaction using Enso intent engine and Vevlet API.

        :param from_:
            Fill int the from field for the tx data.

            Used with Anvil and unlocked accounts.
        """

        if swap_all:
            remaining_tokens.remove(token_in)

        tx_data = swap_with_velvet_and_enso(
            rebalance_address=self.info["rebalancing"],
            owner_address=self.owner_address,
            token_in=token_in,
            token_out=token_out,
            swap_amount=swap_amount,
            slippage=slippage,
            remaining_tokens=remaining_tokens,
            chain_id=self.web3.eth.chain_id,
        )

        if from_:
            tx_data["from"] = Web3.to_checksum_address(from_)

        return tx_data

    def prepare_deposit_with_enso(
        self,
        from_: HexAddress | str,
        deposit_token_address: HexAddress | str,
        amount: int,
        slippage: float,
    ) -> dict:
        """Prepare a deposit transaction with Enso intents.

        - Velvet trades any incoming assets and distributes them on open positions

        :return:
            Ethereum transaction payload
        """
        tx_data = deposit_to_velvet(
            portfolio=self.portfolio_address,
            from_address=from_,
            deposit_token_address=deposit_token_address,
            amount=amount,
            chain_id=self.web3.eth.chain_id,
            slippage=slippage,
        )
        return tx_data

    def prepare_redemption(
        self,
        from_: HexAddress | str,
        amount: int,
        withdraw_token_address: HexAddress | str,
        slippage: float,
    ) -> dict:
        """Perform a redemption.

        :return:
            Ethereum transaction payload
        """

        chain_id = self.web3.eth.chain_id
        tx_data = redeem_from_velvet_velvet(
            from_address=Web3.to_checksum_address(from_),
            portfolio=Web3.to_checksum_address(self.portfolio_address),
            amount=amount,
            chain_id=chain_id,
            withdraw_token_address=Web3.to_checksum_address(withdraw_token_address),
            slippage=slippage,
        )
        return tx_data

    def _make_api_request(
        self,
        endpoint: str,
        params: dict | None = None,
    ) -> dict:
        url = f"{self.api_url}/{endpoint}"
        resp = self.session.get(url)
        resp.raise_for_status()
        data = resp.json()
        return data

    def fetch_denomination_token(self):
        raise NotImplementedError()

    def fetch_share_token(self):
        # Velvet's share token is the same contract as
        portfolio_address = self.info["portfolio"]
        return fetch_erc20_details(self.web3, portfolio_address)

    def fetch_nav(self):
        raise NotImplementedError()

    @property
    def symbol(self):
        raise NotImplementedError()

