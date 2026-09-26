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
excludes a vault by itself: it marks the vault for the agent to research.

Supported probes: Morpho V1 (MetaMorpho), Morpho V2 (idle and adapters only),
Euler Earn, Euler EVK and 40acres. Other protocols get ``unsupported``.
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
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import native_datetime_utc_now
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetailError, fetch_erc20_details
from eth_defi.types import Percent

logger = logging.getLogger(__name__)

#: Morpho Blue singleton, the same address on every chain
MORPHO_BLUE_ADDRESS = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"

#: A position at least this large, as a share of the vault's assets, is checked for suspicious collateral
SUSPICIOUS_POSITION_SHARE: Percent = 0.10

#: Collateral with less DEX liquidity than this, in US dollars, has no practical market
MIN_COLLATERAL_DEX_LIQUIDITY_USD = 50_000

#: Redeemable liquidity below this share of the vault's assets raises an exit liquidity signal
MIN_REDEEMABLE_SHARE: Percent = 0.01

#: Days the liquidity must have stayed low to raise the historical exit liquidity signal
LOW_LIQUIDITY_DAYS = 14

#: DexScreener chain slugs by chain id
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

#: Euler EVK functions not in the committed ABIs; two view functions, per the ABI guide
EVK_ABI = [
    {"name": "cash", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint256"}]},
    {"name": "LTVList", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "address[]"}]},
]

#: Exceptions an individual contract read may raise when a contract does not implement a function
CALL_ERRORS = (ContractLogicError, BadFunctionCallOutput, ValueError, TokenDetailError)


@dataclass(slots=True)
class Exposure:
    """One place a vault's assets are: a Morpho market, an Euler strategy or idle cash."""

    #: Morpho market id, strategy vault address, or ``idle``
    market: str

    #: ``morpho_market``, ``euler_strategy``, ``euler_collateral`` or ``idle``
    kind: str

    #: The vault's assets in this position, in denomination token units
    assets: float

    #: Share of the vault's total assets
    share_of_assets: Percent

    #: Assets the vault could withdraw from this position now, in denomination token units
    redeemable: float | None = None

    #: Market utilisation, borrowed over supplied
    utilisation: Percent | None = None

    #: Collateral token address, if any
    collateral: str | None = None

    #: Collateral token symbol
    collateral_symbol: str | None = None

    #: Collateral token name
    collateral_name: str | None = None

    #: Price oracle address of the market
    oracle: str | None = None

    #: Liquidation loan-to-value
    lltv: float | None = None

    #: When the collateral is itself an ERC-4626 vault share, the vault's underlying asset;
    #: DEX liquidity is then measured for the underlying asset
    collateral_underlying: str | None = None

    #: Largest DEX liquidity of the collateral, or of its underlying asset, in US dollars; ``None`` when unknown
    collateral_dex_liquidity_usd: float | None = None


@dataclass(slots=True)
class VaultFacts:
    """Deterministic facts about one candidate vault."""

    #: Vault id, ``{chain_id}-{address}``
    vault_id: str

    #: Probe used, e.g. ``morpho_v1``, or ``unsupported``
    probe: str

    #: Block the onchain reads used
    block_number: int | None = None

    #: When the facts were read, naive UTC ISO timestamp
    observed_at: str | None = None

    #: Total assets in denomination token units
    total_assets: float | None = None

    #: Assets held idle by the vault
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

    #: Reads that failed
    errors: list[str] = field(default_factory=list)


