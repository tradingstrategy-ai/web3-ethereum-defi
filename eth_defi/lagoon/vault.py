from dataclasses import asdict
from functools import cached_property

from eth_typing import HexAddress, BlockIdentifier, ChecksumAddress
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.balances import fetch_erc20_balances_fallback
from eth_defi.vault.base import VaultBase, VaultSpec, VaultInfo, TradingUniverse, VaultPortfolio

from safe_eth.safe import Safe

from ..abi import get_deployed_contract, encode_function_call
from ..safe.safe_compat import create_safe_ethereum_client


class LagoonVaultInfo(VaultInfo):
    """TODO: Add Lagoon vault info query"""

    #
    # Safe multisig core info
    #
    address: ChecksumAddress
    fallback_handler: ChecksumAddress
    guard: ChecksumAddress
    master_copy: ChecksumAddress
    modules: list[ChecksumAddress]
    nonce: int
    owners: list[ChecksumAddress]
    threshold: int
    version: str

    #
    # Lagoon vault info
    # TODO
    #


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

    def fetch_safe(self) -> Safe:
        """Use :py:meth:`safe` property for cached access"""
        client = create_safe_ethereum_client(self.web3)
        return Safe(
            self.safe_address,
            client,
        )

    def fetch_info(self) -> LagoonVaultInfo:
        """Use :py:meth:`info` property for cached access"""
        info = self.safe.retrieve_all_info()
        return asdict(info)

    @cached_property
    def info(self) -> LagoonVaultInfo:
        """Get info dictionary related to this deployment."""
        return self.fetch_info()

    @cached_property
    def safe(self) -> Safe:
        """Get the underlying Safe object used as an API from safe-eth-py library"""
        return self.fetch_safe()

    @property
    def address(self) -> HexAddress:
        """Alias of :py:meth:`safe_address`"""
        return self.safe_address

    @property
    def safe_address(self) -> HexAddress:
        """Get Safe multisig contract address"""
        return self.spec.vault_address

    @cached_property
    def safe_contract(self) -> Contract:
        return self.safe.contract

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

        TODO: This is MVP implementation. For better deposit/redemption tracking switch
        to use Lagoon events later.
        """
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

    def transact_through_module(
        self,
        func_call: ContractFunction,
        value: int = 0,
        operation=0,
    ) -> ContractFunction:
        """Create a multisig transaction using a module.

        - Calls `execTransactionFromModule` on Gnosis Safe contract

        - Executes a transaction as a multisig

        - Mostly used for testing w/whitelist ignore

        :param func_call:
            Bound smart contract function call

        :param value:
            ETH attached to the transaction

        :param operation:
            Gnosis enum.

            .. code-block:: text
                library Enum {
                    enum Operation {
                        Call,
                        DelegateCall
                    }
                }
        """
        contract_address = func_call.address
        data_payload = encode_function_call(func_call, func_call.arguments)
        contract = self.safe_contract
        bound_func = contract.functions.execTransactionFromModule(
            contract_address,
            value,
            data_payload,
            operation,
        )
        return bound_func

    def post_valuation_commitee(
        self,
        portfolio: VaultPortfolio,
    ):
        """Update the valuations of this vault.

        - Lagoon vault does not currently track individual positions, but takes a "total value" number

        - Updating this number also allows deposits and redemptions to proceed
        """
        raise NotImplementedError()



