"""Local Core settlement boundaries for the three-chain Lagoon tutorial.

Real forked custody contracts execute normally. Only Core sequencing, API
signatures, investment pricing and proof settlement are deterministic doubles.
These helpers never submit a request to an external Core API.
"""

# ruff: noqa: PLR2004 -- protocol wire values are deliberately explicit in this fake.

import json
import logging
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from typing import Any

from eth_account.signers.local import LocalAccount
from requests import Response
from web3 import Web3

from eth_defi.cctp.constants import CHAIN_ID_TO_CCTP_DOMAIN
from eth_defi.cctp.fees import CCTPFeeQuote
from eth_defi.cctp.testing import craft_cctp_message, forge_attestation, replace_attester_on_fork
from eth_defi.erc_4626.vault_protocol.lagoon.roundtrip import CHAINS, RoundtripContext
from eth_defi.hyperliquid.testing import setup_anvil_hypercore_mocks
from eth_defi.lighter.constants import LIGHTER_API_URL
from eth_defi.lighter.session import LighterSession
from eth_defi.lighter.testing import credit_lighter_pending_balance_on_anvil, register_lighter_account_on_anvil
from eth_defi.provider.anvil import fund_erc20_on_anvil, is_anvil
from eth_defi.token import TokenDetails

logger = logging.getLogger(__name__)


def response(data: dict) -> Response:
    """Build a local JSON response accepted by the real Lighter decoders.

    :param data: Recorded-shaped API response.
    :return: Successful HTTP response without network access.
    """
    result = Response()
    result.status_code = 200
    result._content = json.dumps(data).encode()
    return result


