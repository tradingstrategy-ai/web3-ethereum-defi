"""Vault adapter for Lagoon Finance protocol.

*Notes on active Lagoon development*:

Lagoon v0.5.0 changes to the original release

- Affect the vault interactions greatlty
- Vault initialisation parameters changed: fee registry and wrapped native token moved from parameters payload to constructor arguments
- Beacon proxy replaced with BeaconProxyFactory.createVault() patterns
- ``pendingSilo()`` accessor removed, now needs a direct storage slot read
- ``safe()`` accessor added

How to detect version:

- Call pendingSilo(): if reverts is a new version

How to get ``pendingSilo()``: see :py:meth:`eth_defi.lagoon.vault.LagoonVault.silo_address`.

Lagoon error code translation.

- `See Codeslaw page to translate custome errors to human readable <https://www.codeslaw.app/contracts/base/0xe50554ec802375c9c3f9c087a8a7bb8c26d3dedf?tab=abi>`__
"""

import datetime
import enum
import logging
from dataclasses import asdict
from decimal import Decimal
from functools import cached_property

import eth_abi
from eth.typing import BlockRange
from eth_typing import BlockIdentifier, ChecksumAddress, HexAddress
from hexbytes import HexBytes
from safe_eth.safe import Safe
from safe_eth.safe.exceptions import CannotRetrieveSafeInfoException
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction
from web3.exceptions import ContractLogicError, BadFunctionCallOutput

from eth_defi.vault.base import VaultFlowManager, VaultInfo, VaultSpec
from eth_defi.erc_7540.vault import ERC7540Vault

from ..abi import encode_function_call, get_deployed_contract, get_function_abi_by_name, get_function_selector, present_solidity_args
from ..erc_4626.core import ERC4626Feature
from ..event_reader.multicall_batcher import EncodedCall
from ..safe.safe_compat import create_safe_ethereum_client
from ..trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)

#: How much gas we use for valuation post
DEFAULT_LAGOON_POST_VALUATION_GAS = 500_000

#: How much gas we use for valuation post
DEFAULT_LAGOON_SETTLE_GAS = 500_000


class LagoonVaultInfo(VaultInfo):
    """Capture information about Lagoon vault deployment."""

    #: The ERC-20 token that nominates the vault assets
    asset: HexAddress

    #: Lagoon vault deployment info
    safe: HexAddress
    #: Lagoon vault deployment info
    whitelistManager: HexAddress  # Can be 0x0000000000000000000000000000000000000000
    #: Lagoon vault deployment info
    feeReceiver: HexAddress
    #: Lagoon vault deployment info
    feeRegistry: HexAddress
    #: Lagoon vault deployment info
    valuationManager: HexAddress

    #: Safe multisig core info
    address: ChecksumAddress
    #: Safe multisig core info
    fallback_handler: ChecksumAddress
    #: Safe multisig core info
    guard: ChecksumAddress
    #: Safe multisig core info
    master_copy: ChecksumAddress
    #: Safe multisig core info
    modules: list[ChecksumAddress]
    #: Safe multisig core info
    nonce: int
    #: Safe multisig core info
    owners: list[ChecksumAddress]
    #: Safe multisig core info
    threshold: int
    #: Safe multisig core info
    version: str


class LagoonVersion(enum.Enum):
    """Figure out Lagoon version."""

    legacy = "legacy"
    v_0_5_0 = "v0.5.0"
    v_0_4_0 = "v0.4.0"


