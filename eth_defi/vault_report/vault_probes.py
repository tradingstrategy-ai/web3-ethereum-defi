"""Deterministic onchain and API facts for the vault investability check.

The investability check, see :py:mod:`eth_defi.vault_report.vault_checks`,
uses an LLM agent for judgement, but reads everything that code can read
reliably here first:

- where a vault's money is: for Morpho V1 the markets on its withdraw queue,
  for Euler Earn its strategies, with each market's collateral token, oracle,
  loan-to-value and free liquidity;
- how much a depositor could redeem right now: idle assets plus the liquidity
  the vault can pull from its markets or strategies, not only idle assets,
  see ``eth_defi/erc_4626/vault_protocol/README-vault-redeemable.md``;
- DEX liquidity of each collateral token, from `DexScreener <https://docs.dexscreener.com/api/reference>`__;
- liquidity, utilisation and served withdrawals over the last 30 days, from
  the report's vault price Parquet.

The facts also raise deterministic suspicion signals. A signal never
excludes a vault by itself: it marks the vault for the agent to research. The
thresholds are investigation triggers from the plan, not calibrated verdicts.

Supported probes: Morpho V1 (MetaMorpho), Morpho V2 (idle assets only, its
adapters are not read yet), Euler Earn, Euler EVK and 40acres. Other
protocols get ``unsupported``.

Who uses the facts:

- :py:mod:`eth_defi.vault_report.vault_checks` writes them to
  ``vault-check-facts-N.json`` for the agent, and the deterministic
  prescreen of the average yield charts uses only their signals;
- the agent and humans probe a single vault with
  ``scripts/erc-4626/probe-vault-positions.py``.

Design notes:

- All onchain reads of one vault use the same block, the chain head at
  probe time, so positions and totals are consistent with each other. The
  facts describe the vault now, not at the report's data date, which is what
  an exit liquidity judgement needs. Reading the head also works on Monad,
  whose nodes keep only recent state.
- A failed read never aborts the report: it is recorded in
  :py:attr:`VaultFacts.errors` for the agent to see, and the rest of the
  facts are still filled in where possible.
- Reads are plain ``eth_call`` requests through
  :py:func:`~eth_defi.provider.multi_provider.create_multi_provider_web3`
  with the ``JSON_RPC_*`` environment variables, one vault per thread. With a
  few dozen candidates per round this is fast enough without Multicall.
"""

import datetime
import json
import logging
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from eth_typing import HexAddress
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import native_datetime_utc_now
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetailError, TokenDetails, fetch_erc20_details
from eth_defi.types import Percent

logger = logging.getLogger(__name__)

#: A position at least this large, as a share of the vault's assets, is checked for suspicious collateral;
#: smaller positions cannot move a vault's yield or solvency much
SUSPICIOUS_POSITION_SHARE: Percent = 0.10

#: Collateral with less DEX liquidity than this, in US dollars, has no practical market:
#: liquidators could not sell it, so the loans against it cannot be valued at the oracle price.
#: The skill and its thresholds must stay in sync with these constants.
MIN_COLLATERAL_DEX_LIQUIDITY_USD = 50_000

#: Redeemable liquidity below this share of the vault's assets raises an exit liquidity signal
MIN_REDEEMABLE_SHARE: Percent = 0.01

#: Days the liquidity must have stayed low to raise the historical exit liquidity signal;
#: long enough that a temporary full utilisation does not trigger it
LOW_LIQUIDITY_DAYS = 14

#: DexScreener chain slugs by chain id, see the ``chainId`` values of the
#: `DexScreener API <https://docs.dexscreener.com/api/reference>`__;
#: chains missing here get no DEX liquidity figure
DEXSCREENER_CHAINS = {
    1: "ethereum",
    10: "optimism",
    56: "bsc",
    100: "gnosischain",
    130: "unichain",
    137: "polygon",
    143: "monad",
    146: "sonic",
    999: "hyperevm",
    8453: "base",
    9745: "plasma",
    42161: "arbitrum",
    43114: "avalanche",
    59144: "linea",
    80094: "berachain",
    747474: "katana",
}

#: Euler EVK functions not in the committed ABIs: ``cash()``, the assets not lent out, and
#: ``LTVList()``, the collateral vaults the EVK vault accepts. The ABI guide in
#: ``eth_defi/abi/README.md`` allows an inline fragment of at most two functions.
EVK_ABI = [
    {"name": "cash", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint256"}]},
    {"name": "LTVList", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "address[]"}]},
]

