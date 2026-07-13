"""T3tris vault support.

T3tris is a tokenised vault protocol for professional asset managers. It builds
vaults around ERC-4626 shares and adds an ERC-7540-like asynchronous request
lifecycle with protocol-specific method selectors.

- `Homepage <https://t3tris.finance/>`__
- `Vault app <https://app.t3tris.finance/vaults>`__
- `Documentation repository <https://github.com/t3tris-finance/mdoc-t3tris>`__
- `Local research notes <README-t3tris.md>`__
"""

import datetime
import logging
from collections.abc import Iterable
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ABIFunctionNotFound, BadFunctionCallOutput

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.erc_4626.vault_protocol.t3tris.offchain_metadata import T3trisVaultMetadata, fetch_t3tris_vault_metadata
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int, convert_uint256_bytes_to_address
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

logger = logging.getLogger(__name__)

WAD = 10**18

#: Treat a T3tris PPS sample below this fraction of the previous good PPS as a
#: stale-NAV window when async settlement minted shares but the oracle NAV has
#: not yet absorbed the settled assets.
STALE_NAV_SHARE_PRICE_DROP_THRESHOLD = Decimal("0.90")

#: Warn when the first reader sample is already below this PPS value and there
#: is no previous good PPS to hold for correction.
STALE_NAV_FIRST_SAMPLE_WARNING_THRESHOLD = Decimal("0.90")

#: Marker exported with rows whose PPS/NAV was corrected by this reader.
STALE_NAV_CORRECTED_ERROR = "t3tris_stale_nav_gap_corrected"

#: Marker exported when a scan appears to start inside an uncorrectable gap.
STALE_NAV_FIRST_SAMPLE_ERROR = "t3tris_stale_nav_gap_possible_first_sample"


def _wad_to_percent(value: int) -> float:
    """Convert a T3tris WAD fee value to a fraction."""
    return value / WAD


