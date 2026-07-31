"""D2 Finance vault support."""

import datetime
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property
from typing import Literal

from eth_typing import BlockIdentifier, HexAddress
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest, ERC4626RedemptionRequest
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.token import fetch_erc20_details
from eth_defi.utils import from_unix_timestamp
from eth_defi.vault.base import (
    DEPOSIT_CLOSED_FUNDING_PHASE,
    REDEMPTION_CLOSED_FUNDS_CUSTODIED,
    VaultHistoricalRead,
    VaultHistoricalReader,
)
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

logger = logging.getLogger(__name__)


class D2DepositManager(ERC4626DepositManager):
    """D2 Finance epoch-gated ERC-4626 lifecycle with zero-price admission failure.

    D2 vaults are managed derivative-strategy vaults that cycle through
    offchain-scheduled *funding*, *trading* (epoch) and *withdrawal* phases.
    Onchain deposits and redemptions use the standard synchronous ERC-4626
    entry points, but they only succeed inside the correct phase. This manager
    keeps the plain ERC-4626 transaction construction of its parent while adding
    preflight phase gating and an explicit failure when D2 pricing is
    unavailable, so a caller sees an actionable :class:`VaultFlowUnavailable`
    instead of paying gas for a guaranteed revert or trusting a zero estimate.

    **Deposit process**

    Synchronous. The owner ``approve()``s the denomination token and
    :meth:`create_deposit_request` builds a single ERC-4626 ``deposit`` call
    (inherited construction), but only after :meth:`_assert_flow_open` confirms
    the vault is in its funding phase (``isFunding()``); otherwise it raises
    :class:`VaultFlowUnavailable` carrying the next funding open time.
    :meth:`estimate_deposit` overrides the parent: it first rejects a closed
    funding phase, then calls the standard ``previewDeposit``-based estimator,
    and finally raises :class:`ValueError` when the estimate is zero, because a
    zero share price means D2 pricing is undefined rather than that the deposit
    is free.

    **Redemption process**

    Synchronous. :meth:`create_redemption_request` builds a single ERC-4626
    ``redeem`` call (inherited construction) after :meth:`_assert_flow_open`
    confirms redemptions are open — D2 permits withdrawal only when funds are
    not custodied and no epoch is running (``notCustodiedAndNotDuringEpoch()``).
    A closed window raises :class:`VaultFlowUnavailable` with the next
    redemption open time.

    **Queues and settlement**

    No per-owner request queue: each ``deposit`` / ``redeem`` settles in its own
    transaction. The only gating is the vault-wide epoch phase, evaluated live
    from ``isFunding()`` and ``notCustodiedAndNotDuringEpoch()``. Custodied
    epochs, operator NAV changes and delayed withdrawals are outside this
    adapter and are not modelled as tickets.

    **Lockups and cooldowns**

    No per-owner cooldown, but capital is effectively locked for the trading
    epoch: :meth:`D2Vault.get_estimated_lock_up` reports the current epoch
    duration (``epoch_end - epoch_start``), which D2 documents as roughly
    30-60 days. Deposits made during funding are custodied through the following
    trading epoch and can only be redeemed once the vault returns to a
    not-custodied, not-in-epoch state.

    **Deposit eligibility**

    The historical ``onlyWhitelisted`` modifier is not an identity/KYC gate:
    it also admits any account holding more than the public minimum balance of
    ``whitelistAsset``. The lifecycle experiment funds that eligibility asset
    where necessary and reports a failed balance condition as flow
    availability, never as ``whitelisting-needed``.

    **Anvil settlement (force_settle)**

    The standard ``deposit`` and ``redeem`` calls complete in their originating
    transaction, so the inherited :meth:`force_settle` accepts ``None`` and
    performs the Anvil-validated shared no-op. This adapter improves preflight
    estimation and phase gating only; a successful D2 transaction path has not
    been fork-proven.
    """

    def estimate_deposit(
        self,
        owner: HexAddress | None,
        amount: Decimal,
        block_identifier: BlockIdentifier = "latest",
    ) -> Decimal:
        """Return an estimate or an actionable zero-price failure.

        :param owner:
            Deposit owner passed to the standard ERC-4626 estimator.
        :param amount:
            Decimal denomination amount.
        :param block_identifier:
            Block number or ``"latest"``.
        :return:
            Estimated decimal shares.
        :raise ValueError:
            If D2 pricing is undefined or the funding phase is closed.
        """
        closed_reason = self.vault.fetch_deposit_closed_reason()
        if closed_reason is not None:
            raise VaultFlowUnavailable(
                closed_reason,
                protocol=D2_PROTOCOL_NAME,
                vault_address=self.vault.address,
                caller=owner,
                direction="deposit",
                phase="preflight",
                preflight_result="deposit_closed",
                next_open=self.vault.fetch_deposit_next_open(),
            )
        estimate = super().estimate_deposit(owner, amount, block_identifier)
        if estimate <= 0:
            reason = f"D2 deposit estimate is zero for {amount} {self.vault.denomination_token.symbol}; pricing is unavailable"
            raise ValueError(reason)
        return estimate

    def _assert_flow_open(self, owner: HexAddress, direction: Literal["deposit", "redeem"]) -> None:
        """Reject a known closed D2 epoch before constructing a transaction."""
        if direction == "deposit":
            reason = self.vault.fetch_deposit_closed_reason()
            next_open = self.vault.fetch_deposit_next_open()
        else:
            reason = self.vault.fetch_redemption_closed_reason()
            next_open = self.vault.fetch_redemption_next_open()
        if reason is not None:
            preflight_result = "deposit_closed" if direction == "deposit" else "redemption_window_closed"
            raise VaultFlowUnavailable(
                reason,
                protocol=D2_PROTOCOL_NAME,
                vault_address=self.vault.address,
                caller=owner,
                direction=direction,
                phase="preflight",
                preflight_result=preflight_result,
                next_open=next_open,
            )

    def _assert_account_eligible(self, owner: HexAddress, direction: Literal["deposit", "redeem"]) -> None:
        """Enforce D2's account eligibility without conflating it with KYC.

        Mapping-only deployments use the shared whitelist preflight. Public
        deployments accept either mapping membership or an asset balance above
        the configured threshold and report an unmet threshold as a typed
        minimum condition.

        :param owner:
            Deposit controller or redemption owner to inspect.
        :param direction:
            Flow direction used in structured failure evidence.
        :raise VaultFlowUnavailable:
            If a public deployment's balance threshold is not met.
        :raise WhitelistingRequired:
            If a mapping-only deployment does not admit the account.
        """
        if self.vault.is_whitelisted_deposit():
            # A mapping-only D2 deployment is a genuine account allow-list.
            self.check_deposit_whitelist(owner)
            return

        if self.vault.is_account_eligible(owner):
            return

        whitelist_asset = self.vault.vault_contract.functions.whitelistAsset().call()
        whitelist_balance = self.vault.vault_contract.functions.whitelistBalance().call()
        eligibility_token = fetch_erc20_details(
            self.web3,
            whitelist_asset,
            chain_id=self.vault.chain_id,
            cause_diagnostics_message=f"D2 vault {self.vault.address} eligibility check",
        )
        available_balance = eligibility_token.fetch_raw_balance_of(owner)
        minimum_balance = whitelist_balance + 1
        raise VaultFlowUnavailable(
            f"D2 account {owner} does not meet the public {eligibility_token.symbol} balance eligibility minimum",
            protocol=D2_PROTOCOL_NAME,
            vault_address=self.vault.address,
            caller=owner,
            asset_address=eligibility_token.address,
            direction=direction,
            phase="preflight",
            decoded_error="InsufficientEligibilityBalance",
            preflight_result="below_minimum",
            available_raw_amount=available_balance,
            minimum_raw_amount=minimum_balance,
        )

    def create_deposit_request(  # noqa: PLR0917
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        amount: Decimal | None = None,
        raw_amount: int | None = None,
        check_max_deposit: bool = True,  # noqa: FBT001, FBT002
        check_enough_token: bool = True,  # noqa: FBT001, FBT002
    ) -> ERC4626DepositRequest:
        """Build a deposit only during D2's funding phase.

        Arguments match the inherited ERC-4626 request interface so existing
        positional callers remain compatible.

        :return:
            Standard synchronous deposit request.
        :raise VaultFlowUnavailable:
            If the current D2 epoch is not accepting deposits.
        """
        self._assert_flow_open(owner, "deposit")
        self._assert_account_eligible(owner, "deposit")
        return super().create_deposit_request(owner, to, amount, raw_amount, check_max_deposit, check_enough_token)

    def create_deposit_request_for_guard_validation(
        self,
        owner: HexAddress,
        raw_amount: int,
    ) -> ERC4626DepositRequest:
        """Build D2 deposit calldata while its funding epoch is closed.

        This narrow diagnostic exception bypasses D2's temporary funding window
        and ordinary amount/balance checks so a caller can validate the exact
        manager-generated deposit call through GuardV0. It intentionally does
        not construct or validate an ERC-20 approval, nor prove an
        approval-before-deposit sequence: those checks add no evidence to a
        standalone ``validateCall()`` policy check and remain normal live
        simulation responsibilities.

        :param owner:
            SimpleVaultV0/Safe address that would submit the D2 deposit.
        :param raw_amount:
            Raw D2 denomination-token amount from the rejected preflight.
        :return:
            One standard ERC-4626 deposit call for isolated GuardV0 validation.
        """
        self._assert_anvil_guard_validation()
        self._assert_account_eligible(owner, "deposit")
        return ERC4626DepositManager.create_deposit_request(
            self,
            owner=owner,
            to=owner,
            raw_amount=raw_amount,
            check_max_deposit=False,
            check_enough_token=False,
        )

    def create_redemption_request(  # noqa: PLR0917
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        shares: Decimal | None = None,
        raw_shares: int | None = None,
        check_max_deposit: bool = True,  # noqa: FBT001, FBT002
        check_enough_token: bool = True,  # noqa: FBT001, FBT002
    ) -> ERC4626RedemptionRequest:
        """Build a redemption only during D2's withdrawal window.

        Arguments match the inherited ERC-4626 request interface so existing
        positional callers remain compatible.

        :return:
            Standard synchronous redemption request.
        :raise VaultFlowUnavailable:
            If the current D2 epoch is not accepting redemptions.
        """
        self._assert_flow_open(owner, "redeem")
        self._assert_account_eligible(owner, "redeem")
        return super().create_redemption_request(owner, to, shares, raw_shares, check_max_deposit, check_enough_token)