#: Exceptions an individual contract read may raise when a contract does not implement a function:
#: a revert, empty return data, an ABI decoding failure or a token without ERC-20 metadata.
#: Caught narrowly so a missing function on one contract is a recorded fact, not a crash.
CALL_ERRORS = (ContractLogicError, BadFunctionCallOutput, ValueError, TokenDetailError)


@dataclass(slots=True)
class Exposure:
    """One place a vault's assets are: a Morpho market, an Euler strategy or one of its collaterals.

    Written to the facts file under ``exposures``; the agent starts its
    collateral research from these, and :py:func:`raise_signals` checks them.
    """

    #: Morpho market id (``0x``-prefixed bytes32), or the Euler strategy or EVK vault address
    market: str

    #: ``morpho_market``, ``euler_strategy``, ``euler_collateral``, or ``idle`` for a
    #: Morpho market without collateral, which curators use to hold idle assets
    kind: str

    #: The vault's assets in this position, in denomination token units;
    #: 0 for ``euler_collateral``, whose exposure is not known
    assets: float

    #: Share of the vault's total assets; for ``euler_collateral`` the share of the whole
    #: EVK vault, an upper bound
    share_of_assets: Percent

    #: Assets the vault could withdraw from this position now, in denomination token units
    redeemable: float | None = None

    #: Market utilisation, borrowed over supplied
    utilisation: Percent | None = None

    #: Collateral token address, if any
    collateral: HexAddress | None = None

    #: Collateral token symbol
    collateral_symbol: str | None = None

    #: Collateral token name
    collateral_name: str | None = None

    #: Price oracle address of the market
    oracle: HexAddress | None = None

    #: Liquidation loan-to-value, as a fraction (Morpho stores it scaled by 1e18)
    lltv: float | None = None

    #: When the collateral is itself an ERC-4626 vault share, the vault's underlying asset;
    #: DEX liquidity is then measured for the underlying asset
    collateral_underlying: HexAddress | None = None

    #: Largest DEX liquidity of the collateral, or of its underlying asset, in US dollars; ``None`` when unknown
    collateral_dex_liquidity_usd: float | None = None


@dataclass(slots=True)
class VaultFacts:
    """Deterministic facts about one candidate vault.

    One entry of the facts file the agent reads. The agent cites the file,
    with :py:attr:`observed_at`, as evidence, so the observation time must
    be the time of the reads.
    """

    #: Vault id, ``{chain_id}-{address}``
    vault_id: str

    #: Probe used, e.g. ``morpho_v1``, or ``unsupported``, see :py:func:`select_probe`
    probe: str

    #: Block the onchain reads used, the chain head when the probe started
    block_number: int | None = None

    #: When the facts were read, naive UTC ISO timestamp; liquidity evidence older than
    #: seven days before the data date is rejected, see
    #: :py:func:`eth_defi.vault_report.vault_checks.read_check_decisions`
    observed_at: str | None = None

    #: Total assets in denomination token units
    total_assets: float | None = None

    #: Assets held idle by the vault: its own denomination token balance, or the
    #: ``cash()`` of an Euler EVK vault
    idle_assets: float | None = None

    #: Assets a depositor could redeem now: idle plus what the vault can pull from its positions
    redeemable_assets: float | None = None

    #: Redeemable assets as a share of total assets
    redeemable_share: Percent | None = None

    #: Where the assets are
    exposures: list[Exposure] = field(default_factory=list)

    #: Liquidity and withdrawal history, see :py:func:`calculate_liquidity_history`
    history: dict = field(default_factory=dict)

    #: Deterministic suspicion signals for the agent to research
    signals: list[str] = field(default_factory=list)

    #: Reads that failed, or facts the probe cannot read; the agent must not take a
    #: missing figure as a good one
    errors: list[str] = field(default_factory=list)


