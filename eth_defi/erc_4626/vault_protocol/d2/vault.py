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
    WithdrawalDelayType,
    WithdrawalPeriod,
    normalise_vault_description,
)
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable, WhitelistingRequired

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
        vault_contract = self.vault.vault_contract
        if vault_contract.functions.whitelisted(owner).call():
            return

        whitelist_asset = vault_contract.functions.whitelistAsset().call()
        if whitelist_asset.lower() == ZERO_ADDRESS_STR:
            raise WhitelistingRequired(
                f"Depositor {owner} is not whitelisted for D2 vault {self.vault.address} on chain {self.vault.chain_id}",
                protocol=D2_PROTOCOL_NAME,
                vault_address=self.vault.address,
                caller=owner,
                direction=direction,
                phase="preflight",
            )

        whitelist_balance = vault_contract.functions.whitelistBalance().call()
        eligibility_token = fetch_erc20_details(
            self.web3,
            whitelist_asset,
            chain_id=self.vault.chain_id,
            cause_diagnostics_message=f"D2 vault {self.vault.address} eligibility check",
        )
        available_balance = eligibility_token.fetch_raw_balance_of(owner)
        if available_balance > whitelist_balance:
            return

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
        and denomination-token amount/balance checks so a caller can validate
        the exact manager-generated deposit call through GuardV0. D2's separate
        mapping-or-eligibility-asset admission check still applies. It intentionally does
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
        :raise VaultFlowUnavailable:
            If the owner does not meet a public eligibility-asset minimum.
        :raise WhitelistingRequired:
            If a mapping-only deployment does not admit the owner.
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

#: GlobalMacro on Robinhood Chain, reviewed on 2026-11-05.
#: https://tradingstrategy.ai/vaults/globalmacro
#: https://robinhoodchain.blockscout.com/address/0x3a87f340c7aa52694c3dd806ba13d1b2ebf48dd8
D2_GLOBAL_MACRO_CHAIN_ID = 4663
D2_GLOBAL_MACRO_ADDRESS = HexAddress("0x3a87f340c7aa52694c3dd806ba13d1b2ebf48dd8")
D2_GLOBAL_MACRO_BLOG_URL = "https://medium.com/@D2.Finance/globalmacro-gm-vault-on-robinhood-take-from-the-markets-that-sleep-give-to-the-one-that-never-a66b2c62f300"
#: Official post; text checked through the mirror below because X refused access.
#: https://instalker.org/D2_Finance/status/2117815338616152350
D2_GLOBAL_MACRO_UPDATE_URL = "https://x.com/D2_Finance/status/2117815338616152350"

#: Maintained Markdown for this deployment only; historical claims are dated.
D2_GLOBAL_MACRO_DESCRIPTION = f"""#### Vault's trading strategy

The vault strategy uses algorithmic trend signals to trade equity indices, commodities and currencies. Returns come from trading profits and lending interest.

The [published strategy design]({D2_GLOBAL_MACRO_BLOG_URL}) uses several simple moving-average rules, which smooth past prices, to trigger trades. D2's [investment team](https://docs.d2.finance/docs/vault-design) runs the strategy. Signal calculation frequency and the extent of human approval or overrides are not disclosed.

The design favours options, rights to buy or sell at agreed prices, when they are cheap relative to the expected market move, and futures otherwise. [Hyperliquid](https://hyperliquid.xyz/) provides round-the-clock trading in [perpetual futures](https://tradingstrategy.ai/glossary/perpetual-future), contracts without expiry. **The options venue is not identified in the published design.** The lending allocation is a Steakhouse-managed vault on [Morpho](https://morpho.org/).

#### Risk and withdrawals

Positions can exceed the money backing them, magnifying gains and losses. The manager's **10% loss-limit policy per trading cycle** is not a guarantee: losses can exceed it, and it does not cap losses accumulated across cycles. Lending interest may offset only part of a trading loss, and the lending vault adds borrower and smart contract risks.

**Deposits and withdrawals close during trading.** The trader may take custody of funds, which must be returned before withdrawals reopen. An announced trading-cycle end date does not guarantee withdrawal availability.

The [future roadmap]({D2_GLOBAL_MACRO_UPDATE_URL}) outlines proposed changes to the strategy.
"""