D2_PROTOCOL_NAME = "D2 Finance"
D2_STRATEGY_URL_TEMPLATE = "https://d2.finance/strategies/{address}"
D2_TEXAS_HEDGE_ADDRESS = "0x208f63a7f60c319597c05fa5ec67fde41839bad6"
D2_TEXAS_HEDGE_BLOG_URL = "https://medium.com/@D2.Finance/texas-hedge-epoch-2-elon-takes-us-to-space-x-fec66a354ad3"

#: Optional D2-authored strategy articles from the D2 vault API.
D2_VAULT_ARTICLE_URLS: dict[str, str] = {
    "0x195eb4d088f222c982282b5dd495e76dba4bc7d1": "https://medium.com/@D2.Finance/hype-strategy-faq-654bc8d0efc9",
    D2_TEXAS_HEDGE_ADDRESS: D2_TEXAS_HEDGE_BLOG_URL,
    "0x2406aacbdf8463176deb285adaa81768415b6c7e": "https://medium.com/@D2.Finance/hype-base-institutional-grade-defi-bdce160e66b6",
    "0x27d22eb71f00495eccc89bb02c2b68e6988c6a42": "https://medium.com/@D2.Finance/d2-finance-eth-market-launching-on-camelot-5e420cff0181",
    "0x3ebb11ba6a5b61c04d1a703ea10728d519945440": "https://medium.com/@D2.Finance/d2hype-turn-hype-volatility-into-20-apr-yield-b570d72a4ec0",
    "0x4de611ed46aaf54a6850f97542ce7a58e7c8d0fe": "https://medium.com/@D2.Finance/introducing-hkraken-pioneering-tokenized-pre-ipo-options-6292f2f3d03c",
    "0x7410e69958a8ece2a51c231c8528513d4d668c7a": "https://medium.com/@D2.Finance/introducing-usdt0-hrwa-the-ultimate-stablecoin-yield-engine-on-hyperevm-f7e8b58068db",
    "0x75288264fdfea8ce68e6d852696ab1ce2f3e5004": "https://medium.com/@D2.Finance/hype-strategy-faq-654bc8d0efc9",
    "0x8ef30c5ce9a460bfae82f1f039f7c5e5427d7018": "https://medium.com/@D2.Finance/introducing-hsol-20-apr-target-on-sol-with-hyperliquid-5b10672f6369",
    "0xace42f7e3f4672607897bf1951468031f0214359": "https://medium.com/@D2.Finance/hedged-bera-hbera-betting-on-berachains-community-hedging-against-ghost-towns-72d6332e95e2",
    "0xc4fee8c68293a63241b64e5a2ef07fcf89005dd3": "https://medium.com/@D2.Finance/4f10c3fbf0a3",
    "0xcd18006cc69c6d5fa4fd4eaf99910b58464fa3ae": "https://medium.com/@D2.Finance/infrared-origami-d2-2xbera-df7e7129df9a",
    "0xf44f49e6577b3934f981c6f0629d15154d2606e6": "https://medium.com/@D2.Finance/introducing-hyperliquid-xxi-hxxi-the-future-of-bitcoin-accumulation-on-chain-499c488e67ca",
    "0xf650ba4303ce164e1f6b215d4cbb5e212d307056": "https://medium.com/@D2.Finance/hypereuler-x-pol-pol-is-here-is-time-to-supercharge-your-bera-4dfaa8805d23",
}