def fetch_dex_liquidity_usd(chain_id: int, token: HexAddress | str, timeout: float = 20.0) -> float | None:
    """Read the largest DEX pool liquidity of a token from DexScreener.

    Calls the public, keyless ``GET /token-pairs/v1/{chainId}/{tokenAddress}``
    endpoint, see the `DexScreener API <https://docs.dexscreener.com/api/reference>`__.
    It is rate limited per IP, which is why callers look each token up only
    once per run, see :py:func:`fetch_vault_facts`.

    The largest single pool, not the sum over pools, is returned: it is what
    a liquidator could sell into in one place, and it is not inflated by
    many dust pools. The result separates "no market" (0, which can raise a
    signal) from "unknown" (``None``, which never does), so an API outage
    cannot make a vault look suspicious.

    :param chain_id:
        Chain of the token.

    :param token:
        Token address.

    :param timeout:
        HTTP timeout in seconds.

    :return:
        Largest pool liquidity in US dollars, 0 when the token has no pools,
        ``None`` when the chain is not on DexScreener or the lookup failed.
    """
    # The per-chain endpoint lists all pools on the chain; the cross-chain one returns only 30 pools in total
    chain = DEXSCREENER_CHAINS.get(chain_id)
    if chain is None:
        return None
    url = f"https://api.dexscreener.com/token-pairs/v1/{chain}/{token}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            pairs = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning("DexScreener lookup failed for %s: %s", token, e)
        return None
    # A pool's liquidity block, or its usd figure, is missing for pools DexScreener cannot price
    return max(((pair.get("liquidity") or {}).get("usd") or 0.0 for pair in pairs or []), default=0.0)


def _token(web3: Web3, address: HexAddress, chain_id: int) -> TokenDetails | None:
    """Read token details, or ``None`` when the address is not an ERC-20 token.

    Market collateral and strategy addresses are not always ERC-20 tokens, so a
    failed read is logged and returns ``None`` instead of raising.

    :param web3:
        Web3 connection of the vault's chain.

    :param address:
        Token address.

    :param chain_id:
        Chain id of the token.

    :return:
        Token details, or ``None`` when the address is not an ERC-20 token.
    """
    try:
        return fetch_erc20_details(web3, address, chain_id=chain_id)
    except CALL_ERRORS as e:
        logger.info("Not an ERC-20 token %s: %s", address, e)
        return None


def _fetch_vault_assets(web3: Web3, vault: Contract, chain_id: int, block: int) -> tuple[TokenDetails, int, float, float]:
    """Read a vault's denomination token and its total and idle assets.

    Idle assets are the denomination token balance the vault holds itself,
    outside its markets or strategies.

    :param web3:
        Web3 connection of the vault's chain.

    :param vault:
        ERC-4626 vault contract.

    :param chain_id:
        Chain id of the vault.

    :param block:
        Block number to read at.

    :return:
        Denomination token, its decimal scale, total assets and idle assets in token units.
    """
    asset = _token(web3, vault.functions.asset().call(block_identifier=block), chain_id)
    assert asset is not None, f"Vault {vault.address} denomination token is not an ERC-20 token"
    scale = 10**asset.decimals
    total_assets = vault.functions.totalAssets().call(block_identifier=block) / scale
    idle = asset.contract.functions.balanceOf(vault.address).call(block_identifier=block) / scale
    return asset, scale, total_assets, idle


def _share(assets: float, total_assets: float) -> float:
    """Share of the vault's total assets, zero for an empty vault."""
    return assets / total_assets if total_assets else 0.0