class SimulatedLighter(LighterSession):
    """Deterministic API/signing transport; the real execution library still validates it."""

    def __init__(self, simulation: "RoundtripSimulation") -> None:
        """Bind the local ledger and explicit simulation network boundary.

        :param simulation: Owning tutorial simulation.
        """
        super().__init__()
        self.simulation = simulation
        self.public_key = ""
        self.api_url = LIGHTER_API_URL

    @property
    def ledger(self) -> dict:
        """Return the persisted local ledger; a fresh executor can resume it."""
        return self.simulation.ctx.state.steps.setdefault("simulation_lighter", {"collateral": "0", "shares": 0, "nonce": 0, "receipts": {}, "withdraws": [], "price": "2.5"})

    def credit_deposit(self, amount: Decimal) -> None:
        """Credit collateral only after the corresponding real L1 deposit receipt.

        :param amount: Actual deposited USDC.
        """
        self.ledger["collateral"] = str(Decimal(self.ledger["collateral"]) + amount)
        self.simulation.ctx.save()

    def get(self, url: str, params: dict | None = None, **_kwargs: Any) -> Response:
        """Decode replay-safe reads against the local sequencer ledger.

        :param url: A recognised Lighter endpoint.
        :param params: Original API query.
        :param _kwargs: HTTP compatibility arguments; no request is sent.
        :return: Strict account/pool/receipt/history response.
        """
        params = params or {}
        path = url.rsplit("/api/v1/", 1)[-1]
        ledger = self.ledger
        if path == "accountsByL1Address":
            return response({"code": 200, "sub_accounts": [{"index": self.simulation.account_index}]})
        if path == "apikeys":
            return response({"code": 200, "api_keys": [{"api_key_index": 4, "public_key": self.public_key}] if self.public_key else []})
        if path == "withdrawalDelay":
            return response({"seconds": 1})
        if path == "systemConfig":
            return response({"code": 200, "liquidity_pool_index": 99, "liquidity_pool_cooldown_period": 300000})
        if path == "nextNonce":
            return response({"code": 200, "nonce": ledger["nonce"]})
        if path == "account":
            index = int(params["value"])
            collateral = ledger["collateral"]
            account = {"index": index, "collateral": collateral, "available_balance": collateral, "total_asset_value": collateral, "cross_initial_margin_requirement": "0", "cross_maintenance_margin_requirement": "0", "positions": [], "shares": []}
            if index == self.simulation.pool_index:
                account.update(total_asset_value=str(Decimal(400) * Decimal(ledger["price"])), available_balance="1000", pool_info={"status": 0, "total_shares": 400, "operator_shares": 100, "min_operator_share_rate": "5", "operator_fee": "0"})
            elif ledger["shares"]:
                account["shares"] = [{"public_pool_index": self.simulation.pool_index, "shares_amount": ledger["shares"], "principal_amount": ledger["principal"], "entry_usdc": ledger["principal"], "entry_timestamp": 1700000000000}]
            return response({"code": 200, "accounts": [account]})
        if path == "tx":
            receipt = ledger["receipts"].get(params["value"])
            assert receipt is not None, "Simulation only recognises an originally submitted hash"
            return response(receipt)
        if path == "withdraw/history":
            return response({"code": 200, "withdraws": ledger["withdraws"], "cursor": None})
        raise AssertionError(f"Unexpected simulated API read: {path}")

    def sign(self, kind: str, amount: int, pool: int | None, nonce: int, api_key_index: int) -> tuple[int, str, str, None]:
        """Produce a labelled fake payload, never Lighter cryptography.

        :param kind: Simulated operation type.
        :param amount: Raw integer quantity.
        :param pool: Target pool for share operations.
        :param nonce: Explicit API nonce.
        :param api_key_index: Original key slot.
        :return: Fake signer tuple consumed by real executor validation.
        """
        payload = {"Nonce": nonce, "ApiKeyIndex": api_key_index}
        if kind == "withdraw":
            payload.update(FromAccountIndex=self.simulation.account_index, AssetIndex=3, RouteType=0, Amount=amount)
        else:
            payload.update(AccountIndex=self.simulation.account_index, PublicPoolIndex=pool, ShareAmount=amount)
        tx_info = json.dumps(payload, sort_keys=True)
        tx_type = {"mint": 18, "burn": 19, "withdraw": 12}[kind]
        tx_hash = Web3.to_hex(Web3.keccak(text=str(tx_type) + tx_info))
        return tx_type, tx_info, tx_hash, None

    def sign_mint_shares(self, public_pool_index: int, share_amount: int, *, nonce: int, api_key_index: int) -> tuple:
        """Adapt the fake mint signer to the SDK interface."""
        return self.sign("mint", share_amount, public_pool_index, nonce, api_key_index)

    def sign_burn_shares(self, public_pool_index: int, share_amount: int, *, nonce: int, api_key_index: int) -> tuple:
        """Adapt the fake burn signer to the SDK interface."""
        return self.sign("burn", share_amount, public_pool_index, nonce, api_key_index)

    def sign_withdraw(self, asset_index: int, route_type: int, amount: int, *, nonce: int, api_key_index: int) -> tuple:
        """Adapt the fake secure-withdrawal signer to the SDK interface."""
        assert asset_index == 3 and route_type == 0
        return self.sign("withdraw", amount, None, nonce, api_key_index)

    @staticmethod
    def create_auth_token_with_expiry(*, api_key_index: int) -> tuple[str, None]:
        """Return a local token that is never accepted by any live endpoint."""
        assert api_key_index == 4
        return "simulation-only", None

    def post(self, url: str, data: dict, **kwargs: Any) -> Response:
        """Execute a submitted fake operation exactly once in the local ledger.

        :param url: Must be the recognised submission endpoint.
        :param data: Original executor payload.
        :param kwargs: HTTP compatibility parameters.
        :return: Acceptance; execution is independently fetched through ``tx``.
        """
        assert url.endswith("/api/v1/sendTx") and kwargs.get("allow_redirects") is False
        payload, kind = json.loads(data["tx_info"]), int(data["tx_type"])
        ledger, ctx = self.ledger, self.simulation.ctx
        nonce = payload["Nonce"]
        assert nonce == ledger["nonce"], "Duplicate or colliding simulated API nonce"
        collateral = Decimal(ledger["collateral"])
        price = Decimal(ledger["price"])
        if kind == 18:
            cost = payload["ShareAmount"] * price
            assert collateral >= cost and ledger["shares"] == 0
            ledger.update(shares=payload["ShareAmount"], principal=str(cost), collateral=str(collateral - cost))
        elif kind == 19:
            assert ledger["shares"] == payload["ShareAmount"]
            ledger.update(collateral=str(collateral + ledger["shares"] * price), shares=0)
        else:
            assert kind == 12
            amount = ctx.usdc["ethereum"].convert_to_decimals(payload["Amount"])
            assert collateral >= amount
            ledger["collateral"] = str(collateral - amount)
            # Replace proof settlement only after a confirmed fake withdrawal.
            credit_lighter_pending_balance_on_anvil(ctx.web3["ethereum"], ctx.safe("ethereum").safe_address, payload["Amount"])
            ledger["withdraws"].append({"id": f"simulation-{nonce}", "status": "claimable", "type": "secure", "asset_id": 3, "amount": str(amount)})
        tx_hash = Web3.to_hex(Web3.keccak(text=str(kind) + data["tx_info"]))
        ledger["receipts"][tx_hash] = {"code": 200, "hash": tx_hash, "type": kind, "account_index": self.simulation.account_index, "api_key_index": payload["ApiKeyIndex"], "nonce": nonce, "status": 2, "executed_at": 1700000000}
        ledger["nonce"] += 1
        ctx.save()
        return response({"code": 200, "tx_hash": tx_hash})