#: D2 vault addresses checked from the D2 Finance API and the Trading Strategy
#: public vault export on 2026-07-09.
D2_VAULT_ADDRESSES: set[str] = {
    "0x0178b56fea3d7b5b9f9e0cdad486522de948730f",
    "0x07dff4087b43c4a759f4fc69511c26d51929daf4",
    "0x09dfcf4731149582d163a763e7dd553ebc18852d",
    "0x0f00008223b4d4c2c466b4456df1d483743cbd60",
    "0x0f76de33a3679a6065d14780618b54584a3907d4",
    "0x1176c3760af6a1dbaa5bbd0cc6cda8a2ed6b785e",
    "0x140e81f8c033c0705036e55be2f2fbce43e4595c",
    "0x17fd8c3d1e0379cf6b1dace21750e624eb9573c2",
    "0x183424d5ae5ec9fd486634bc566d0f75ad9c9109",
    "0x195a9e0f29f96d4ab2139ee1272380a4aa352890",
    "0x195eb4d088f222c982282b5dd495e76dba4bc7d1",
    "0x1f1fc659e69318a5f3aab5d69aaad9c9a6245c9a",
    D2_TEXAS_HEDGE_ADDRESS,
    "0x21219173ff017cd333364612764fb2579fd9b0c1",
    "0x23f556f4df7a263ce68ee29659aa4ea632a6a5bc",
    "0x2406aacbdf8463176deb285adaa81768415b6c7e",
    "0x26eca524d0eb6ce02603850424c56bc42f53e54b",
    "0x27d22eb71f00495eccc89bb02c2b68e6988c6a42",
    "0x291344fbaac4fe14632061e4c336fe3b94c52320",
    "0x2b8d0420996a2753ef21c25c94eae9fc0c0aed1e",
    "0x34f0fdd80a51dfd8ba42343c20f89217280d760e",
    "0x36b1939adf539a4ac94b57dbad32faecd5bcf4d0",
    "0x36b933554782b108bb9962ac00c498acbceb706d",
    "0x3d493db0d3d616937a930b29c57b3f654f9b41d9",
    "0x3ebb11ba6a5b61c04d1a703ea10728d519945440",
    "0x4ada76cc8755f62508a2df65d7fafa4fd26e76c6",
    "0x4d823951a8b3a614667e9cabf6948d7d0e73911d",
    "0x4de611ed46aaf54a6850f97542ce7a58e7c8d0fe",
    "0x575224c6b1fa1d26977cd651974b6d7694f30d52",
    "0x57f467c9c4639b066f5a4d676cd8ed7d87c1791b",
    "0x5b49d7fae00de64779ddcd6b067c8eb046bd9a0b",
    "0x64167cd42859f64cff2aa4b63c3175ccef9659dd",
    "0x6a4d2462c5f9cf21f05e441911d88d38754a1137",
    "0x6a798ae978a51f976ec667bdd94a9dcf81d940ed",
    "0x6bf9345b5d6b27b5cbf2e463dc5e0b2afcedc21c",
    "0x6c05a7d2c24b48fc3c615d294fec2eb068548897",
    "0x7348925d3c63e4e61e9f5308eeec0f06eaa3bb7b",
    "0x7410e69958a8ece2a51c231c8528513d4d668c7a",
    "0x75288264fdfea8ce68e6d852696ab1ce2f3e5004",
    "0x7da637df83778704464208f092dbdbf9f386df96",
    "0x7efe92efb73e95fe6481e9bf81bbbe8c03fe2d61",
    "0x80c403807b1032d7cb19b6d612ce23f05a213d36",
    "0x864f2414a99f56e28fcc4deac520169f6e81f4ae",
    "0x8b8773f88816387f8aba5bef5cfc2d5e329600cb",
    "0x8ef30c5ce9a460bfae82f1f039f7c5e5427d7018",
    "0x907a9f69061736ad82811cccd6add9dc4a2352a9",
    "0x90a23c5cf9dd0897ced19de6a77a856c228b57c3",
    "0x91acd32da9bea6da3751dc12ee0fbe47169349c1",
    "0x999a57ae7694298126a5db2e44f778ca486b14fc",
    "0x9aa9bd7b032e527b0820c85cee89093dc33df8bd",
    "0xa0820f0934e47d6c191450f47ec6430483d1394e",
    "0xa6c99234ebcda225ef32cefb8472363e09a51aea",
    "0xab2743a3a2e06d457368e901f5f927f271fa1374",
    "0xac75f0c46723432a2303f2a7c7769535a179ed56",
    "0xace42f7e3f4672607897bf1951468031f0214359",
    "0xb0730aa7d6e880f901b5d71a971096db56895a0f",
    "0xbe75c8a7e58c7901d2e128dc8d3b6de2481f1f79",
    "0xbf075980792f8cc89dfb74b553acf6750a7e941b",
    "0xc027ec28f76d92d4124fcbffcf6b25137a84968c",
    "0xc4fee8c68293a63241b64e5a2ef07fcf89005dd3",
    "0xc51971f0676c58c1156e3e08e5e66367f2aa3d1e",
    "0xc5baffd6d9b3755ea680e6c630c44a120154dc58",
    "0xc9ebd0975e7d207c2f8ca2c82007cbbafb262c8c",
    "0xcd18006cc69c6d5fa4fd4eaf99910b58464fa3ae",
    "0xd0db54d54e227584563226206e0f74a7e4ef54af",
    "0xd1d64daeed7504ef3eb056aa2d973bd064843a84",
    "0xeb3194d69201b04729f1a618d95519cdae24d6c0",
    "0xee8bbccaa590a4c087d9d2e48b92f60813ed2b43",
    "0xeee6e115cb08ac280fc55642a61d8adfba85dfed",
    "0xf44f49e6577b3934f981c6f0629d15154d2606e6",
    "0xf650ba4303ce164e1f6b215d4cbb5e212d307056",
    "0xfddd73ecd0d0d75e902a567811a70e167a262fab",
}