def fetch_dex_liquidity_usd(chain_id: int, token: HexAddress | str, timeout: float = 20.0) -> float | None:
    """Read the largest DEX pool liquidity of a token from DexScreener.

    See the `DexScreener API <https://docs.dexscreener.com/api/reference>`__.

    :param chain_id:
        Chain of the token.

    :param token:
        Token address.

    :param timeout:
        HTTP timeout in seconds.

    :return:
        Largest pool liquidity in US dollars, 0 when the token has no pools, ``None`` when the lookup failed.
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
    return max(((pair.get("liquidity") or {}).get("usd") or 0.0 for pair in pairs or []), default=0.0)


def _token(web3: Web3, address: str, chain_id: int):
    """Read token details, or ``None`` when the address is not an ERC-20 token."""
    try:
        return fetch_erc20_details(web3, address, chain_id=chain_id)
    except CALL_ERRORS as e:
        logger.info("Not an ERC-20 token %s: %s", address, e)
        return None


def probe_morpho_v1(web3: Web3, vault_address: str, chain_id: int, block: int) -> tuple[list[Exposure], float, float]:
    """Read the markets of a MetaMorpho V1 vault.

    For each market on the withdraw queue: the vault's supply position, the
    market's free liquidity, utilisation, collateral token, oracle and LLTV.
    The vault can redeem the smaller of its position and the market's free
    liquidity from each market.

    See the `MetaMorpho documentation <https://docs.morpho.org/curation/concepts/vault>`__.

    :return:
        Exposures, total assets and idle assets, in denomination token units.
    """
    vault = get_deployed_contract(web3, "morpho/MetaMorpho.json", Web3.to_checksum_address(vault_address))
    morpho = get_deployed_contract(web3, "morpho/MorphoBlue.json", Web3.to_checksum_address(MORPHO_BLUE_ADDRESS))
    asset = _token(web3, vault.functions.asset().call(block_identifier=block), chain_id)
    scale = 10**asset.decimals
    total_assets = vault.functions.totalAssets().call(block_identifier=block) / scale
    idle = asset.contract.functions.balanceOf(vault.address).call(block_identifier=block) / scale

    exposures = []
    for index in range(vault.functions.withdrawQueueLength().call(block_identifier=block)):
        market_id = vault.functions.withdrawQueue(index).call(block_identifier=block)
        loan, collateral, oracle, _irm, lltv = morpho.functions.idToMarketParams(market_id).call(block_identifier=block)
        supply_assets, supply_shares, borrow_assets, _borrow_shares, _last_update, _fee = morpho.functions.market(market_id).call(block_identifier=block)
        position_shares = morpho.functions.position(market_id, vault.address).call(block_identifier=block)[0]
        position = position_shares * supply_assets / supply_shares / scale if supply_shares else 0.0
        if position <= 0:
            continue
        free = (supply_assets - borrow_assets) / scale
        collateral_token = _token(web3, collateral, chain_id) if int(collateral, 16) else None
        exposures.append(
            Exposure(
                market="0x" + market_id.hex(),
                kind="morpho_market" if collateral_token else "idle",
                assets=position,
                share_of_assets=position / total_assets if total_assets else 0.0,
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


def probe_euler_earn(web3: Web3, vault_address: str, chain_id: int, block: int) -> tuple[list[Exposure], float, float]:
    """Read the strategies of an Euler Earn vault.

    Each strategy is an ERC-4626 vault, usually an Euler EVK lending vault.
    ``maxWithdrawFromStrategy`` gives what the Earn vault can pull from it now.

    See the `Euler Earn documentation <https://docs.euler.finance/concepts/core/euler-earn>`__.

    :return:
        Exposures, total assets and idle assets, in denomination token units.
    """
    vault = get_deployed_contract(web3, "euler/EulerEarn.json", Web3.to_checksum_address(vault_address))
    asset = _token(web3, vault.functions.asset().call(block_identifier=block), chain_id)
    scale = 10**asset.decimals
    total_assets = vault.functions.totalAssets().call(block_identifier=block) / scale
    idle = asset.contract.functions.balanceOf(vault.address).call(block_identifier=block) / scale

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
        exposures.append(Exposure(market=strategy, kind="euler_strategy", assets=position_assets, share_of_assets=position_assets / total_assets if total_assets else 0.0, redeemable=min(redeemable, position_assets)))
        exposures.extend(_euler_collateral(web3, strategy, chain_id, block, position_assets / total_assets if total_assets else 0.0))
    return exposures, total_assets, idle


def _euler_collateral(web3: Web3, evk_vault: str, chain_id: int, block: int, share: float) -> list[Exposure]:
    """List the collateral tokens an Euler EVK vault lends against, weighted by the parent's exposure."""
    contract = web3.eth.contract(address=Web3.to_checksum_address(evk_vault), abi=EVK_ABI)
    try:
        collateral_vaults = contract.functions.LTVList().call(block_identifier=block)
    except CALL_ERRORS:
        return []
    exposures = []
    for collateral_vault in collateral_vaults:
        # EVK collateral is itself an ERC-4626 vault; its asset is the collateral token
        try:
            underlying = get_deployed_contract(web3, "lagoon/IERC4626.json", collateral_vault).functions.asset().call(block_identifier=block)
        except CALL_ERRORS:
            underlying = collateral_vault
        token = _token(web3, underlying, chain_id)
        exposures.append(Exposure(market=evk_vault, kind="euler_collateral", assets=0.0, share_of_assets=share, collateral=underlying, collateral_symbol=token.symbol if token else None, collateral_name=token.name if token else None))
    return exposures


def probe_simple_pool(web3: Web3, vault_address: str, chain_id: int, block: int, cash_function: bool) -> tuple[list[Exposure], float, float]:
    """Read a single-pool lending vault: Euler EVK (``cash()``) or 40acres (idle balance).

    :return:
        No exposures for 40acres, the EVK's collateral tokens otherwise; total assets and redeemable cash.
    """
    vault = get_deployed_contract(web3, "lagoon/IERC4626.json", Web3.to_checksum_address(vault_address))
    asset = _token(web3, vault.functions.asset().call(block_identifier=block), chain_id)
    scale = 10**asset.decimals
    total_assets = vault.functions.totalAssets().call(block_identifier=block) / scale
    if cash_function:
        cash = web3.eth.contract(address=vault.address, abi=EVK_ABI).functions.cash().call(block_identifier=block) / scale
        return _euler_collateral(web3, vault_address, chain_id, block, 1.0), total_assets, cash
    return [], total_assets, asset.contract.functions.balanceOf(vault.address).call(block_identifier=block) / scale