def fetch_morpho_v1_facts(web3: Web3, vault_address: HexAddress, chain_id: int, block: int) -> tuple[list[Exposure], float, float]:
    """Read the markets of a MetaMorpho V1 vault.

    For each market on the withdraw queue: the vault's supply position, the
    market's free liquidity, utilisation, collateral token, oracle and LLTV.
    The vault can redeem the smaller of its position and the market's free
    liquidity from each market. The withdraw queue is read because a
    MetaMorpho vault can only pull assets from the markets on it.

    Contract reads, all at ``block``:

    - vault ``MORPHO()``, ``withdrawQueueLength()`` and ``withdrawQueue(i)``;
    - Morpho Blue ``idToMarketParams(id)`` for the loan and collateral tokens,
      oracle and LLTV; ``market(id)`` for the total supply and borrow assets
      and supply shares; ``position(id, vault)`` for the vault's supply shares.

    The vault's position is its supply shares converted at the market's
    stored totals, without the interest accrued since the market's last
    update, so it can be slightly low. Free liquidity is total supply minus
    total borrow. A market without a collateral token is an idle market and
    is reported with kind ``idle``.

    See the `MetaMorpho documentation <https://docs.morpho.org/curation/concepts/vault>`__.

    :param web3:
        Web3 connection of the vault's chain.

    :param vault_address:
        Vault address.

    :param chain_id:
        Chain id of the vault.

    :param block:
        Block number to read at, so all reads are consistent.

    :return:
        Exposures, total assets and idle assets, in denomination token units.
    """
    vault = get_deployed_contract(web3, "morpho/MetaMorpho.json", Web3.to_checksum_address(vault_address))
    # Morpho Blue is not at the same address on every chain, so ask the vault
    morpho = get_deployed_contract(web3, "morpho/MorphoBlue.json", vault.functions.MORPHO().call(block_identifier=block))
    _asset, scale, total_assets, idle = _fetch_vault_assets(web3, vault, chain_id, block)

    exposures = []
    for index in range(vault.functions.withdrawQueueLength().call(block_identifier=block)):
        market_id = vault.functions.withdrawQueue(index).call(block_identifier=block)
        loan, collateral, oracle, _irm, lltv = morpho.functions.idToMarketParams(market_id).call(block_identifier=block)
        supply_assets, supply_shares, borrow_assets, _borrow_shares, _last_update, _fee = morpho.functions.market(market_id).call(block_identifier=block)
        position_shares = morpho.functions.position(market_id, vault.address).call(block_identifier=block)[0]
        position = position_shares * supply_assets / supply_shares / scale if supply_shares else 0.0
        # Queued markets the vault has fully left hold none of its assets
        if position <= 0:
            continue
        free = (supply_assets - borrow_assets) / scale
        # The zero address marks an idle market without collateral
        collateral_token = _token(web3, collateral, chain_id) if int(collateral, 16) else None
        exposures.append(
            Exposure(
                market="0x" + market_id.hex(),
                kind="morpho_market" if collateral_token else "idle",
                assets=position,
                share_of_assets=_share(position, total_assets),
                redeemable=min(position, free),
                utilisation=borrow_assets / supply_assets if supply_assets else None,
                collateral=collateral if collateral_token else None,
                collateral_symbol=collateral_token.symbol if collateral_token else None,
                collateral_name=collateral_token.name if collateral_token else None,
                oracle=oracle if int(oracle, 16) else None,
                lltv=lltv / 1e18,
            )
        )
    return exposures, total_assets, idle


def fetch_euler_earn_facts(web3: Web3, vault_address: HexAddress, chain_id: int, block: int) -> tuple[list[Exposure], float, float]:
    """Read the strategies of an Euler Earn vault.

    Each strategy is an ERC-4626 vault, usually an Euler EVK lending vault.
    ``maxWithdrawFromStrategy`` gives what the Earn vault can pull from it now.

    Contract reads, all at ``block``: the Earn vault's ``withdrawQueueLength()``
    and ``withdrawQueue(i)``; each strategy's ``balanceOf(earn vault)`` and
    ``convertToAssets()`` for the position; the Earn vault's
    ``maxWithdrawFromStrategy(strategy)``, or the strategy's
    ``maxWithdraw(earn vault)`` on versions without it, for the redeemable
    amount. For each EVK strategy, its collateral tokens are listed too,
    see :py:func:`_fetch_euler_collateral`.

    See the `Euler Earn documentation <https://docs.euler.finance/concepts/core/euler-earn>`__.

    :param web3:
        Web3 connection of the vault's chain.

    :param vault_address:
        Vault address.

    :param chain_id:
        Chain id of the vault.

    :param block:
        Block number to read at, so all reads are consistent.

    :return:
        Exposures, total assets and idle assets, in denomination token units.
    """
    vault = get_deployed_contract(web3, "euler/EulerEarn.json", Web3.to_checksum_address(vault_address))
    _asset, scale, total_assets, idle = _fetch_vault_assets(web3, vault, chain_id, block)

    exposures = []
    for index in range(vault.functions.withdrawQueueLength().call(block_identifier=block)):
        strategy = vault.functions.withdrawQueue(index).call(block_identifier=block)
        # Read the position from the strategy vault: Euler Earn versions differ in their config() layout
        strategy_vault = get_deployed_contract(web3, "lagoon/IERC4626.json", strategy)
        shares = strategy_vault.functions.balanceOf(vault.address).call(block_identifier=block)
        position_assets = strategy_vault.functions.convertToAssets(shares).call(block_identifier=block) / scale if shares else 0.0
        if position_assets <= 0:
            continue
        # maxWithdrawFromStrategy is missing or reverts on older Euler Earn versions; fall back to the strategy's own limit
        try:
            redeemable = vault.functions.maxWithdrawFromStrategy(strategy).call(block_identifier=block) / scale
        except CALL_ERRORS:
            redeemable = strategy_vault.functions.maxWithdraw(vault.address).call(block_identifier=block) / scale
        share = _share(position_assets, total_assets)
        exposures.append(Exposure(market=strategy, kind="euler_strategy", assets=position_assets, share_of_assets=share, redeemable=min(redeemable, position_assets)))
        exposures.extend(_fetch_euler_collateral(web3, strategy, chain_id, block, share))
    return exposures, total_assets, idle