#: Reviewed against the official catalogue on 2026-11-05, including hidden entries.
#: Sources and evidence gaps: docs/protocol-research/d2-vault-descriptions.md.
#: Chain/address keys distinguish different products deployed at the same address.
D2_VAULT_SHORT_DESCRIPTIONS: dict[tuple[int, str], str] = {
    # DRV++
    (1, "0x07dff4087b43c4a759f4fc69511c26d51929daf4"): "The strategy combines lending income with options and futures trading.",
    # ftUSDC
    (1, "0x09dfcf4731149582d163a763e7dd553ebc18852d"): "The vault earns fixed-rate interest by lending to the HYPE++ trading strategy.",
    # Debasement++
    (1, "0x1f7bae0549f9f0baf15d0d637900056bb9434c36"): "The strategy combines gold and bitcoin exposure with options and bets against Strategy shares.",
    # HYPE++ Ethena
    (1, "0x2406aacbdf8463176deb285adaa81768415b6c7e"): "The strategy trades cryptoasset contracts to profit from price differences and payments between traders.",
    # Kimchi Corridor
    (10, "0x20833f2e96ab236d66d18a0064e1e8171b70e22e"): "The strategy bets that Korean share price swings will shrink relative to bitcoin's.",
    # Kimchi Premium
    (10, "0xafd240a8e564ad34663b6c42475027ed083088a2"): "The strategy earns a contract payment when Korean stock-market price swings stay below an agreed level.",
    # HGB
    (999, "0x0f00008223b4d4c2c466b4456df1d483743cbd60"): "The strategy borrows against HYPE to add gold and bitcoin investments.",
    # dgnHYPE++
    (999, "0x140e81f8c033c0705036e55be2f2fbce43e4595c"): "The strategy borrows against HYPE to buy fixed-maturity claims on HYPE++.",
    # Alpha Strategies
    (999, "0x195eb4d088f222c982282b5dd495e76dba4bc7d1"): "The strategy trades cryptoasset contracts to profit from price differences and payments between traders.",
    # texasHedge
    (999, "0x208f63a7f60c319597c05fa5ec67fde41839bad6"): "The strategy makes concentrated, actively managed bets using options and borrowed money.",
    # d2HYPE
    (999, "0x3ebb11ba6a5b61c04d1a703ea10728d519945440"): "The strategy earns HYPE option premiums alongside lending and trading income.",
    # hKRAKEN
    (999, "0x4de611ed46aaf54a6850f97542ce7a58e7c8d0fe"): "The strategy sells rights to future Kraken share price gains against tokenised investment claims.",
    # dgnVengeance
    (999, "0x6bf9345b5d6b27b5cbf2e463dc5e0b2afcedc21c"): "The documented strategy bought ether options to increase returns from a strong ether price rise.",
    # USDT0 hRWA
    (999, "0x7410e69958a8ece2a51c231c8528513d4d668c7a"): "The strategy combines lending and liquidity income with contracts tied to traditional markets.",
    # hSOL
    (999, "0x8ef30c5ce9a460bfae82f1f039f7c5e5427d7018"): "The strategy earns SOL option premiums and reinvests them in trading strategies.",
    # USDT0 Hyperliquid-RWA Strategies
    (999, "0xade27c7dec9211973278876f3819aedc28cd50ca"): "The strategy combines lending and liquidity income with contracts tied to traditional markets.",
    # hXXI BTC
    (999, "0xf44f49e6577b3934f981c6f0629d15154d2606e6"): "The strategy seeks additional bitcoin through options, price differences and payments between futures traders.",
    # CoreSolvBTC Covered Call
    (1116, "0xa6c99234ebcda225ef32cefb8472363e09a51aea"): "The strategy earns premiums by selling some future bitcoin gains against bitcoin-backed holdings.",
    # Global Macro
    (4663, "0x3a87f340c7aa52694c3dd806ba13d1b2ebf48dd8"): "The vault strategy trades equity indices, commodities and currencies alongside lending income.",
    # Alpha Strategies
    (8453, "0x2406aacbdf8463176deb285adaa81768415b6c7e"): "The strategy trades cryptoasset contracts to profit from price differences and payments between traders.",
    # dgnBTC v2
    (8453, "0x6c05a7d2c24b48fc3c615d294fec2eb068548897"): "The strategy increases exposure to bitcoin price rises through derivative positions.",
    # Hot7
    (8453, "0xafd240a8e564ad34663b6c42475027ed083088a2"): "The strategy selects seven US stocks by price momentum and adds options income and partial downside protection.",
    # HyperBera.weETH
    (42161, "0x0178b56fea3d7b5b9f9e0cdad486522de948730f"): "The strategy combines ether exposure with trading income and liquidity rewards.",
    # dgnETH v2
    (42161, "0x0f76de33a3679a6065d14780618b54584a3907d4"): "The strategy increases exposure to ether price rises through derivative positions.",
    # GTxD2
    (42161, "0x1176c3760af6a1dbaa5bbd0cc6cda8a2ed6b785e"): "The strategy spreads large purchases and sales over time.",
    # d2ETH
    (42161, "0x195a9e0f29f96d4ab2139ee1272380a4aa352890"): "The strategy borrows against ether to invest in ETH++ while retaining ether price exposure.",
    # ETH++
    (42161, "0x27d22eb71f00495eccc89bb02c2b68e6988c6a42"): "The strategy trades ether contracts to earn income from price differences and payments between traders.",
    # ARB++
    (42161, "0x291344fbaac4fe14632061e4c336fe3b94c52320"): "The strategy seeks additional income while retaining partial exposure to ARB prices.",
    # Dewhales v2
    (42161, "0x34f0fdd80a51dfd8ba42343c20f89217280d760e"): "The vault holds investments selected for an institutional client.",
    # PlsDAO++ v2
    (42161, "0x36b1939adf539a4ac94b57dbad32faecd5bcf4d0"): "The vault holds investments selected for an institutional client.",
    # Alpha Strategies
    (42161, "0x75288264fdfea8ce68e6d852696ab1ce2f3e5004"): "The strategy trades cryptoasset contracts to profit from price differences and payments between traders.",
    # wHLP
    (42161, "0x907a9f69061736ad82811cccd6add9dc4a2352a9"): "The strategy uses borrowed money to increase its investment in Hyperliquid's trading liquidity pool.",
    # xD2e
    (42161, "0x999a57ae7694298126a5db2e44f778ca486b14fc"): "The vault holds a transferable form of xD2 tokens for participation in protocol rewards.",
    # GMX++
    (42161, "0xab2743a3a2e06d457368e901f5f927f271fa1374"): "The strategy borrows against GMX tokens to fund additional investments and trading.",
    # wHYPE
    (42161, "0xac75f0c46723432a2303f2a7c7769535a179ed56"): "The vault holds a wrapped form of HYPE, retaining exposure to HYPE prices.",
    # iARB
    (42161, "0xb0730aa7d6e880f901b5d71a971096db56895a0f"): "The strategy seeks additional income while retaining partial exposure to ARB prices.",
    # HyperBera
    (42161, "0xc027ec28f76d92d4124fcbffcf6b25137a84968c"): "The strategy combines bitcoin exposure with trading income and liquidity rewards.",
    # d2USDC v2
    (42161, "0xd1d64daeed7504ef3eb056aa2d973bd064843a84"): "The strategy combines trading profits, payments between futures traders and investment rewards.",
    # HyperTRUMP
    (42161, "0xfddd73ecd0d0d75e902a567811a70e167a262fab"): "The strategy trades TRUMP price swings and payments between futures traders.",
    # Kodiak++
    (80094, "0x2b8d0420996a2753ef21c25c94eae9fc0c0aed1e"): "The strategy uses borrowed money to earn trading fees and liquidity rewards on Kodiak.",
    # HyperBERA.USDC
    (80094, "0x36b933554782b108bb9962ac00c498acbceb706d"): "The strategy seeks income from options, investment rewards and payments between futures traders.",
    # hBERA
    (80094, "0xace42f7e3f4672607897bf1951468031f0214359"): "The strategy holds BERA and bets against other blockchain tokens to offset some market exposure.",
    # dgnBeraland
    (80094, "0xbe75c8a7e58c7901d2e128dc8d3b6de2481f1f79"): "The strategy uses options to increase exposure to BERA price rises.",
    # dgnUpside
    (80094, "0xbf075980792f8cc89dfb74b553acf6750a7e941b"): "The strategy uses options to increase exposure to bitcoin, ether and BERA prices.",
    # dgnOOGA
    (80094, "0xc4fee8c68293a63241b64e5a2ef07fcf89005dd3"): "The strategy invests in OOGA and adjusts BERA allocations to earn fees and rewards.",
    # 2xBERA
    (80094, "0xcd18006cc69c6d5fa4fd4eaf99910b58464fa3ae"): "The strategy increases BERA exposure through derivatives while earning staking rewards and futures payments.",
    # HyperEuler
    (80094, "0xf650ba4303ce164e1f6b215d4cbb5e212d307056"): "The strategy borrows to earn lending and liquidity rewards, alongside futures trading income.",
}

