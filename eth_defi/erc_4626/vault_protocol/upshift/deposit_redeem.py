# ruff: noqa: EM101
"""Upshift multi-asset deposit flow."""

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from eth_abi import decode
from eth_typing import BlockIdentifier, HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3._utils.events import EventLogErrorFlags  # noqa: PLC2701
from web3.contract.contract import ContractFunction

from eth_defi.abi import ZERO_ADDRESS_STR, get_deployed_contract, get_topic_signature_from_event
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest, ERC4626RedemptionRequest
from eth_defi.provider.anvil import is_anvil, mine
from eth_defi.timestamp import get_block_timestamp
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.vault.deposit_redeem import (
    AsyncVaultRequestStatus,
    CannotParseRedemptionTransaction,
    DepositRedeemEventAnalysis,
    DepositRedeemEventFailure,
    DepositTicket,
    RedemptionRequest,
    RedemptionTicket,
    UnsupportedVaultSimulation,
    VaultFlowUnavailable,
    VaultForcedSettlementResult,
    create_synchronous_settlement_result,
)
from eth_defi.vault.flow_events import fetch_vault_flow_logs_hypersync, run_vault_flow_log_fetch


@dataclass(slots=True)
class UpshiftQueuedRedemptionTicket(RedemptionTicket):
    """Persist an Upshift request/claim withdrawal date and epoch identity."""

    #: Epoch emitted by the verified ``requestRedeem`` return value.
    claimable_epoch: int
    #: Calendar date selecting the batched operator settlement and owner claim.
    year: int
    month: int
    day: int

    #: Fixed net legacy entitlement; multi-asset claims are priced at claim time.
    raw_assets: int | None = None

    def get_request_id(self) -> int:
        """Return the protocol claimable epoch as the request identity.

        :return:
            Upshift claimable epoch.
        """
        return self.claimable_epoch


class UpshiftQueuedRedemptionRequest(RedemptionRequest):
    """Parse an Upshift ``requestRedeem`` into a dated redemption ticket."""

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> UpshiftQueuedRedemptionTicket:
        """Validate the native request event and read its scheduled date.

        The verified request event does not include the date returned by
        ``requestRedeem``. The immediately-read ``getWithdrawalEpoch`` state is
        therefore persisted after the successful transaction, alongside the
        event's holder, receiver and share count checks.

        :param tx_hashes:
            Broadcast request hashes; the final hash is ``requestRedeem``.
        :return:
            Persistable queued-redemption ticket.
        :raise CannotParseRedemptionTransaction:
            If the receipt has no matching Upshift request event.
        """
        tx_hash = tx_hashes[-1]
        receipt = self.web3.eth.get_transaction_receipt(tx_hash)
        logs = self.vault.upshift_contract.events.WithdrawalRequested().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        logs = [log for log in logs if log["address"].lower() == self.vault.address.lower()]
        if len(logs) != 1 or receipt["status"] != 1:
            raise CannotParseRedemptionTransaction(f"Expected one successful Upshift request at {tx_hash.hex()}")
        args = logs[0]["args"]
        holder = args.get("holderAddr", args.get("ownerAddr"))
        if int(args["shares"]) != self.raw_shares or holder.lower() != self.owner.lower() or args["receiverAddr"].lower() != self.to.lower():
            raise CannotParseRedemptionTransaction(f"Upshift request does not match ticket at {tx_hash.hex()}")
        if self.vault.multi_asset_like:
            year, month, day, claimable_epoch = self.vault.upshift_contract.functions.getWithdrawalEpoch().call(block_identifier=receipt["blockNumber"])
            raw_assets = None
        else:
            year, month, day = (int(args[k]) for k in ("year", "month", "day"))
            _, _, _, claimable_epoch = self.vault.upshift_contract.functions.getWithdrawalEpoch().call(block_identifier=receipt["blockNumber"])
            raw_assets = int(args["assets"]) - int(args["fee"])

        return UpshiftQueuedRedemptionTicket(
            vault_address=Web3.to_checksum_address(self.vault.address),
            owner=Web3.to_checksum_address(self.owner),
            to=Web3.to_checksum_address(self.to),
            raw_shares=self.raw_shares,
            tx_hash=HexBytes(tx_hash),
            claimable_epoch=int(claimable_epoch),
            year=int(year),
            month=int(month),
            day=int(day),
            raw_assets=raw_assets,
        )