@dataclass(slots=True)
class RoundtripSimulation:
    """Local ledgers and attesters for real forked custody contracts."""

    #: Local-only context, attached before any simulated call.
    ctx: RoundtripContext | None = None
    #: Explicit deterministic dummy Core user-vault target.
    hypercore_vault: str = "0x1111111111111111111111111111111111111111"
    #: Deterministic public-pool identity, independent of live pool selection.
    pool_index: int = 42
    #: Assigned local Safe-owned Lighter identity.
    account_index: int = 123456
    #: Destination attesters configured lazily on each local fork.
    attesters: dict[str, LocalAccount] = field(default_factory=dict)
    #: Lighter fake API and signer, attached by setup.
    lighter: SimulatedLighter | None = None

    def attach(self, ctx: RoundtripContext) -> None:
        """Attach local transports without monkeypatching production globals.

        :param ctx: Workflow whose transaction connections are all Anvil.
        """
        assert all(is_anvil(web3) for web3 in ctx.web3.values())
        self.ctx = ctx
        self.lighter = SimulatedLighter(self)
        ctx.lighter_session = self.lighter
        ctx.simulation = self

    @staticmethod
    def fetch_quote(token: TokenDetails, chain_id: int, net: Decimal, ceiling: Decimal) -> CCTPFeeQuote:
        """Use deterministic fees; actual burn and receive remain real contracts.

        :param token: Source native USDC metadata.
        :param chain_id: Source identity.
        :param net: Required net receipt.
        :param ceiling: Gross-included fee cap.
        :return: Integer quote with a non-zero Fast Transfer fee.
        """
        amount = token.convert_to_raw(net + ceiling)
        fee = int((Decimal(amount) * Decimal("1.4") / 10000).to_integral_value(rounding=ROUND_CEILING)) if chain_id != 999 else 0
        maximum = token.convert_to_raw(ceiling)
        assert fee <= maximum, "Simulated fee exceeds the configured ceiling"
        return CCTPFeeQuote(2000 if chain_id == 999 else 1000, amount, fee, maximum)

    def attest(self, ctx: RoundtripContext, row: dict) -> tuple[bytes, bytes]:
        """Forge settlement fields for the saved real source burn, including fees.

        :param ctx: Forked run.
        :param row: Persisted real burn amount and destination.
        :return: Message and local test attestation.
        """
        destination, source = row["destination"], row["source"]
        receipt = ctx.web3[source].eth.get_transaction_receipt(row["burn_hash"])
        assert receipt["status"] == 1
        if destination not in self.attesters:
            self.attesters[destination] = replace_attester_on_fork(ctx.web3[destination])
        message = bytearray(craft_cctp_message(source_domain=CHAIN_ID_TO_CCTP_DOMAIN[CHAINS[source]], destination_domain=CHAIN_ID_TO_CCTP_DOMAIN[CHAINS[destination]], nonce=int(row["burn_hash"], 16), mint_recipient=ctx.safe(destination).safe_address, amount=row["amount"], burn_token=ctx.usdc[source].address, min_finality_threshold=row["finality"]))
        message[280:312] = row["max_fee"].to_bytes(32, "big")
        message[312:344] = row["fee_quote"].to_bytes(32, "big")
        message = bytes(message)
        return message, forge_attestation(message, self.attesters[destination])

    @staticmethod
    def install_core(ctx: RoundtripContext) -> None:
        """Install recording Core contracts only after real Safe/guard deployment.

        :param ctx: Three-chain deployment.
        """
        setup_anvil_hypercore_mocks(ctx.web3["hyperliquid"])
        ctx.state.steps["simulation_core"] = {"spot": "0", "perp": "0", "equity": "0", "escrow": "0", "locked_until": None, "applied": []}
        ctx.save()

    def fetch_core_snapshot(self) -> dict:
        """Read the persisted fake Core state, with no live read fallback."""
        return dict(self.ctx.state.steps.get("simulation_core", {"spot": "0", "perp": "0", "equity": "0", "escrow": "0", "locked_until": None}))

    @staticmethod
    def apply_core(ctx: RoundtripContext, name: str, amount: Decimal) -> None:
        """Apply a recorded action once and explicitly debit/credit Safe cash.

        :param ctx: Local fork and recovery evidence.
        :param name: Confirmed CoreWriter/deposit stage.
        :param amount: Original stage amount.
        """
        ledger = ctx.state.steps["simulation_core"]
        if name in ledger["applied"]:
            return
        token, safe, web3 = ctx.usdc["hyperliquid"], ctx.safe("hyperliquid").safe_address, ctx.web3["hyperliquid"]
        cash = token.fetch_balance_of(safe)
        spot, perp, equity = (Decimal(ledger[k]) for k in ("spot", "perp", "equity"))
        if name in {"core_activation", "core_deposit"}:
            assert cash >= amount
            fund_erc20_on_anvil(web3, token.address, safe, token.convert_to_raw(cash - amount))
            spot += amount - (Decimal(1) if name == "core_activation" else 0)
        elif name == "core_to_perp":
            assert spot >= amount
            spot -= amount
            perp += amount
        elif name == "core_invest":
            assert perp >= amount
            perp -= amount
            equity += amount
        elif name == "core_exit" or name.startswith("core_exit_"):
            assert equity >= amount
            equity -= amount
            perp += amount
        elif name == "core_to_spot":
            assert perp >= amount
            perp -= amount
            spot += amount
        elif name == "core_return":
            assert spot >= amount + Decimal("0.001")
            spot -= amount + Decimal("0.001")
            fund_erc20_on_anvil(web3, token.address, safe, token.convert_to_raw(cash + amount))
        else:
            raise AssertionError(f"Unknown simulated Core action {name}")
        ledger.update(spot=str(spot), perp=str(perp), equity=str(equity))
        ledger["applied"].append(name)
        ctx.save()
        logger.info("SIMULATED Core settlement %s: spot=%s perp=%s equity=%s", name, spot, perp, equity)

    def prepare_lighter(self, ctx: RoundtripContext) -> None:
        """Represent L1 registration before the real guarded deposit/key calls.

        :param ctx: Already deployed Ethereum Safe.
        """
        register_lighter_account_on_anvil(ctx.web3["ethereum"], ctx.safe("ethereum").safe_address, self.account_index)

    def advance_investments(self) -> None:
        """Model a small declared gain and no lock, rather than waiting overnight."""
        ledger = self.ctx.state.steps["simulation_core"]
        ledger["equity"] = str(Decimal(ledger["equity"]) + Decimal("0.1"))
        before_price = Decimal(self.lighter.ledger["price"])
        self.lighter.ledger["price"] = "2.525"
        pool_gain = self.lighter.ledger["shares"] * (Decimal("2.525") - before_price)
        self.ctx.state.results["simulated_profit_usdc"] = str(Decimal("0.1") + pool_gain)
        self.ctx.save()