#: D2 Finance strategy page for every known D2 vault.
#:
#: Keep native vault links on D2's own website. Trading Strategy page links
#: are generated separately by the vault metrics reporting layer.
D2_VAULT_LINK_MATRIX: dict[str, str] = {
    **{address: D2_STRATEGY_URL_TEMPLATE.format(address=address) for address in D2_VAULT_ADDRESSES},
}


def format_d2_strategy_url(address: HexAddress | str) -> str:
    """Format a D2 Finance strategy page URL.

    Each D2 vault has an authoritative strategy page under the D2 website.
    This helper keeps vault note links consistent across D2 notes.

    :param address:
        Vault address.

    :return:
        D2 Finance strategy page URL.
    """

    return D2_STRATEGY_URL_TEMPLATE.format(address=str(address).lower())


def format_d2_vault_note(address: HexAddress | str) -> str:
    """Format a generic D2 Finance vault note.

    D2 vaults are managed strategy vaults with proprietary off-chain trading
    logic. The authoritative source for the per-vault setup is the D2 strategy
    page, so link to it for every D2 vault.

    :param address:
        Vault address.

    :return:
        Markdown vault note.
    """

    address = str(address).lower()
    strategy_url = format_d2_strategy_url(address)
    article_url = D2_VAULT_ARTICLE_URLS.get(address)
    article_note = f"\n\nD2 also describes this strategy in its [D2 strategy article]({article_url})." if article_url else ""

    return f"""D2 Finance strategy vault.

**Summary:** This vault is a D2 Finance managed strategy vault. D2 strategies are tokenised derivative strategies executed on-chain with proprietary off-chain trading logic and epoch-based funding, trading and withdrawal phases.

The authoritative D2 source for this vault is the [D2 strategy page]({strategy_url}).{article_note}
"""