#: Do not guess strategies for test, retired or undocumented deployments.
D2_UNDISCLOSED_SHORT_DESCRIPTION = "Strategy details are unavailable for this vault."


#: Explicit evidence gap for the seven undocumented catalogue entries.
D2_UNDISCLOSED_DESCRIPTION = """#### Vault's trading strategy

The [published catalogue](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) does not explain this vault's strategy or source of returns. It also does not identify trading venues, a decision-maker, decision rules or trading frequency.
"""


#: Deployment-specific strategy explanations, researched on 2026-11-09.
#: Sources and undisclosed details: docs/protocol-research/d2-vault-descriptions.md.
D2_VAULT_DESCRIPTIONS: dict[tuple[int, str], str] = {
    # DRV++
    (1, "0x07dff4087b43c4a759f4fc69511c26d51929daf4"): """#### Vault's trading strategy

The strategy combines interest income with trades that seek profits from differences between options and futures prices. The [published design](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) uses [Aave](https://aave.com/) for lending, [Pendle](https://pendle.finance/) for fixed-maturity investment claims and [Ethena](https://ethena.fi/) for its yield-bearing token, alongside futures on [Hyperliquid](https://hyperliquid.xyz/) and options on [Derive](https://www.derive.xyz/).

D2's [investment team](https://docs.d2.finance/docs/vault-design) actively manages the trading allocation. The entry rules, extent of automation and rebalance frequency are not disclosed. The catalogue's launch allocations and reward programmes do not establish current holdings or continuing incentives.
""",
    # ftUSDC
    (1, "0x09dfcf4731149582d163a763e7dd553ebc18852d"): """#### Vault's trading strategy

The vault earns fixed-rate interest by lending to the HYPE++ trading strategy. The [lending mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) allows the borrower to use the funds as backing for trades when opportunities arise.

D2's [investment team](https://docs.d2.finance/docs/vault-design) decides when to deploy the borrowed funds. The lending vault does not describe a separate trading algorithm; drawdown rules, lending decision frequency and the borrower's complete trading venues are not disclosed. Repayment depends on the borrower, so a fixed interest rate does not make the loan risk-free.
""",
    # Debasement++
    (1, "0x1f7bae0549f9f0baf15d0d637900056bb9434c36"): """#### Vault's trading strategy

The strategy holds gold exposure, sells contracts that pay the buyer if gold falls, buys rights to bitcoin price gains and bets against shares in Strategy, the bitcoin-holding company. The [published design](https://medium.com/@D2.Finance/debasement-do-not-fade-the-money-printer-8f39a12eb777) seeks gains from gold and bitcoin alongside option premiums and falls in the share price.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the positions within stated exposure limits, using option prices and the relationship between Strategy shares and bitcoin as inputs. The options venues, share-trading venue, automation and adjustment frequency are not disclosed. Several positions can lose money together.
""",
    # HYPE++ Ethena
    (1, "0x2406aacbdf8463176deb285adaa81768415b6c7e"): """#### Vault's trading strategy

The strategy trades mispriced bitcoin and ether options and futures, including price differences across markets. It also seeks [funding payments](https://tradingstrategy.ai/glossary/funding-rate), periodic transfers between traders holding opposing futures positions. Positions can also respond to events such as regulatory decisions.

The [strategy explanation](https://medium.com/@D2.Finance/hype-strategy-faq-654bc8d0efc9) describes active portfolio management with rule-based offsetting trades. D2's [investment team](https://docs.d2.finance/docs/vault-design) runs the strategy, but the division between human judgement and algorithms, and the trading frequency, are not disclosed.

[Hyperliquid](https://hyperliquid.xyz/) is identified for futures trading. The [shared fund's venue design](https://medium.com/@D2.Finance/hype-base-institutional-grade-defi-bdce160e66b6) names [Derive](https://www.derive.xyz/) for options, with [Flowdesk](https://www.flowdesk.co/) and [Hyperdelta](https://hyperdelta.com/) as counterparties. The vault shares strategy results with the other HYPE++ access points; current balances at each venue are not disclosed.
""",
    # Kimchi Corridor
    (10, "0x20833f2e96ab236d66d18a0064e1e8171b70e22e"): """#### Vault's trading strategy

The strategy uses a contract that profits when Korean share price swings fall relative to bitcoin's. The published contract measures the difference using daily closing prices of funds tracking the two markets.

D2 fixes the terms at the start and holds the contract to expiry without discretionary trading during the cycle. The contract is matched with HYPE++ through [D2's internal Central Book](https://medium.com/@D2.Finance/texas-hedge-got-twins-kimchi-premium-and-the-ibit-ewy-volatility-corridor-e88c72b03ec0); Bloomberg supplies pricing inputs rather than an execution venue. Its 40% contractual loss floor applies to scheduled settlement, not to counterparty or contract failures. The documented cycle ended in September 2026; terms of a later cycle are not established by that announcement.
""",
    # Kimchi Premium
    (10, "0xafd240a8e564ad34663b6c42475027ed083088a2"): """#### Vault's trading strategy

The strategy sells a contract that pays out when Korean stock-market price swings are below an agreed level, and loses money when they are higher. The published contract uses daily closing prices to calculate the outcome.

D2 fixes the terms at the start and holds the contract to expiry without discretionary trading during the cycle. The contract is matched with HYPE++ through [D2's internal Central Book](https://medium.com/@D2.Finance/texas-hedge-got-twins-kimchi-premium-and-the-ibit-ewy-volatility-corridor-e88c72b03ec0); Bloomberg supplies pricing inputs rather than an execution venue. Its 40% contractual loss floor applies to scheduled settlement, not to counterparty or contract failures. The documented cycle ended in September 2026; terms of a later cycle are not established by that announcement.
""",
    # HGB
    (999, "0x0f00008223b4d4c2c466b4456df1d483743cbd60"): """#### Vault's trading strategy

The strategy retains HYPE exposure and borrows against it to add gold and bitcoin investments. The [published design](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) also refers to basket options, contracts whose returns depend on several assets.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the allocation. The lending platform, options venue or counterparty, decision rules and adjustment frequency are not disclosed. Borrowing adds interest costs and the risk of a forced sale if the collateral loses value.
""",
    # dgnHYPE++
    (999, "0x140e81f8c033c0705036e55be2f2fbce43e4595c"): """#### Vault's trading strategy

The strategy borrows against HYPE and buys principal tokens on [Pendle](https://pendle.finance/), which are claims redeemable against the HYPE++ investment at maturity. The [published rule](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) is to invest when the price difference is favourable.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the borrowing and purchases, but the required price difference, lending platform, automation and review frequency are not disclosed. Returns depend on the purchase price, borrowing costs and the underlying HYPE++ investment.
""",
    # Alpha Strategies
    (999, "0x195eb4d088f222c982282b5dd495e76dba4bc7d1"): """#### Vault's trading strategy

The strategy trades mispriced bitcoin and ether options and futures, including price differences across markets. It also seeks [funding payments](https://tradingstrategy.ai/glossary/funding-rate), periodic transfers between traders holding opposing futures positions. Positions can also respond to events such as regulatory decisions.

The [strategy explanation](https://medium.com/@D2.Finance/hype-strategy-faq-654bc8d0efc9) describes active portfolio management with rule-based offsetting trades. D2's [investment team](https://docs.d2.finance/docs/vault-design) runs the strategy, but the division between human judgement and algorithms, and the trading frequency, are not disclosed.

[Hyperliquid](https://hyperliquid.xyz/) is identified for futures trading. The [shared fund's venue design](https://medium.com/@D2.Finance/hype-base-institutional-grade-defi-bdce160e66b6) names [Derive](https://www.derive.xyz/) for options, with [Flowdesk](https://www.flowdesk.co/) and [Hyperdelta](https://hyperdelta.com/) as counterparties. The vault shares strategy results with the other HYPE++ access points; current balances at each venue are not disclosed.
""",
    # texasHedge
    (999, "0x208f63a7f60c319597c05fa5ec67fde41839bad6"): """#### Vault's trading strategy

The strategy makes concentrated bets on asset prices using options and borrowed money. D2's [investment team](https://docs.d2.finance/docs/vault-design) changes the positions between trading cycles; a fixed algorithm or regular intra-cycle trading schedule is not disclosed.

The [June 2026 design](https://medium.com/@D2.Finance/texas-hedge-epoch-2-elon-takes-us-to-space-x-fec66a354ad3) combined SpaceX exposure with options on US stock indices, Tesla and memory-chip companies. HYPE++ supplied the loan for the SpaceX position and bought the sold memory-chip options. The design increased exposure only after gains and used a stop intended to close positions after a fall from their peak. The source does not identify all external venues or the counterparties for the purchased options. This was a discretionary trade idea, not a permanent portfolio, and **the entire deposit can be lost**.
""",
    # d2HYPE
    (999, "0x3ebb11ba6a5b61c04d1a703ea10728d519945440"): """#### Vault's trading strategy

The strategy sells rights to some future HYPE price gains and trades differences in option prices. Its [published design](https://medium.com/@D2.Finance/d2hype-turn-hype-volatility-into-20-apr-yield-b570d72a4ec0) sets call-option terms weekly, while D2's [investment team](https://docs.d2.finance/docs/vault-design) adjusts other positions using market-price and onchain signals.

The design names [Flowdesk](https://www.flowdesk.co/) among the dealers supplying protective options and [HypurrFi](https://hypurrfi.com/) for lending. It describes algorithmic execution but does not publish the signal rules, adjustment frequency or every options counterparty. Selling calls limits gains above their agreed price; protective options and lending do not remove the risk of loss.
""",
    # hKRAKEN
    (999, "0x4de611ed46aaf54a6850f97542ce7a58e7c8d0fe"): """#### Vault's trading strategy

The strategy earns premiums by selling rights to Kraken share price gains above an agreed price. The [published structure](https://medium.com/@D2.Finance/introducing-hkraken-pioneering-tokenized-pre-ipo-options-6292f2f3d03c) uses tokenised investment claims as backing and pays premiums in dgnUpside, a separate strategy exposed to cryptoasset prices.

D2's contracts handle option selling and collateral. HYPE++ is the named option buyer in this internally arranged product. The article does not identify an external exchange, how terms are selected or how often they change. Returns depend on both the share-linked contract and the value of dgnUpside.
""",
    # dgnVengeance
    (999, "0x6bf9345b5d6b27b5cbf2e463dc5e0b2afcedc21c"): """#### Vault's trading strategy

The documented strategy bought ether options that paid off above an agreed price, seeking a larger gain from a strong ether rally. The [rationale for that cycle](https://medium.com/@D2.Finance/dgnvengeance-dgnupsides-last-dance-969739fb2325) compared option prices with their historical range and with technology-share options to identify inexpensive ether exposure.

D2's [investment team](https://docs.d2.finance/docs/vault-design) selected the positions. The options venue, counterparty, repeatable entry thresholds, automation and adjustment frequency were not disclosed. The described final cycle ended in November 2026; the announcement does not establish a subsequent strategy. Its 40% loss limit applied to the scheduled derivative payoff, not to contract or counterparty failures.
""",
    # USDT0 hRWA
    (999, "0x7410e69958a8ece2a51c231c8528513d4d668c7a"): """#### Vault's trading strategy

The strategy combines lending and liquidity income with trades tied to traditional markets, including S&P 500 option pricing. D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the allocation, but the decision rules, automation and trading frequency are not disclosed.

The [published design](https://medium.com/@D2.Finance/introducing-usdt0-hrwa-the-ultimate-stablecoin-yield-engine-on-hyperevm-f7e8b58068db) names [Felix](https://www.usefelix.xyz/) and [Hyperlend](https://hyperlend.finance/) for lending, [Hyperswap](https://hyperswap.exchange/) for trading liquidity and [Kinetiq](https://kinetiq.xyz/) for staking. It does not identify the options venues or counterparties, or confirm the current allocation to each platform.
""",
    # Hype EVM++
    (999, "0x7da637df83778704464208f092dbdbf9f386df96"): D2_UNDISCLOSED_DESCRIPTION,
    # hSOL
    (999, "0x8ef30c5ce9a460bfae82f1f039f7c5e5427d7018"): """#### Vault's trading strategy

The strategy sells contracts that compensate the buyer if SOL falls below an agreed price, earning premiums in return. Its published structure reinvests that income in trades that collect differences in futures payments or in the hRWA strategy.

D2's [investment team](https://docs.d2.finance/docs/vault-design) selects the options and allocations. HYPE++ is the [named options counterparty](https://medium.com/@D2.Finance/introducing-hsol-20-apr-target-on-sol-with-hyperliquid-5b10672f6369), rather than an external options exchange; the article describes access through [Hyperliquid](https://hyperliquid.xyz/). It does not disclose the complete execution venues, automated decision rules or trading frequency. Falling SOL prices can produce option losses larger than the premiums.
""",
    # USDT0 Hyperliquid-RWA Strategies
    (999, "0xade27c7dec9211973278876f3819aedc28cd50ca"): """#### Vault's trading strategy

The strategy combines lending and liquidity income with trades tied to traditional markets, including S&P 500 option pricing. D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the allocation, but the decision rules, automation and trading frequency are not disclosed.

The [published design](https://medium.com/@D2.Finance/introducing-usdt0-hrwa-the-ultimate-stablecoin-yield-engine-on-hyperevm-f7e8b58068db) names [Felix](https://www.usefelix.xyz/) and [Hyperlend](https://hyperlend.finance/) for lending, [Hyperswap](https://hyperswap.exchange/) for trading liquidity and [Kinetiq](https://kinetiq.xyz/) for staking. It does not identify the options venues or counterparties, or confirm the current allocation to each platform.
""",
    # hXXI BTC
    (999, "0xf44f49e6577b3934f981c6f0629d15154d2606e6"): """#### Vault's trading strategy

The strategy seeks to increase its bitcoin holdings by selling rights to future bitcoin gains, trading differences in option prices and collecting [funding payments](https://tradingstrategy.ai/glossary/funding-rate) between opposing futures positions. D2's [investment team](https://docs.d2.finance/docs/vault-design) actively adjusts positions and offsetting trades, but the entry rules, automation and trading frequency are not disclosed.

The [published design](https://medium.com/@D2.Finance/introducing-hyperliquid-xxi-hxxi-the-future-of-bitcoin-accumulation-on-chain-499c488e67ca) describes deployment on HyperEVM without identifying each execution venue or the synthetic-options counterparties.
""",
    # CoreSolvBTC Covered Call
    (1116, "0xa6c99234ebcda225ef32cefb8472363e09a51aea"): """#### Vault's trading strategy

The strategy sells rights to bitcoin gains above an agreed price, backed by SolvBTC, a token representing bitcoin holdings. The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) describes rule-based option selling, combining external orders with purchases by other internal portfolios.

D2's [investment team](https://docs.d2.finance/docs/vault-design) operates the strategy. The external options venues, internal counterparties, strike-selection rules and sale frequency are not disclosed. Premium income comes at the cost of giving up some gains if bitcoin rises beyond the agreed price.
""",
    # Alpha Strategies
    (8453, "0x2406aacbdf8463176deb285adaa81768415b6c7e"): """#### Vault's trading strategy

The strategy trades mispriced bitcoin and ether options and futures, including price differences across markets. It also seeks [funding payments](https://tradingstrategy.ai/glossary/funding-rate), periodic transfers between traders holding opposing futures positions. It shares strategy results with the other HYPE++ access points.

The [Base strategy design](https://medium.com/@D2.Finance/hype-base-institutional-grade-defi-bdce160e66b6) names [Derive](https://www.derive.xyz/) for options, [Flowdesk](https://www.flowdesk.co/) and [Hyperdelta](https://hyperdelta.com/) as trading counterparties, and [Aave](https://aave.com/) and [ether.fi](https://www.ether.fi/) for collateral.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the portfolio using active positioning and rule-based offsetting trades. The [shared fund](https://medium.com/@D2.Finance/hype-strategy-faq-654bc8d0efc9) also uses [Hyperliquid](https://hyperliquid.xyz/) for futures. The division between human judgement and algorithms, the trading frequency and current venue balances are not disclosed.
""",
    # dgnBTC v2
    (8453, "0x6c05a7d2c24b48fc3c615d294fec2eb068548897"): """#### Vault's trading strategy

The strategy increases exposure to bitcoin price rises through contracts whose value depends on bitcoin. The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) describes protection intended to reduce exposure to falling prices, without publishing its implementation.

A [completed trading cycle](https://medium.com/@D2.Finance/hype-base-institutional-grade-defi-bdce160e66b6) used options on [Derive](https://www.derive.xyz/) and D2's internal options system. D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the strategy; current venue allocation, decision rules and trading frequency are not disclosed. The earlier cycle does not establish current holdings.
""",
    # Hot7
    (8453, "0xafd240a8e564ad34663b6c42475027ed083088a2"): """#### Vault's trading strategy

The strategy selects seven US stocks using a rule that ranks recent price strength. The published design gives them equal weights and resets the selection at monthly option expiry, rather than trading the ranking every day.

D2's [investment team](https://docs.d2.finance/docs/vault-design) implements the rules. Stock-price exposure trades through futures on [Hyperliquid](https://hyperliquid.xyz/); options are matched with HYPE++ in [D2's internal Central Book](https://medium.com/@D2.Finance/hot7-structured-products-rwas-are-boring-d2-brings-the-spice-7b8d14dcd662). Selling some upside pays for limited protection against falls in the S&P 500. The catalogue also identifies [Aave](https://aave.com/) for the initial lending allocation; the current capital venue is set at each scheduled reset. The options do not protect against every loss.
""",
    # HyperBera.weETH
    (42161, "0x0178b56fea3d7b5b9f9e0cdad486522de948730f"): """#### Vault's trading strategy

The strategy retains ether exposure while seeking trading income and rewards for supplying capital. Its [original design](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) allocated to HLP on [Hyperliquid](https://hyperliquid.xyz/), [Derive](https://www.derive.xyz/) and Berachain reward programmes.

D2's [investment team](https://docs.d2.finance/docs/vault-design) actively manages the allocation, but the trading rules, automation and adjustment frequency are not disclosed. The catalogue refers to the 2025 Boyco launch programme and does not identify the current pool allocations or map every instrument to a venue. Those historical rewards should not be assumed to continue.
""",
    # dgnETH v2
    (42161, "0x0f76de33a3679a6065d14780618b54584a3907d4"): """#### Vault's trading strategy

The strategy increases exposure to ether price rises through contracts whose value depends on ether. The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) describes protection designed to reduce exposure when prices fall, but does not explain how that protection is implemented.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the positions. The venues, rules for changing exposure, use of algorithms and trading frequency are not disclosed. The protection should not be treated as a guarantee against losses.
""",
    # GTxD2
    (42161, "0x1176c3760af6a1dbaa5bbd0cc6cda8a2ed6b785e"): """#### Vault's trading strategy

The strategy executes large trades in smaller portions over time, aiming for an average price across the execution period. The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) describes an on-demand trading service rather than a separate source of investment yield.

D2's [investment team](https://docs.d2.finance/docs/vault-design) operates the strategy. The mandate does not disclose the trading venues, order intervals, algorithm parameters or who chooses the start and end times.
""",
    # LYRA++
    (42161, "0x183424d5ae5ec9fd486634bc566d0f75ad9c9109"): D2_UNDISCLOSED_DESCRIPTION,
    # d2ETH
    (42161, "0x195a9e0f29f96d4ab2139ee1272380a4aa352890"): """#### Vault's trading strategy

The strategy borrows against ether and invests part of the proceeds in ETH++, which trades ether and other cryptoasset contracts. The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) aims to add trading income while retaining ether price exposure.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the borrowing and offsetting positions. The lending platforms, allocation rules, use of algorithms and adjustment frequency are not disclosed. Borrowing costs and losses in ETH++ can reduce returns.
""",
    # VERTEXMOBY++
    (42161, "0x23f556f4df7a263ce68ee29659aa4ea632a6a5bc"): D2_UNDISCLOSED_DESCRIPTION,
    # ETH++
    (42161, "0x27d22eb71f00495eccc89bb02c2b68e6988c6a42"): """#### Vault's trading strategy

The strategy buys and sells ether and other cryptoasset contracts when their prices or expected price swings differ across markets. It also trades differences in [funding payments](https://tradingstrategy.ai/glossary/funding-rate), the periodic transfers between opposing futures positions.

The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) names [Hyperliquid](https://hyperliquid.xyz/) and [GMX](https://gmx.io/) for adjusting positions that offset market exposure. D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the trades; the entry rules, use of algorithms and adjustment frequency are not disclosed. The [launch article](https://medium.com/@D2.Finance/d2-finance-eth-market-launching-on-camelot-5e420cff0181) explains the original ether strategy.
""",
    # ARB++
    (42161, "0x291344fbaac4fe14632061e4c336fe3b94c52320"): """#### Vault's trading strategy

The strategy seeks investment income while retaining some exposure to ARB price changes. The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) describes this objective but does not explain which trades generate the income.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the strategy. Its trading venues, decision rules and trading frequency are not disclosed.
""",
    # Dewhales v2
    (42161, "0x34f0fdd80a51dfd8ba42343c20f89217280d760e"): """#### Vault's trading strategy

The vault holds investments selected for an institutional client. The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) does not identify the investments or explain how they generate returns.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the allocation, but the client's approval rights, trading venues, decision rules and rebalance frequency are not disclosed.
""",
    # PlsDAO++ v2
    (42161, "0x36b1939adf539a4ac94b57dbad32faecd5bcf4d0"): """#### Vault's trading strategy

The vault holds investments selected for an institutional client. The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) does not identify the investments or explain how they generate returns.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the allocation, but the client's approval rights, trading venues, decision rules and rebalance frequency are not disclosed.
""",
    # GEARPENDLE++
    (42161, "0x5b49d7fae00de64779ddcd6b067c8eb046bd9a0b"): D2_UNDISCLOSED_DESCRIPTION,
    # Alpha Strategies
    (42161, "0x75288264fdfea8ce68e6d852696ab1ce2f3e5004"): """#### Vault's trading strategy

The strategy trades mispriced bitcoin and ether options and futures, including price differences across markets. It also seeks [funding payments](https://tradingstrategy.ai/glossary/funding-rate), periodic transfers between traders holding opposing futures positions. Positions can also respond to events such as regulatory decisions.

The [strategy explanation](https://medium.com/@D2.Finance/hype-strategy-faq-654bc8d0efc9) describes active portfolio management with rule-based offsetting trades. D2's [investment team](https://docs.d2.finance/docs/vault-design) runs the strategy, but the division between human judgement and algorithms, and the trading frequency, are not disclosed.

[Hyperliquid](https://hyperliquid.xyz/) is identified for futures trading. The [shared fund's venue design](https://medium.com/@D2.Finance/hype-base-institutional-grade-defi-bdce160e66b6) names [Derive](https://www.derive.xyz/) for options, with [Flowdesk](https://www.flowdesk.co/) and [Hyperdelta](https://hyperdelta.com/) as counterparties. The vault shares strategy results with the other HYPE++ access points; current balances at each venue are not disclosed.
""",
    # renzoSilo++
    (42161, "0x80c403807b1032d7cb19b6d612ce23f05a213d36"): D2_UNDISCLOSED_DESCRIPTION,
    # wHLP
    (42161, "0x907a9f69061736ad82811cccd6add9dc4a2352a9"): """#### Vault's trading strategy

The strategy uses borrowed money to increase its investment in [Hyperliquid](https://hyperliquid.xyz/)'s HLP liquidity pool. Returns depend on the pool's trading results and fees, less borrowing costs.

The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) targets increased HLP exposure but does not identify the lending platform or the rules for adjusting debt. D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the allocation; its rebalance frequency and use of algorithms are not disclosed. Losses in HLP are amplified by borrowing.
""",
    # xD2e
    (42161, "0x999a57ae7694298126a5db2e44f778ca486b14fc"): """#### Vault's trading strategy

The vault holds a transferable form of xD2 tokens. The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) describes participation in protocol rewards, but does not specify a contractual income stream or explain how rewards are valued.

No active trading process, execution venues, decision-maker or rebalance schedule is disclosed. Holding a transferable token does not itself establish a source of yield.
""",
    # dgnLYRA
    (42161, "0x9aa9bd7b032e527b0820c85cee89093dc33df8bd"): D2_UNDISCLOSED_DESCRIPTION,
    # GMX++
    (42161, "0xab2743a3a2e06d457368e901f5f927f271fa1374"): """#### Vault's trading strategy

The strategy borrows against GMX tokens and uses the money for trading and other investments, while retaining GMX price exposure. The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) names [Silo](https://www.silo.finance/) and [Dolomite](https://dolomite.io/) as lending platforms, with allocations to GMX liquidity vaults and the ETH++ trading strategy.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the borrowing and allocations. The venue for trades between GMX tokens and futures, the decision rules and the rebalance frequency are not disclosed. Borrowing adds interest costs and the risk of a forced sale if collateral loses value.
""",
    # wHYPE
    (42161, "0xac75f0c46723432a2303f2a7c7769535a179ed56"): """#### Vault's trading strategy

The vault holds a wrapped, transferable form of HYPE. Its value remains exposed to HYPE price changes; wrapping a token does not itself generate lending or trading income.

The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) also mentions reward points without specifying a cash entitlement. It does not disclose an active trading process, execution venues, decision-maker or rebalance schedule.
""",
    # iARB
    (42161, "0xb0730aa7d6e880f901b5d71a971096db56895a0f"): """#### Vault's trading strategy

The strategy seeks investment income while retaining some exposure to ARB price changes. The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) describes this objective but does not explain which trades generate the income.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the strategy. Its trading venues, decision rules and trading frequency are not disclosed.
""",
    # HyperBera
    (42161, "0xc027ec28f76d92d4124fcbffcf6b25137a84968c"): """#### Vault's trading strategy

The strategy retains bitcoin exposure while seeking trading income and rewards for supplying capital. Its [original design](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) allocated to HLP on [Hyperliquid](https://hyperliquid.xyz/), [Derive](https://www.derive.xyz/) and Berachain reward programmes.

D2's [investment team](https://docs.d2.finance/docs/vault-design) actively manages the allocation, but the trading rules, automation and adjustment frequency are not disclosed. The catalogue refers to the 2025 Boyco launch programme and does not identify the current pool allocations or map every instrument to a venue. Those historical rewards should not be assumed to continue.
""",
    # d2USDC v2
    (42161, "0xd1d64daeed7504ef3eb056aa2d973bd064843a84"): """#### Vault's trading strategy

The strategy seeks profits from price differences, payments between futures traders and token rewards. It adjusts offsetting positions on [GMX](https://gmx.io/) to reduce exposure to market direction.

The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) describes continuous monitoring with periodic adjustments by D2's [investment team](https://docs.d2.finance/docs/vault-design). It does not specify the adjustment rules, the extent of automation or the venues used for the other trades and reward allocations.
""",
    # Test
    (42161, "0xeb3194d69201b04729f1a618d95519cdae24d6c0"): D2_UNDISCLOSED_DESCRIPTION,
    # HyperTRUMP
    (42161, "0xfddd73ecd0d0d75e902a567811a70e167a262fab"): """#### Vault's trading strategy

The strategy trades around TRUMP-related events, seeking profits from price swings and differences in payments between futures traders. Its [published design](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) also allocates to HyperBera.weETH for additional trading income and liquidity rewards.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the positions in response to events. The exact triggers, use of algorithms, trading frequency and venue for the TRUMP trades are not disclosed. [Hyperliquid](https://hyperliquid.xyz/) is named for activity within the underlying allocation; the TRUMP execution venue is unspecified.
""",
    # Kodiak++
    (80094, "0x2b8d0420996a2753ef21c25c94eae9fc0c0aed1e"): """#### Vault's trading strategy

The strategy uses borrowed money to supply trading liquidity on [Kodiak](https://www.kodiak.finance/), seeking fees and Berachain rewards. Its [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) also includes options intended to manage risk.

D2's [investment team](https://docs.d2.finance/docs/vault-design) actively manages the liquidity positions. The borrowing platform, options venue, rules for moving liquidity and adjustment frequency are not disclosed. Borrowing magnifies losses, and the catalogue's historical Boyco incentives should not be treated as ongoing income.
""",
    # HyperBERA.USDC
    (80094, "0x36b933554782b108bb9962ac00c498acbceb706d"): """#### Vault's trading strategy

The strategy combines options trading, investment rewards and differences in payments between futures traders. The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) aims to offset market exposure but does not explain the individual trades.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the strategy. The options and futures venues, reward platforms, decision rules, automation and trading frequency are not disclosed. Historical Boyco incentives in the catalogue do not establish ongoing income.
""",
    # hBERA
    (80094, "0xace42f7e3f4672607897bf1951468031f0214359"): """#### Vault's trading strategy

The strategy holds BERA and takes positions that profit when selected other blockchain tokens fall. The [selection approach](https://medium.com/@D2.Finance/hedged-bera-hbera-betting-on-berachains-community-hedging-against-ghost-towns-72d6332e95e2) targets tokens with high valuations and low activity, seeking to benefit if BERA outperforms them.

D2's [investment team](https://docs.d2.finance/docs/vault-design) adjusts the positions as opportunities change; the precise ranking rules, automation and review frequency are not disclosed. The design borrows against BERA on [Dolomite](https://dolomite.io/) or [Beraborrow](https://www.beraborrow.com/) and trades on [Hyperliquid](https://hyperliquid.xyz/). The opposing positions can both lose money.
""",
    # dgnBeraland
    (80094, "0xbe75c8a7e58c7901d2e128dc8d3b6de2481f1f79"): """#### Vault's trading strategy

The strategy uses [options](https://tradingstrategy.ai/glossary/options), contracts giving rights to buy or sell at agreed prices, to increase exposure to BERA price rises. Returns depend on the price move being large enough to cover the cost of those contracts.

The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) does not identify the options venue or counterparty. D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the strategy, but its strike selection, entry rules, automation and trading frequency are not disclosed. Historical Boyco incentives in the catalogue do not establish a continuing source of income.
""",
    # dgnUpside
    (80094, "0xbf075980792f8cc89dfb74b553acf6750a7e941b"): """#### Vault's trading strategy

The strategy combines liquidity rewards with options that increase exposure to bitcoin, ether and BERA price rises. The [published mandate](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) describes automatic option purchases and adjustments to exposure, but does not publish the signals or their calculation frequency.

D2's [investment team](https://docs.d2.finance/docs/vault-design) operates the strategy. The options venues, counterparties, reward pools and human override rights are not disclosed. Purchased options can lose their cost if the expected price rise does not occur.
""",
    # dgnOOGA
    (80094, "0xc4fee8c68293a63241b64e5a2ef07fcf89005dd3"): """#### Vault's trading strategy

The strategy invests in OOGA, a token linked to a trading aggregator whose fees fund token purchases. Its [published design](https://medium.com/@D2.Finance/4f10c3fbf0a3) combines that exposure with lending and liquidity rewards, taking profits after price rises and reducing exposure during falls.

The [catalogue](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) names [Kodiak](https://www.kodiak.finance/) for trading liquidity, [Infrared](https://infrared.finance/) for staking those liquidity positions, [Origami](https://origami.finance/) for reinvesting rewards and [Dolomite](https://dolomite.io/) for lending and borrowing. D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the allocation, but the entry thresholds, automation and rebalance frequency are not disclosed. OOGA price falls can outweigh the fees supporting its token purchases.
""",
    # 2xBERA
    (80094, "0xcd18006cc69c6d5fa4fd4eaf99910b58464fa3ae"): """#### Vault's trading strategy

The strategy increases BERA price exposure while earning staking rewards and payments from futures trades. Its [published design](https://medium.com/@D2.Finance/infrared-origami-d2-2xbera-df7e7129df9a) uses [Origami](https://origami.finance/) and [Infrared](https://infrared.finance/) for the staking allocation, and [Hyperliquid](https://hyperliquid.xyz/) for futures.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the allocation. A protective contract is intended to reduce exposure as BERA falls, but its counterparty and the complete automation rules are not disclosed. The rebalance frequency is also unspecified. The launch claim of protection against liquidation should not be read as a guarantee against loss.
""",
    # HyperEuler
    (80094, "0xf650ba4303ce164e1f6b215d4cbb5e212d307056"): """#### Vault's trading strategy

The strategy combines lending incentives on [Euler](https://www.euler.finance/) with trading fees and liquidity rewards on [Kodiak](https://www.kodiak.finance/). The [catalogue](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc) also names [Hyperliquid](https://hyperliquid.xyz/) for trades that collect differences in payments between futures positions.

D2's [investment team](https://docs.d2.finance/docs/vault-design) manages the borrowing and allocations. The [launch design](https://medium.com/@D2.Finance/hypereuler-x-pol-pol-is-here-is-time-to-supercharge-your-bera-4dfaa8805d23) describes borrowed assets flowing into liquidity pools, but does not publish decision rules, automation or rebalance frequency. Borrowing costs and pool losses can outweigh rewards.
""",
}

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

