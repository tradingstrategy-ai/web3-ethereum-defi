from web3 import Web3

from eth_defi.vault.base import VaultBase, VaultSpec, VaultInfo


class LagoonVaultInfo(VaultInfo):
    """TODO: Add Lagoon vault info query"""
    pass


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

    def has_block_range_event_support(self):
        return True

    def get_flow_manager(self):
        raise NotImplementedError("Velvet does not support individual deposit/redemption events yet")

    def fetch_info(self) -> LagoonVaultInfo:
        """Read vault parameters from the chain."""



        raise NotImplementedError("Velvet does not support fetching info yet")

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

        erc20_balances = fetch_erc20_balances_multicall(
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
    ):
        """Prepare a deposit transaction with Enso intents.

        - Velvet trades any incoming assets and distributes them on open positions
        """
        tx_data = deposit_to_velvet(
            portfolio=self.portfolio_address,
            from_address=from_,
            deposit_token_address=deposit_token_address,
            amount=amount,
            chain_id=self.web3.eth.chain_id,
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