def format_d2_texas_hedge_note() -> str:
    """Format the D2 texasHedge vault note.

    The texasHedge vault has a D2-authored strategy article with specific
    tactical and risk context that is more useful than the generic D2 note.

    :return:
        Markdown vault note.
    """

    return f"""D2 Finance texasHedge (TXHEDGE) is a HyperEVM USDC tactical strategy vault.

**Summary:** The vault is a high-risk, multi-leg directional strategy around SpaceX pre-IPO exposure and related market flows. It combines a financed SpaceX perpetual position with long S&P downside convexity and long Tesla/Mag-7 upside convexity, funded by selling DRAM upside convexity. D2 describes the strategy in its [Texas Hedge Epoch 2 blog post]({D2_TEXAS_HEDGE_BLOG_URL}).

The authoritative D2 source for this vault is the [D2 strategy page]({format_d2_strategy_url(D2_TEXAS_HEDGE_ADDRESS)}).

This is not a market-neutral yield product; D2 categorises it as a degen/tactical strategy where investors can lose the full vault stake.
"""


@dataclass(slots=True, frozen=True)
class Epoch:
    funding_start: datetime.datetime
    epoch_start: datetime.datetime
    epoch_end: datetime.datetime


class D2HistoricalReader(ERC4626HistoricalReader):
    """Read D2 Finance vault core data + epoch-based deposit/redemption/trading state.

    - Deposits are open during the funding phase (``isFunding()``)
    - Redemptions are open when funds are not custodied and not during epoch
      (``notCustodiedAndNotDuringEpoch()``)
    - Trading is active when the vault is in an epoch (``isInEpoch()``)
    """

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_d2_epoch_calls()

    def construct_d2_epoch_calls(self) -> Iterable[EncodedCall]:
        """Add D2-specific epoch state calls."""
        is_funding = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.isFunding(),
            extra_data={
                "function": "isFunding",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield is_funding

        is_in_epoch = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.isInEpoch(),
            extra_data={
                "function": "isInEpoch",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield is_in_epoch

        not_custodied_and_not_during_epoch = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.notCustodiedAndNotDuringEpoch(),
            extra_data={
                "function": "notCustodiedAndNotDuringEpoch",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield not_custodied_and_not_during_epoch

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # Decode common variables
        share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)

        # Decode D2-specific epoch state
        deposits_open = None
        is_funding_result = call_by_name.get("isFunding")
        if is_funding_result and is_funding_result.success:
            deposits_open = bool(convert_int256_bytes_to_int(is_funding_result.result))

        trading = None
        is_in_epoch_result = call_by_name.get("isInEpoch")
        if is_in_epoch_result and is_in_epoch_result.success:
            trading = bool(convert_int256_bytes_to_int(is_in_epoch_result.result))

        redemption_open = None
        not_custodied_result = call_by_name.get("notCustodiedAndNotDuringEpoch")
        if not_custodied_result and not_custodied_result.success:
            redemption_open = bool(convert_int256_bytes_to_int(not_custodied_result.result))

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
            deposits_open=deposits_open,
            redemption_open=redemption_open,
            trading=trading,
        )


class D2Vault(ERC4626Vault):
    """D2 Finance vaults.

    - Most vault logic is offchain, proprietary
    - VaultV1Whitelisted is a wrapper around a Hyperliquid trading account
    - Deposits have asset-holding eligibility conditions but no KYC requirement
    - The vault smart contract does not have visibility to the fees
    - Redemption must happen not during epoch
    - Fees are set and calculated offchain
    - The vaults have funding, trading and withdraw phases and you can only deposit/withdraw on the correct epoch
    - Lockups are up to 30-60 days or so
    - The vault owner can set epochs offhain, up to 10 years

    More information:

    - `Docs <https://gitbook.d2.finance/>`__
    - `HYPE++ strategy blog post <https://medium.com/@D2.Finance/hype-capitalizing-on-hyperliquids-launch-396f8665a2c0>`__

    Despite the contract's historical ``whitelisted`` naming, its balance and
    schedule conditions do not represent a KYC or manual identity gate. They
    must not change the public ``deposit_permission`` status.
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment.

        - Example impl https://arbiscan.io/address/0x350856A672e7bF7D7327c8a5e72Ac49833DBfB75#code
        """
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="d2/VaultV1Whitelisted.json",
        )

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return D2HistoricalReader(self, stateful)

    def get_deposit_manager(self) -> D2DepositManager:
        """Create the D2 phase-aware lifecycle manager.

        :return:
            D2 manager that avoids returning a zero share estimate.
        """
        return D2DepositManager(self)

    def is_whitelisted_deposit(self) -> bool:
        """Report whether D2 uses a mapping-only identity gate.

        The historical ``onlyWhitelisted`` name is misleading for public
        status purposes: any account can satisfy the rule by holding the
        configured public asset minimum. It is an economic eligibility
        condition, not KYC or manual identity approval. A zero whitelist-asset
        address removes that public route and leaves only the explicit mapping,
        which is a genuine account allow-list.

        :return:
            ``False`` when public asset-balance admission is configured;
            otherwise ``True`` for a mapping-only deployment.
        """
        whitelist_asset = self.vault_contract.functions.whitelistAsset().call()
        return whitelist_asset.lower() == ZERO_ADDRESS_STR

    def is_account_eligible(self, address: HexAddress) -> bool:
        """Evaluate D2's legacy mapping-or-asset-balance eligibility rule.

        Both ``deposit()`` and ``redeem()`` execute ``onlyWhitelisted``. The
        balance branch is deliberately read at request construction time,
        rather than cached, because a deposit can consume the balance that
        granted admission.

        :param address:
            Account whose D2 economic eligibility is queried.
        :return:
            ``True`` when the explicit mapping or configured asset balance
            currently admits the account.
        """
        vault_contract = self.vault_contract
        if vault_contract.functions.whitelisted(address).call():
            return True

        whitelist_asset = vault_contract.functions.whitelistAsset().call()
        if whitelist_asset.lower() == ZERO_ADDRESS_STR:
            return False

        whitelist_balance = vault_contract.functions.whitelistBalance().call()
        whitelist_token = fetch_erc20_details(
            self.web3,
            whitelist_asset,
            chain_id=self.chain_id,
            cause_diagnostics_message=f"D2 vault {self.address} whitelist admission check",
        )
        return whitelist_token.fetch_raw_balance_of(address) > whitelist_balance

    def is_account_whitelisted(self, address: HexAddress) -> bool:
        """Read D2 mapping membership when no public balance route exists.

        Public asset-balance eligibility is deliberately exposed through
        :meth:`is_account_eligible` instead of being labelled as whitelist
        membership.

        :param address:
            Account whose explicit D2 mapping membership is inspected.
        :return:
            Whether the account is present in the explicit mapping.
        :raise NotImplementedError:
            If the deployment has a public asset-balance eligibility route.
        """
        if not self.is_whitelisted_deposit():
            raise NotImplementedError("Public D2 asset-balance eligibility is not KYC membership")
        return bool(self.vault_contract.functions.whitelisted(address).call())

    def get_link(self, referral: str | None = None) -> str:  # noqa: ARG002
        """Get the canonical public page for this D2 vault.

        The matrix keeps every native vault link on D2 Finance's strategy
        pages. Trading Strategy links are exported separately by the vault
        metrics reporting layer.

        :param referral:
            Unused because neither supported D2 destination accepts a referral
            parameter.

        :return:
            D2 Finance strategy page URL.
        """
        address = self.address.lower()
        return D2_VAULT_LINK_MATRIX.get(address, format_d2_strategy_url(address))

    def get_notes(self) -> str | None:
        """Get D2-specific vault notes.

        D2 vault notes are owned by the D2 protocol adapter, so scanner output
        can persist the note in the vault metadata row. Manual shared notes and
        flags from the base implementation keep priority.

        :return:
            Markdown note for Trading Strategy vault exports.
        """

        manual_notes = super().get_notes()
        if manual_notes:
            return manual_notes

        address = self.address.lower()
        if address == D2_TEXAS_HEDGE_ADDRESS:
            return format_d2_texas_hedge_note()

        return format_d2_vault_note(address)

    def fetch_current_epoch_id(self) -> int:
        return self.vault_contract.functions.getCurrentEpoch().call()

    def fetch_current_epoch_info(self) -> Epoch:
        data = self.vault_contract.functions.getCurrentEpochInfo().call()
        return Epoch(
            funding_start=from_unix_timestamp(data[0]),
            epoch_start=from_unix_timestamp(data[1]),
            epoch_end=from_unix_timestamp(data[2]),
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Return the separately reported management fee.

        - D2 share price is fees-inclusive per them: https://x.com/D2_Finance/status/1988624499588116979
        """
        del block_identifier
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Return the performance fee internalised in the share price.

        - D2 share price is fees-inclusive per them: https://x.com/D2_Finance/status/1988624499588116979
        """
        del block_identifier
        return 0.20

    def get_estimated_lock_up(self) -> datetime.timedelta:
        epoch = self.fetch_current_epoch_info()
        return epoch.epoch_end - epoch.epoch_start

    def fetch_observation_time(self) -> datetime.datetime:
        """Read the EVM timestamp used by the current vault observation.

        Delay calculations use the same block context as the vault state
        instead of the wall clock, which keeps historical and forked reads
        deterministic.

        :return:
            Naive UTC timestamp of the adapter's configured block.
        """
        block = self.web3.eth.get_block(self._get_block_identifier())
        return from_unix_timestamp(block["timestamp"])

    def fetch_deposit_closed_reason(self) -> str | None:
        """Deposits open during isFunding() phase."""
        try:
            is_funding = self.vault_contract.functions.isFunding().call()
            if not is_funding:
                next_open = self.fetch_deposit_next_open()
                if next_open:
                    remaining = next_open - self.fetch_observation_time()
                    hours = remaining.total_seconds() / 3600
                    if hours < 24:
                        return f"{DEPOSIT_CLOSED_FUNDING_PHASE} (opens in {hours:.0f}h)"
                    return f"{DEPOSIT_CLOSED_FUNDING_PHASE} (opens in {hours / 24:.1f}d)"
                return DEPOSIT_CLOSED_FUNDING_PHASE
        except (BadFunctionCallOutput, ContractLogicError, ValueError) as error:
            logger.debug("Could not read D2 deposit phase for %s: %s", self.address, error)
        return None

    def fetch_redemption_closed_reason(self) -> str | None:
        """Redemptions open when notCustodiedAndNotDuringEpoch()."""
        try:
            can_redeem = self.vault_contract.functions.notCustodiedAndNotDuringEpoch().call()
            if not can_redeem:
                next_open = self.fetch_redemption_next_open()
                if next_open:
                    remaining = next_open - self.fetch_observation_time()
                    hours = remaining.total_seconds() / 3600
                    if hours < 24:
                        return f"{REDEMPTION_CLOSED_FUNDS_CUSTODIED} (opens in {hours:.0f}h)"
                    return f"{REDEMPTION_CLOSED_FUNDS_CUSTODIED} (opens in {hours / 24:.1f}d)"
                return REDEMPTION_CLOSED_FUNDS_CUSTODIED
        except (BadFunctionCallOutput, ContractLogicError, ValueError) as error:
            logger.debug("Could not read D2 redemption phase for %s: %s", self.address, error)
        return None

    def fetch_deposit_next_open(self) -> datetime.datetime | None:
        """Get when deposits will next be open.

        - Deposits open at the start of the next funding phase (after epoch ends)
        """
        try:
            if self.vault_contract.functions.isFunding().call():
                return None  # Already open
            epoch = self.fetch_current_epoch_info()
            observation_time = self.fetch_observation_time()
            return epoch.epoch_end if epoch.epoch_end > observation_time else None
        except (BadFunctionCallOutput, ContractLogicError, ValueError) as error:
            logger.debug("Could not read D2 next deposit opening for %s: %s", self.address, error)
            return None

    def fetch_redemption_next_open(self) -> datetime.datetime | None:
        """Get when withdrawals will next be open.

        - Redemptions open when funds are not custodied and not during epoch
        """
        try:
            if self.vault_contract.functions.notCustodiedAndNotDuringEpoch().call():
                return None  # Already open
            epoch = self.fetch_current_epoch_info()
            observation_time = self.fetch_observation_time()
            return epoch.epoch_end if epoch.epoch_end > observation_time else None
        except (BadFunctionCallOutput, ContractLogicError, ValueError) as error:
            logger.debug("Could not read D2 next redemption opening for %s: %s", self.address, error)
            return None