def _fetch_euler_collateral(web3: Web3, evk_vault: HexAddress, chain_id: int, block: int, share: float) -> list[Exposure]:
    """List the collateral tokens an Euler EVK vault lends against, weighted by the parent's exposure.

    The EVK ``LTVList()`` names the collateral vaults; each is an ERC-4626
    vault whose asset is the collateral token. The vault's exposure to each
    collateral is not known, so every collateral gets the parent's share.
    That overstates the exposure to any one collateral, which is the safe
    direction for a research trigger; :py:func:`raise_signals` words the
    signal accordingly.

    See the `Euler Vault Kit whitepaper <https://github.com/euler-xyz/euler-vault-kit/blob/master/docs/whitepaper.md>`__.

    :param web3:
        Web3 connection of the vault's chain.

    :param evk_vault:
        Euler EVK vault address.

    :param chain_id:
        Chain id of the vault.

    :param block:
        Block number to read at.

    :param share:
        Share of the parent vault's assets in this EVK vault.

    :return:
        One ``euler_collateral`` exposure per collateral token; empty when the
        vault has no ``LTVList()``.
    """
    contract = web3.eth.contract(address=Web3.to_checksum_address(evk_vault), abi=EVK_ABI)
    try:
        collateral_vaults = contract.functions.LTVList().call(block_identifier=block)
    except CALL_ERRORS:
        # A strategy that is not an EVK vault has no LTVList(); it simply lists no collateral
        return []
    exposures = []
    for collateral_vault in collateral_vaults:
        # EVK collateral is itself an ERC-4626 vault; its asset is the collateral token.
        # If it is not a vault, take the address as the token
        try:
            underlying = get_deployed_contract(web3, "lagoon/IERC4626.json", collateral_vault).functions.asset().call(block_identifier=block)
        except CALL_ERRORS:
            underlying = collateral_vault
        token = _token(web3, underlying, chain_id)
        exposures.append(Exposure(market=evk_vault, kind="euler_collateral", assets=0.0, share_of_assets=share, collateral=underlying, collateral_symbol=token.symbol if token else None, collateral_name=token.name if token else None))
    return exposures


def fetch_simple_pool_facts(web3: Web3, vault_address: HexAddress, chain_id: int, block: int, cash_function: bool) -> tuple[list[Exposure], float, float]:
    """Read a single-pool lending vault: Euler EVK (``cash()``) or 40acres (idle balance).

    An Euler EVK vault's redeemable liquidity is its ``cash()``, the assets not
    lent out; other single-pool vaults report their idle balance. Also used as
    a lower bound for Morpho V2 vaults, whose adapters are not probed yet.

    For a 40acres pool the free liquidity depends on loan repayments, so the
    current idle balance alone says little; :py:func:`raise_signals` also uses
    its 14-day history from the price Parquet.

    :param web3:
        Web3 connection of the vault's chain.

    :param vault_address:
        Vault address.

    :param chain_id:
        Chain id of the vault.

    :param block:
        Block number to read at, so all reads are consistent.

    :param cash_function:
        Read redeemable liquidity with the EVK ``cash()`` function and list the
        EVK collateral tokens.

    :return:
        Exposures, total assets and redeemable assets in denomination token
        units: with ``cash_function``, the EVK's collateral tokens at the whole
        vault's share and ``cash()``; otherwise no exposures and the idle balance.
    """
    vault = get_deployed_contract(web3, "lagoon/IERC4626.json", Web3.to_checksum_address(vault_address))
    _asset, scale, total_assets, idle = _fetch_vault_assets(web3, vault, chain_id, block)
    if cash_function:
        cash = web3.eth.contract(address=vault.address, abi=EVK_ABI).functions.cash().call(block_identifier=block) / scale
        return _fetch_euler_collateral(web3, vault_address, chain_id, block, 1.0), total_assets, cash
    return [], total_assets, idle