def select_probe(protocol_slug: str, features: list[str] | None) -> str:
    """Pick the probe for a vault.

    :param protocol_slug:
        Protocol slug from the vault metadata.

    :param features:
        ERC-4626 feature names from the vault metadata.

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

    TVL changes include deposits and returns, so withdrawals are measured
    from the scanner's withdrawal counters instead.

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
        ``utilisation_median``, ``withdrawal_count`` and ``withdrawal_usd``.
    """
    columns = ["id", "timestamp", "available_liquidity", "utilisation", "total_assets", "daily_withdrawal_count", "daily_withdrawal_usd"]
    table = pq.read_table(prices_path, columns=columns, filters=[("id", "in", vault_ids), ("timestamp", ">=", pd.Timestamp(end_at - datetime.timedelta(days=days)))])
    df = table.to_pandas(ignore_metadata=True)
    result = {}
    for vault_id, rows in df.groupby("id"):
        daily = rows.set_index("timestamp").sort_index().resample("D").last().dropna(subset=["total_assets"])
        share = (daily["available_liquidity"] / daily["total_assets"]).replace([np.inf, -np.inf], np.nan)
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

    :param facts:
        Facts with exposures and history filled in.

    :return:
        Human-readable signals; empty when nothing looks suspicious.
    """
    signals = []
    for exposure in facts.exposures:
        if exposure.collateral and exposure.share_of_assets >= SUSPICIOUS_POSITION_SHARE:
            if exposure.collateral_dex_liquidity_usd is not None and exposure.collateral_dex_liquidity_usd < MIN_COLLATERAL_DEX_LIQUIDITY_USD:
                signals.append(f"{exposure.share_of_assets:.0%} of assets lent against {exposure.collateral_symbol or exposure.collateral} with ${exposure.collateral_dex_liquidity_usd:,.0f} DEX liquidity")
    if facts.redeemable_share is not None and facts.redeemable_share < MIN_REDEEMABLE_SHARE:
        signals.append(f"redeemable liquidity is {facts.redeemable_share:.2%} of assets")
    idle_share = facts.history.get("idle_share_max_14d")
    if facts.probe == "forty_acres" and idle_share is not None and idle_share < MIN_REDEEMABLE_SHARE:
        signals.append(f"free liquidity stayed below {MIN_REDEEMABLE_SHARE:.0%} of assets for {LOW_LIQUIDITY_DAYS} days")
    return signals


def fetch_vault_facts(vault_id: str, protocol_slug: str, features: list[str] | None, dex_cache: dict) -> VaultFacts:
    """Read the onchain facts of one vault.

    :param vault_id:
        ``{chain_id}-{address}``.

    :param protocol_slug:
        Protocol slug.

    :param features:
        ERC-4626 feature names.

    :param dex_cache:
        Shared ``(chain_id, token) -> liquidity`` cache, so each collateral token is looked up once.

    :return:
        Facts; failed reads are recorded in ``errors`` instead of raising.
    """
    chain_id, address = vault_id.split("-", 1)
    chain_id = int(chain_id)
    probe = select_probe(protocol_slug, features)
    facts = VaultFacts(vault_id=vault_id, probe=probe, observed_at=native_datetime_utc_now().isoformat())
    if probe == "unsupported":
        return facts
    try:
        web3 = create_multi_provider_web3(read_json_rpc_url(chain_id))
        block = web3.eth.block_number
        facts.block_number = block
        if probe == "morpho_v1":
            exposures, total, idle = probe_morpho_v1(web3, address, chain_id, block)
            redeemable = idle + sum(exposure.redeemable or 0.0 for exposure in exposures)
        elif probe == "euler_earn":
            exposures, total, idle = probe_euler_earn(web3, address, chain_id, block)
            redeemable = idle + sum(exposure.redeemable or 0.0 for exposure in exposures if exposure.kind == "euler_strategy")
        elif probe in ("euler_evk", "forty_acres"):
            exposures, total, idle = probe_simple_pool(web3, address, chain_id, block, cash_function=probe == "euler_evk")
            redeemable = idle
        else:
            # Morpho V2: adapters hold the assets, and their liquidity is not read yet; idle is a lower bound
            exposures, total, idle = probe_simple_pool(web3, address, chain_id, block, cash_function=False)
            redeemable = None
            facts.errors.append("Morpho V2 adapter liquidity is not probed; redeemable liquidity is unknown")
        facts.exposures, facts.total_assets, facts.idle_assets, facts.redeemable_assets = exposures, total, idle, redeemable
        facts.redeemable_share = redeemable / total if redeemable is not None and total else None
    except (*CALL_ERRORS, ConnectionError, TimeoutError, AssertionError) as e:
        logger.warning("Probe %s failed for %s: %s", probe, vault_id, e)
        facts.errors.append(f"{probe} probe failed: {e}")
    web3 = None
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

    :param candidates:
        Candidate records with ``vault_id``, ``protocol_slug`` and ``features``.

    :param prices_path:
        Vault price Parquet for the liquidity history, or ``None`` to skip it.

    :param end_at:
        Report data date.

    :param max_workers:
        Parallel threads.

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
