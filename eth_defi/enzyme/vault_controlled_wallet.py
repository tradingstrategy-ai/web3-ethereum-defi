"""Vault owner wallet implementation."""

from dataclasses import dataclass, field
from typing import List, Collection, Any, Optional, Tuple

from eth_defi.abi import encode_function_call
from eth_defi.enzyme.generic_adapter import execute_calls_for_generic_adapter
from eth_typing import HexAddress

from eth_defi.enzyme.vault import Vault
from eth_defi.hotwallet import HotWallet, SignedTransactionWithNonce
from hexbytes import HexBytes
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.tx import AssetDelta


@dataclass(slots=True, frozen=True)
class EnzymeVaultTransaction:
    """Describing a transaction Enzyme vault performs.

    - This structure contains inputs needed to perform a vault transaction

    - Unlike regular transcation, Enzyme vault transactions need information
      about expected inbound and outbound assets in the transaction

    - Multiple vault contract calls could be packed into a single transaction,
      but we do not support it ATM

    .. note::

        TODO: kwargs support missing

    """

    #: The contract this transaction si for
    contract: Contract

    #: Which smart contract we are calling
    #:
    #: `ContractFunction` must be bound to its args, have function.args and/or function.kwargs set.
    function: ContractFunction

    #: How much gas the hot wallet can spend on this tx
    gas_limit: int

    #: If this transaction results to changes in the vault balance it must be listed here.
    #:
    #: This will check that any trade will
    #:
    #: - Give you the expected assets
    #:
    #: - Give you the expected slippage tolerance
    #:
    #: - Tells the vault what is the amount of the payment we make for a trade
    asset_deltas: List[AssetDelta] = field(default_factory=list)

    def __repr__(self):
        incoming = ", ".join(self.incoming_assets)
        spending = ", ".join(self.spend_assets)
        arg_str = ", ".join([str(a) for a in self.args])
        name = getattr(self.contract, "name", "<unnamed contract>")
        return f"Transaction with {name}.{self.function.fn_name}({arg_str}), incoming:[{incoming}], spending:[{spending}], gas:{self.gas_limit:,}"

    @property
    def args(self) -> List[Any]:
        """Unnamed arguments for this Solidity function."""
        return self.function.args or []

    @property
    def incoming_assets(self) -> List[HexAddress]:
        return [a.asset for a in self.asset_deltas if a.is_incoming()]

    @property
    def spend_assets(self) -> List[HexAddress]:
        return [a.asset for a in self.asset_deltas if a.is_spending()]

    @property
    def min_incoming_assets_amounts(self) -> List[int]:
        return [a.raw_amount for a in self.asset_deltas if a.is_incoming()]

    @property
    def spend_asset_amounts(self) -> List[int]:
        return [-a.raw_amount for a in self.asset_deltas if a.is_spending()]

    def encode_payload(self) -> HexBytes:
        """Get the data payload in Solidity's ABI encodePacked format"""
        return encode_function_call(self.function, self.args)

    def as_json_friendly_dict(self) -> dict:
        """Return human-readable, JSON'able, output for this transaction."""
        return {
            "contract": self.contract.address,
            "function": self.function.fn_name,
            "gas": self.gas_limit,
            "args": [str(a) for a in self.args],
            "asset_deltas": [a.as_json_friendly_dict() for a in self.asset_deltas],
        }


class VaultControlledWallet:
    """A wallet that transacts through Enzyme Vault as the fund owner.

    - Allows you to sign and broadcast transactions concerning Enzyme's vault as a vault owner.

    - Vault owner can only broadcast specific transactions allowed by Enzyme's GenericAdapter
    """

    def __init__(self, vault: Vault, hot_wallet: HotWallet):
        """Create a vault controlling wallet.

        :param hot_wallet:
            The fund deployment account as a EOA wallet.
        """
        self.vault = vault
        self.hot_wallet = hot_wallet

    @property
    def address(self) -> HexAddress:
        """Get the vault address."""
        return self.vault.address

    @property
    def generic_adapter(self) -> Contract:
        """Get the adapter configured for the vault."""
        generic_adapter = self.vault.generic_adapter
        assert generic_adapter is not None, "GenericAdapter not configured for Enzyme deployment"
        return generic_adapter

    def sign_transaction_with_new_nonce(
        self,
        tx: EnzymeVaultTransaction,
        gas_params: Optional[dict] = None,
    ) -> Tuple[SignedTransactionWithNonce, ContractFunction]:
        """Signs a transaction and allocates a nonce for it.

        :param:
            Ethereum transaction data as a dict. This is modified in-place to include nonce.

        :return:
            Signed transaction and encoding information.

            Tuple(Signed transaction ready for broadcasst, bound IntegrationManager.callOnExtension() function
        """

        assert isinstance(tx, EnzymeVaultTransaction), f"Got {tx}"

        vault = self.vault
        deployment = vault.deployment

        bound_call = execute_calls_for_generic_adapter(
            comptroller=vault.comptroller,
            external_calls=((tx.contract, tx.encode_payload()),),
            generic_adapter=self.generic_adapter,
            incoming_assets=tx.incoming_assets,
            integration_manager=deployment.contracts.integration_manager,
            min_incoming_asset_amounts=tx.min_incoming_assets_amounts,
            spend_asset_amounts=tx.spend_asset_amounts,
            spend_assets=tx.spend_assets,
        )

        tx_params = {
            "from": self.hot_wallet.address,
            "gas": tx.gas_limit,
        }

        if gas_params:
            tx_params.update(gas_params)

        tx = bound_call.build_transaction(tx_params)

        return self.hot_wallet.sign_transaction_with_new_nonce(tx), bound_call
