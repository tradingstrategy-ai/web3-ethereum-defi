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
from hexbytes import HexBytes
from web3 import Web3
from web3.logs import DISCARD

from eth_defi.abi import get_deployed_contract
from eth_defi.vault.deposit_redeem import DepositRedeemEventAnalysis, RedemptionRequest, RedemptionTicket, VaultDepositManager, VaultFlowUnavailable


def read_vault_events(web3: Web3, address: str, receipt: dict, name: str) -> list[dict]:
    """Decode only this vault's events, excluding logs from adapters and wrappers."""
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

    penalty_shares: int
    penalty_assets: int

    @property
    def remaining_shares(self) -> int:
        """Exact requested shares still to burn, excluding the already mined fee."""
        return self.raw_shares - self.penalty_shares

    @property
    def raw_shares_remaining_in_wallet(self) -> int:
        """The fee is burned; all other requested shares remain locked to this exit."""
        return self.remaining_shares

    def get_request_id(self) -> int:
        """Use the mined request hash as a local identity, not an onchain queue ID."""
        return int.from_bytes(self.tx_hash, "big")


@dataclass(slots=True)
class MorphoRecoveryAnalysis(DepositRedeemEventAnalysis):
    """Report total shares consumed and net cash, with the penalty disclosed once."""

    withdrawal_fee: Decimal


@dataclass(slots=True)
class MorphoRecoveryRequest(RedemptionRequest):
    """First transaction of a staged, immediately finishable redemption.

    ``plan`` is saved in the executor's existing preflight dictionary before
    signing. Rebuilding the receipt parser must restore it, without repeating
    availability checks after the first transaction has already burned shares.
    """

    requires_settlement: ClassVar[bool] = True
    plan: dict = field(default_factory=dict)

    def get_preflight_data(self) -> dict:
        """Retain the rate cap and exact deallocation submitted for this trade."""
        return {"morpho_recovery": self.plan}

    def restore_preflight_data(self, data: dict) -> None:
        """Restore original authorisation rather than using today's manual rule."""
        self.plan = data["morpho_recovery"]
        assert self.plan["schema"] == 1, "Unsupported Morpho recovery schema"

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> MorphoRecoveryTicket:
        """Require matching vault events proving both the recovery and fee burn.

        Assets remain in this vault; the fee Withdraw receiver must be the vault
        itself. Never infer a burn from a balance difference, which could also
        include another wallet operation. Integer asset fees round upwards.
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
    recovery is attempted when one alternate market cannot fund it.
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
