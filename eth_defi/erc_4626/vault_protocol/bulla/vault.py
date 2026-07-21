"""Bulla Network invoice-factoring vault support.

Bulla Factoring pools stablecoin liquidity to finance invoices and direct loan
offers. The protocol uses Bulla Claim v2 receivables and BullaFrendLend v2.

- `Bulla Network <https://www.bulla.network/>`__
- `Factoring contracts <https://github.com/bulla-network/factoring-contracts>`__
- `Example BullaFactoringV2_1 vault <https://arbiscan.io/address/0xc099773267308D8e9E805f47EABf9ab13bBc9e37#code>`__

Pool participation is permissioned. Deposit, redemption and factoring rights
are separately configured by the pool, and redemptions can be queued when
liquidity is unavailable. Therefore this read adapter deliberately does not
certify the generic public deposit manager.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal

import eth_abi
from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.types import Percent
from eth_defi.vault.base import VaultDepositManager
from eth_defi.vault.fee import FeeData, VaultFeeMode

#: Public transaction flows need a pool-specific permission and redemption-queue adapter.
BULLA_BLOCKED_FLOW_REASON = "Bulla Network deposit manager is blocked: permissioned deposits and queued redemptions are not implemented"

#: Bulla stores percentage values in basis points, e.g. ``30`` means 0.30%.
#:
#: Source: the verified `BullaFactoringV2_1 contract on Arbiscan
#: <https://arbiscan.io/address/0xc099773267308D8e9E805f47EABf9ab13bBc9e37#code>`__.
BULLA_BASIS_POINT_DENOMINATOR = 10_000

#: Every view used below returns one ABI word, except the mapping getter.
#:
#: Keeping this explicit makes the selector-only reader fail closed if a
#: deployment returns malformed data instead of a Solidity ``uint`` value.
BULLA_ABI_WORD_BYTES = 32


@dataclass(slots=True, frozen=True)
class BullaFeeData:
    """Bulla Factoring pool fees in the protocol's native representation.

    Bulla has two pool-wide fee rates and two corresponding accrued token
    balances. The protocol rate is withheld when an invoice is funded, while
    the administrator rate is time-prorated as part of the invoice fee
    calculation. The public source explains that calculation in
    `FeeCalculations.sol <https://github.com/bulla-network/factoring-contracts/blob/main/contracts/libraries/FeeCalculations.sol>`__.

    The underwriter's ``spreadBps`` deliberately does **not** appear here: it
    is selected per approved invoice, rather than configured as a pool-wide
    getter. Use :class:`BullaInvoiceFeeData` for that native invoice record.
    Likewise, ``target_yield_bps`` is an investment return target, not a fee.
    It is retained so applications cannot confuse it with the administrator
    or underwriter charge.
    """

    #: Block at which every value in this snapshot was read.
    block_identifier: BlockIdentifier

    #: Protocol fee rate in basis points from ``protocolFeeBps()``.
    #:
    #: The contract takes this amount off the top when it funds an invoice and
    #: earmarks it for the Bulla DAO; it is not an annual management fee.
    protocol_fee_bps: int

    #: Administrator fee rate in basis points from ``adminFeeBps()``.
    #:
    #: ``FeeCalculations.calculateFees()`` prorates this rate over an invoice's
    #: financing period. It is therefore the closest available counterpart to
    #: the shared annual management-fee field.
    admin_fee_bps: int

    #: Amount currently accrued for the protocol in denomination-token units.
    #:
    #: This is ``protocolFeeBalance()`` after token-decimal conversion, not a
    #: fee rate and not necessarily an amount immediately withdrawable by a LP.
    protocol_fee_balance: Decimal

    #: Amount currently accrued for the administrator in denomination-token units.
    #:
    #: Bulla adds realised administrator fees and invoice-level underwriter
    #: spreads to this balance, so it must not be interpreted as admin fees alone.
    admin_fee_balance: Decimal

    #: Pool-wide target investor yield in basis points from ``targetYieldBps()``.
    #:
    #: This is included as Bulla-native fee context only; it does not map to a
    #: generic fee field because it is a target return for liquidity providers.
    target_yield_bps: int

    @property
    def protocol_fee(self) -> Percent:
        """Return the one-off protocol fee as a fractional percentage.

        :return: For example, ``0.003`` for a 30 basis point protocol fee.
        """

        return self.protocol_fee_bps / BULLA_BASIS_POINT_DENOMINATOR

    @property
    def admin_fee(self) -> Percent:
        """Return the time-prorated administrator fee as a fractional percentage.

        :return: For example, ``0.01`` for a 100 basis point annualised rate.
        """

        return self.admin_fee_bps / BULLA_BASIS_POINT_DENOMINATOR

    @property
    def target_yield(self) -> Percent:
        """Return the pool's target yield as a fractional percentage.

        :return: For example, ``0.08`` for an 800 basis point target yield.
        """

        return self.target_yield_bps / BULLA_BASIS_POINT_DENOMINATOR

    def as_generic_fee_data(self) -> FeeData:
        """Map Bulla fees to the shared schema as internalised skimming.

        ``adminFeeBps`` is time-prorated over invoice financing and is the only
        pool-level rate with the same shape as a generic management fee. The
        protocol fee and the underwriter's ``spreadBps`` are financing terms,
        not LP entry, exit or vault performance fees. The spread is also set
        separately for each approved invoice, so it has no truthful pool-wide
        percentage to place in :class:`FeeData`.

        Bulla accounts for these charges before calculating the value backing
        LP shares. In the verified V2.1 implementation,
        ``reconcileSingleInvoice()`` passes realised interest, admin fee and
        underwriter spread to ``incrementProfitAndFeeBalances()``. That helper
        records the admin fee and spread in ``adminFeeBalance`` but adds only
        the LP's net interest to ``paidInvoicesGain``. ``calculateCapitalAccount()``
        then derives the capital account from deposits, ``paidInvoicesGain``
        and withdrawals; ERC-4626 ``previewRedeem()`` and ``previewWithdraw()``
        use that capital account. Thus the fee amounts are already excluded
        from the amount supporting shares, rather than deducted at redemption.

        The protocol fee follows the same investor-facing pattern: Bulla
        reserves it when funding an invoice and tracks it in
        ``protocolFeeBalance``. It is not an ERC-4626 deposit or withdrawal
        charge. See the verified `BullaFactoringV2_1 source
        <https://arbiscan.io/address/0xc099773267308D8e9E805f47EABf9ab13bBc9e37#code>`__
        and `FeeCalculations.sol
        <https://github.com/bulla-network/factoring-contracts/blob/main/contracts/libraries/FeeCalculations.sol>`__.

        Therefore the generic record uses
        :attr:`~eth_defi.vault.fee.VaultFeeMode.internalised_skimming`. The
        known administrator rate remains in ``management``; the unsupported
        performance, deposit and withdrawal fee fields are known to be zero.
        Call :meth:`fetch_bulla_invoice_fee_data` when native protocol-fee or
        invoice-spread detail is required.

        :return: Generic fee record for Bulla's fees-net share value.
        """

        return FeeData(
            # Fees are excluded from invoice profit before the capital account
            # (and therefore the share value) is calculated; see the source
            # walkthrough in this method's docstring.
            fee_mode=VaultFeeMode.internalised_skimming,
            management=self.admin_fee,
            # Bulla does not expose a vault-wide performance-fee percentage.
            # The invoice-specific underwriter spread remains Bulla-native.
            performance=0.0,
            # Invoice funding charges do not alter ERC-4626 deposit or
            # redemption amounts, so both investor transaction fees are zero.
            deposit=0.0,
            withdraw=0.0,
        )

    @classmethod
    def fetch(cls, vault: "BullaVault", block_identifier: BlockIdentifier) -> "BullaFeeData":
        """Fetch every pool-wide Bulla V2.1 fee value in one coherent snapshot.

        Only stable, zero-argument view selectors are used instead of copying
        the large explorer ABI. The verified V2.1 source declares the five
        getters used here: `BullaFactoringV2_1 on Arbiscan
        <https://arbiscan.io/address/0xc099773267308D8e9E805f47EABf9ab13bBc9e37#code>`__.

        :param vault: Bulla vault from which to read the configuration.
        :param block_identifier: Block tag or number shared by all calls.
        :return: Pool-wide Bulla rates, balances and yield target.
        """

        denomination_token = vault.denomination_token
        assert denomination_token is not None, "Bulla fee balances require the ERC-4626 denomination token"

        # Bulla's rate getters return ``uint16`` in ABI terms, but Solidity ABI
        # pads all integer return values to one 32-byte word. The helper decodes
        # the word as ``int`` after checking its exact width.
        protocol_fee_bps = vault._fetch_bulla_uint256("protocolFeeBps()", block_identifier)
        admin_fee_bps = vault._fetch_bulla_uint256("adminFeeBps()", block_identifier)
        target_yield_bps = vault._fetch_bulla_uint256("targetYieldBps()", block_identifier)

        # Fee balances use the same raw units as the pool asset (PYUSD for the
        # reviewed TCS pool). Convert them through TokenDetails so this remains
        # correct for Bulla deployments with a different stablecoin precision.
        protocol_fee_balance_raw = vault._fetch_bulla_uint256("protocolFeeBalance()", block_identifier)
        admin_fee_balance_raw = vault._fetch_bulla_uint256("adminFeeBalance()", block_identifier)

        return cls(
            block_identifier=block_identifier,
            protocol_fee_bps=protocol_fee_bps,
            admin_fee_bps=admin_fee_bps,
            protocol_fee_balance=denomination_token.convert_to_decimals(protocol_fee_balance_raw),
            admin_fee_balance=denomination_token.convert_to_decimals(admin_fee_balance_raw),
            target_yield_bps=target_yield_bps,
        )


@dataclass(slots=True, frozen=True)
class BullaInvoiceFeeData:
    """Bulla's per-invoice fee terms, including the underwriter spread.

    The verified V2.1 ``approvedInvoices(uint256)`` getter returns a nested
    ``FeeParams`` tuple containing the target-yield, spread, upfront, protocol
    and administrator rates. The struct is documented in the verified
    `BullaFactoringV2_1 source <https://arbiscan.io/address/0xc099773267308D8e9E805f47EABf9ab13bBc9e37#code>`__.

    This is intentionally separate from :class:`BullaFeeData`: a pool can have
    many invoices with different underwriter spreads, so there is no truthful
    aggregate percentage to inject into generic vault fee metadata.
    """

    #: Bulla Claim invoice identifier supplied to ``approvedInvoices``.
    invoice_id: int

    #: Whether the underwriter has approved this invoice for funding.
    approved: bool

    #: Base liquidity-provider return target in basis points; not a fee.
    target_yield_bps: int

    #: Underwriter-selected invoice spread in basis points.
    underwriter_spread_bps: int

    #: Maximum creditor upfront funding percentage in basis points; not a fee.
    upfront_bps: int

    #: Protocol fee copied into the immutable invoice approval in basis points.
    protocol_fee_bps: int

    #: Administrator fee copied into the immutable invoice approval in basis points.
    admin_fee_bps: int

    #: Protocol fee amount reserved when this invoice was funded, in asset units.
    protocol_fee_amount: Decimal

    @property
    def underwriter_spread(self) -> Percent:
        """Return the invoice-specific spread as a fractional percentage.

        :return: For example, ``0.015`` for a 150 basis point spread.
        """

        return self.underwriter_spread_bps / BULLA_BASIS_POINT_DENOMINATOR

    @classmethod
    def fetch(cls, vault: "BullaVault", invoice_id: int, block_identifier: BlockIdentifier) -> "BullaInvoiceFeeData":
        """Fetch one invoice's native Bulla fee parameters.

        ``approvedInvoices`` is a Solidity public-mapping getter. Its return
        tuple is decoded from the verified V2.1 ABI rather than a hand-written
        contract ABI file. The field order below follows the explorer ABI:
        11 scalar approval fields, a five-value ``FeeParams`` tuple, then the
        pre-calculated per-second interest rate. Only the fee-related values
        are retained in this focused data class.

        :param vault: Bulla vault holding the invoice approval mapping.
        :param invoice_id: Bulla Claim invoice identifier.
        :param block_identifier: Block tag or number at which to read the record.
        :return: Invoice-level Bulla fee record, including the underwriter spread.
        """

        assert invoice_id >= 0, f"Invoice id must be non-negative, got {invoice_id}"
        denomination_token = vault.denomination_token
        assert denomination_token is not None, "Bulla protocol-fee amount requires the ERC-4626 denomination token"

        # ``approvedInvoices(uint256)`` has one ABI-encoded uint256 argument.
        # Passing the canonical selector plus this payload avoids persisting a
        # generated ABI while still retaining exact V2.1 tuple decoding below.
        call_data = eth_abi.encode(["uint256"], [invoice_id])
        result = vault._fetch_bulla_result("approvedInvoices(uint256)", block_identifier, call_data)

        # Explorer ABI order for the V2.1 ``InvoiceApproval`` mapping getter:
        # approved, creditor, 9 uint256 scalar fields, receiver, FeeParams,
        # and perSecondInterestRateRay. FeeParams is ABI-expanded into five
        # uint16 values even though Solidity packs it into one storage word.
        decoded = eth_abi.decode(
            [
                "bool",
                "address",
                *(["uint256"] * 9),
                "address",
                "(uint16,uint16,uint16,uint16,uint16)",
                "uint256",
            ],
            result,
        )
        fee_params = decoded[12]

        return cls(
            invoice_id=invoice_id,
            approved=decoded[0],
            target_yield_bps=fee_params[0],
            underwriter_spread_bps=fee_params[1],
            upfront_bps=fee_params[2],
            protocol_fee_bps=fee_params[3],
            admin_fee_bps=fee_params[4],
            protocol_fee_amount=denomination_token.convert_to_decimals(decoded[10]),
        )


class BullaVault(ERC4626Vault):
    """Read a Bulla Network Factoring vault.

    The vault exposes the standard asset and share-accounting interface, but
    its operational flows are governed by Bulla's permission and redemption
    queue contracts. This adapter supplies safe read support only until a
    permissioned end-to-end transaction flow can be certified.
    """

    def _fetch_bulla_result(self, function_signature: str, block_identifier: BlockIdentifier, data: bytes = b"") -> bytes:
        """Call a documented Bulla view selector without committing a full ABI.

        The narrow selector reader is limited to the verified contract's pure
        view getters. It is not used for protocol detection; classification
        remains limited to the single ``bullaDao()`` probe.

        :param function_signature: Canonical Solidity function signature.
        :param block_identifier: Block tag or number at which to execute the call.
        :param data: ABI-encoded function arguments, if the getter has any.
        :return: Raw ABI return bytes.
        """

        return self.web3.eth.call(
            {
                "to": self.vault_address,
                "data": Web3.keccak(text=function_signature)[:4] + data,
            },
            block_identifier=block_identifier,
        )

    def _fetch_bulla_uint256(self, function_signature: str, block_identifier: BlockIdentifier) -> int:
        """Read a zero-argument Bulla integer getter with strict ABI validation.

        :param function_signature: Canonical Solidity function signature.
        :param block_identifier: Block tag or number at which to execute the call.
        :return: Unsigned integer decoded from exactly one ABI word.
        :raises ValueError: If the contract response is not one ABI word.
        """

        result = self._fetch_bulla_result(function_signature, block_identifier)
        if len(result) != BULLA_ABI_WORD_BYTES:
            raise ValueError(f"Bulla getter {function_signature} returned {len(result)} bytes, expected {BULLA_ABI_WORD_BYTES}")
        return int.from_bytes(result, byteorder="big")

    def fetch_bulla_fee_data(self, block_identifier: BlockIdentifier = "latest") -> BullaFeeData:
        """Fetch all pool-wide Bulla fee configuration and accrued balances.

        :param block_identifier: Block tag or number shared by all fee reads.
        :return: Bulla-native pool fee snapshot.
        """

        return BullaFeeData.fetch(self, block_identifier)

    def fetch_bulla_invoice_fee_data(self, invoice_id: int, block_identifier: BlockIdentifier = "latest") -> BullaInvoiceFeeData:
        """Fetch native fee terms for one Bulla invoice approval.

        :param invoice_id: Bulla Claim invoice identifier.
        :param block_identifier: Block tag or number at which to read the terms.
        :return: Per-invoice Bulla fee record, including underwriter spread.
        """

        return BullaInvoiceFeeData.fetch(self, invoice_id, block_identifier)

    def get_fee_data(self) -> FeeData:
        """Map Bulla's pool configuration into the shared fee-data schema.

        This is an internalised-skimming record: Bulla removes financing fees
        before they contribute to the capital account backing the share price.
        The administrator rate is retained as management; performance, deposit
        and withdrawal rates are explicitly zero. See
        :meth:`BullaFeeData.as_generic_fee_data` for the source-linked
        accounting rationale and the Bulla-native fields that remain outside
        the shared model.

        :return: Generic fee data with a fees-net share price.
        """

        return self.fetch_bulla_fee_data().as_generic_fee_data()

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Return Bulla's administrator rate as the generic management fee.

        The rate is prorated over invoice financing periods by Bulla's fee
        library, making it the closest mapped value in the shared interface.

        :param block_identifier: Block at which the fee would be read.
        :return: Administrator fee ratio, such as ``0.01`` for 100 basis points.
        """

        return self.fetch_bulla_fee_data(block_identifier).admin_fee

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:  # noqa: PLR6301
        """Return zero because Bulla has no vault-wide performance fee.

        An underwriter can set an invoice-specific ``spreadBps``, but this is
        part of the invoice financing calculation, not a percentage of vault
        investment profits. The V2.1 reconciliation logic puts that spread in
        ``adminFeeBalance`` before it calculates the capital account used for
        share redemptions. It is therefore represented by the
        ``internalised_skimming`` mode rather than this generic field. See the
        `verified BullaFactoringV2_1 source
        <https://arbiscan.io/address/0xc099773267308D8e9E805f47EABf9ab13bBc9e37#code>`__.

        :param block_identifier: Unused because this fee is structurally zero.
        :return: Always ``0.0``.
        """
        del block_identifier
        return 0.0

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> float:  # noqa: PLR6301
        """Return zero because Bulla V2.1 does not charge an ERC-4626 entry fee.

        Bulla's protocol charge applies when a pool finances an invoice, not
        when a liquidity provider deposits into the ERC-4626 share contract.

        :param block_identifier: Unused because this fee is structurally zero.
        :return: Always ``0.0``.
        """

        del block_identifier
        return 0.0

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> float:  # noqa: PLR6301
        """Return zero because Bulla V2.1 does not charge an ERC-4626 exit fee.

        A redemption can be permissioned or queued, but the reviewed contract
        does not expose a percentage fee deducted from the LP's withdrawal.

        :param block_identifier: Unused because this fee is structurally zero.
        :return: Always ``0.0``.
        """

        del block_identifier
        return 0.0

    def has_custom_fees(self) -> bool:  # noqa: PLR6301
        """Report that Bulla invoice-financing fees exceed the shared model.

        The generic zero performance/deposit/withdraw values do not mean Bulla
        has no fees. The protocol supports distinct protocol, administrator
        and underwriter-spread components, and the latter differs between
        financing operations. Those charges are reflected in the fees-net
        share value and can be inspected through the Bulla-native data classes.

        :return: Always ``True`` for Bulla Factoring vaults.
        """
        return True

    def get_estimated_lock_up(self) -> datetime.timedelta | None:  # noqa: PLR6301
        """Return no fixed lock-up estimate for the pool.

        Withdrawals depend on pool liquidity and can enter a FIFO redemption
        queue, so no reliable time period can be inferred from the contract.

        :return: ``None`` because redemption timing is pool-state dependent.
        """
        return None

    def get_deposit_manager(self) -> VaultDepositManager:
        """Block the generic ERC-4626 transaction manager.

        Bulla pools can gate deposits and redemptions through distinct
        permission managers, while redemption can require a queued claim when
        liquidity is unavailable. A generic synchronous manager would promise
        a lifecycle this adapter has not certified.

        :raises NotImplementedError:
            Always, by deliberate transaction-safety policy.
        """

        raise NotImplementedError(BULLA_BLOCKED_FLOW_REASON)

    def fetch_deposit_closed_reason(self) -> str:  # noqa: PLR6301
        """Explain why public deposits are unavailable through this adapter.

        :return: Permanent transaction-adapter block reason.
        """

        return BULLA_BLOCKED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str:  # noqa: PLR6301
        """Explain why public redemptions are unavailable through this adapter.

        :return: Permanent transaction-adapter block reason.
        """

        return BULLA_BLOCKED_FLOW_REASON

    def get_link(self, referral: str | None = None) -> str:  # noqa: PLR6301
        """Return Bulla's public pool dashboard.

        The dashboard is the protocol's public entry point and does not expose
        a stable per-vault URL pattern for every permissioned pool.

        :param referral: Unused optional referral value.
        :return: URL for Bulla Finance liquidity pools.
        """
        del referral
        return "https://banker.bulla.network/#/yield"