def select_probe(protocol_slug: str, features: list[str] | None) -> str:
    """Pick the probe for a vault.

    One protocol slug covers several contract families, so the scanner's
    detected ERC-4626 features choose between them: ``morpho_v2_like``
    separates Morpho V2 from MetaMorpho V1, and ``euler_earn_like`` separates
    Euler Earn aggregators from EVK lending pools.

    :param protocol_slug:
        Protocol slug from the vault metadata.

    :param features:
        ERC-4626 feature names from the vault metadata, as in the top vaults JSON.

    :return:
        Probe name.
    """
    features = set(features or [])
    if protocol_slug == "morpho":
        return "morpho_v2" if "morpho_v2_like" in features else "morpho_v1"
    if protocol_slug == "euler":
        return "euler_earn" if "euler_earn_like" in features else "euler_evk"
    if protocol_slug == "40acres":
        return "forty_acres"
    return "unsupported"


def calculate_liquidity_history(prices_path: Path, vault_ids: list[str], end_at: datetime.datetime, days: int = 30) -> dict[str, dict]:
    """Summarise liquidity, utilisation and served withdrawals over recent days.

    The probes see one moment; this history shows whether low liquidity is
    lasting and whether depositors actually got out. TVL changes include
    deposits and returns, so withdrawals are measured from the scanner's
    withdrawal counters instead.

    Only the needed columns and rows are read: the Parquet filters push the
    vault ids and the window down to the reader, which matters for a
    file of several hundred megabytes. The hourly rows are resampled to the
    last value of each day. ``idle_share_max_14d`` is the highest daily
    ``available_liquidity / total_assets`` over the last
    :py:data:`LOW_LIQUIDITY_DAYS` days with data: if even the maximum is
    below the threshold, liquidity stayed low for the whole period.

    :param prices_path:
        Vault price Parquet with ``available_liquidity``, ``utilisation``,
        ``total_assets``, ``daily_withdrawal_count`` and ``daily_withdrawal_usd`` columns.

    :param vault_ids:
        Vaults to summarise.

    :param end_at:
        End of the window.

    :param days:
        Window length.

    :return:
        Vault id -> summary with ``days_with_data``, ``idle_share_max_14d``,
        ``utilisation_median``, ``withdrawal_count`` and ``withdrawal_usd``;
        a figure is ``None`` when its column has no data in the window.
        Vaults without rows in the window are missing.
    """
    columns = ["id", "timestamp", "available_liquidity", "utilisation", "total_assets", "daily_withdrawal_count", "daily_withdrawal_usd"]
    table = pq.read_table(prices_path, columns=columns, filters=[("id", "in", vault_ids), ("timestamp", ">=", pd.Timestamp(end_at - datetime.timedelta(days=days)))])
    df = table.to_pandas(ignore_metadata=True)
    result = {}
    for vault_id, rows in df.groupby("id"):
        daily = rows.set_index("timestamp").sort_index().resample("D").last().dropna(subset=["total_assets"])
        # A zero total_assets gives an infinite share; treat it as missing rather than as ample liquidity
        share = (daily["available_liquidity"] / daily["total_assets"]).replace([np.inf, -np.inf], np.nan)
        # The window is anchored to the vault's last day with data, not to end_at, so a scan lag does not empty it
        recent = share.loc[share.index >= share.index.max() - pd.Timedelta(days=LOW_LIQUIDITY_DAYS)] if len(share) else share
        result[vault_id] = {
            "days_with_data": int(len(daily)),
            "idle_share_max_14d": None if recent.dropna().empty else float(recent.max()),
            "utilisation_median": None if daily["utilisation"].dropna().empty else float(daily["utilisation"].median()),
            "withdrawal_count": None if daily["daily_withdrawal_count"].dropna().empty else int(daily["daily_withdrawal_count"].sum()),
            "withdrawal_usd": None if daily["daily_withdrawal_usd"].dropna().empty else float(daily["daily_withdrawal_usd"].sum()),
        }
    return result


