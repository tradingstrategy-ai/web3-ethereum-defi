"""Llama Lend V1 and V2 lender vaults.

Lenders supply the borrowed token and earn borrower interest. LLAMMA manages
borrower collateral; the controller holds the market's unborrowed liquidity.
"""

import datetime
import logging
from collections.abc import Iterable
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from requests.exceptions import RequestException
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import Web3Exception

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.provider.rpcdb import normalise_rpc_error
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import INSTANT_WITHDRAWAL_PERIOD, VaultHistoricalRead, VaultHistoricalReader, VaultTechnicalRisk, WithdrawalPeriod

logger = logging.getLogger(__name__)

#: Exact return-data length for a scalar EVM uint256.
UINT256_RESULT_LENGTH = 32

#: Shared V1/V2 controller debt getter.
TOTAL_DEBT_SELECTOR = Web3.keccak(text="total_debt()")[:4]


def _calculate_lending_metrics(total_assets_raw: int, total_debt_raw: int, token: TokenDetails) -> tuple[Decimal, Percent]:
    """Calculate lender liquidity and utilisation from controller debt.

    Both versions include debt in ``totalAssets()``; V2 additionally deducts
    protocol fees. Subtracting debt therefore gives net withdrawable liquidity.
    See the `V1 accounting <https://docs.curve.finance/developer/lending/contracts/vault/>`__
    and `V2 accounting <https://docs.curve.finance/developer/llamalend-v2/vault/>`__.

    :param total_assets_raw: Vault assets in raw denomination-token units.
    :param total_debt_raw: Controller debt in raw denomination-token units.
    :param token: Denomination token used to convert liquidity to human units.
    :return: Available liquidity and utilisation between zero and one.
    """
    liquidity = token.convert_to_decimals(max(total_assets_raw - total_debt_raw, 0))
    utilisation = min(total_debt_raw / total_assets_raw, 1.0) if total_assets_raw else 0.0
    return liquidity, utilisation


class LlamaLendVaultHistoricalReader(ERC4626HistoricalReader):
    """Read Llama Lend vault core data + utilisation."""

    def __init__(self, vault: "LlamaLendVault", stateful: bool) -> None:  # noqa: FBT001
        """Resolve the controller during the scanner's parallel reader preparation.

        A failed lookup disables only this reader's lending metrics for the scan,
        leaving core vault observations and other vaults available.

        :param vault: Llama lender vault to read.
        :param stateful: Enable the existing adaptive reader state.
        """
        super().__init__(vault, stateful)
        self._total_debt_call: EncodedCall | None = None
        try:
            self._total_debt_call = vault.create_total_debt_call(first_block_number=self.first_block)
        except (Web3Exception, ValueError, RequestException) as exc:
            logger.warning("Cannot resolve Llama controller for %s (%s, code %s)", vault.address, type(exc).__name__, normalise_rpc_error(exc)[0])

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_utilisation_calls()

    def construct_utilisation_calls(self) -> Iterable[EncodedCall]:
        """Read controller debt alongside the core vault assets.

        Deposited tokens are held by the controller, not the vault.

        :return: Debt multicall attributed to this vault.
        """
        if self.vault.denomination_token is None or self._total_debt_call is None:
            return
        yield self._total_debt_call

    def process_utilisation_result(
        self,
        call_by_name: dict[str, EncodedCallResult],
    ) -> tuple[Decimal | None, Percent | None]:
        """Decode controller debt without treating failed calls as zero debt.

        Uses the same fee-adjusted accounting as the spot methods.

        :param call_by_name: Multicall results keyed by function name.
        :return: Liquidity and utilisation, or missing values on a failed read.
        """
        debt_result = call_by_name.get("total_debt")
        assets_result = call_by_name.get("total_assets")
        if self.vault.denomination_token is None or self._total_debt_call is None:
            return None, None
        if not all(r is not None and r.success and len(r.result) == UINT256_RESULT_LENGTH for r in (assets_result, debt_result)):
            logger.warning("Cannot read Llama lending metrics for %s: missing, failed or malformed assets/debt result", self.vault.address)
            return None, None
        return _calculate_lending_metrics(
            int.from_bytes(assets_result.result, "big"),
            int.from_bytes(debt_result.result, "big"),
            self.vault.denomination_token,
        )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        row = super().process_result(block_number, timestamp, call_results)
        row.performance_fee = row.management_fee = 0.0
        call_by_name = self.dictify_multicall_results(block_number, call_results)
        row.available_liquidity, row.utilisation = self.process_utilisation_result(call_by_name)
        if row.available_liquidity is None:
            row.errors = [*(row.errors or []), "Llama lending metrics unavailable: missing, failed or malformed assets/debt read"]
        return row


