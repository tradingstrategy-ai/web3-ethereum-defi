"""High level interface to read Enzyme vaults.

See :py:class:`Vault`.
"""

from decimal import Decimal
from dataclasses import dataclass
from functools import cached_property
from typing import Collection, Optional
import logging

from web3.exceptions import ContractLogicError

from eth_defi.abi import get_deployed_contract, ZERO_ADDRESS
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.enzyme.deployment import EnzymeDeployment
from eth_defi.enzyme.price_feed import EnzymePriceFeed
from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.reader import Web3EventReader
from eth_defi.hotwallet import HotWallet
from eth_defi.token import TokenDetails, fetch_erc20_details

logger = logging.getLogger(__name__)


# Cannot be slots because of cached property
# @dataclass(slots=True)
@dataclass()
class Vault:
    """Enzyme vault wrapper.

    - Vaults are Enzyme Protocol "funds" where you have investors and assets

    - Investors have ownership of vault assets with a share token

    - Each vault has its denomiation asset, e.g. USDC that you use for the buy in

    - You buy-in to a vault using `buyShares`

    - Redemption is "in-kind" and you swap your share tokens to the tokens
      of underlying open positions and other assets

    - A separate vault owner (fund owner) can make the vault to perform trades

    Vault in Enzyme are presented by two smart contracts

    - Vault contract

    - Comptroller contract

    - `Vaults are upgradeable <https://specs.enzyme.finance/architecture/persistent>`__

    - `See Enzyme documentation for general information about vaults <https://docs.enzyme.finance/managers/setup/fund-basics>`__.

    - `See Enzyme spec for technical information about vaults <https://specs.enzyme.finance/>`__.

    Example:

    .. code-block:: python

        vault = Vault(vault_contract, comptroller_contract, deployment)
        print(f"Vault name: {vault.get_name()}")
        print(f"Denominated in: {vault.denomination_token}")
        raw_gross_asset_value = vault.get_gross_asset_value()
        print(f"Gross asset value: {vault.denomination_token.convert_to_decimals(raw_gross_asset_value):.2f} {vault.denomination_token.symbol}")

    """

    #: Vault smart contract
    #:
    #: The VaultLib contract contains the storage layout, event signatures, and logic for VaultProxy instances that are attached to this release.
    vault: Contract

    #: Comptroller smart contract
    #:
    #: A ComptrollerProxy is deployed per-fund, and it is the canonical contract for interacting with a fund in this release. It stores core release-level configuration and is attached to a VaultProxy via the latter's accessor role.
    #:
    #: Emits important events like `SharesBought`, `SharesRedeemed`
    comptroller: Contract

    #: Enzyme deployment reference
    #:
    #:
    deployment: EnzymeDeployment

    #: Our custom adapter for vault trades.
    #:
    #: See :py:mod:`~eth_defi.enzyme.generic_adapter.
    #:
    #: The Enzyme deployment does not know anything about the generic adapter.
    #: The generic adapter whitelists Enzyme's integration manager on the launch.
    #: Thus, it is not possible to resolve any GenericAdapter deployment,
    # ; but we need to track them ourselves for each chain.
    #:
    generic_adapter: Optional[Contract] = None

    #: Our custom EIP-3009 payment forwarder for the vault
    #:
    #: See :py:mod:`~eth_defi.usdc.transfer_with_authorization.
    #:
    #: Allows single click buy ins if there is no USDC in the vallet.
    #:
    payment_forwarder: Optional[Contract] = None

    #: Generic adapter guard contract
    #:
    #: - Generic adapter must be GuardedGenericAdapter
    #: - Resolved from GuardedGenericAdapter.guard() accessor
    #:
    guard_contract: Optional[Contract] = None

    #: Terms of service contract.
    #:
    #: - We must have a TermedVaultUSDCPaymentForwarder
    #: - Resolved from TermedVaultUSDCPaymentForwarder.termsOfService() accessor
    #:
    terms_of_service_contract: Optional[Contract] = None

    #: What was the block number when this vault was deployed
    #:
    deployed_at_block: int | None = None

    #: If this vault has a dedicated asset manager address set for it.
    #:
    #: Vaults can have multiple asset managers, but it is rare.
    #:
    asset_manager: str | None = None

    #: If this vault was set to be transferred to a owner multisig after the deployment.
    #:
    #: The owner needs to confirm the transfer.
    #:
    nominated_owner: str | None = None

    #: Drag the deployer hot wallet along
    #:
    #: Used for unit testing and such, so that we can easily
    #: configure guard with the same account that deployed it.
    #:
    deployer_hot_wallet: HotWallet | None = None

    def __repr__(self) -> str:
        return f"<Vault vault={self.vault.address} adapter={self.generic_adapter and self.generic_adapter.address} payment_forwader={self.payment_forwarder and self.payment_forwarder.address}>"

    def get_deployment_info(self) -> dict:
        """Get vault contract addresses to be saved as a deployment info.

        Useful for shell scripting.

        :return:
            Bunch of env mappings
        """
        return {
            "VAULT_ADDRESS": self.vault.address,
            "VAULT_ADAPTER_ADDRESS": self.generic_adapter.address if self.generic_adapter else "",
            "VAULT_PAYMENT_FORWARDER_ADDRESS": self.payment_forwarder.address if self.payment_forwarder else "",
            "VAULT_GUARD_ADDRESS": self.guard_contract.address if self.guard_contract else "",
            "VAULT_DEPLOYMENT_BLOCK_NUMBER": self.deployed_at_block or "",
            "VAULT_ASSET_MANAGER_ADDRESS": self.asset_manager or "",
            "VAULT_NOMINATED_OWNER_ADDRESS": self.nominated_owner or "",
        }

    @property
    def web3(self) -> Web3:
        """Web3 connection.

        Used for reading JSON-RPC calls
        """
        return self.vault.w3

    @property
    def address(self) -> HexAddress:
        """The address of the vault contract."""
        return self.vault.address

    @cached_property
    def denomination_token(self) -> TokenDetails:
        """Get the denominator token for withdrawal/deposit.

        - Read the token on-chain details.

        - Cache the results for the future calls

        :return:
            Usually ERC-20 details for USDC

        """
        return fetch_erc20_details(self.web3, self.get_denomination_asset())

    @cached_property
    def shares_token(self) -> TokenDetails:
        """Get the shares token for withdrawal/deposit.

        - Read the token on-chain details.

        - Cache the results for the future calls

        :return:
            ERC-20 details for a token with the fund name/symbol and 18 decimals.

        """
        return fetch_erc20_details(self.web3, self.get_shares_asset())

    def get_owner(self) -> HexAddress:
        """Who is the vault owner.

        Vault owner has special priviledges like calling the adapters.

        See `IVaultCore.sol`.
        """
        return self.vault.functions.getOwner().call()

    def get_name(self) -> str:
        """Get the name of the share token.

        See `SharesTokenBase.sol`.
        """
        return self.vault.functions.name().call()

    def get_symbol(self) -> str:
        """Get the symbol of share tokens.

        See `SharesTokenBase.sol`.
        """
        return self.vault.functions.symbol().call()

    def get_total_supply(self) -> int:
        """Get the number of share tokens.

        See `SharesTokenBase.sol`.
        """
        return self.vault.functions.totalSupply().call()

    def get_decimals(self) -> int:
        """Get the ERC-20 decimals of the shares.

        See `SharesTokenBase.sol`.
        """
        return self.vault.functions.decimals().call()

    def get_denomination_asset(self) -> HexAddress:
        """Get the reserve ERC-20 asset for this vault."""
        return self.comptroller.functions.getDenominationAsset().call()

    def get_shares_asset(self) -> HexAddress:
        """Get the shares ERC-20 token for this vault.

        Enzyme vault acts as ERC-20 contract as well.
        """
        return self.vault.address

    def get_tracked_assets(self) -> Collection[HexAddress]:
        """Get the list of assets this vault tracks.

        :return:
            List of ERC-20 addresses
        """
        return self.vault.functions.getTrackedAssets().call()

    def get_gross_asset_value(self) -> int:
        """Calculate the gross asset value (GAV) of the fund.

        Call the Solidity function that does this on the smart contract side.

        See `ComptrollerLib.sol`.

        :return:
            The gross assets in the denominated token.
        """
        return self.comptroller.functions.calcGav().call()

    def get_share_gross_asset_value(self) -> int:
        """Calculate the one share unit gross asset value (GAV) on the smart contract side.

        Call the Solidity function that does this on the smart contract side.

        See `ComptrollerLib.sol`.

        :return:
            TODO - no idea
        """
        return self.comptroller.functions.calcGrossShareValue().call()

    def get_share_count_for_user(self, user: HexAddress) -> int:
        """How mayn shares a user has.

        :return:
            Raw token amount
        """
        return self.vault.functions.balanceOf(user).call()

    def is_supported_asset(self, address: HexAddress) -> bool:
        """Does the vault support a particular asset.

        If the asset is not supported, policy manager

        At the moment, assets are whitelisted on Enzyme protocol level.
        """
        assert address.startswith("0x")
        value_interpreter = self.deployment.contracts.value_interpreter
        return value_interpreter.functions.isSupportedAsset(address).call()

    def fetch_deployment_event(self, reader: Web3EventReader, start_block=1) -> dict:
        """Get when the vault was deployed.

        .. warning::

            Because Ethereum nodes do not have indexes to get events per contract,
            this scan is going to take forever.

        :param start_block:
            The first block to scan

        :param reader:
            Event reader method used

        :return:
            Event log details

        :raise AssertionError:
            If blockchain does not have an event for the deplyoed vault
        """
        web3 = self.web3

        fund_deployer = self.deployment.contracts.fund_deployer

        filter = Filter.create_filter(
            fund_deployer.address,
            [fund_deployer.events.NewFundCreated],
        )

        last_block = web3.eth.block_number

        events_iter = reader(web3, start_block=start_block, end_block=last_block, filter=filter)

        for event in events_iter:
            return event

        raise AssertionError(f"No fund deployment event for {self.vault.address}, start block: {start_block:,}, end block: {last_block:,}")

    def fetch_denomination_token_usd_exchange_rate(self) -> Decimal:
        """Get the exchange rate between token/USD.

        Read the exchange rate using the configured Enzyme's VaultInterpreter
        and its Chainlink aggregators.

        :return:
            USD exchange rate
        """
        token = self.denomination_token
        price_feed = EnzymePriceFeed.fetch_price_feed(self.deployment, token)
        return price_feed.calculate_current_onchain_price(token)

    @staticmethod
    def fetch(
        web3: Web3,
        vault_address: str | HexAddress,
        generic_adapter_address: str | HexAddress | None = None,
        payment_forwarder: str | HexAddress | None = None,
        deployed_at_block: int | None = None,
        asset_manager: HexAddress | None = None,
        extra_addresses: dict[str, str] | None = None,
    ) -> "Vault":
        """Fetch Enzyme vault and deployment information based only on the vault address.

        Because vault does not have a way to cross-reference its contracts,
        we are now manually passing around a bunch of contracts and addresses.

        :param extra_addresses:
            Enzyme contract addresses we have deployed and we must pass along.

        :return:
            Enzyme vault instance with all the information populated in
        """

        contract_name = "VaultLib"
        vault_contract = get_deployed_contract(web3, f"enzyme/{contract_name}.json", vault_address)

        contract_name = "ComptrollerLib"
        comptroller_address = vault_contract.functions.getAccessor().call()
        comptroller_contract = get_deployed_contract(web3, f"enzyme/{contract_name}.json", comptroller_address)

        if not extra_addresses:
            extra_addresses = {}

        extra_addresses.update(
            {
                "comptroller_lib": comptroller_address,
                "generic_adapter": generic_adapter_address,
            }
        )

        deployment = EnzymeDeployment.fetch_deployment(
            web3,
            contract_addresses=extra_addresses,
        )

        if generic_adapter_address is not None:
            try:
                generic_adapter_contract = get_deployed_contract(web3, f"GuardedGenericAdapter.json", generic_adapter_address)
                generic_adapter_contract.functions.guard().call()  # Version check, will cause exception
            except (ValueError, ContractLogicError):
                # EthereumTester raises ValueError, but are the other exceptions?
                generic_adapter_contract = get_deployed_contract(web3, f"VaultSpecificGenericAdapter.json", generic_adapter_address)
        else:
            generic_adapter_contract = None

        terms_of_service_contract = None
        if payment_forwarder is not None:
            try:
                payment_forwarder_contract = get_deployed_contract(web3, f"TermedVaultUSDCPaymentForwarder.json", payment_forwarder)
                payment_forwarder_contract.functions.isTermsOfServiceEnabled().call()
                terms_of_service_address = payment_forwarder_contract.functions.termsOfService().call()
                terms_of_service_contract = get_deployed_contract(web3, "terms-of-service/TermsOfService.json", terms_of_service_address)

            except (ValueError, ContractLogicError):
                # EVMTester will give ValueError if the function does not exist
                # Legacy
                payment_forwarder_contract = get_deployed_contract(web3, f"VaultUSDCPaymentForwarder.json", payment_forwarder)
        else:
            payment_forwarder_contract = None

        guard_contract = None
        if generic_adapter_contract is not None:
            try:
                guard_address = generic_adapter_contract.functions.guard().call()
                guard_contract = get_deployed_contract(web3, f"guard/GuardV0.json", guard_address)
            except:
                pass

        nominated_owner = vault_contract.functions.getNominatedOwner().call()
        if nominated_owner == ZERO_ADDRESS:
            nominated_owner = None

        return Vault(
            vault_contract,
            comptroller_contract,
            deployment,
            generic_adapter_contract,
            payment_forwarder_contract,
            guard_contract,
            terms_of_service_contract,
            deployed_at_block=deployed_at_block,
            nominated_owner=nominated_owner,
            asset_manager=asset_manager,  # We cannot read asset manager back from the vault because it's just EVM hash map
        )
