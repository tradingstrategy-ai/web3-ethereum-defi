"""One public, fee-capped liquidity recovery before a Morpho V2 redemption.

Vault V2 prices the whole portfolio but its automatic exit uses one configured
market. Api3 dCOMP USDC and Purinta USDC demonstrated that this market can own
fewer supply shares than an aggregate-priced redemption needs, making Blue's
``withdraw`` underflow. Another market in the same adapter can fund the exit.
``forceDeallocate`` is public, but burns penalty shares before the final redeem.

This module contains contract mechanics, not an address allowlist. The caller
must explicitly authorise a maximum penalty through the vault instance. See
``tradeexecutor/strategy/vault_protocol/morpho.py`` for the reviewed identities,
quantities and full case analysis, and
https://github.com/tradingstrategy-ai/vault-notebooks/pull/25#issuecomment-6896601999.
There are no injected assets, privileged allocator calls or oracle overrides.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar

from eth_abi import encode
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.logs import DISCARD
from web3.types import TxReceipt

from eth_defi.abi import get_deployed_contract
from eth_defi.vault.deposit_redeem import DepositRedeemEventAnalysis, RedemptionRequest, RedemptionTicket, VaultDepositManager, VaultFlowUnavailable


def read_vault_events(web3: Web3, address: HexAddress, receipt: TxReceipt, name: str) -> list[dict]:
    """Decode a receipt without attributing another contract's logs to this vault.

    :py:meth:`MorphoRecoveryRequest.parse_redeem_transaction` uses this helper
    to prove the first fee burn. The finishing manager's
    :py:meth:`~eth_defi.erc_4626.vault_protocol.morpho.deposit_redeem.MorphoV2DepositManager.analyse_redemption`
    uses them to check the remaining-share burn. Both need a materialised list
    so zero or ambiguous matching events can be refused before accounting.

    :param web3: Connection used to bind the stored Vault V2 ABI; no RPC read is made.
    :param address: Vault whose own emitted logs may establish the trade outcome.
    :param receipt: Mined receipt, which may also contain adapter and custody logs.
    :param name: ABI event name, such as ``ForceDeallocate`` or ``Withdraw``.
    :return: Decoded argument dictionaries from this address only, in receipt order.
    """
    contract = get_deployed_contract(web3, "morpho/VaultV2.json", address)
    return [event["args"] for event in getattr(contract.events, name)().process_receipt(receipt, errors=DISCARD) if event["address"].lower() == address.lower()]


@dataclass(slots=True)
class MorphoRecoveryTicket(RedemptionTicket):
    """Persist the original disposal and the fee already paid in its first receipt.

    ``raw_shares`` always includes the penalty burn. Only ``remaining_shares``
    may be submitted to the finishing redeem. No protocol queue exists: the
    request transaction hash is the local ticket identity. Existing executor
    transaction records provide signing, broadcast and restart idempotency.
    """

    #: Exact share burn proven by the first transaction's Withdraw event.
    penalty_shares: int
    #: Denomination assets retained by the vault for the mined penalty burn.
    penalty_assets: int

    @property
    def remaining_shares(self) -> int:
        """Keep the second burn bounded to shares left after the paid penalty.

        :py:meth:`~eth_defi.erc_4626.vault_protocol.morpho.deposit_redeem.MorphoV2DepositManager.finish_redemption`
        uses this amount because submitting the original disposal would try to burn
        fee shares a second time.

        :return: Non-negative raw share-token units reserved for the finishing redeem.
        """
        return self.raw_shares - self.penalty_shares

    @property
    def raw_shares_remaining_in_wallet(self) -> int:
        """Retain custody evidence without releasing the pending disposal for trading.

        :py:meth:`~eth_defi.vault.deposit_redeem.VaultDepositManager.serialize_redemption_ticket`
        stores this amount so the executor can reconcile the wallet after the fee
        burn while still reserving the full requested exit against duplicate sales.

        :return: Remaining raw share-token units; these shares are still in custody.
        """
        return self.remaining_shares

    def get_request_id(self) -> int:
        """Identify a staged exit without inventing a protocol withdrawal queue.

        The mined first transaction uniquely identifies this local ticket. Generic
        settlement consumers can use the usual integer identity even though Vault V2
        has no separate onchain request ID for public recovery.

        :return: Unsigned integer representation of the recovery transaction hash.
        """
        return int.from_bytes(self.tx_hash, "big")


@dataclass(slots=True)
class MorphoRecoveryAnalysis(DepositRedeemEventAnalysis):
    """Report total shares consumed and net cash, with the penalty disclosed once."""

    #: Paid penalty for disclosure; denomination_amount is already the net cash.
    withdrawal_fee: Decimal


@dataclass(slots=True)
class MorphoRecoveryRequest(RedemptionRequest):
    """First transaction of a staged, immediately finishable redemption.

    ``plan`` is saved in the executor's existing preflight dictionary before
    signing. Rebuilding the receipt parser must restore it, without repeating
    availability checks after the first transaction has already burned shares.
    """

    requires_settlement: ClassVar[bool] = True
    #: Submitted adapter, raw assets and authorised rate cap, with schema version.
    plan: dict = field(default_factory=dict)

    def get_preflight_data(self) -> dict:
        """Retain the authorisation that applied when the recovery was signed.

        The executor's :py:meth:`tradeexecutor.ethereum.vault.vault_routing.VaultRouting.deposit_or_redeem`
        saves this evidence before broadcast, allowing receipt analysis to check the
        submitted amount and cap after a restart or manual rule change.

        :return: Preflight dictionary containing the versioned ``morpho_recovery`` plan.
        """
        return {"morpho_recovery": self.plan}

    def restore_preflight_data(self, data: dict) -> None:
        """Restore the submitted plan before interpreting an already mined fee burn.

        The executor's :py:meth:`tradeexecutor.ethereum.vault.vault_routing.VaultRouting.settle_trade`
        rebuilds a receipt-only request, then restores this evidence so today's
        authorisation cannot replace the cap that governed the signed transaction.

        :param data: Saved preflight fields containing the original ``morpho_recovery`` plan.
        :return: None; update this parser's plan in place.
        :raises AssertionError: The stored plan uses an unsupported schema version.
        """
        self.plan = data["morpho_recovery"]
        assert self.plan["schema"] == 1, "Unsupported Morpho recovery schema"

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> MorphoRecoveryTicket:
        """Require matching vault events proving both the recovery and fee burn.

        Assets remain in this vault; the fee Withdraw receiver must be the vault
        itself. Never infer a burn from a balance difference, which could also
        include another wallet operation. Integer asset fees round upwards.

        :py:meth:`tradeexecutor.ethereum.vault.vault_routing.VaultRouting.settle_trade`
        calls this after confirmation to persist a paid ticket before any finish attempt.

        :param tx_hashes: Exactly one confirmed recovery transaction; approval calls are excluded.
        :return: Ticket containing the original disposal and receipt-proven penalty amounts.
        :raises AssertionError: Receipt, ownership, amount or fee evidence is inconsistent.
        """
        assert len(tx_hashes) == 1 and self.plan
        receipt = self.web3.eth.get_transaction_receipt(tx_hashes[0])
        assert receipt["status"] == 1, "Recovery transaction reverted"
        recoveries = read_vault_events(self.web3, self.vault.address, receipt, "ForceDeallocate")
        withdrawals = read_vault_events(self.web3, self.vault.address, receipt, "Withdraw")
        assert len(recoveries) == len(withdrawals) == 1, "Ambiguous recovery or penalty events"
        recovery, burn = recoveries[0], withdrawals[0]
        assert recovery["sender"].lower() == recovery["onBehalf"].lower() == self.owner.lower()
        assert recovery["adapter"].lower() == self.plan["adapter"].lower()
        assert recovery["assets"] == int(self.plan["assets"])
        assert burn["sender"].lower() == burn["onBehalf"].lower() == self.owner.lower()
        assert burn["receiver"].lower() == self.vault.address.lower()
        assert burn["assets"] == recovery["penaltyAssets"]
        maximum = (recovery["assets"] * self.plan["max_fee_bps"] + 9_999) // 10_000
        assert 0 <= burn["assets"] <= maximum, "Mined recovery penalty exceeded authorisation"
        assert 0 <= burn["shares"] < self.raw_shares, "Recovery consumed the entire requested position"
        return MorphoRecoveryTicket(
            vault_address=self.vault.address,
            owner=self.owner,
            to=self.to,
            raw_shares=self.raw_shares,
            tx_hash=HexBytes(tx_hashes[0]),
            penalty_shares=burn["shares"],
            penalty_assets=burn["assets"],
        )


def prepare_recovery(manager: VaultDepositManager, request: RedemptionRequest) -> MorphoRecoveryRequest | None:
    """Plan one public alternate-market recovery for a proven exit-route shortfall.

    Called only after exact redemption panics with arithmetic code 0x11. The
    configured adapter must belong to this vault and expose the reviewed Blue
    market interface. Aggregate entitlement must exceed idle assets plus this
    adapter's position in the configured exit market. This excludes unrelated
    arithmetic errors. Unknown interfaces fail closed through normal RPC errors.

    We conservatively free the gross requested assets from one other funded
    market, rather than optimise a moving tiny shortfall. Thus the fee is at
    most the authorised rate of that gross amount (plus integer rounding).
    The final exit can still use its ordinary configured market for any small
    accrual difference. The exact force call must preflight successfully; no
    recovery is attempted when the selected alternate market cannot fund it.
    Select the largest non-exit supply position covering the gross claim, then
    check its actual cash through the exact call. Do not try other markets after a
    refusal or build a multi-market recovery plan.

    :py:meth:`~eth_defi.erc_4626.vault_protocol.morpho.deposit_redeem.MorphoV2DepositManager.create_redemption_request`
    calls this only after the exact direct request proves the qualifying panic.

    :param manager: Adapter supplying the chain connection, vault and explicit fee cap.
    :param request: Failed direct request; its exact raw shares and owner remain binding.
    :return: Unsigned recovery request, or None when the authorisation, route proof
        or alternate supply position does not qualify. No transaction is broadcast.
    :raises VaultFlowUnavailable: The configured live penalty exceeds the authorised rate.
    :raises ValueError: The selected market's asset or simulated share burn is unsafe.
    """
    vault, web3 = manager.vault, manager.web3
    limit = vault.redemption_recovery_max_fee_bps
    if limit is None:
        return None
    assert type(limit) is int and 0 <= limit <= 10_000
    # A common observation block avoids mixing a changing route with its balances.
    block = web3.eth.block_number
    outer = get_deployed_contract(web3, "morpho/VaultV2.json", vault.address)
    adapter_address = outer.functions.liquidityAdapter().call(block_identifier=block)
    if not int(adapter_address, 16) or not outer.functions.isAdapter(adapter_address).call(block_identifier=block):
        return None
    adapter = get_deployed_contract(web3, "morpho/MorphoMarketV1AdapterV2.json", adapter_address)
    if adapter.functions.parentVault().call(block_identifier=block).lower() != vault.address.lower():
        return None
    data = outer.functions.liquidityData().call(block_identifier=block)
    if len(data) != 160:
        return None
    route_id = Web3.keccak(data)
    gross = outer.functions.previewRedeem(request.raw_shares).call(block_identifier=block)
    idle = vault.denomination_token.contract.functions.balanceOf(vault.address).call(block_identifier=block)
    route_assets = adapter.functions.expectedSupplyAssets(route_id).call(block_identifier=block)
    if gross <= idle + route_assets:
        return None
    penalty = outer.functions.forceDeallocatePenalty(adapter_address).call(block_identifier=block)
    # Contract rates use 1e18 for 100%; the caller authorises basis points.
    if penalty > limit * 10**14:
        raise VaultFlowUnavailable("Morpho public recovery penalty exceeds the reviewed cap", protocol="Morpho", vault_address=vault.address, caller=request.owner, direction="redeem", phase="preflight", decoded_error="RecoveryPenaltyTooHigh")
    blue = get_deployed_contract(web3, "morpho/MorphoBlue.json", adapter.functions.morpho().call(block_identifier=block))
    candidates = []
    for index in range(adapter.functions.marketIdsLength().call(block_identifier=block)):
        market = adapter.functions.marketIds(index).call(block_identifier=block)
        available = adapter.functions.expectedSupplyAssets(market).call(block_identifier=block)
        if market != route_id and available >= gross:
            candidates.append((available, market))
    if not candidates:
        return None
    # One deterministic choice, no unbounded search or repeated penalty attempts.
    _, market = max(candidates)
    params = blue.functions.idToMarketParams(market).call(block_identifier=block)
    if params[0].lower() != vault.denomination_token.address.lower():
        raise ValueError("Recovery loan asset mismatch")
    call = outer.functions.forceDeallocate(adapter_address, encode(["(address,address,address,address,uint256)"], [params]), gross, request.owner)
    # Includes actual market cash, allowance, gate and fee-burn checks. A funded
    # position alone is insufficient evidence that cash can be withdrawn now.
    fee_shares = call.call({"from": request.owner}, block_identifier=block)
    if not 0 <= fee_shares < request.raw_shares:
        raise ValueError("Recovery would consume the requested position")
    return MorphoRecoveryRequest(
        vault=vault,
        owner=request.owner,
        to=request.to,
        shares=request.shares,
        raw_shares=request.raw_shares,
        funcs=[call],
        plan={"schema": 1, "adapter": adapter_address, "assets": str(gross), "max_fee_bps": limit},
    )