class T3trisHistoricalReader(ERC4626HistoricalReader):
    """Read T3tris historical prices with async settlement gap correction.

    T3tris async vaults can mint shares during deposit settlement before the
    oracle NAV reflects the settled assets. During this stale-NAV window the
    standard ERC-4626 ``convertToAssets(1 share)`` value can show a large
    phantom drawdown even though no economic loss happened.

    This reader follows the conservative "hold last good PPS" strategy for
    async vaults. When supply jumps and raw PPS collapses while the vault is
    closed for async settlement, it keeps the previous good share price and
    reports effective total assets as ``held_pps * total_supply`` while the
    same stale-NAV window remains open. The hold ends when raw PPS recovers or a
    later oracle valuation timestamp indicates that the low PPS is now measured
    NAV instead of stale accounting.

    PPS scenarios handled by this reader:

    - **Sync/open vault:** ``isVaultOpen()`` returns ``True``. T3tris is using
      live vault accounting, so the generic ERC-4626 PPS is already the
      effective PPS. No stale-NAV correction is applied.
    - **Normal async vault:** ``isVaultOpen()`` returns ``False`` but supply and
      PPS move without a large supply-driven collapse. The raw
      ``convertToAssets(1 share)`` value is accepted and stored as the next
      ``last_good_share_price``.
    - **Async settlement gap starts:** total supply increases and raw PPS falls
      below :py:data:`STALE_NAV_SHARE_PRICE_DROP_THRESHOLD` of the previous good
      PPS. This is the T3tris phantom drawdown case where shares were minted
      before the oracle NAV absorbed the settled assets. The reader holds the
      previous PPS and recalculates NAV as ``held_pps * total_supply``.
    - **Settlement timestamp advances at gap start:** some real T3tris samples
      show a newer ``lastValuationTimestamp()`` at the settlement sample even
      though NAV is still stale. A timestamp advance therefore does not block
      starting a new gap if the supply jump and PPS collapse are present.
    - **Gap continues:** once a stale-NAV gap is active, the reader keeps holding
      PPS while raw PPS remains collapsed and the oracle valuation timestamp is
      still the same timestamp that opened the gap.
    - **Gap ends by fresh valuation or recovery:** if raw PPS recovers, or a
      later valuation timestamp appears after the gap has started, the reader
      accepts the raw PPS again and clears the stale-NAV latch.
    - **First sample already inside a gap:** there is no previous good PPS to
      hold. The reader returns the raw sample, emits
      :py:data:`STALE_NAV_FIRST_SAMPLE_ERROR`, and does not seed
      ``last_good_share_price`` from the suspect value.
    - **Protocol-specific call failure:** if ``isVaultOpen()`` or
      ``lastValuationTimestamp()`` fails while raw PPS is collapsed, the reader
      avoids poisoning ``last_good_share_price`` with the collapsed value. If a
      gap was already active, it keeps holding PPS for the failed sample.
    - **Real loss ambiguity:** a true economic loss that happens in the same
      sample as a deposit-driven supply increase can look identical to a
      stale-NAV window. The reader intentionally favours the T3tris accounting
      correction because the purpose of this protocol-specific reader is to
      avoid user-facing phantom drawdowns in the historical PPS series.
    """

    def __init__(self, vault: "T3trisVault", stateful: bool):  # noqa: FBT001
        super().__init__(vault, stateful=stateful)
        self.previous_total_supply: Decimal | None = None
        self.previous_block_number: int | None = None
        self.last_good_share_price: Decimal | None = None
        self.in_stale_nav_gap = False
        self.stale_nav_gap_started_at_valuation_timestamp: int | None = None

    @cached_property
    def oracle_address(self) -> HexAddress:
        """Get the T3tris oracle address used for timestamp polling.

        T3tris' live ABI exposes ``getOracle()`` even though the minimal oracle
        interface is usually documented as ``oracle()``. The vault method keeps
        the ABI-specific lookup in one place.

        The scanner constructs one multicall set for the whole historical read,
        so this address is cached for the reader lifetime. This assumes the
        vault's oracle address is immutable for the scanned deployment history.
        If a future T3tris vault can migrate oracle contracts, the reader must be
        extended to split the historical scan at oracle-change boundaries before
        polling ``lastValuationTimestamp()``.

        :return:
            The current oracle contract address.
        """
        return self.vault.fetch_oracle_address(self.vault._get_block_identifier())

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Get on-chain calls needed for corrected T3tris historical prices.

        In addition to the generic ERC-4626 calls we read ``isVaultOpen()`` to
        distinguish sync and async vault modes, and
        ``lastValuationTimestamp()`` from the configured oracle to observe
        valuation refreshes.

        :return:
            Encoded calls consumed by the multicall historical reader.
        """
        yield from self.construct_core_erc_4626_multicall()

        yield EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.isVaultOpen(),
            extra_data={
                "function": "isVaultOpen",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )

        yield EncodedCall.from_keccak_signature(
            address=self.oracle_address,
            signature=Web3.keccak(text="lastValuationTimestamp()")[0:4],
            function="lastValuationTimestamp",
            data=b"",
            extra_data={
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )

    @staticmethod
    def _decode_uint_call(
        call_by_name: dict[str, EncodedCallResult],
        function_name: str,
        errors: list[str],
    ) -> int | None:
        """Decode a uint-like T3tris reader call.

        :param call_by_name:
            Results keyed by the call function name.

        :param function_name:
            Function name to decode.

        :param errors:
            Error accumulator to update if the call failed.

        :return:
            Decoded integer, or ``None`` if the call failed.
        """
        result = call_by_name.get(function_name)
        if result is not None and result.success:
            return convert_int256_bytes_to_int(result.result)

        errors.append(f"{function_name} call failed")
        return None

    def _decode_bool_call(
        self,
        call_by_name: dict[str, EncodedCallResult],
        function_name: str,
        errors: list[str],
    ) -> bool | None:
        """Decode a bool T3tris reader call.

        :param call_by_name:
            Results keyed by the call function name.

        :param function_name:
            Function name to decode.

        :param errors:
            Error accumulator to update if the call failed.

        :return:
            Decoded boolean, or ``None`` if the call failed.
        """
        value = self._decode_uint_call(call_by_name, function_name, errors)
        if value is None:
            return None
        return bool(value)

    def _process_core_without_state_update(
        self,
        call_by_name: dict[str, EncodedCallResult],
    ) -> tuple[Decimal | None, Decimal | None, Decimal | None, list[str], Decimal | None, object | None]:
        """Decode generic ERC-4626 values without updating reader state.

        The parent decoder updates adaptive reader state with raw ERC-4626
        values. T3tris needs to apply stale-NAV correction before state is
        updated, so this helper temporarily detaches the state from the raw
        ``total_assets`` result.

        :param call_by_name:
            Results keyed by function name.

        :return:
            Decoded core values and the original scanner state.
        """
        reader_state = self._get_reader_state(call_by_name)
        total_assets_result = call_by_name.get("total_assets")
        original_total_assets_state = total_assets_result.state if total_assets_result is not None else None
        if total_assets_result is not None:
            total_assets_result.state = None

        try:
            share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)
        finally:
            if total_assets_result is not None:
                total_assets_result.state = original_total_assets_state

        return share_price, total_supply, total_assets, list(errors or []), max_deposit, reader_state

    def _detect_stale_nav_gap(
        self,
        *,
        async_vault: bool,
        protocol_reads_failed: bool,
        total_supply: Decimal | None,
        share_price: Decimal | None,
        last_valuation_timestamp: int | None,
    ) -> tuple[bool, bool]:
        """Detect whether the current sample should use held PPS.

        This method is deliberately a small state machine instead of a pure
        timestamp comparison. On T3tris, the settlement transaction can mint
        shares and move the oracle valuation timestamp without immediately
        increasing oracle NAV. Because of this, the first stale-NAV sample is
        detected primarily from the accounting shape: async vault, increased
        supply, and collapsed raw PPS.

        Scenario matrix:

        - **No previous good PPS:** correction is impossible. If this is the
          first sample and raw PPS is already below
          :py:data:`STALE_NAV_FIRST_SAMPLE_WARNING_THRESHOLD`, the caller gets
          ``possible_first_gap_sample=True`` and should avoid using this value as
          a baseline. This also applies when T3tris-specific reads fail, because
          a missing ``isVaultOpen()`` result means the reader cannot prove the
          low first sample came from a sync/live vault.
        - **Supply increased + raw PPS collapsed:** this starts a new stale-NAV
          gap. The caller should hold the previous good PPS even if
          ``lastValuationTimestamp()`` advanced in the same sample.
        - **Already in stale-NAV gap + same gap timestamp + raw PPS collapsed:**
          this continues the gap. The caller should keep holding PPS.
        - **Already in stale-NAV gap + later valuation timestamp:** this method
          returns ``False`` so the caller accepts the new raw PPS as measured
          NAV. If the loss was real, it now appears in the series.
        - **Raw PPS recovered:** this method returns ``False`` because the
          on-chain ERC-4626 PPS and held PPS are back in line.
        - **Sync vault:** correction is disabled regardless of supply/PPS shape,
          because sync T3tris vaults are not oracle-valued and should not have
          this settlement gap.

        :param async_vault:
            ``True`` when T3tris reports the vault is in async/oracle mode.

        :param protocol_reads_failed:
            ``True`` when either T3tris-specific mode or oracle timestamp polling
            failed for this sample.

        :param total_supply:
            Current total share supply.

        :param share_price:
            Raw share price from ``convertToAssets(1 share)``.

        :param last_valuation_timestamp:
            Oracle valuation timestamp.

        :return:
            ``(stale_nav_gap, possible_first_gap_sample)``.
        """
        supply_increased = self.previous_total_supply is not None and total_supply is not None and total_supply > self.previous_total_supply
        raw_price_collapsed = self.last_good_share_price is not None and share_price is not None and share_price < self.last_good_share_price * STALE_NAV_SHARE_PRICE_DROP_THRESHOLD
        possible_first_gap_sample = self.previous_block_number is None and (async_vault or protocol_reads_failed) and self.last_good_share_price is None and share_price is not None and share_price < STALE_NAV_FIRST_SAMPLE_WARNING_THRESHOLD

        starts_new_gap = supply_increased and raw_price_collapsed
        continues_existing_gap = self.in_stale_nav_gap and raw_price_collapsed and self.stale_nav_gap_started_at_valuation_timestamp is not None and last_valuation_timestamp == self.stale_nav_gap_started_at_valuation_timestamp
        stale_nav_gap = async_vault and (starts_new_gap or continues_existing_gap)
        return stale_nav_gap, possible_first_gap_sample

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Process one T3tris historical sample.

        The raw ERC-4626 values are decoded first. If the sample is inside a
        correctable stale-NAV window, ``share_price`` is replaced by the last
        known good value and ``total_assets`` is recalculated from the corrected
        price and current total supply. If the first sample already looks like a
        stale-NAV gap, the reader cannot infer a previous good PPS and therefore
        only tags the row as suspicious.

        PPS handling details:

        1. Decode generic ERC-4626 values without letting the parent reader
           update adaptive scanner state. The raw values may be a known-bad
           T3tris stale-NAV sample, so state must only be updated after the
           T3tris correction decision is made.
        2. Decode ``isVaultOpen()`` and oracle ``lastValuationTimestamp()``.
           ``isVaultOpen() == True`` means sync/live accounting. ``False`` means
           async/oracle accounting and may need stale-NAV correction.
        3. Reject out-of-order blocks. The stale-NAV detector depends on the
           previous supply, current gap valuation timestamp, and previous good
           PPS; processing historical samples backwards would make the state
           machine unsafe.
        4. If a stale-NAV gap is detected, hold ``last_good_share_price`` and
           recompute ``total_assets`` from the held PPS and the current supply.
           The row is tagged with :py:data:`STALE_NAV_CORRECTED_ERROR` so
           downstream consumers can see that the value is protocol-corrected,
           not the raw ERC-4626 value.
        5. If this looks like the first sample inside a gap, emit
           :py:data:`STALE_NAV_FIRST_SAMPLE_ERROR` and do not update
           ``last_good_share_price``. Without a previous good PPS, any
           correction would be guesswork.
        6. If T3tris-specific protocol reads fail on a collapsed raw PPS, avoid
           updating ``last_good_share_price`` from the collapsed value. If this
           is the first low-PPS sample, tag it as a possible uncorrectable gap.
           When the failed sample is not already part of a confirmed gap, the
           previous supply baseline is also preserved so the next successful
           sample can still detect the supply jump and correct the gap.
        7. Update adaptive reader state exactly once, using the corrected PPS
           and corrected total assets when correction was applied. This prevents
           the adaptive scanner from learning a phantom drawdown and reducing
           scan quality for the vault.

        :param block_number:
            Block number of the sample.

        :param timestamp:
            Naive UTC timestamp of the block.

        :param call_results:
            Results for calls produced by :py:meth:`construct_multicalls`.

        :return:
            Historical read with raw or T3tris-corrected share price and total
            assets.
        """
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        if self.previous_block_number is not None and block_number <= self.previous_block_number:
            raise ValueError(f"T3trisHistoricalReader received out-of-order block {block_number:,} after {self.previous_block_number:,} for vault {self.vault}")

        share_price, total_supply, total_assets, errors, max_deposit, reader_state = self._process_core_without_state_update(call_by_name)

        is_vault_open = self._decode_bool_call(call_by_name, "isVaultOpen", errors)
        last_valuation_timestamp = self._decode_uint_call(call_by_name, "lastValuationTimestamp", errors)
        protocol_reads_failed = is_vault_open is None or last_valuation_timestamp is None
        raw_price_collapsed = self.last_good_share_price is not None and share_price is not None and share_price < self.last_good_share_price * STALE_NAV_SHARE_PRICE_DROP_THRESHOLD
        uncertain_collapsed_sample = protocol_reads_failed and raw_price_collapsed

        stale_nav_gap, possible_first_gap_sample = self._detect_stale_nav_gap(
            async_vault=is_vault_open is False,
            protocol_reads_failed=protocol_reads_failed,
            total_supply=total_supply,
            share_price=share_price,
            last_valuation_timestamp=last_valuation_timestamp,
        )
        if protocol_reads_failed and self.in_stale_nav_gap and raw_price_collapsed:
            stale_nav_gap = True

        if stale_nav_gap and self.last_good_share_price is not None:
            share_price = self.last_good_share_price
            if total_supply is not None:
                total_assets = share_price * total_supply
            else:
                total_assets = None
            if self.stale_nav_gap_started_at_valuation_timestamp is None:
                self.stale_nav_gap_started_at_valuation_timestamp = last_valuation_timestamp
            self.in_stale_nav_gap = True
            errors.append(STALE_NAV_CORRECTED_ERROR)
        else:
            self.in_stale_nav_gap = False
            self.stale_nav_gap_started_at_valuation_timestamp = None
            if possible_first_gap_sample:
                errors.append(STALE_NAV_FIRST_SAMPLE_ERROR)
            elif share_price is not None and share_price > 0 and not uncertain_collapsed_sample:
                self.last_good_share_price = share_price

        convert_to_assets_result = call_by_name.get("convertToAssets")
        if convert_to_assets_result is not None and reader_state is not None and share_price is not None:
            reader_state.on_called(
                convert_to_assets_result,
                total_assets=total_assets,
                share_price=share_price,
            )

        self.previous_block_number = block_number
        if not (uncertain_collapsed_sample and not stale_nav_gap):
            self.previous_total_supply = total_supply

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=None,
            management_fee=None,
            errors=errors or None,
            max_deposit=max_deposit,
        )

    @staticmethod
    def _get_reader_state(call_by_name: dict[str, EncodedCallResult]):
        """Get scanner state from any T3tris call result.

        The generic ERC-4626 decoder updates state from ``total_assets``. This
        reader suppresses that raw update and performs exactly one update after
        T3tris gap correction has been applied.

        :param call_by_name:
            Results keyed by the call function name.

        :return:
            Attached scanner state, if this is a stateful scan.
        """
        for result in call_by_name.values():
            if result.state is not None:
                return result.state
        return None