def raise_signals(facts: VaultFacts) -> list[str]:
    """Deterministic suspicion signals for the agent to research.

    Signals are triggers, never verdicts: the agent decides, and a known
    asset with another price source, such as a tokenised fund with a
    primary-market NAV, may have no DEX market and still be fine. In the
    prescreen of the average yield charts a vault with any signal is
    escalated to the agent, so a missing figure must not raise one.

    The signals:

    - **Illiquid collateral.** A position of at least
      :py:data:`SUSPICIOUS_POSITION_SHARE` of the assets lent against a
      collateral token, or its underlying asset, with less than
      :py:data:`MIN_COLLATERAL_DEX_LIQUIDITY_USD` of DEX liquidity. Unknown
      DEX liquidity raises nothing. Duplicates are dropped, because an Euler
      Earn vault can reach the same collateral through several strategies.
    - **No redeemable liquidity now.** Redeemable assets below
      :py:data:`MIN_REDEEMABLE_SHARE` of the total.
    - **Lasting low liquidity, 40acres only.** Free liquidity below
      :py:data:`MIN_REDEEMABLE_SHARE` on every day of the last
      :py:data:`LOW_LIQUIDITY_DAYS` days. For 40acres pools the price
      Parquet's available liquidity is the idle balance, which is what a
      depositor can redeem. For Morpho and Euler Earn it is only the vault's
      own idle balance, not what it can pull from its markets, see
      ``eth_defi/erc_4626/vault_protocol/README-vault-redeemable.md``, so it
      would raise false signals.

    :param facts:
        Facts with exposures and history filled in.

    :return:
        Human-readable signals; empty when nothing looks suspicious.
    """
    signals = []
    for exposure in facts.exposures:
        if exposure.collateral and exposure.share_of_assets >= SUSPICIOUS_POSITION_SHARE:
            if exposure.collateral_dex_liquidity_usd is not None and exposure.collateral_dex_liquidity_usd < MIN_COLLATERAL_DEX_LIQUIDITY_USD:
                symbol = exposure.collateral_symbol or exposure.collateral
                if exposure.kind == "euler_collateral":
                    # An EVK pool accepts several collaterals and its exposure to each one is not known
                    signal = f"{exposure.share_of_assets:.0%} of assets in an Euler pool that accepts {symbol} as collateral, with ${exposure.collateral_dex_liquidity_usd:,.0f} DEX liquidity"
                else:
                    signal = f"{exposure.share_of_assets:.0%} of assets lent against {symbol} with ${exposure.collateral_dex_liquidity_usd:,.0f} DEX liquidity"
                if signal not in signals:
                    signals.append(signal)
    if facts.redeemable_share is not None and facts.redeemable_share < MIN_REDEEMABLE_SHARE:
        signals.append(f"redeemable liquidity is {facts.redeemable_share:.2%} of assets")
    idle_share = facts.history.get("idle_share_max_14d")
    if facts.probe == "forty_acres" and idle_share is not None and idle_share < MIN_REDEEMABLE_SHARE:
        signals.append(f"free liquidity stayed below {MIN_REDEEMABLE_SHARE:.0%} of assets for {LOW_LIQUIDITY_DAYS} days")
    return signals