class LagoonVault(ERC7540Vault):
    """Python interface for interacting with Lagoon Finance vaults.

    For information see :py:class:`~eth_defi.vault.base.VaultBase` base class documentation.

    Example vault: https://basescan.org/address/0x6a5ea384e394083149ce39db29d5787a658aa98a#readContract

    Notes

    - Vault contract knows about Safe, Safe does not know about the Vault
    - Ok so for settlement you dont have to worry about this metric, the only thing you have to value is the assets inside the safe (what you currently have under management) and update the NAV of the vault by calling updateNewTotalAssets (ex: if you have 1M inside the vault and 500K pending deposit you only need to call updateTotalAssets with the 1M that are currently inside the safe). Then, to settle you just call settleDeposit and the vault calculate everything for you.
    - To monitor the pending deposits it's a bit more complicated. You have to check the balanceOf the pendingSilo contract (0xAD1241Ba37ab07fFc5d38e006747F8b92BB217D5) in term of underlying (here USDC) for pending deposit and in term of shares (so the vault itself) for pending withdraw requests

    Lagoon tokens can be in

    - Safe: Tradeable assets
    - Silo: pending deposits (USDC)
    - Vault: pending redemptions (USDC)
    - User wallets: after `deposit()` have been called share tokens are moved to the user wallet
    """

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        trading_strategy_module_address: HexAddress | None = None,
        token_cache: dict | None = None,
        vault_abi: str | None = None,
        features: set[ERC4626Feature] = None,
    ):
        """
        :param spec:
            Address must be Lagoon vault  address (not Safe address)

        :param trading_strategy_module_address:
            TradingStrategyModuleV0 enabled on Safe for automated trading.

            If not given, not known.

        :param vault_abi:
            ABI filename we use.

            Lagoon has different versions.

            None = autodetect.
        """
        super().__init__(web3, spec, features=features or {ERC4626Feature.lagoon_like, ERC4626Feature.erc_7540_like}, token_cache=token_cache)
        self.trading_strategy_module_address = trading_strategy_module_address

        if vault_abi is None:
            version = self.version
            if version == LagoonVersion.legacy:
                vault_abi = "lagoon/Vault.json"
            else:
                vault_abi = "lagoon/v0.5.0/Vault.json"

        self.vault_abi = vault_abi
        self.check_version_compatibility()

    def __repr__(self):
        return f"<Lagoon vault:{self.vault_contract.address} safe:{self.safe_address}>"

    def fetch_version(self) -> LagoonVersion:
        """Figure out Lagoon version.

        - Poke the smart contract with probe functions to get version
        - Specifically call pendingSilo() that has been removed because the contract is too big
        - Our ABI definitions and callign conventions change between Lagoon versions
        """

        probe_call = EncodedCall.from_keccak_signature(
            function="version",
            address=Web3.to_checksum_address(self.spec.vault_address),
            signature=Web3.keccak(text="version()")[0:4],
            data=b"",
            extra_data={},
        )
        try:
            result = probe_call.call(self.web3, block_identifier="latest")
            decoded = eth_abi.decode(["string"], result)
            decoded_version = decoded[0]
            if decoded_version == "v0.4.0":
                return LagoonVersion.v_0_4_0
            elif decoded_version == "v0.5.0":
                return LagoonVersion.v_0_5_0
            else:
                raise NotImplementedError(f"Unknown Lagoon version {decoded_version} for vault {self.spec.vault_address}")
        except (ValueError, ContractLogicError) as e:
            pass

        probe_call = EncodedCall.from_keccak_signature(
            function="pendingSilo",
            address=Web3.to_checksum_address(self.spec.vault_address),
            signature=Web3.keccak(text="pendingSilo()")[0:4],
            data=b"",
            extra_data={},
        )

        try:
            probe_call.call(self.web3, block_identifier="latest")
            version = LagoonVersion.legacy
        except (ValueError, ContractLogicError) as e:
            version = LagoonVersion.v_0_5_0

        return version

    def fetch_trading_strategy_module_version(self) -> str | None:
        """ "Perform deployed smart contract probing.

        :return:
            v0.1.0 or v0.1.1.

            None if not TS module associated.
        """

        if not self.trading_strategy_module_address:
            return None

        probe_call = EncodedCall.from_keccak_signature(
            function="getTradingStrategyModuleVersion",
            address=Web3.to_checksum_address(self.trading_strategy_module_address),
            signature=Web3.keccak(text="getTradingStrategyModuleVersion()")[0:4],
            data=b"",
            extra_data={},
        )

        try:
            version_bytes = probe_call.call(self.web3, block_identifier="latest")
            return version_bytes.decode("utf-8")
        except (ValueError, ContractLogicError) as e:
            # getTradingStrategyModuleVersion() was not yet created
            return "v0.1.0"

    def check_version_compatibility(self):
        """Throw if there is mismatch between ABI and contract exposed EVM calls"""
        if self.version != LagoonVersion.legacy:
            # Check we have correct ABI file loaded
            settle_deposit_abi = get_function_abi_by_name(self.vault_contract, "settleDeposit")
            # function settleDeposit(uint256 _newTotalAssets) public override onlySafe onlyOpen {
            assert len(settle_deposit_abi["inputs"]) == 1, f"Wrong old Lagoon ABI file loaded for {self.vault_address}"

        # We have one broken Lagoon deployment on Arbitrum with 0x0 as Safe address
        # assert self.safe is not None, f"Safe multisig address is not set for {self.vault_address}"

    @cached_property
    def version(self) -> LagoonVersion:
        """Get Lagoon version.

        - Cached property to avoid multiple calls
        """
        version = self.fetch_version()
        return version

    @cached_property
    def trading_strategy_module_version(self) -> str:
        """Get TradingStrategyModuleV0 contract ABI version.

        - Subject to change, development in progress
        """
        version = self.fetch_trading_strategy_module_version()
        return version

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_contract(
            self.web3,
            self.vault_abi,
            self.spec.vault_address,
        )

    def has_block_range_event_support(self):
        return True

    def has_deposit_distribution_to_all_positions(self):
        return False

    def get_flow_manager(self) -> "LagoonFlowManager":
        return LagoonFlowManager(self)

    def fetch_safe(self, address) -> Safe:
        """Use :py:meth:`safe` property for cached access"""
        client = create_safe_ethereum_client(self.web3)
        return Safe(
            address,
            client,
        )

    @cached_property
    def trading_strategy_module(self) -> Contract:
        assert self.trading_strategy_module_address, "TradingStrategyModuleV0 address must be separately given in the configuration"
        return get_deployed_contract(
            self.web3,
            "safe-integration/TradingStrategyModuleV0.json",
            self.trading_strategy_module_address,
        )

    def fetch_vault_info(self) -> dict:
        """Get all information we can extract from the vault smart contracts."""
        vault = self.vault_contract
        try:
            roles_tuple = vault.functions.getRolesStorage().call()
            whitelistManager, feeReceiver, safe, feeRegistry, valuationManager = roles_tuple
            broken = False
        except (ValueError, BadFunctionCallOutput) as e:
            logger.error("Failed to fetch Lagoon roles for vault %s, error: %s", self.vault_address, e, exc_info=e)
            whitelistManager = feeReceiver = safe = feeRegistry = valuationManager = None
            broken = True

        asset = vault.functions.asset().call()
        return {
            "address": vault.address,
            "whitelistManager": whitelistManager,
            "feeReceiver": feeReceiver,
            "feeRegistry": feeRegistry,
            "valuationManager": valuationManager,
            "safe": safe,
            "asset": asset,
            "tradingStrategyModuleAddress": self.trading_strategy_module_address,
            "broken": broken,
        }

    def fetch_info(self) -> LagoonVaultInfo:
        """Use :py:meth:`info` property for cached access.

        :return:
            See :py:class:`LagoonVaultInfo`
        """
        vault_info = self.fetch_vault_info()
        safe_address = vault_info["safe"]

        safe_info_dict = {}
        # We have broken Lagoon contract on Arbitrum with 0x0 as Safe address
        if safe_address:
            try:
                safe = self.fetch_safe(safe_address)
                safe_info_dict = asdict(safe.retrieve_all_info())
                del safe_info_dict["address"]  # Key conflict
            except CannotRetrieveSafeInfoException as e:
                # Safe is not a safe but EOA address
                # https://arbiscan.io/address/0xb03EdA433d5bB1ef76b63087D4042A92C02822bD
                cause = getattr(e, "__cause__", None)
                logger.error(f"Lagoon Safe info fetch failed, exception {e} (cause: {cause}) for Safe {safe}, vault {self.vault_address}, vault info is {vault_info}", exc_info=cause)

        return vault_info | safe_info_dict

    @property
    def safe_address(self) -> HexAddress:
        """Get Safe multisig contract address"""
        return self.info["safe"]

    @cached_property
    def safe(self) -> Safe:
        """Get the underlying Safe object used as an API from safe-eth-py library.

        - Warps Safe Contract using Gnosis's in-house library
        """
        return self.fetch_safe(self.info["safe"])

    @cached_property
    def safe_contract(self) -> Contract:
        """Safe multisig as a contract.

        - Interact with Safe multisig ABI
        """
        return self.safe.contract

    @property
    def valuation_manager(self) -> HexAddress:
        """Valuation manager role on the vault."""
        return self.info["valuationManager"]

    @cached_property
    def silo_address(self) -> HexAddress:
        """Pending Silo contract address.

        :return:
            Checksummed Silo contract addrewss "pendingSilo".
        """

        # Because of EVM is such piece of shit,
        # Lagoon team removed pendingSilo() function
        # as they hit the contract size limit
        vault_contract = self.vault_contract
        if self.version in (LagoonVersion.v_0_5_0, LagoonVersion.v_0_4_0):
            web3 = self.web3
            # Magic storage slot for Silo address
            slot = "0x5c74d456014b1c0eb4368d944667a568313858a3029a650ff0cb7b56f8b57a08"
            value = web3.eth.get_storage_at(vault_contract.address, slot)
            # Take the last 20 bytes as the address
            silo_address = Web3.to_checksum_address("0x" + value.hex()[-40:])
        else:
            silo_address = vault_contract.functions.pendingSilo().call()
        return silo_address

    @cached_property
    def silo_contract(self) -> Contract:
        """Pending Silo contract.

        - This contract does not have any functionality, but stores deposits (pending USDC) and redemptions (pending share token)
        """
        return get_deployed_contract(self.web3, "lagoon/Silo.json", self.silo_address)

    def transact_via_exec_module(
        self,
        func_call: ContractFunction,
        value: int = 0,
        operation=0,
    ) -> ContractFunction:
        """Create a multisig transaction using a module.

        - Calls `execTransactionFromModule` on Gnosis Safe contract

        - Executes a transaction as a multisig

        - Mostly used for testing w/whitelist ignore

        .. warning ::

            A special gas fix is needed, because `eth_estimateGas` seems to fail for these Gnosis Safe transactions.

        Example:

        .. code-block:: python

            # Then settle the valuation as the vault owner (Safe multisig) in this case
            settle_call = vault.settle()
            moduled_tx = vault.transact_through_module(settle_call)
            tx_data = moduled_tx.build_transaction(
                {
                    "from": asset_manager,
                }
            )
            # Normal estimate_gas does not give enough gas for
            # Safe execTransactionFromModule() transaction for some reason
            gnosis_gas_fix = 1_000_000
            tx_data["gas"] = web3.eth.estimate_gas(tx_data) + gnosis_gas_fix
            tx_hash = web3.eth.send_transaction(tx_data)
            assert_execute_module_success(web3, tx_hash)

        :param func_call:
            Bound smart contract function call

        :param value:
            ETH attached to the transaction

        :param operation:
            Gnosis enum.

            Call = 0, DelegateCall = 1.
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

    def transact_via_trading_strategy_module(
        self,
        func_call: ContractFunction,
        value: int = 0,
        abi_version: str = None,
    ) -> ContractFunction:
        """Create a Safe multisig transaction using TradingStrategyModuleV0.

        :param module:
            Deployed TradingStrategyModuleV0 contract that is enabled on Safe.

        :param func_call:
            Bound smart contract function call

        :param abi_version:
            Use specific TradingStrategyModuleV0 ABI version.

        :return:
            Bound Solidity functionc all you need to turn to a transaction

        """
        assert self.trading_strategy_module_address is not None, f"TradingStrategyModuleV0 address not set for vault {self.vault_address}"
        contract_address = func_call.address
        data_payload = encode_function_call(func_call, func_call.arguments)

        module_version = abi_version or self.trading_strategy_module_version

        logger.info(
            "Lagoon: Wrapping call to TradingStrategyModuleV0 %s. Target: %s, function: %s (0x%s), args: %s, payload is %d bytes",
            module_version,
            contract_address,
            func_call.fn_name,
            get_function_selector(func_call).hex(),
            present_solidity_args(func_call.arguments),
            len(data_payload),
        )

        if module_version == "v0.1.0":
            bound_func = self.trading_strategy_module.functions.performCall(
                contract_address,
                data_payload,
            )
        else:
            # Value parameter was added for Orderly
            bound_func = self.trading_strategy_module.functions.performCall(
                contract_address,
                data_payload,
                value,
            )
        return bound_func

    def post_new_valuation(
        self,
        total_valuation: Decimal,
    ) -> ContractFunction:
        """Update the valuations of this vault.

        - Lagoon vault does not currently track individual positions, but takes a "total value" number

        - Updating this number also allows deposits and redemptions to proceed

        Notes:

            How can I post a valuation commitee update 1. as the valuationManager, call the function updateNewTotalAssets(_newTotalAssets) _newTotalAssets being expressed in underlying in its smallest unit for usdc, it would  with its 6 decimals. Do not take into account requestDeposit and requestRedeem in your valuation

            2. as the safe, call the function settleDeposit()

        :param total_valuation:
            The vault value nominated in :py:meth:`denomination_token`.

        :return:
            Bound contract function that can be turned to a transaction
        """
        logger.info("Updating vault %s valuation to %s %s", self.address, total_valuation, self.denomination_token.symbol)
        raw_amount = self.denomination_token.convert_to_raw(total_valuation)
        bound_func = self.vault_contract.functions.updateNewTotalAssets(raw_amount)
        return bound_func

    def settle_via_trading_strategy_module(self, valuation: Decimal = None, abi_version: None = None) -> ContractFunction:
        """Settle the new valuation and deposits.

        - settleDeposit will also settle the redeems request if possible. If there are enough assets in the safe it will settleRedeem
          It there are not enough assets, it will only settleDeposit.

        - if there is nothing to settle: no deposit and redeem requests you can still call settleDeposit/settleRedeem to validate the new nav

        - If there is not enough USDC to redeem, the transaction will revert

        :param abi_version:
            Use specific ABI version.

        :param raw_amount:
            Needed in Lagoon v0.5+
        """
        assert self.trading_strategy_module_address, "TradingStrategyModuleV0 not configured"
        if self.version != LagoonVersion.legacy:
            assert valuation is not None, f"Lagoon v0.5.0+ needs valuation raw amount when calling settle"
            assert isinstance(valuation, Decimal), f"Expected DEcimal, got {type(valuation)}"
            raw_amount = self.denomination_token.convert_to_raw(valuation)
        else:
            raw_amount = None
        block = self.web3.eth.block_number
        pending = self.get_flow_manager().fetch_pending_deposit(block)
        logger.info(
            "Settling deposits for the block %d, we have %s %s deposits pending, raw mount is %s",
            block,
            pending,
            self.underlying_token.symbol,
            raw_amount,
        )
        if raw_amount is not None:
            bound_func = self.vault_contract.functions.settleDeposit(raw_amount)
        else:
            bound_func = self.vault_contract.functions.settleDeposit()
        return self.transact_via_trading_strategy_module(bound_func, abi_version=abi_version)

    def post_valuation_and_settle(
        self,
        valuation: Decimal,
        asset_manager: HexAddress,
        gas=1_000_000,
    ) -> HexBytes:
        """Do both new valuation and settle.

        - Quickhand method for asset_manager code

        - Only after this we can read back

        - Broadcasts two transactions and waits for the confirmation

        - If there is not enough USDC to redeem, the second transaction will fail with revert

        :return:
            The transaction hash of the settlement transaction
        """

        assert isinstance(valuation, Decimal)

        bound_func = self.post_new_valuation(valuation)
        tx_hash = bound_func.transact({"from": asset_manager, "gas": gas})
        assert_transaction_success_with_explanation(self.web3, tx_hash)

        if self.version == LagoonVersion.legacy:
            bound_func = self.settle_via_trading_strategy_module()
            tx_hash = bound_func.transact({"from": asset_manager, "gas": gas})
            assert_transaction_success_with_explanation(self.web3, tx_hash)
        else:
            # New secure method safe for frontrunning
            logger.info("Settling new valuation using settleDeposit(_newTotalAssets), valuation is %s", valuation)
            bound_func = self.settle_via_trading_strategy_module(valuation)
            tx_hash = bound_func.transact({"from": asset_manager, "gas": gas})
            assert_transaction_success_with_explanation(self.web3, tx_hash)

        return tx_hash

    def request_deposit(
        self,
        depositor: HexAddress,
        raw_amount: int,
        check_allowance=True,
        check_balance=True,
    ) -> ContractFunction:
        """Build a deposit transction.

        - Phase 1 of deposit before settlement
        - Used for testing
        - Must be approved() first
        - Uses the vault underlying token (USDC)

        .. note::

            Legacy. Use :py:meth:`get_deposit_manager` instead.

        :param raw_amount:
            Raw amount in underlying token
        """
        assert type(raw_amount) == int, f"Deposit amount must be int, got {raw_amount} {type(raw_amount)}"
        underlying = self.underlying_token
        existing_balance = underlying.fetch_raw_balance_of(depositor)
        if check_balance:
            assert existing_balance >= raw_amount, f"Cannot deposit {underlying.symbol} by {depositor}. Have: {existing_balance}, asked to deposit: {raw_amount}"
        existing_allowance = underlying.contract.functions.allowance(depositor, self.vault_address).call()
        if check_allowance:
            assert existing_allowance >= raw_amount, f"Cannot deposit {underlying.symbol} by {depositor}. Allowance: {existing_allowance}, asked to deposit: {raw_amount}"
        return self.vault_contract.functions.requestDeposit(
            raw_amount,
            depositor,
            depositor,
        )

    def finalise_deposit(self, depositor: HexAddress, raw_amount: int | None = None) -> ContractFunction:
        """Move shares we received to the user wallet.

        - Phase 2 of deposit after settlement
        """

        if raw_amount is None:
            raw_amount = self.vault_contract.functions.maxDeposit(depositor).call()

        return self.vault_contract.functions.deposit(raw_amount, depositor)

    def request_redeem(self, depositor: HexAddress, raw_amount: int) -> ContractFunction:
        """Build a redeem transction.

        - Phase 1 of redemption, before settlement
        - Used for testing
        - Sets up a redemption request for X shares

        :param raw_amount:
            Raw amount in share token
        """
        assert type(raw_amount) == int, f"Got {raw_amount} {type(raw_amount)}"
        shares = self.share_token
        block_number = self.web3.eth.block_number

        # Check we have shares
        owned_raw_amount = shares.fetch_raw_balance_of(depositor, block_number)
        assert owned_raw_amount >= raw_amount, f"Cannot redeem, has only {owned_raw_amount} shares when {raw_amount} needed"

        human_amount = shares.convert_to_decimals(raw_amount)
        total_shares = self.fetch_total_supply(block_number)
        logger.info("Setting up redemption for %s %s shares out of %s, for %s", human_amount, shares.symbol, total_shares, depositor)
        return self.vault_contract.functions.requestRedeem(
            raw_amount,
            depositor,
            depositor,
        )

    def finalise_redeem(self, depositor: HexAddress, raw_amount: int | None = None) -> ContractFunction:
        """Move redeemed assets to the user wallet.

        - Phase 2 of the redemption
        """

        assert type(depositor) == str, f"Got {depositor} {type(depositor)}"

        if raw_amount is None:
            raw_amount = self.vault_contract.functions.maxRedeem(depositor).call()

        return self.vault_contract.functions.redeem(raw_amount, depositor, depositor)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get Lagoon vault rates"""
        rates = self.vault_contract.functions.feeRates().call(block_identifier=block_identifier)
        return rates[0] / 10_000

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get Lagoon vault rates"""
        # struct Rates {
        #     uint16 managementRate;
        #     uint16 performanceRate;
        # }
        rates = self.vault_contract.functions.feeRates().call(block_identifier=block_identifier)
        return rates[1] / 10_000

    def is_trading_strategy_module_enabled(self) -> bool:
        """Check if TradingStrategyModuleV0 is enabled on the Safe multisig."""
        assert self.trading_strategy_module_address, "TradingStrategyModuleV0 address must be separately given in the configuration"
        return self.safe.contract.functions.isModuleEnabled(self.trading_strategy_module_address).call() == True

    def get_deposit_manager(self) -> "eth_defi.lagoon.deposit_redeem.ERC7540DepositManager":
        from eth_defi.lagoon.deposit_redeem import ERC7540DepositManager

        return ERC7540DepositManager(self)

    def get_link(self, referral: str | None = None) -> str:
        return f"https://app.lagoon.finance/{self.chain_id}/{self.vault_address}"


class LagoonFlowManager(VaultFlowManager):
    """Manage deposit/redemption queue for Lagoon.

    - Lagoon uses `ERC-7540 <https://eips.ethereum.org/EIPS/eip-7540>`__ Asynchronous ERC-4626 Tokenized Vaults for
      deposits and redemptions flow

    On the Lagoon flow:

        Ok so for settlement you dont have to worry about this metric, the only thing you have to value is the assets inside the safe (what you currently have under management) and update the NAV of the vault by calling updateNewTotalAssets (ex: if you have 1M inside the vault and 500K pending deposit you only need to call updateTotalAssets with the 1M that are currently inside the safe). Then, to settle you just call settleDeposit and the vault calculate everything for you.

        To monitor the pending deposits it's a bit more complicated. You have to check the balanceOf the pendingSilo contract (0xAD1241Ba37ab07fFc5d38e006747F8b92BB217D5) in term of underlying (here USDC) for pending deposit and in term of shares (so the vault itself) for pending withdraw requests

    """

    def __init__(self, vault: LagoonVault) -> None:
        self.vault = vault

    def fetch_pending_redemption(self, block_identifier: BlockIdentifier) -> Decimal:
        silo = self.vault.silo_contract
        return self.vault.share_token.fetch_balance_of(silo.address, block_identifier)

    def fetch_pending_deposit(self, block_identifier: BlockIdentifier) -> Decimal:
        silo = self.vault.silo_contract
        return self.vault.underlying_token.fetch_balance_of(silo.address, block_identifier)

    def fetch_pending_deposit_events(self, range: BlockRange) -> None:
        raise NotImplementedError()

    def fetch_pending_redemption_event(self, range: BlockRange) -> None:
        raise NotImplementedError()

    def fetch_processed_deposit_event(self, range: BlockRange) -> None:
        pass

    def fetch_processed_redemption_event(self, vault: VaultSpec, range: BlockRange) -> None:
        raise NotImplementedError()

    def calculate_underlying_needed_for_redemptions(self, block_identifier: BlockIdentifier) -> Decimal:
        """How much underlying token (USDC) we are going to need on the next redemption cycle.

        :return:
            Raw token amount
        """
        # How many shares we have pending for the redemption
        shares_pending = self.fetch_pending_redemption(block_identifier)
        share_price = self.vault.fetch_share_price(block_identifier)
        return shares_pending * share_price

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """TODO: Add vault specific lock up period retrieval."""
        return datetime.timedelta(days=3)