class T3trisVault(ERC4626Vault):
    """T3tris protocol vaults.

    - T3tris vaults expose standard ERC-4626 accounting methods
    - Async deposit/redemption flow uses custom ``DepositRequest`` and
      ``RedeemRequest`` events and custom request/claim methods
    - Fee values are exposed as WAD-scaled integers by the live vault ABI
    - Offchain descriptions are fetched from the T3tris page API
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="t3tris/IVault.json",
        )

    @cached_property
    def t3tris_metadata(self) -> T3trisVaultMetadata | None:
        """Offchain metadata from T3tris' web app API.

        Fetched from ``api.t3tris.finance/api/v1/vaults`` and cached on
        disk and in-process to avoid repeated API calls.
        """
        return fetch_t3tris_vault_metadata(self.web3, self.spec.vault_address)

    def fetch_oracle_address(self, block_identifier: BlockIdentifier) -> HexAddress:
        """Fetch the configured T3tris oracle address.

        T3tris live vaults expose ``getOracle()`` in their frontend ABI. If a
        future compatible deployment exposes the documented ``oracle()`` getter
        instead, fall back to the raw selector.

        :param block_identifier:
            Block number or tag to read.

        :return:
            Oracle contract address.
        """
        try:
            return self.vault_contract.functions.getOracle().call(block_identifier=block_identifier)
        except (ABIFunctionNotFound, BadFunctionCallOutput, ValueError):
            oracle_call = EncodedCall.from_keccak_signature(
                address=self.vault_address,
                signature=Web3.keccak(text="oracle()")[0:4],
                function="oracle",
                data=b"",
                extra_data=None,
            )
            return convert_uint256_bytes_to_address(
                oracle_call.call(
                    self.web3,
                    block_identifier=block_identifier,
                    silent_error=True,
                    attempts=2,
                )
            )

    @property
    def description(self) -> str | None:
        """Full vault strategy description from T3tris' offchain metadata."""
        if self.t3tris_metadata:
            return self.t3tris_metadata.get("description")
        return None

    @property
    def short_description(self) -> str | None:
        """Short vault summary from T3tris' offchain metadata."""
        metadata = self.t3tris_metadata
        if not metadata:
            return None
        parts = [metadata.get("category"), metadata.get("rating")]
        parts.extend(metadata.get("attributes") or [])
        return ", ".join(part for part in parts if part) or None

    @property
    def manager_name(self) -> str | None:
        """T3tris curator name from offchain vault metadata."""
        if self.t3tris_metadata:
            return self.t3tris_metadata.get("curator_name")
        return None

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current annual management fee as a fraction.

        T3tris returns ``(managementFeeWad, managementFeeDays)``. The first
        value is WAD-scaled where ``1e18`` is 100%.

        :param block_identifier:
            Block to read.

        :return:
            ``0.02`` means 2%.
        """
        management_fee_wad, _management_fee_days = self.vault_contract.functions.getManagementFee().call(block_identifier=block_identifier)
        return _wad_to_percent(management_fee_wad)

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current performance fee as a fraction.

        :param block_identifier:
            Block to read.

        :return:
            ``0.2`` means 20%.
        """
        return _wad_to_percent(self.vault_contract.functions.getPerformanceFee().call(block_identifier=block_identifier))

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current entry fee as a fraction.

        :param block_identifier:
            Block to read.

        :return:
            ``0.01`` means 1%.
        """
        return _wad_to_percent(self.vault_contract.functions.getEntryFee().call(block_identifier=block_identifier))

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current exit fee as a fraction.

        :param block_identifier:
            Block to read.

        :return:
            ``0.01`` means 1%.
        """
        return _wad_to_percent(self.vault_contract.functions.getExitFee().call(block_identifier=block_identifier))

    def get_link(self, referral: str | None = None) -> str:
        """Link to the T3tris vault app."""
        url = f"https://app.t3tris.finance/vaults?chainId={self.chain_id}&address={self.vault_address}"
        if referral:
            return f"{url}&ref={referral}"
        return url

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:  # noqa: FBT001
        """Get the T3tris historical reader.

        T3tris async settlement can temporarily expose supply and oracle NAV out
        of sync. Use :py:class:`T3trisHistoricalReader` instead of the generic
        ERC-4626 reader so the historical PPS series does not record phantom
        drawdowns.

        :param stateful:
            Enable adaptive scanner state.

        :return:
            T3tris-specific historical reader.
        """
        return T3trisHistoricalReader(self, stateful=stateful)