def fetch_vault_facts(vault_id: str, protocol_slug: str, features: list[str] | None, dex_cache: dict) -> VaultFacts:
    """Read the onchain facts of one vault.

    Runs the probe chosen by :py:func:`select_probe` at the current chain
    head, then derives the vault-wide redeemable liquidity:

    - Morpho V1: idle balance plus what each withdraw-queue market can return;
    - Euler Earn: idle balance plus what each strategy can return; the
      ``euler_collateral`` exposures hold no assets of their own and are
      not added;
    - Euler EVK: ``cash()``; 40acres: the idle balance;
    - Morpho V2: unknown, recorded in ``errors``, because its adapters are
      not read yet.

    Then each collateral token's DEX liquidity is looked up on DexScreener.
    A collateral that is itself an ERC-4626 vault share, e.g. a Morpho vault
    token, has no DEX pools of its own, so its underlying asset is looked up
    instead. That read is at the latest block, not ``block``, which is
    harmless for a vault's immutable ``asset()``.

    Signals are not raised here: they also need the liquidity history,
    which :py:func:`fetch_candidate_facts` adds for all vaults at once.

    :param vault_id:
        ``{chain_id}-{address}``.

    :param protocol_slug:
        Protocol slug.

    :param features:
        ERC-4626 feature names.

    :param dex_cache:
        Shared ``(chain_id, token) -> liquidity`` cache, so each collateral token is looked up once.
        Shared between the probe threads without a lock: a race only costs a duplicate lookup.

    :return:
        Facts; failed reads are recorded in ``errors`` instead of raising.
    """
    chain_id, address = vault_id.split("-", 1)
    chain_id = int(chain_id)
    probe = select_probe(protocol_slug, features)
    facts = VaultFacts(vault_id=vault_id, probe=probe, observed_at=native_datetime_utc_now().isoformat())
    if probe == "unsupported":
        return facts
    web3 = None
    try:
        web3 = create_multi_provider_web3(read_json_rpc_url(chain_id))
        # Pin one block, so all reads of the vault and its markets are consistent
        block = web3.eth.block_number
        facts.block_number = block
        if probe == "morpho_v1":
            exposures, total, idle = fetch_morpho_v1_facts(web3, address, chain_id, block)
            redeemable = idle + sum(exposure.redeemable or 0.0 for exposure in exposures)
        elif probe == "euler_earn":
            exposures, total, idle = fetch_euler_earn_facts(web3, address, chain_id, block)
            redeemable = idle + sum(exposure.redeemable or 0.0 for exposure in exposures if exposure.kind == "euler_strategy")
        elif probe in ("euler_evk", "forty_acres"):
            exposures, total, idle = fetch_simple_pool_facts(web3, address, chain_id, block, cash_function=probe == "euler_evk")
            redeemable = idle
        else:
            # Morpho V2: adapters hold the assets, and their liquidity is not read yet; idle is a lower bound
            exposures, total, idle = fetch_simple_pool_facts(web3, address, chain_id, block, cash_function=False)
            redeemable = None
            facts.errors.append("Morpho V2 adapter liquidity is not probed; redeemable liquidity is unknown")
        facts.exposures, facts.total_assets, facts.idle_assets, facts.redeemable_assets = exposures, total, idle, redeemable
        facts.redeemable_share = redeemable / total if redeemable is not None and total else None
    # OSError covers requests' connection errors and timeouts; the multi-provider setup raises RuntimeError for a dead RPC.
    # AssertionError is a denomination token without ERC-20 details. One broken vault or RPC must not abort the whole
    # report: the error goes into the facts, where the agent sees why a figure is missing.
    except (*CALL_ERRORS, OSError, RuntimeError, AssertionError) as e:
        logger.warning("Probe %s failed for %s: %s", probe, vault_id, e)
        facts.errors.append(f"{probe} probe failed: {e}")
    # Exposures are assigned only after the whole probe succeeded, so after any failure this loop has nothing to do
    for exposure in facts.exposures:
        if not exposure.collateral:
            continue
        # Vault share collateral, e.g. a Morpho vault token, has no DEX pools of its own: look through to its asset
        if web3 is None:
            web3 = create_multi_provider_web3(read_json_rpc_url(chain_id))
        try:
            exposure.collateral_underlying = get_deployed_contract(web3, "lagoon/IERC4626.json", Web3.to_checksum_address(exposure.collateral)).functions.asset().call()
        except CALL_ERRORS:
            exposure.collateral_underlying = None
        token = (exposure.collateral_underlying or exposure.collateral).lower()
        key = (chain_id, token)
        if key not in dex_cache:
            dex_cache[key] = fetch_dex_liquidity_usd(chain_id, token)
        exposure.collateral_dex_liquidity_usd = dex_cache[key]
    return facts


def fetch_candidate_facts(candidates: list[dict], prices_path: Path | None, end_at: datetime.datetime, max_workers: int = 8) -> dict[str, VaultFacts]:
    """Read facts for all in-scope candidates in parallel.

    Called by the investability check for each agent round and for the
    prescreen of the average yield charts. The work is network-bound
    (JSON-RPC and DexScreener), so a joblib threading pool runs one vault per
    thread, with a tqdm progress bar so a slow RPC is visible in the log.
    After the probes, the liquidity history is read from the price Parquet
    in one pass for all vaults, and the signals are raised.

    :param candidates:
        Candidate records with ``vault_id``, ``protocol_slug`` and ``features``,
        e.g. :py:class:`~eth_defi.vault_report.vault_checks.CheckCandidate` as a dict.

    :param prices_path:
        Vault price Parquet for the liquidity history, or ``None`` to skip it.

    :param end_at:
        Report data date.

    :param max_workers:
        Parallel threads, ``MAX_WORKERS`` in the scripts.

    :return:
        Vault id -> facts.
    """
    dex_cache: dict = {}
    results = Parallel(n_jobs=max_workers, backend="threading")(delayed(fetch_vault_facts)(c["vault_id"], c["protocol_slug"], c.get("features"), dex_cache) for c in tqdm(candidates, desc="Probing vaults"))
    facts = {result.vault_id: result for result in results}
    if prices_path is not None and facts:
        history = calculate_liquidity_history(prices_path, list(facts), end_at)
        for vault_id, summary in history.items():
            facts[vault_id].history = summary
    for item in facts.values():
        item.signals = raise_signals(item)
    return facts


def facts_to_json(facts: dict[str, VaultFacts]) -> dict:
    """Convert facts to plain JSON data.

    :param facts:
        Output of :py:func:`fetch_candidate_facts`.

    :return:
        Vault id -> fact dictionary.
    """
    return {vault_id: asdict(item) for vault_id, item in facts.items()}
