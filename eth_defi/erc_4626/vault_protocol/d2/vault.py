"""D2 Finance vault support."""

import datetime
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property
import logging
from typing import Iterable

from web3.contract import Contract
from eth_typing import BlockIdentifier, HexAddress

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.compat import native_datetime_utc_now
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.utils import from_unix_timestamp
from eth_defi.vault.base import (
    DEPOSIT_CLOSED_FUNDING_PHASE,
    REDEMPTION_CLOSED_FUNDS_CUSTODIED,
    VaultHistoricalRead,
    VaultHistoricalReader,
    VaultTechnicalRisk,
)

logger = logging.getLogger(__name__)


class D2DepositManager(ERC4626DepositManager):
    """D2 ERC-4626 lifecycle with explicit zero-price admission failure.

    **Supported simulation path**

    :meth:`force_settle` receives ``None`` and uses the shared Anvil-only
    no-op implementation for a direct ERC-4626 call. This adapter only
    improves preflight estimation; it does not certify a successful D2
    transaction path.

    **Known limitations**

    Successful D2 deposits and redemptions have not yet been fork-proven.
    Custodied epochs, operator NAV changes, delayed withdrawals and other
    epoch transitions are deliberately outside this adapter.
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
            raise ValueError(f"D2 deposit is unavailable: {closed_reason}")
        estimate = super().estimate_deposit(owner, amount, block_identifier)
        if estimate <= 0:
            raise ValueError(f"D2 deposit estimate is zero for {amount} {self.vault.denomination_token.symbol}; pricing is unavailable")
        return estimate


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
    - VaultV1Whitelisted is a wrapper around Hyperliquid trading account
    - You need to hold a minimum amount of USDC (whitelistedAsset) to be able to deposit
    - The vault smart contract does not have visibility to the fees
    - Redemption must happen not during epoch
    - Fees are set and calculated offchain
    - The vaults have funding, trading and withdraw phases and you can only deposit/withdraw on the correct epoch
    - Lockups are up to 30-60 days or so
    - The vault owner can set epochs offhain, up to 10 years

    More information:

    - `Docs <https://gitbook.d2.finance/>`__
    - `HYPE++ strategy blog post <https://medium.com/@D2.Finance/hype-capitalizing-on-hyperliquids-launch-396f8665a2c0>`__

    Whitelist function logic:

    .. code-block:: solidity

            modifier onlyWhitelisted() {
                bool holder = false;
                if (whitelistAsset != address(0)) {
                    holder = IERC20(whitelistAsset).balanceOf(msg.sender) > whitelistBalance;
                }
                require(whitelisted[msg.sender] || holder, "!whitelisted");
                _;
            }

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
        """Non on-chain fee information available.

        - D2 share price is fees-inclusive per them: https://x.com/D2_Finance/status/1988624499588116979
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are internalized in the share price.

        - D2 share price is fees-inclusive per them: https://x.com/D2_Finance/status/1988624499588116979
        """
        return 0.20

    def get_estimated_lock_up(self) -> datetime.timedelta:
        epoch = self.fetch_current_epoch_info()
        return epoch.epoch_end - epoch.epoch_start

    def fetch_deposit_closed_reason(self) -> str | None:
        """Deposits open during isFunding() phase."""
        try:
            is_funding = self.vault_contract.functions.isFunding().call()
            if not is_funding:
                next_open = self.fetch_deposit_next_open()
                if next_open:
                    remaining = next_open - native_datetime_utc_now()
                    hours = remaining.total_seconds() / 3600
                    if hours < 24:
                        return f"{DEPOSIT_CLOSED_FUNDING_PHASE} (opens in {hours:.0f}h)"
                    return f"{DEPOSIT_CLOSED_FUNDING_PHASE} (opens in {hours / 24:.1f}d)"
                return DEPOSIT_CLOSED_FUNDING_PHASE
        except Exception:
            pass
        return None

    def fetch_redemption_closed_reason(self) -> str | None:
        """Redemptions open when notCustodiedAndNotDuringEpoch()."""
        try:
            can_redeem = self.vault_contract.functions.notCustodiedAndNotDuringEpoch().call()
            if not can_redeem:
                next_open = self.fetch_redemption_next_open()
                if next_open:
                    remaining = next_open - native_datetime_utc_now()
                    hours = remaining.total_seconds() / 3600
                    if hours < 24:
                        return f"{REDEMPTION_CLOSED_FUNDS_CUSTODIED} (opens in {hours:.0f}h)"
                    return f"{REDEMPTION_CLOSED_FUNDS_CUSTODIED} (opens in {hours / 24:.1f}d)"
                return REDEMPTION_CLOSED_FUNDS_CUSTODIED
        except Exception:
            pass
        return None

    def fetch_deposit_next_open(self) -> datetime.datetime | None:
        """Get when deposits will next be open.

        - Deposits open at the start of the next funding phase (after epoch ends)
        """
        try:
            if self.vault_contract.functions.isFunding().call():
                return None  # Already open
            epoch = self.fetch_current_epoch_info()
            return epoch.epoch_end  # Next funding starts after epoch ends
        except Exception:
            return None

    def fetch_redemption_next_open(self) -> datetime.datetime | None:
        """Get when withdrawals will next be open.

        - Redemptions open when funds are not custodied and not during epoch
        """
        try:
            if self.vault_contract.functions.notCustodiedAndNotDuringEpoch().call():
                return None  # Already open
            epoch = self.fetch_current_epoch_info()
            return epoch.epoch_end
        except Exception:
            return None
