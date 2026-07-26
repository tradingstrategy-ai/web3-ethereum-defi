"""Upshift multi-asset deposit flow."""

from decimal import Decimal
from typing import Literal, NoReturn

from eth_typing import BlockIdentifier, HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3._utils.events import EventLogErrorFlags  # noqa: PLC2701

from eth_defi.abi import ZERO_ADDRESS_STR, get_deployed_contract
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest
from eth_defi.timestamp import get_block_timestamp
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.vault.deposit_redeem import DepositRedeemEventAnalysis, DepositRedeemEventFailure, DepositTicket, VaultFlowUnavailable


class UpshiftMultiAssetDepositManager(ERC4626DepositManager):
    """Build and decode Upshift's direct multi-asset ``deposit`` flow.

    Upshift ``multiAssetVault`` proxies are accounting contracts, not plain
    ERC-4626 share tokens: they accept a whitelist of deposit tokens through a
    protocol-specific ``deposit(asset, amount, receiver)`` entry point and expose
    share metadata on a separate LP token. This manager builds and decodes that
    synchronous deposit flow; it deliberately exposes no redemption, because the
    protocol's request/claim redemption lifecycle is not yet fork-proven.

    **Deposit process**

    Synchronous and asset-aware. The caller must select a token from the vault's
    onchain whitelist (:meth:`fetch_accepted_assets`, resolved by
    :meth:`_fetch_accepted_asset`); an unselected or non-whitelisted asset raises
    :class:`VaultFlowUnavailable`. :meth:`create_deposit_request` returns two
    calls — ``approve`` on the selected token followed by the vault's
    ``deposit(asset, amount, receiver)`` — and rejects a zero-address receiver.
    Capacity is preflighted through :meth:`fetch_max_deposit_for_asset`, which
    combines the per-deposit ``maxDepositAmount()`` cap and the vault-wide
    ``depositCap()`` minus ``getTotalAssets()``, converted into the selected
    token's units with the protocol's asset-aware ``previewDeposit``. Deposits
    are also gated vault-wide by :meth:`UpshiftVault.fetch_deposit_closed_reason`
    (``depositsPaused()``, zero ``maxDepositAmount()`` or a reached
    ``depositCap()``). There is no per-account minimum or whitelist; the amount
    must be strictly positive.

    **Redemption process**

    Not implemented. :meth:`create_redemption_request` and :meth:`estimate_redeem`
    always raise :class:`VaultFlowUnavailable`,
    :meth:`can_create_redemption_request` and :meth:`has_synchronous_redemption`
    always return ``False``, and
    :meth:`UpshiftVault.get_deposit_manager_capability` advertises
    ``can_redeem=False`` (``multi_asset_application_flow_not_implemented``). No
    redemption capacity model is exposed by this manager.

    **Queues and settlement**

    None exposed. The underlying Upshift protocol services withdrawals through a
    daily request-claim system (with an optional instant-redemption path), but
    this manager models neither, so there is no queue or settlement surface here.

    **Lockups and cooldowns**

    Not applicable to deposits (synchronous). At the vault level,
    :meth:`UpshiftVault.get_estimated_lock_up` reports a nominal one-day
    redemption claim cycle, but this manager implements no redemption path to
    which that would apply.

    **Whitelisting / access control**

    Deposits are permissionless per account, but the deposit *token* must be on
    the vault's onchain asset whitelist. Availability is otherwise controlled
    vault-wide by the pause flags and caps above, not by a per-account whitelist.

    **Anvil settlement (force_settle)**

    No-op for the supported deposit path: deposits are synchronous, so
    :meth:`force_settle` takes ``None`` and there is no ticket to settle. There is
    no redemption ticket to settle because redemption is unimplemented.
    """

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
        raw_total_limit = int(functions.depositCap().call(block_identifier=block_identifier))
        raw_total_assets = int(functions.getTotalAssets().call(block_identifier=block_identifier))
        raw_remaining_capacity = max(raw_total_limit - raw_total_assets, 0)
        raw_reference_capacity = min(raw_per_deposit_limit, raw_remaining_capacity)
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
        del owner
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
    ) -> NoReturn:
        """Refuse estimates for the unimplemented redemption flow.

        :param owner:
            Share owner used for structured error context.
        :param shares:
            Unused requested share amount.
        :param block_identifier:
            Unused because the adapter has no redemption flow.
        :raise VaultFlowUnavailable:
            Always, because multi-asset redemption is not implemented.
        """
        del shares, block_identifier
        reason = "Upshift multi-asset redemption flow is not implemented"
        raise self._create_unavailable(reason, owner, "redeem", "estimate")

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

    def can_create_redemption_request(self, owner: HexAddress) -> bool:  # noqa: PLR6301
        """Return false because no multi-asset redemption path is implemented.

        :param owner:
            Unused share owner.
        :return:
            Always ``False``.
        """
        del owner
        return False

    def has_synchronous_redemption(self) -> bool:  # noqa: PLR6301
        """Return false because redemption is unsupported, not synchronous.

        :return:
            Always ``False``.
        """
        return False

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
        asset = self._fetch_accepted_asset(owner, accepted_asset)
        if (amount is None) == (raw_amount is None):
            reason = "Give exactly one of amount or raw_amount"
            raise ValueError(reason)
        if raw_amount is None:
            raw_amount = asset.convert_to_raw(amount)
        if raw_amount <= 0:
            reason = "Upshift deposit amount must be positive"
            raise ValueError(reason)
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

    def create_redemption_request(  # noqa: PLR0917
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        shares: Decimal | None = None,
        raw_shares: int | None = None,
        check_max_deposit: bool = True,  # noqa: FBT001, FBT002
        check_enough_token: bool = True,  # noqa: FBT001, FBT002
    ) -> NoReturn:
        """Refuse unverified Upshift multi-asset redemption handling.

        :param owner:
            Share owner used for structured error context.
        :param to:
            Unused receiver.
        :param shares:
            Unused decimal share amount.
        :param raw_shares:
            Unused native share amount.
        :param check_max_deposit:
            Unused base-interface compatibility flag.
        :param check_enough_token:
            Unused base-interface compatibility flag.
        :raise VaultFlowUnavailable:
            Always, because multi-asset redemption is not implemented.
        """
        del to, shares, raw_shares, check_max_deposit, check_enough_token
        reason = "Upshift multi-asset redemption flow is not implemented"
        raise self._create_unavailable(reason, owner, "redeem", "preflight")

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
