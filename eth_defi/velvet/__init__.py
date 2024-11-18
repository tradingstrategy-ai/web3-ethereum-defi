"""


Velvet Capital URLs

- Swagger API https://eventsapi.velvetdao.xyz/swagge

- Vault metadata https://api.velvet.capital/api/v3/portfolio/0xbdd3897d59843220927f0915aa943ddfa1214703r

"""

import requests
from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.balances import fetch_erc20_balances_by_token_list
from eth_defi.vault.base import VaultBase, VaultInfo, VaultSpec, TradingUniverse, VaultPortfolio


#: Signing API URL
DEFAULT_VELVET_API_URL = "https://eventsapi.velvetdao.xyz/api/v3"


class VelvetVaultInfo(VaultInfo):
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
        assert isinstance(web3, Web3)
        assert isinstance(spec, VaultSpec)
        self.web3 = web3
        self.api_url = api_url
        self.session = requests.Session()
        self.spec = spec

    def has_block_range_event_support(self):
        return False

    def get_flow_manager(self):
        raise NotImplementedError("Velvet does not support individual deposit/redemption events yet")

    def fetch_info(self) -> VelvetVaultInfo:
        """Read vault parameters from the chain."""
        url = f"https://api.velvet.capital/api/v3/portfolio/{self.spec.vault_address}"
        data = self.session.get(url).json()
        return data["data"]

    def fetch_portfolio(
        self,
        universe: TradingUniverse,
        block_identifier: BlockIdentifier | None = None,
    ) -> VaultPortfolio:
        """Read the current token balances of a vault.

        - SHould be supported by all implementations
        """

        erc20_balances = fetch_erc20_balances_by_token_list(
            self.web3,
            self.spec.vault_address,
            universe.spot_token_addresses,
            block_identifier=block_identifier,
            decimalise=True,
        )
        return VaultPortfolio(
            spot_erc20=erc20_balances,
        )

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