class LlamaLendVault(ERC4626Vault):
    """Llama Lend vaults.

    Lenders deposit the borrowed token into an isolated market and receive
    ERC-4626 shares. Borrower interest increases the share value. LLAMMA
    rebalances borrower collateral through soft liquidations; it does not
    guarantee lenders against losses or insufficient withdrawal liquidity.

    Both versions expose controller debt. V2 deducts accrued protocol fees from
    vault assets, so ``totalAssets() - total_debt()`` measures net liquidity.

    - `LLAMMA explained <https://docs.curve.finance/crvUSD/amm/>`__
    - `Vault smart contract code: <https://arbiscan.io/address/0xe296ee7f83d1d95b3f7827ff1d08fe1e4cf09d8d#code>`__
    - `Llama Lend markets <https://www.curve.finance/llamalend/ethereum/markets>`__
    """

    @cached_property
    def name(self) -> str:
        """Get vault name."""
        return f"Llama Lend {self.collateral_token.symbol} / {self.denomination_token.symbol}"

    def get_risk(self) -> VaultTechnicalRisk | None:
        return VaultTechnicalRisk.low

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="llama-lend/Vault.json",
        )

    @cached_property
    def controller_address(self) -> HexAddress:
        """Resolve the immutable controller associated with this vault.

        V1 and V2 vaults expose the same controller getter.

        :return: Controller address cached for subsequent scans.
        """
        return self.vault_contract.functions.controller().call()

    def create_total_debt_call(self, first_block_number: int | None = None) -> EncodedCall:
        """Encode the shared V1/V2 controller debt getter.

        The call targets the controller while retaining the vault attribution
        used to group historical multicall results. See the
        `controller reference <https://docs.curve.finance/developer/llamalend-v2/lend-controller/>`__.

        :param first_block_number: Earliest block at which to execute the call.
        :return: Encoded ``total_debt()`` call.
        """
        return EncodedCall.from_keccak_signature(
            address=self.controller_address,
            function="total_debt",
            signature=TOTAL_DEBT_SELECTOR,
            data=b"",
            extra_data={"vault": self.address},
            first_block_number=first_block_number,
        )

    @cached_property
    def borrowed_token(self) -> TokenDetails:
        """The underlying token supplied by lenders and borrowed from the market."""
        addr = self.vault_contract.functions.borrowed_token().call()
        return fetch_erc20_details(
            self.web3,
            addr,
            cache=self.token_cache,
        )

    @cached_property
    def collateral_token(self) -> TokenDetails:
        """The token borrowers pledge as collateral for their loans."""
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
        """No separate management charge; protocol deductions are reflected in NAV."""
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """No separate performance charge; V2 deducts protocol fees from borrower interest."""
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta:
        return datetime.timedelta(days=0)

    def get_withdrawal_period(self) -> WithdrawalPeriod:
        """No scheduled withdrawal delay; execution still requires market liquidity."""
        return INSTANT_WITHDRAWAL_PERIOD

    def get_link(self, referral: str | None = None) -> str:
        chain_name = get_chain_name(self.chain_id).lower()
        return f"https://www.curve.finance/lend/{chain_name}/markets/{self.vault_address}/"

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get Llama Lend-specific historical reader with utilisation metrics."""
        return LlamaLendVaultHistoricalReader(self, stateful)

    def fetch_lending_metrics(self, block_identifier: BlockIdentifier = "latest") -> tuple[Decimal | None, Percent | None]:
        """Read assets and debt at one block for consistent lending metrics.

        Dynamic block tags are resolved once before reading either input.
        Provider failures leave the metrics missing for this observation.

        :param block_identifier: Block number, hash or tag to query.
        :return: Available liquidity and utilisation, or missing values.
        """
        try:
            if self.denomination_token is None:
                return None, None
            if block_identifier in {"latest", "safe", "finalized", "pending"}:
                block_identifier = self.web3.eth.get_block(block_identifier)["number"]
                if block_identifier is None:
                    msg = "Cannot pin pending block to a block number"
                    raise ValueError(msg)
            total_assets = self.vault_contract.functions.totalAssets().call(block_identifier=block_identifier)
            debt_data = self.create_total_debt_call().call(self.web3, block_identifier=block_identifier, attempts=0)
            if len(debt_data) != UINT256_RESULT_LENGTH:
                raise ValueError(f"total_debt returned {len(debt_data)} bytes, expected 32")
            return _calculate_lending_metrics(total_assets, int.from_bytes(debt_data, "big"), self.denomination_token)
        except (Web3Exception, ValueError, RequestException) as exc:
            logger.warning("Cannot read Llama lending metrics for %s at block %s (%s, code %s)", self.address, block_identifier, type(exc).__name__, normalise_rpc_error(exc)[0])
            return None, None

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Get the amount of denomination token available for immediate withdrawal.

        Subtracts controller debt from vault assets, including V2's deduction
        of protocol fees from assets.

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Amount in denomination token units (human-readable Decimal).
        """
        return self.fetch_lending_metrics(block_identifier)[0]

    def fetch_utilisation_percent(self, block_identifier: BlockIdentifier = "latest") -> Percent | None:
        """Get the percentage of assets currently lent out.

        Divides controller debt by vault assets at the same block, capped at
        100% when accrued protocol fees exhaust available liquidity.

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Utilisation as float between 0.0 and 1.0 (0% to 100%).
        """
        return self.fetch_lending_metrics(block_identifier)[1]