class UpshiftMultiAssetDepositManager(ERC4626DepositManager):  # noqa: PLR0904
    """Execute Upshift deposits and dated request/claim withdrawals.

    Multi-asset vaults mint a separate LP token. A queued request escrows that
    token; after maturity any admitted caller can claim the reference asset.
    Public batch processing also pays claims directly. There is no mandatory
    operator settlement step. One receiver/date obligation must belong to one
    ticket; aggregate requests halt instead of allocating proceeds.

    See https://docs.upshift.finance/contracts/vault-interface and the verified
    implementation provenance in ``eth_defi/abi/upshift/README.md``.
    """

    #: The direct deposit accepts an explicitly selected asset.
    supports_multi_asset_deposit = True

    def check_deposit_whitelist(self, owner: HexAddress) -> None:
        """Check allow-list membership and the legacy negative admission policy.

        The shared helper handles positive whitelists. Legacy blacklists apply
        to both request senders and payout receivers even without an allow-list.

        :param owner: Account funding or receiving the flow.
        """
        super().check_deposit_whitelist(owner)
        if not self.vault.multi_asset_like and not self.vault.is_account_whitelisted(owner):
            raise self._create_unavailable("Upshift account is blacklisted", owner, "deposit", "preflight")

    def _create_unavailable(
        self,
        reason: str,
        owner: HexAddress | None,
        direction: Literal["deposit", "redeem"],
        phase: str,
        *,
        asset: HexAddress | None = None,
        requested_raw_amount: int | None = None,
        available_raw_amount: int | None = None,
    ) -> VaultFlowUnavailable:
        """Create a consistently contextualised Upshift flow rejection.

        :param reason:
            Human-readable rejection reason.
        :param owner:
            Account attempting the flow.
        :param direction:
            Deposit or redemption direction.
        :param phase:
            Lifecycle phase that rejected the flow.
        :param asset:
            Selected deposit asset, when available.
        :param requested_raw_amount:
            Requested token amount in native units.
        :param available_raw_amount:
            Available token amount in native units.
        :return:
            Structured exception ready to raise.
        """
        return VaultFlowUnavailable(
            reason,
            protocol="Upshift",
            vault_address=self.vault.address,
            caller=owner,
            asset_address=asset,
            direction=direction,
            phase=phase,
            requested_raw_amount=requested_raw_amount,
            available_raw_amount=available_raw_amount,
        )

    def fetch_accepted_assets(self) -> tuple[TokenDetails, ...]:
        """Return every token currently accepted by the vault.

        :return:
            Accepted tokens in the protocol whitelist order.
        """
        return self.vault.fetch_all_denomination_tokens()

    def _fetch_accepted_asset(self, owner: HexAddress | None, accepted_asset: HexAddress | None) -> TokenDetails:
        """Resolve and validate an explicitly selected deposit token.

        :param owner:
            Deposit owner used for structured error context.
        :param accepted_asset:
            Token address selected by the caller.
        :return:
            Selected token details.
        :raise VaultFlowUnavailable:
            If no token was selected or it is not accepted by the vault.
        """
        if accepted_asset is None:
            reason = "Upshift multi-asset deposit requires an explicitly selected accepted asset"
            raise self._create_unavailable(reason, owner, "deposit", "preflight")
        asset = next((token for token in self.fetch_accepted_assets() if token.address_lower == accepted_asset.lower()), None)
        if asset is None:
            reason = "Upshift selected deposit asset is not on the vault whitelist"
            raise self._create_unavailable(reason, owner, "deposit", "preflight", asset=accepted_asset)
        return asset

    def _fetch_deposit_preview(
        self,
        asset: TokenDetails,
        raw_amount: int,
        block_identifier: BlockIdentifier,
    ) -> tuple[int, int]:
        """Fetch Upshift's asset-aware deposit preview.

        The verified interface returns minted raw shares followed by the input
        value converted to the vault's raw reference-asset units.

        :param asset:
            Accepted deposit token.
        :param raw_amount:
            Input amount in the selected token's native units.
        :param block_identifier:
            Block at which to execute the preview.
        :return:
            Raw shares and raw reference-asset value.
        """
        raw_shares, raw_reference_amount = self.vault.upshift_contract.functions.previewDeposit(asset.address, raw_amount).call(block_identifier=block_identifier)
        return int(raw_shares), int(raw_reference_amount)

    def fetch_max_deposit_for_asset(self, accepted_asset: HexAddress, block_identifier: BlockIdentifier = "latest") -> int:
        """Return current deposit capacity in selected-token raw units.

        Upshift constrains both one deposit through ``maxDepositAmount`` and
        total vault assets through ``depositCap``. The lower current reference
        capacity is converted with the protocol's asset-aware preview.

        :param accepted_asset:
            Whitelisted token selected for the deposit.
        :param block_identifier:
            Block at which to read the cap.
        :return:
            Current maximum selected-token amount in native raw units.
        :raise ValueError:
            If the protocol reports a zero reference value for one token.
        """
        asset = self._fetch_accepted_asset(None, accepted_asset)
        raw_asset_unit = asset.convert_to_raw(Decimal(1))
        _, raw_reference_unit = self._fetch_deposit_preview(asset, raw_asset_unit, block_identifier)
        if raw_reference_unit <= 0:
            reason = f"Upshift preview returned a zero reference value for {asset.symbol}"
            raise ValueError(reason)
        functions = self.vault.upshift_contract.functions
        raw_per_deposit_limit = int(functions.maxDepositAmount().call(block_identifier=block_identifier))
        raw_total_limit = self.vault.fetch_deposit_cap(block_identifier)
        raw_total_assets = int(functions.getTotalAssets().call(block_identifier=block_identifier))
        raw_reference_capacity = raw_per_deposit_limit if raw_total_limit is None else min(raw_per_deposit_limit, max(raw_total_limit - raw_total_assets, 0))
        return raw_reference_capacity * raw_asset_unit // raw_reference_unit

    def fetch_depositable_raw_assets(self, owner: HexAddress) -> int | None:
        """Answer the generic deposit-limit hook without ERC-4626 ``maxDeposit``.

        The multi-asset Upshift vault does not implement the standard ERC-4626
        ``maxDeposit`` (its limit surface is ``maxDepositAmount`` / ``depositCap``
        / asset-aware ``previewDeposit``). Overriding the generic hook means a
        generic-path deposit preflight receives a real limit — for the vault's
        first whitelisted asset, in that asset's raw units — instead of the raw
        ``ABIFunctionNotFound`` the base implementation would raise. The
        protocol-specific :meth:`create_deposit_request` still uses the
        per-selected-asset :meth:`fetch_max_deposit_for_asset` for an actual
        deposit.

        :param owner:
            Unused; the multi-asset limit is not owner-specific.
        :return:
            Deposit limit for the vault's first accepted asset in raw units, or
            ``None`` when the vault currently accepts no asset.
        """
        self.check_deposit_whitelist(owner)
        accepted = self.fetch_accepted_assets()
        if not accepted:
            return None
        return self.fetch_max_deposit_for_asset(accepted[0].address)

    def estimate_deposit(
        self,
        owner: HexAddress | None,
        amount: Decimal,
        block_identifier: BlockIdentifier = "latest",
    ) -> Decimal:
        """Refuse an ambiguous estimate without an accepted-asset selection.

        :param owner:
            Deposit owner used for structured error context.
        :param amount:
            Ambiguous amount whose token is not specified.
        :param block_identifier:
            Unused because the request is rejected before an onchain read.
        :raise VaultFlowUnavailable:
            Always, directing the caller to :meth:`estimate_deposit_for_asset`.
        """
        del amount, block_identifier
        reason = "Upshift multi-asset deposit estimation requires an explicitly selected accepted asset"
        raise self._create_unavailable(reason, owner, "deposit", "estimate")

    def estimate_deposit_for_asset(
        self,
        owner: HexAddress | None,
        amount: Decimal,
        accepted_asset: HexAddress,
        block_identifier: BlockIdentifier = "latest",
    ) -> Decimal:
        """Estimate LP shares for a selected accepted asset.

        The estimate comes from Upshift's asset-aware ``previewDeposit`` call,
        avoiding local assumptions about reference-asset conversion.

        :param owner:
            Deposit owner used for structured error context.
        :param amount:
            Selected accepted-asset amount.
        :param accepted_asset:
            Whitelisted deposit-token address.
        :param block_identifier:
            Block at which to read the share price.
        :return:
            Estimated LP shares rounded down to share-token precision.
        """
        asset = self._fetch_accepted_asset(owner, accepted_asset)
        raw_amount = asset.convert_to_raw(amount)
        raw_shares, _ = self._fetch_deposit_preview(asset, raw_amount, block_identifier)
        return self.vault.share_token.convert_to_decimals(raw_shares)

    def estimate_redeem(
        self,
        owner: HexAddress | None,
        shares: Decimal,
        block_identifier: BlockIdentifier = "latest",
    ) -> Decimal:
        """Estimate queued redemption assets through the verified vault preview.

        :param owner:
            Share owner used for structured error context.
        :param shares:
            Requested LP shares.
        :param block_identifier:
            Block at which to read the preview.
        :return:
            Estimated denomination assets after the queued redemption fee.
        """
        del owner
        raw_shares = self.vault.share_token.convert_to_raw(shares)
        _, raw_assets_after_fee = self.vault.upshift_contract.functions.previewRedemption(raw_shares, int(self.vault.upshift_contract.functions.lagDuration().call(block_identifier=block_identifier)) == 0).call(block_identifier=block_identifier)
        return self.vault.denomination_token.convert_to_decimals(int(raw_assets_after_fee))

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Return whether the protocol-wide deposit gate is currently open.

        :param owner:
            Unused because Upshift's pause and cap are vault-wide.
        :return:
            ``True`` when deposits are neither paused nor configured with a
            zero cap.
        """
        del owner
        return self.vault.fetch_deposit_closed_reason() is None

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Return whether the live vault gate and owner share balance permit redemption.

        :param owner:
            Share owner.
        :return:
            ``True`` when withdrawals are open and the owner has LP shares.
        """
        return self.vault.fetch_redemption_closed_reason() is None and int(self.vault.share_token.fetch_raw_balance_of(owner)) > 0

    def estimate_redemption_delay(self) -> datetime.timedelta:
        """Return the configured lag rather than the synchronous base default.

        The calendar liquidation boundary can extend the actual wait. A parsed
        ticket supplies the exact scheduled date through the ticket ETA hook.

        :return: Configured minimum lag.
        """
        return datetime.timedelta(seconds=int(self.vault.upshift_contract.functions.lagDuration().call()))

    def get_redemption_ticket_delay_over(self, ticket: UpshiftQueuedRedemptionTicket) -> datetime.datetime:
        """Expose the persisted claim epoch to the existing settlement UI.

        This date is a maturity estimate, not a guarantee of funded payout.

        :param ticket: Parsed request with its original claim date.
        :return: Naive UTC maturity timestamp.
        """
        return datetime.datetime.fromtimestamp(ticket.claimable_epoch, datetime.timezone.utc).replace(tzinfo=None)

    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime:  # noqa: ARG002
        """Estimate a new request's maturity using the vault calendar.

        Existing requests use the ticket hook instead of current configuration.

        :param address: Interface-compatible account; the calendar is global.
        :return: Naive UTC scheduled maturity.
        """
        _, _, _, epoch = self.vault.upshift_contract.functions.getWithdrawalEpoch().call()
        return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).replace(tzinfo=None)

    def has_synchronous_redemption(self) -> bool:
        """Return whether the default withdrawal pays in its request transaction.

        Both families support zero-lag exits, using different call signatures.
        The answer follows the current configured lag, not the protocol label.

        :return: Whether there is no request/claim delay.
        """
        return int(self.vault.upshift_contract.functions.lagDuration().call()) == 0

    def create_deposit_request(  # noqa: PLR0917
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        amount: Decimal | None = None,
        raw_amount: int | None = None,
        check_max_deposit: bool = True,  # noqa: FBT001, FBT002
        check_enough_token: bool = True,  # noqa: FBT001, FBT002
        *,
        accepted_asset: HexAddress | None = None,
    ) -> ERC4626DepositRequest:
        """Create approval and deposit calls for one selected asset.

        The returned synchronous request approves the vault and then calls its
        multi-asset ``deposit`` entry point.

        :param owner:
            Address funding the deposit.
        :param to:
            Share receiver, defaulting to ``owner``.
        :param amount:
            Decimal selected-token amount, exclusive with ``raw_amount``.
        :param raw_amount:
            Native selected-token amount, exclusive with ``amount``.
        :param check_max_deposit:
            Whether to reject requests above the current protocol cap.
        :param check_enough_token:
            Whether to check the owner's selected-token balance.
        :param accepted_asset:
            Explicitly selected whitelist token.
        :return:
            Synchronous approval and deposit request.
        :raise VaultFlowUnavailable:
            If the asset is invalid or current protocol state rejects the flow.
        """
        self.check_deposit_whitelist(owner)
        asset = self._fetch_accepted_asset(owner, accepted_asset)
        if (amount is None) == (raw_amount is None):
            reason = "Give exactly one of amount or raw_amount"
            raise ValueError(reason)
        if raw_amount is None:
            raw_amount = asset.convert_to_raw(amount)
        if raw_amount <= 0:
            reason = "Upshift deposit amount must be positive"
            raise ValueError(reason)
        raw_preview_shares, _ = self._fetch_deposit_preview(asset, raw_amount, "latest")
        if raw_preview_shares <= 0:
            raise self._create_unavailable("Upshift deposit rounds to zero shares", owner, "deposit", "preflight", asset=asset.address)
        closed_reason = self.vault.fetch_deposit_closed_reason()
        if closed_reason is not None:
            raise self._create_unavailable(closed_reason, owner, "deposit", "preflight", asset=asset.address)
        if check_max_deposit:
            max_deposit = self.fetch_max_deposit_for_asset(asset.address)
            if raw_amount > max_deposit:
                reason = "Upshift deposit exceeds current protocol limits"
                raise self._create_unavailable(
                    reason,
                    owner,
                    "deposit",
                    "preflight",
                    asset=asset.address,
                    requested_raw_amount=raw_amount,
                    available_raw_amount=max_deposit,
                )
        if check_enough_token:
            balance = asset.fetch_raw_balance_of(owner)
            if balance < raw_amount:
                reason = "Insufficient selected Upshift deposit asset balance"
                raise self._create_unavailable(
                    reason,
                    owner,
                    "deposit",
                    "preflight",
                    asset=asset.address,
                    requested_raw_amount=raw_amount,
                    available_raw_amount=balance,
                )
        receiver = owner if to is None else to
        if Web3.to_checksum_address(receiver) == Web3.to_checksum_address(ZERO_ADDRESS_STR):
            reason = "Upshift deposit receiver cannot be the zero address"
            raise ValueError(reason)
        self.check_deposit_whitelist(receiver)
        whitelist = self.vault.upshift_contract.functions.sendersWhitelistAddress().call()
        if whitelist.lower() != ZERO_ADDRESS_STR and self.vault.fetch_deposit_cap() is not None:
            quota = get_deployed_contract(self.web3, "upshift/ISendersAllocationWhitelist.json", whitelist)
            _, raw_reference = self._fetch_deposit_preview(asset, raw_amount, "latest")
            quota.functions.checkAndRecordDeposit(receiver, raw_reference).call({"from": self.vault.address})
        return ERC4626DepositRequest(
            vault=self.vault,
            owner=owner,
            to=receiver,
            amount=asset.convert_to_decimals(raw_amount),
            raw_amount=raw_amount,
            funcs=[
                asset.contract.functions.approve(self.vault.address, raw_amount),
                self.vault.upshift_contract.functions.deposit(asset.address, raw_amount, receiver),
            ],
        )

    def create_redemption_request(  # noqa: PLR0917, PLR0914
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        shares: Decimal | None = None,
        raw_shares: int | None = None,
        check_max_deposit: bool = True,  # noqa: FBT001, FBT002
        check_enough_token: bool = True,  # noqa: FBT001, FBT002
        *,
        instant: bool = False,
    ) -> RedemptionRequest:
        """Build a verified Upshift instant or request/claim redemption.

        :param owner:
            Share owner used for structured error context.
        :param to:
            Asset receiver, defaulting to the share owner.
        :param shares:
            Decimal LP shares, exclusive with ``raw_shares``.
        :param raw_shares:
            Native LP shares, exclusive with ``shares``.
        :param check_max_deposit:
            Retained base-interface compatibility flag.
        :param check_enough_token:
            Check the owner's LP share balance.
        :param instant:
            Select the verified fee-bearing multi-asset instant path explicitly.
            The default follows the configured lag. Legacy instant selectors
            remain unsupported; zero-lag legacy requests already pay directly.
        :return:
            Synchronous standard request for instant redemption, or a queued
            request that parses a dated redemption ticket.
        """
        del check_max_deposit
        if (shares is None) == (raw_shares is None):
            raise ValueError("Give exactly one of shares or raw_shares")
        receiver = owner if to is None else to
        if Web3.to_checksum_address(receiver) == Web3.to_checksum_address(ZERO_ADDRESS_STR):
            raise ValueError("Upshift redemption receiver cannot be the zero address")
        if raw_shares is None:
            raw_shares = self.vault.share_token.convert_to_raw(shares)
        if raw_shares <= 0:
            raise ValueError("Upshift redemption shares must be positive")
        closed_reason = self.vault.fetch_redemption_closed_reason()
        if closed_reason is not None:
            raise self._create_unavailable(closed_reason, owner, "redeem", "preflight")
        if check_enough_token:
            balance = int(self.vault.share_token.fetch_raw_balance_of(owner))
            if balance < raw_shares:
                raise self._create_unavailable("Insufficient Upshift LP share balance", owner, "redeem", "preflight", requested_raw_amount=raw_shares, available_raw_amount=balance)
        self.check_deposit_whitelist(owner)
        self.check_deposit_whitelist(receiver)
        functions = self.vault.upshift_contract.functions
        synchronous = instant or self.has_synchronous_redemption()
        if instant and not self.vault.multi_asset_like:
            raise self._create_unavailable("Legacy instant selector is unsupported; use the request path", owner, "redeem", "preflight")
        year, month, day, _ = functions.getWithdrawalEpoch().call()
        existing = int(functions.getBurnableAmountByReceiver(year, month, day, receiver).call())
        if existing:
            raise self._create_unavailable("Upshift receiver/date already has a withdrawal; aggregate claims are unsupported", owner, "redeem", "preflight")
        if self.vault.multi_asset_like:
            function = functions.instantRedeem(raw_shares, receiver) if synchronous else functions.requestRedeem(raw_shares, receiver)
            raw_assets, _ = functions.previewRedemption(raw_shares, synchronous).call()
        else:
            function = functions.requestRedeem(raw_shares, receiver, owner)
            raw_assets = functions.convertToAssets(raw_shares).call()
        cap = int(functions.maxWithdrawalAmount().call())
        if raw_assets > cap:
            raise self._create_unavailable("Upshift withdrawal exceeds configured cap", owner, "redeem", "preflight", requested_raw_amount=int(raw_assets), available_raw_amount=cap)
        request_class = ERC4626RedemptionRequest if synchronous else UpshiftQueuedRedemptionRequest
        funcs = [function]
        if self.vault.multi_asset_like and not synchronous:
            funcs.insert(0, self.vault.share_token.approve(self.vault.address, self.vault.share_token.convert_to_decimals(raw_shares)))
        return request_class(
            vault=self.vault,
            owner=owner,
            to=receiver,
            shares=self.vault.share_token.convert_to_decimals(raw_shares),
            raw_shares=raw_shares,
            funcs=funcs,
        )

    def create_redemption_request_for_analysis(self, owner: HexAddress, raw_shares: int) -> UpshiftQueuedRedemptionRequest:
        """Rebuild the queued parser without repeating post-request admission.

        Shares have already moved to escrow and the date obligation now exists.
        Only original call metadata is rebuilt; receipt parsing validates it.

        :param owner: Original custody owner and receiver.
        :param raw_shares: Exact native shares from persisted trade metadata.
        :return: Queued parser with the original approval/request call count.
        """
        if self.vault.multi_asset_like:
            funcs = [self.vault.share_token.approve(self.vault.address, self.vault.share_token.convert_to_decimals(raw_shares)), self.vault.upshift_contract.functions.requestRedeem(raw_shares, owner)]
        else:
            funcs = [self.vault.upshift_contract.functions.requestRedeem(raw_shares, owner, owner)]
        return UpshiftQueuedRedemptionRequest(vault=self.vault, owner=owner, to=owner, shares=self.vault.share_token.convert_to_decimals(raw_shares), raw_shares=raw_shares, funcs=funcs)

    def force_settle(
        self,
        ticket: DepositTicket | RedemptionTicket | None,
        *,
        mock: object | None = None,  # noqa: ARG002 - Shared simulation interface.
        ignore_liquidity: bool = False,
    ) -> VaultForcedSettlementResult:
        """Advance an Anvil fork to maturity without fabricating liquidity.

        Ordinary claim execution remains with the caller and its custody signer.
        A funded mature request becomes claimable without operator processing.

        :param ticket: Queued ticket, or ``None`` for a synchronous no-op.
        :param mock: Unused; real fork contracts are exercised.
        :param ignore_liquidity: Unsupported liquidity-bypass request.
        :return: Observed status before and after advancing local time.
        """
        if ticket is None:
            return create_synchronous_settlement_result()
        if not is_anvil(self.web3) or ignore_liquidity:
            raise UnsupportedVaultSimulation("Upshift requires an Anvil fork with real liquidity", unsupported_reason="anvil_real_liquidity_required")
        assert isinstance(ticket, UpshiftQueuedRedemptionTicket)
        before = self.get_redemption_request_status(ticket)
        if self.web3.eth.get_block("latest")["timestamp"] < ticket.claimable_epoch:
            mine(self.web3, timestamp=ticket.claimable_epoch + 1)
        after = self.get_redemption_request_status(ticket)
        return VaultForcedSettlementResult(ticket=ticket, settlement_required=True, status_before=before, status_after=after)

    def get_redemption_request_status(self, ticket: UpshiftQueuedRedemptionTicket, block_identifier: BlockIdentifier = "latest") -> AsyncVaultRequestStatus:
        """Check exact outstanding shares, maturity, permissions and liquidity.

        A missing obligation needs receipt recovery. It is never proof of a
        zero payout. Outside additions violate the single-request invariant.

        :param ticket: Persisted withdrawal identity.
        :param block_identifier: Block used consistently for all reads.
        :return: Missing, pending, or executable claim.
        """
        block = self.web3.eth.get_block(block_identifier)
        number = block["number"]
        raw_shares = int(self.vault.upshift_contract.functions.getBurnableAmountByReceiver(ticket.year, ticket.month, ticket.day, ticket.to).call(block_identifier=number))
        if raw_shares == 0:
            return AsyncVaultRequestStatus.none
        assert raw_shares == ticket.raw_shares, "Upshift receiver/date combines requests; halt aggregate settlement"
        if block["timestamp"] < ticket.claimable_epoch or self.vault.upshift_contract.functions.withdrawalsPaused().call(block_identifier=number):
            return AsyncVaultRequestStatus.pending
        self.check_deposit_whitelist(ticket.owner)
        self.check_deposit_whitelist(ticket.to)
        net = self.fetch_pending_redemption_value(ticket, block_identifier=number)
        liquidity = self.vault.denomination_token.fetch_balance_of(self.vault.address, block_identifier=number)
        return AsyncVaultRequestStatus.claimable if liquidity >= net else AsyncVaultRequestStatus.pending

    def fetch_pending_redemption_value(self, ticket: UpshiftQueuedRedemptionTicket, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Value one ticket without making its proceeds spendable cash.

        Legacy entitlement is fixed by the request event. Multi-asset requests
        retain escrowed shares whose net value changes until the claim.

        :param ticket: Request being valued.
        :param block_identifier: Block used for floating valuation.
        :return: Net denomination-token value.
        """
        raw_shares = int(self.vault.upshift_contract.functions.getBurnableAmountByReceiver(ticket.year, ticket.month, ticket.day, ticket.to).call(block_identifier=block_identifier))
        if raw_shares == 0:
            raise self._create_unavailable("Consumed Upshift claim awaits payout receipt recovery", ticket.owner, "redeem", "valuation")
        assert raw_shares == ticket.raw_shares, "Upshift aggregate claim; halt before valuation"
        assert self.vault.multi_asset_like or ticket.raw_assets is not None, "Legacy ticket lacks fixed entitlement; recover its request event"
        if ticket.raw_assets is not None:
            return self.vault.denomination_token.convert_to_decimals(ticket.raw_assets)
        return self.estimate_redeem(ticket.owner, self.vault.share_token.convert_to_decimals(ticket.raw_shares), block_identifier=block_identifier)

    def can_finish_redeem(self, redemption_ticket: UpshiftQueuedRedemptionTicket) -> bool:
        """Return whether a queued ticket has an onchain claimable amount.

        :param redemption_ticket:
            Upshift queued redemption ticket.
        :return:
            Whether an admitted caller can execute the mature funded claim.
        """
        return self.get_redemption_request_status(redemption_ticket) is AsyncVaultRequestStatus.claimable

    def serialize_redemption_ticket(self, ticket: UpshiftQueuedRedemptionTicket) -> dict:
        """Serialise the scheduled Upshift receiver/date aggregate identity.

        :param ticket:
            Queued Upshift redemption ticket.
        :return:
            JSON-compatible base and calendar/epoch fields.
        """
        data = super().serialize_redemption_ticket(ticket)
        data.update(
            upshift_claimable_epoch=ticket.claimable_epoch,
            upshift_year=ticket.year,
            upshift_month=ticket.month,
            upshift_day=ticket.day,
            upshift_raw_assets=ticket.raw_assets,
        )
        return data

    def reconstruct_redemption_ticket(self, data: dict) -> UpshiftQueuedRedemptionTicket:
        """Restore a persisted Upshift receiver/date aggregate ticket.

        :param data:
            Data produced by :meth:`serialize_redemption_ticket`.
        :return:
            Queued Upshift redemption ticket.
        """
        try:
            date = datetime.date(int(data["upshift_year"]), int(data["upshift_month"]), int(data["upshift_day"]))
            assert datetime.datetime.fromtimestamp(int(data["upshift_claimable_epoch"]), datetime.timezone.utc).date() == date, "Upshift ticket date and epoch disagree; recover the request receipt"
            assert data["vault_address"].lower() == self.vault.address.lower(), "Ticket belongs to another Upshift vault"
            return UpshiftQueuedRedemptionTicket(
                vault_address=data["vault_address"],
                owner=data["vault_owner"],
                to=data.get("vault_to", data["vault_owner"]),
                raw_shares=int(data["vault_raw_amount"]),
                tx_hash=HexBytes(data["vault_request_tx_hash"]),
                claimable_epoch=int(data["upshift_claimable_epoch"]),
                year=int(data["upshift_year"]),
                month=int(data["upshift_month"]),
                day=int(data["upshift_day"]),
                raw_assets=int(data["upshift_raw_assets"]) if data.get("upshift_raw_assets") is not None else None,
            )
        except (ValueError, TypeError, KeyError) as error:
            raise AssertionError("Malformed Upshift ticket; inspect persisted request evidence before retrying") from error

    def finish_redemption(self, redemption_ticket: UpshiftQueuedRedemptionTicket) -> ContractFunction:
        """Build the verified dated Upshift claim call.

        :param redemption_ticket:
            Claimable Upshift ticket.
        :return:
            ``claim(year, month, day, receiver)`` call.
        """
        if not self.can_finish_redeem(redemption_ticket):
            raise ValueError("Upshift redemption is not claimable")
        return self.vault.upshift_contract.functions.claim(redemption_ticket.year, redemption_ticket.month, redemption_ticket.day, redemption_ticket.to)

    def analyse_deposit(self, claim_tx_hash: HexBytes | str, deposit_ticket: DepositTicket | None) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Decode Upshift's verified protocol deposit event.

        :param claim_tx_hash:
            Mined deposit transaction hash.
        :param deposit_ticket:
            Unused synchronous deposit ticket.
        :return:
            Executed deposit amounts or a structured transaction failure.
        :raise ValueError:
            If the receipt does not contain exactly one matching deposit event.
        """
        del deposit_ticket
        receipt = self.vault.web3.eth.get_transaction_receipt(claim_tx_hash)
        if receipt["status"] != 1:
            return DepositRedeemEventFailure(HexBytes(claim_tx_hash), "Transaction reverted", protocol="Upshift", vault_address=self.vault.address, direction="deposit", phase="transaction", receipt_status=0)
        event_contract = get_deployed_contract(self.vault.web3, "upshift/IMultiAssetVaultEvents.json", self.vault.address)
        logs = event_contract.events.Deposit().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        logs = [log for log in logs if log["address"].lower() == self.vault.address.lower()]
        if len(logs) != 1:
            reason = f"Expected exactly one Upshift Deposit event, got {len(logs)} at {claim_tx_hash}"
            raise ValueError(reason)
        args = logs[0]["args"]
        asset = fetch_erc20_details(
            self.vault.web3,
            args["assetIn"],
            chain_id=self.vault.chain_id,
            cache=self.vault.token_cache,
        )
        return DepositRedeemEventAnalysis(
            from_=Web3.to_checksum_address(args["senderAddr"]),
            to=Web3.to_checksum_address(args["receiverAddr"]),
            denomination_amount=asset.convert_to_decimals(int(args["amountIn"])),
            share_count=self.vault.share_token.convert_to_decimals(int(args["shares"])),
            tx_hash=HexBytes(claim_tx_hash),
            block_number=int(receipt["blockNumber"]),
            block_timestamp=get_block_timestamp(self.vault.web3, int(receipt["blockNumber"])),
        )

    def _fetch_redemption_call(self, tx_hash: HexBytes) -> tuple[ContractFunction, dict]:
        """Decode a direct vault call or an existing guarded performCall envelope.

        Unknown wrappers are refused; searching arbitrary calldata for a date
        would not establish which call actually paid a claim.

        :param tx_hash: Successful payout transaction.
        :return: Vault function and decoded arguments.
        """
        tx = self.web3.eth.get_transaction(tx_hash)
        target, data = tx["to"], HexBytes(tx["input"])
        wrappers = {
            Web3.keccak(text="performCall(address,bytes)")[:4]: ["address", "bytes"],
            Web3.keccak(text="performCall(address,bytes,uint256)")[:4]: ["address", "bytes", "uint256"],
        }
        if target.lower() != self.vault.address.lower():
            types = wrappers.get(data[:4])
            assert types is not None, "Unknown Upshift payout wrapper; cannot attribute receipt"
            args = decode(types, data[4:])
            target, data = args[:2]
        assert target.lower() == self.vault.address.lower(), "Payout targets another vault"
        return self.vault.upshift_contract.decode_function_input(data)

    def analyse_redemption(self, claim_tx_hash: HexBytes | str, redemption_ticket: RedemptionTicket | None) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Validate native payout evidence for a synchronous or dated withdrawal.

        Dated payouts must match calldata, exact outstanding shares immediately
        before the payout block, and the actual reference-token transfer. A
        blacklist redirection or receiver/date aggregate is never credited.

        :param claim_tx_hash: Mined claim, public batch, or zero-lag request.
        :param redemption_ticket: Request whose proceeds are being recognised.
        :return: Net executed amounts or a mined transaction failure.
        """
        ticket = redemption_ticket
        tx_hash = HexBytes(claim_tx_hash)
        receipt = self.web3.eth.get_transaction_receipt(tx_hash)
        if receipt["status"] != 1:
            return DepositRedeemEventFailure(tx_hash, "Upshift withdrawal reverted", protocol="Upshift", direction="redeem", receipt_status=0)
        function, args = self._fetch_redemption_call(tx_hash)
        if ticket is None:
            if function.fn_name == "requestRedeem" and not self.vault.multi_asset_like:
                owner = args["holderAddr"]
            elif function.fn_name == "instantRedeem" and self.vault.multi_asset_like:
                withdrawals = self.vault.upshift_contract.events.Withdraw().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
                withdrawals = [log["args"] for log in withdrawals if log["address"].lower() == self.vault.address.lower()]
                assert len(withdrawals) == 1, "Missing or ambiguous synchronous Upshift withdrawal"
                owner = withdrawals[0]["owner"]
            else:
                raise AssertionError("Dated Upshift payouts require their persisted request ticket")
            ticket = RedemptionTicket(vault_address=self.vault.address, owner=owner, to=args["receiverAddr"], raw_shares=int(args["shares"]), tx_hash=tx_hash)
        block = int(receipt["blockNumber"])
        if isinstance(ticket, UpshiftQueuedRedemptionTicket):
            assert function.fn_name in {"claim", "processAllClaimsByDate"}, "Unexpected dated payout call"
            assert tuple(int(args[k]) for k in ("year", "month", "day")) == (ticket.year, ticket.month, ticket.day), "Payout belongs to another request date"
            if function.fn_name == "claim":
                assert args["receiverAddr"].lower() == ticket.to.lower(), "Claim receiver differs from ticket"
            outstanding = self.vault.upshift_contract.functions.getBurnableAmountByReceiver(ticket.year, ticket.month, ticket.day, ticket.to).call(block_identifier=block - 1)
            assert outstanding == ticket.raw_shares, "Upshift consumed an aggregate or unrecognised obligation"
            logs = self.vault.upshift_contract.events.WithdrawalProcessed().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
            payouts = [log["args"] for log in logs if log["address"].lower() == self.vault.address.lower() and log["args"]["receiverAddr"].lower() == ticket.to.lower()]
            assert len(payouts) == 1 and not payouts[0].get("wasBlacklisted", False), "Missing, ambiguous or redirected payout"
            raw_assets = int(payouts[0]["assetsAmount"])
            if ticket.raw_assets is not None:
                assert raw_assets == ticket.raw_assets, "Legacy payout differs from fixed net entitlement"
            # Prove the actual burn matches the pre-block obligations, including
            # every independent receiver in a public batch. This also detects
            # same-block outside additions not visible in the pre-block state.
            receivers = {log["args"]["receiverAddr"] for log in logs if log["address"].lower() == self.vault.address.lower()}
            expected_burn = sum(int(self.vault.upshift_contract.functions.getBurnableAmountByReceiver(ticket.year, ticket.month, ticket.day, receiver).call(block_identifier=block - 1)) for receiver in receivers)
            transfers = self.vault.share_token.contract.events.Transfer().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
            actual_burn = sum(int(log["args"]["value"]) for log in transfers if log["address"].lower() == self.vault.share_token.address_lower and log["args"]["from"].lower() == self.vault.address.lower() and log["args"]["to"].lower() == ZERO_ADDRESS_STR)
            assert actual_burn == expected_burn, "Upshift payout combined same-block or unattributed requests"
        elif self.vault.multi_asset_like:
            assert function.fn_name == "instantRedeem" and int(args["shares"]) == ticket.raw_shares and args["receiverAddr"].lower() == ticket.to.lower()
            logs = self.vault.upshift_contract.events.Withdraw().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
            payouts = [log["args"] for log in logs if log["address"].lower() == self.vault.address.lower()]
            assert len(payouts) == 1 and int(payouts[0]["shares"]) == ticket.raw_shares
            raw_assets = int(payouts[0]["assets"])
        else:
            assert function.fn_name == "requestRedeem" and int(args["shares"]) == ticket.raw_shares and args["receiverAddr"].lower() == ticket.to.lower() and args["holderAddr"].lower() == ticket.owner.lower()
            logs = self.vault.upshift_contract.events.WithdrawalProcessed().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
            payouts = [log["args"] for log in logs if log["address"].lower() == self.vault.address.lower() and log["args"]["receiverAddr"].lower() == ticket.to.lower()]
            assert len(payouts) == 1 and not payouts[0]["wasBlacklisted"]
            raw_assets = int(payouts[0]["assetsAmount"])
            requests = self.vault.upshift_contract.events.WithdrawalRequested().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
            requests = [log["args"] for log in requests if log["address"].lower() == self.vault.address.lower()]
            assert len(requests) == 1 and int(requests[0]["shares"]) == ticket.raw_shares
            assert requests[0]["ownerAddr"].lower() == ticket.owner.lower() and requests[0]["receiverAddr"].lower() == ticket.to.lower()
            assert int(requests[0]["assets"]) - int(requests[0]["fee"]) == raw_assets, "Zero-lag payout includes another request"
        transfers = self.vault.denomination_token.contract.events.Transfer().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        received = sum(int(log["args"]["value"]) for log in transfers if log["address"].lower() == self.vault.denomination_token.address_lower and log["args"]["from"].lower() == self.vault.address.lower() and log["args"]["to"].lower() == ticket.to.lower())
        assert received == raw_assets and raw_assets > 0, "Payout token transfer does not match protocol evidence"
        return DepositRedeemEventAnalysis(from_=ticket.owner, to=ticket.to, denomination_amount=self.vault.denomination_token.convert_to_decimals(raw_assets), share_count=self.vault.share_token.convert_to_decimals(ticket.raw_shares), tx_hash=tx_hash, block_number=block, block_timestamp=get_block_timestamp(self.web3, block))

    def fetch_completed_redemption_tx_hash(self, ticket: UpshiftQueuedRedemptionTicket) -> HexBytes | None:
        """Find and validate an already-paid dated claim through Hypersync.

        A missing or lagging indexer leaves the ticket unresolved. Candidate
        events must pass the same calldata and transfer analysis as our claims.

        :param ticket: Persisted request identity.
        :return: Attributable payout hash, or ``None`` if none is indexed.
        """
        # Keep the Hypersync extra optional for normal RPC execution.
        from eth_defi.hypersync.utils import configure_hypersync_from_env  # noqa: PLC0415

        client = configure_hypersync_from_env(self.web3).hypersync_client
        if client is None:
            raise self._create_unavailable("Hypersync is required for Upshift recovery", ticket.owner, "redeem", "recovery")
        start = self.web3.eth.get_transaction_receipt(ticket.tx_hash)["blockNumber"]
        head = self.web3.eth.block_number
        end = min(head, int(run_vault_flow_log_fetch(client.get_height())) - 1)
        if end < start:
            raise self._create_unavailable("Upshift payout indexer is behind chain", ticket.owner, "redeem", "recovery")
        logs = fetch_vault_flow_logs_hypersync(hypersync_client=client, vault_address=self.vault.address, topic0_list=[get_topic_signature_from_event(self.vault.upshift_contract.events.WithdrawalProcessed)], start_block=start, end_block=end)
        matches = []
        if self.vault.multi_asset_like:
            logs = [log for log in logs if log.topics[1] and log.topics[1][-40:].lower() == ticket.to[2:].lower()]
        else:
            logs = [log for log in logs if decode(["uint256", "uint256", "address", "uint256", "bool"], HexBytes(log.data))[2].lower() == ticket.to.lower()]
        for tx_hash in dict.fromkeys(HexBytes(log.transaction_hash) for log in logs):
            receipt = self.web3.eth.get_transaction_receipt(tx_hash)
            events = self.vault.upshift_contract.events.WithdrawalProcessed().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
            if not any(log["address"].lower() == self.vault.address.lower() and log["args"]["receiverAddr"].lower() == ticket.to.lower() for log in events):
                continue
            function, args = self._fetch_redemption_call(tx_hash)
            if function.fn_name not in {"claim", "processAllClaimsByDate"} or tuple(int(args[k]) for k in ("year", "month", "day")) != (ticket.year, ticket.month, ticket.day):
                continue
            self.analyse_redemption(tx_hash, ticket)
            matches.append(tx_hash)
        assert len(matches) <= 1, "Multiple payouts for one Upshift ticket; halt recovery"
        if not matches and end < head:
            raise self._create_unavailable("Upshift payout indexer is behind chain", ticket.owner, "redeem", "recovery")
        return matches[0] if matches else None


class UpshiftDepositManager(UpshiftMultiAssetDepositManager):  # noqa: PLR0904
    """Reuse the dated Upshift lifecycle for the legacy single-asset ABI.

    Deposits are ordinary ERC-4626 calls. Withdrawals always use
    ``requestRedeem(shares,receiver,holder)``, which pays immediately only when
    the configured lag is zero. Standard ``redeem`` is not supported.
    """

    #: Legacy pools accept only their denomination asset.
    supports_multi_asset_deposit = False

    def create_deposit_request(self, owner: HexAddress, to: HexAddress | None = None, amount: Decimal | None = None, raw_amount: int | None = None, check_max_deposit: bool = True, check_enough_token: bool = True, *, accepted_asset: HexAddress | None = None) -> ERC4626DepositRequest:
        """Build the standard legacy deposit using the existing ERC-4626 helper.

        The base helper retains admission, pause, capacity and balance checks.
        Legacy deposits currently require owner and receiver to coincide.

        :param owner: Depositor and share receiver.
        :param to: Optional receiver, required to equal owner.
        :param amount: Decimal amount, exclusive with raw amount.
        :param raw_amount: Raw denomination amount.
        :param check_max_deposit: Whether to preflight capacity.
        :param check_enough_token: Whether to preflight the owner's balance.
        :return: Standard synchronous deposit request.
        """
        assert accepted_asset is None or accepted_asset.lower() == self.vault.denomination_token.address_lower
        assert to is None or to.lower() == owner.lower(), "Legacy deposit receiver must equal owner"
        return ERC4626DepositManager.create_deposit_request(self, owner=owner, to=to, amount=amount, raw_amount=raw_amount, check_max_deposit=check_max_deposit, check_enough_token=check_enough_token)

    def fetch_depositable_raw_assets(self, owner: HexAddress) -> int | None:
        return ERC4626DepositManager.fetch_depositable_raw_assets(self, owner)

    def estimate_deposit(self, owner: HexAddress | None, amount: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        return ERC4626DepositManager.estimate_deposit(self, owner, amount, block_identifier=block_identifier)

    def estimate_redeem(self, owner: HexAddress | None, shares: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        raw = self.vault.upshift_contract.functions.previewRedeem(self.vault.share_token.convert_to_raw(shares)).call(block_identifier=block_identifier)
        return self.vault.denomination_token.convert_to_decimals(raw)

    def analyse_deposit(self, claim_tx_hash: HexBytes | str, deposit_ticket: DepositTicket | None) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        if deposit_ticket is None:
            receipt = self.web3.eth.get_transaction_receipt(claim_tx_hash)
            if receipt["status"] == 0:
                return DepositRedeemEventFailure(HexBytes(claim_tx_hash), "Upshift deposit reverted", receipt_status=0)
            logs = self.vault.upshift_contract.events.Deposit().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
            logs = [log for log in logs if log["address"].lower() == self.vault.address.lower()]
            assert len(logs) == 1, "Missing or ambiguous Upshift legacy deposit"
            args = logs[0]["args"]
            deposit_ticket = DepositTicket(vault_address=self.vault.address, owner=args["sender"], to=args["owner"], raw_amount=int(args["assets"]), tx_hash=HexBytes(claim_tx_hash), gas_used=int(receipt["gasUsed"]), block_number=int(receipt["blockNumber"]), block_timestamp=get_block_timestamp(self.web3, receipt["blockNumber"]))
        return ERC4626DepositManager.analyse_deposit(self, claim_tx_hash, deposit_ticket)

    def estimate_deposit_for_asset(self, owner: HexAddress | None, amount: Decimal, accepted_asset: HexAddress, block_identifier: BlockIdentifier = "latest") -> Decimal:
        assert accepted_asset.lower() == self.vault.denomination_token.address_lower
        return self.estimate_deposit(owner, amount, block_identifier=block_identifier)