#: The same address hosts HYPE++ Ethena on Ethereum and Alpha Strategies on Base.
#: Keep the legacy address table unchanged for migration 034's old-note recognition.
D2_VAULT_ARTICLE_OVERRIDES: dict[tuple[int, str], str] = {
    (1, "0x2406aacbdf8463176deb285adaa81768415b6c7e"): "https://medium.com/@D2.Finance/hype-strategy-faq-654bc8d0efc9",
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


#: All D2 strategies use epoch-gated withdrawals; documentation reviewed
#: 2026-11-02 following the user's request to explain closed withdrawal windows.
#: https://docs.d2.finance/docs/vault-design
D2_WITHDRAWAL_NOTE = """#### Withdrawal restrictions

**Deposits and withdrawals are closed during the trading phase.** Each [trading cycle](https://docs.d2.finance/docs/vault-design), called an epoch, has separate funding, trading and withdrawal phases. During trading, the trader may take custody of the vault's funds.

Withdrawals reopen after the trader returns the funds and the epoch ends. A displayed epoch-end time is an estimate, not a guaranteed withdrawal deadline."""


def format_d2_vault_note(address: HexAddress | str, chain_id: int | None = None) -> str:
    """Format a generic D2 Finance vault note.

    D2 vaults are managed strategy vaults with proprietary offchain trading
    logic. The authoritative source for the per-vault setup is the D2 strategy
    page, so link to it for every D2 vault. Chain-specific corrections take
    precedence; omitting the chain retains legacy rendering for migrations.

    :param address:
        Vault address.
    :param chain_id:
        Deployment chain, or ``None`` for legacy address-only rendering.
    :return:
        Markdown vault note.
    """

    address = str(address).lower()
    strategy_url = format_d2_strategy_url(address)
    article_url = D2_VAULT_ARTICLE_OVERRIDES.get((chain_id, address), D2_VAULT_ARTICLE_URLS.get(address))
    article_note = f" and [strategy background]({article_url})" if article_url else ""

    return f"""{D2_WITHDRAWAL_NOTE}

See the [investment strategy]({strategy_url}){article_note} for product-specific information.
"""


def format_d2_texas_hedge_note() -> str:
    """Format the D2 texasHedge vault note.

    The texasHedge vault has a D2-authored strategy article with specific
    tactical and risk context that is more useful than the generic D2 note.

    :return:
        Markdown vault note.
    """

    # The June article explicitly calls the internal funding borrowing: a
    # credit line from HYPE++ charged at ten percent, drawn as needed.
    return f"""USDC funds actively managed bets on asset prices. **The entire deposit can be lost.** The [investment strategy]({format_d2_strategy_url(D2_TEXAS_HEDGE_ADDRESS)}) changes between trading cycles.

#### Historical strategy

The [June 2026 trading cycle]({D2_TEXAS_HEDGE_BLOG_URL}) combined a position funded with borrowed money that tracked SpaceX before its stock-market listing with [options](https://tradingstrategy.ai/glossary/options), contracts granting the right to buy or sell at an agreed price before a deadline. Some options benefited from falls in the S&P 500 stock index; others benefited from rises in Tesla and a group of large US technology stocks.

Selling options on DRAM, a fund investing in memory-chip companies, helped pay for the purchased options. The sold options could lose money if memory-chip stocks rose, with losses compounded if SpaceX also fell. This historical setup does not establish the vault's current holdings.

{D2_WITHDRAWAL_NOTE}
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


class D2Vault(ERC4626Vault):  # noqa: PLR0904 - Implements the shared vault interface, including display metadata.
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

    @property
    def short_description(self) -> str:
        """Summarise the reviewed strategy without inferring it from the name.

        The official catalogue contains historical and undocumented deployments.
        Use a clear evidence-gap sentence for these and unknown deployments.

        :return: One plain sentence, scoped to chain and case-insensitive address.
        """
        return D2_VAULT_SHORT_DESCRIPTIONS.get((self.chain_id, self.address.lower()), D2_UNDISCLOSED_SHORT_DESCRIPTION)

    @property
    def description(self) -> str | None:
        """Expose a researched explanation for each reviewed D2 deployment.

        Match both chain and address because different products reuse addresses.
        Reviewed entries with no disclosed strategy receive an explicit evidence
        gap; deployments outside the catalogue retain inherited behaviour.

        :return: Normalised maintained Markdown, or the inherited description.
        """
        if self.chain_id == D2_GLOBAL_MACRO_CHAIN_ID and self.address.lower() == D2_GLOBAL_MACRO_ADDRESS:
            return normalise_vault_description(D2_GLOBAL_MACRO_DESCRIPTION)
        description = D2_VAULT_DESCRIPTIONS.get((self.chain_id, self.address.lower()))
        if description is not None:
            return normalise_vault_description(description)
        return super().description

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

        return format_d2_vault_note(address, chain_id=self.chain_id)

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

    def get_withdrawal_period(self) -> WithdrawalPeriod:
        """Return the D2 redemption window bounds for the current epoch.

        D2 has no post-request cooldown: redemptions become available in the
        withdrawal phase after a trading epoch. Consequently the shortest
        wait is zero and the longest normal wait is one configured epoch.

        :return:
            Current epoch-length withdrawal window bounds.
        """
        return WithdrawalPeriod(
            min_period=datetime.timedelta(0),
            max_period=self.get_estimated_lock_up(),
            delay_type=WithdrawalDelayType.epoch,
        )

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
