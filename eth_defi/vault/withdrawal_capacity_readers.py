"""Block-pinned measurement helpers reused by protocol adapters.

Exporters do not select protocols: adapters opt into these readers only when
their verified withdrawal path matches the measurement. Contract bindings use
complete verified ABI files; fetch provenance is recorded in the Euler, Fluid
and IPOR ABI-folder README inventories, never in hand-declared view stubs.
"""

import logging
from concurrent.futures import Future
from decimal import Decimal
from typing import Any

from eth_typing import BlockIdentifier, ChecksumAddress
from requests.exceptions import RequestException
from web3 import Web3
from web3.contract.contract import Contract, ContractFunction
from web3.exceptions import ABIFunctionNotFound, BadFunctionCallOutput, ContractLogicError, Web3Exception

from eth_defi.abi import get_deployed_contract
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.token import BRIDGED_USDC_TOKEN, USDC_NATIVE_TOKEN, USDT_NATIVE_TOKEN, WRAPPED_NATIVE_TOKEN, TokenDetails
from eth_defi.vault.base import VaultBase

logger = logging.getLogger(__name__)

#: The Harvest vault and strategy proxies both use the EIP-1967 implementation slot.
_HARVEST_IMPLEMENTATION_SLOT = int.from_bytes(Web3.keccak(text="eip1967.proxy.implementation")) - 1

#: Runtime code identities checked against Base block 62,552,749.
_HARVEST_VAULT_PROXY_HASH = "fbc60368b1ab806d8f3620062d3ed319bb66d2ecfe9ee92e3d5c29438fc6ee1f"
_HARVEST_VAULT_IMPL_HASH = "b58f9758249024b060c49151103446b2aad64a814b23c9c7d8b64f0fb5e8e5ac"
_HARVEST_STRATEGY = "0x1d59868D7767d703929393bDaB313302840f533c"
_HARVEST_STRATEGY_PROXY_HASH = "936ab4e26dfce3cfa33ba0cc75ba979985c7758682f996fe8752f64af3882e5c"
_HARVEST_STRATEGY_IMPL_HASH = "9bef50734fbf017647ac67d6d81fb6845c063a8956973dfc112efb3d0867c1ee"
_HARVEST_FORTY_ACRES_POOL = "0xB99B6dF96d4d5448cC0a5B3e0ef7896df9507Cf5"
_HARVEST_FORTY_ACRES_POOL_HASH = "c4e71555e8031fe5cf27f448779deb40893c85f1f27fe9f0873cf0892346adcf"

#: Verified fToken configuration tuple length.
FLUID_DATA_FIELDS = 9
#: ABI encoding of the five-word Morpho market parameters.
MORPHO_MARKET_ROUTE_BYTES = 160


def erc4626_observation(vault: VaultBase, block: int) -> dict[str, Any]:
    """Read TVL and idle cash without claiming idle cash is the full exit route."""
    contract = vault.vault_contract
    token = vault.denomination_token
    total = _read(vault.web3, contract.functions.totalAssets(), block)
    cash = _read(vault.web3, token.contract.functions.balanceOf(vault.address), block)
    data = {"status": "partial", "current_tvl": str(token.convert_to_decimals(total)), "current_capacity": None, "idle_assets": str(token.convert_to_decimals(min(cash, total))), "denomination_symbol": token.symbol, "denomination_address": token.address, "reason": "Idle cash alone does not establish the complete withdrawal capacity", "deposits_paused": None, "withdrawals_paused": None}
    return _with_usd_tvl(data, token)


def direct_cash_observation(vault: VaultBase, block: int) -> dict[str, Any]:
    """Measure a verified direct-underlying payout, capped by asset entitlement."""
    data = erc4626_observation(vault, block)
    data.update(status="measured", current_capacity=data.pop("idle_assets"), reason="Withdrawals pay directly from the vault denomination-token balance")
    return data


def _reviewed_harvest_proxy(web3: Web3, address: ChecksumAddress, proxy_hash: str, implementation_hash: str, block: int) -> bool:
    """Match both the Harvest forwarding code and its current implementation."""
    if Web3.keccak(web3.eth.get_code(address, block)).hex() != proxy_hash:
        return False
    slot = web3.eth.get_storage_at(address, _HARVEST_IMPLEMENTATION_SLOT, block)
    implementation = Web3.to_checksum_address(slot[-20:])
    return Web3.keccak(web3.eth.get_code(implementation, block)).hex() == implementation_hash


def _harvest_route_mismatch(web3: Web3, wrapper: Contract, strategy: Contract, pool: Contract, block: int) -> str | None:
    """Identify changed code or configuration before using reviewed cash semantics."""
    if not _reviewed_harvest_proxy(web3, wrapper.address, _HARVEST_VAULT_PROXY_HASH, _HARVEST_VAULT_IMPL_HASH, block):
        return "Harvest vault code changed"
    if _read(web3, wrapper.functions.strategy(), block) != strategy.address:
        return "Harvest active strategy changed"
    if not _reviewed_harvest_proxy(web3, strategy.address, _HARVEST_STRATEGY_PROXY_HASH, _HARVEST_STRATEGY_IMPL_HASH, block):
        return "Harvest strategy code changed"
    # Harvest's 40 Acres strategy inherits the lending integration from Euler.
    if _read(web3, strategy.functions.eulerVault(), block) != pool.address or _read(web3, strategy.functions.vault(), block) != wrapper.address:
        return "Harvest strategy parent vault or lending pool changed"
    if Web3.keccak(web3.eth.get_code(pool.address, block)).hex() != _HARVEST_FORTY_ACRES_POOL_HASH:
        return "40 Acres pool code changed"
    usdc = Web3.to_checksum_address(USDC_NATIVE_TOKEN[8453])
    if any(_read(web3, call, block) != usdc for call in (wrapper.functions.asset(), strategy.functions.underlying(), pool.functions.asset())):
        return "Harvest route denomination changed"
    return None


def harvest_forty_acres_observation(vault: VaultBase, block_identifier: BlockIdentifier) -> dict[str, Any]:  # ruff: ignore[too-many-locals] - Keep same-block cash and entitlement reads together.
    """Read the reviewed Harvest Base strategy's gross USDC withdrawal buffer.

    The strategy uses its idle USDC first, then withdraws the shortfall from
    40 Acres. Cap the pool contribution by both the strategy's share
    entitlement and the pool's reported assets. Report gross USDC before the
    holder's share rounding and fee accrual. The in-kind path pays separately
    in pool shares. Code pinning guards these assumptions against upgrades.

    :param vault: Harvest USDC vault with the reviewed deployment address.
    :param block_identifier: Block for verifying the route and reading balances.
    :return: Capacity observation with structural warning for a verified route.
    """
    web3 = vault.web3
    block = block_identifier if isinstance(block_identifier, int) else web3.eth.get_block(block_identifier)["number"]
    address = Web3.to_checksum_address(vault.address)
    pool = get_deployed_contract(web3, "forty_acres/Vault.json", _HARVEST_FORTY_ACRES_POOL)
    wrapper = get_deployed_contract(web3, "harvest/VaultV2InKind.json", address)
    strategy = get_deployed_contract(web3, "harvest/FortyAcresLendStrategy.json", _HARVEST_STRATEGY)
    try:
        mismatch = _harvest_route_mismatch(web3, wrapper, strategy, pool, block)
    except (Web3Exception, ExtraValueError, RequestException) as error:
        logger.warning("Harvest 40 Acres route verification inconclusive for %s at block %d (%s)", address, block, type(error).__name__)
        return {"status": "failed", "reason": "Harvest 40 Acres route could not be verified at the observation block"}
    if mismatch:
        logger.warning("Harvest Base withdrawal notice needs review for %s at block %d: %s", address, block, mismatch)
        return {"status": "unsupported", "structural_warning": False, "reason": mismatch}

    data = {"status": "partial", "structural_warning": True, "reason": "Harvest 40 Acres cash withdrawal capacity is unknown"}
    try:
        data.update(erc4626_observation(vault, block))
        data["redeem_in_kind_enabled"] = _read(web3, wrapper.functions.redeemInKindEnabled(), block)
        if _read(web3, wrapper.functions.compoundOnWithdraw(), block):
            data["reason"] = "Harvest compounds before withdrawal, which can change the cash available for redemption"
            return data

        token = vault.denomination_token
        balance_of = token.contract.functions.balanceOf
        pool_cash = _read(web3, balance_of(pool.address), block)
        pool_assets = _read(web3, pool.functions.totalAssets(), block)
        pool_shares = _read(web3, pool.functions.balanceOf(strategy.address), block)
        position = _read(web3, pool.functions.convertToAssets(pool_shares), block)
        strategy_cash = _read(web3, balance_of(strategy.address), block)
    except (Web3Exception, ExtraValueError, RequestException) as error:
        logger.warning("Harvest 40 Acres capacity read failed for %s at block %d (%s)", address, block, type(error).__name__)
        data["reason"] = "Harvest 40 Acres route verified but cash withdrawal capacity could not be read"
        return data

    total = token.convert_to_raw(Decimal(data["current_tvl"]))
    idle = token.convert_to_raw(Decimal(data["idle_assets"]))
    capacity = min(total, idle + strategy_cash + min(pool_cash, pool_assets, position))
    data.update(
        status="measured",
        current_capacity=str(token.convert_to_decimals(capacity)),
        reason="Gross USDC before fee accrual and share rounding: Harvest and strategy idle cash plus the share of available 40 Acres cash accessible to the strategy",
    )
    return data


def morpho_v1_capacity(web3: Web3, address: str, block: int, token: TokenDetails) -> int:
    """Sum queue-accessible market cash capped by this vault's supplied position.

    Stored Morpho totals exclude unaccrued interest, so the calculation rounds
    conservatively and never includes liquidity from markets outside the queue.
    :return: Conservative raw denomination assets, excluding stray direct token donations.
    """
    contract = get_deployed_contract(web3, "morpho/MetaMorpho.json", address)
    morpho = get_deployed_contract(web3, "morpho/MorphoBlue.json", _read(web3, contract.functions.MORPHO(), block))
    result = 0
    visited = set()
    for index in range(_read(web3, contract.functions.withdrawQueueLength(), block)):
        market = _read(web3, contract.functions.withdrawQueue(index), block)
        if market in visited:
            continue
        visited.add(market)
        supply, shares, borrow, *_ = _read(web3, morpho.functions.market(market), block)
        position = _read(web3, morpho.functions.position(market, address), block)[0]
        # Morpho Blue includes one virtual asset and 1e6 virtual shares.
        supplied = position * (supply + 1) // (shares + 10**6)
        result += min(supplied, max(0, supply - borrow))
    return min(result, _read(web3, token.contract.functions.balanceOf(morpho.address), block))


def morpho_v1_observation(vault: VaultBase, block: int) -> dict[str, Any]:
    """Measure a MetaMorpho V1 queue's conservative immediate asset buffer."""
    data = erc4626_observation(vault, block)
    amount = morpho_v1_capacity(vault.web3, vault.address, block, vault.denomination_token)
    total = vault.denomination_token.convert_to_raw(Decimal(data["current_tvl"]))
    data.update(status="measured", current_capacity=str(vault.denomination_token.convert_to_decimals(min(amount, total))), reason="Withdrawal-queue market liquidity, capped by supplied positions; stored-interest conservative estimate", deposits_paused=False, withdrawals_paused=False)
    return data


def upshift_observation(vault: VaultBase, block: int) -> dict[str, Any]:
    """Keep deposit-only pauses distinct from withdrawal capacity."""
    contract = vault.upshift_contract
    total_fn = contract.functions.getTotalAssets if vault.multi_asset_like else contract.functions.totalAssets
    total = _read(vault.web3, total_fn(), block)
    data = {"status": "partial", "current_tvl": str(vault.denomination_token.convert_to_decimals(total)), "current_capacity": None, "denomination_symbol": vault.denomination_token.symbol, "denomination_address": vault.denomination_token.address, "reason": "Async or protocol-specific withdrawal capacity is not established by a deposit pause"}

    for key, name in [("deposits_paused", "depositsPaused"), ("withdrawals_paused", "withdrawalsPaused")]:
        try:
            data[key] = _read(vault.web3, getattr(contract.functions, name)(), block)
        except (ContractLogicError, BadFunctionCallOutput, ABIFunctionNotFound):
            data[key] = None
    if data["withdrawals_paused"]:
        data.update(status="measured", current_capacity="0", reason="Withdrawals are administratively paused")
    return _with_usd_tvl(data, vault.denomination_token)


def ipor_observation(vault: VaultBase, block: int) -> dict[str, Any]:
    """Include IPOR fuse uncertainty instead of equating idle cash with exits."""
    data = erc4626_observation(vault, block)
    contract = get_deployed_contract(vault.web3, "ipor/PlasmaVaultBase.json", vault.address)
    fuses = _read(vault.web3, contract.functions.getInstantWithdrawalFuses(), block)
    if not fuses:
        reserved = 0
        # PlasmaVaultStorageLib corrected slot and deployed legacy fallback.
        # A configured manager can reserve idle assets for released requests.
        for slot in ("0x465d2ff0062318fe6f4c7e9ac78cfcd70bc86a1d992722875ef83a9770513100", "0xb37e8684757599da669b8aea811ee2b3693b2582d2c730fab3f4965fa2ec3e11"):
            word = vault.web3.eth.get_storage_at(vault.address, slot, block_identifier=block)
            if int.from_bytes(word[-20:]):
                manager = get_deployed_contract(vault.web3, "ipor/WithdrawManager.json", Web3.to_checksum_address(word[-20:]))
                shares = _read(vault.web3, manager.functions.getSharesToRelease(), block)
                reserved = _read(vault.web3, vault.vault_contract.functions.convertToAssets(shares), block)
                break
        cash = vault.denomination_token.convert_to_raw(Decimal(data.pop("idle_assets")))
        data.update(status="measured", current_capacity=str(vault.denomination_token.convert_to_decimals(max(0, cash - reserved))), reason="IPOR has no instant withdrawal fuses; idle denomination assets minus assets reserved for released withdrawal requests")
    else:
        data["reason"] = f"IPOR has {len(fuses)} instant withdrawal fuses; global accessible market capacity has not been established"
    return data


def fluid_observation(vault: VaultBase, block: int) -> dict[str, Any]:
    """Prove zero Fluid liquidity without substituting an owner-bound maximum.

    Nonzero liquidity still requires the deployment-specific withdrawal-limit
    resolver. That case remains partial rather than overstating cash availability.
    """

    data = erc4626_observation(vault, block)
    fluid = get_deployed_contract(vault.web3, "fluid/iToken.json", vault.address)
    raw = _read(vault.web3, fluid.functions.getData(), block)
    if len(raw) != FLUID_DATA_FIELDS:
        data["reason"] = "Unrecognised Fluid liquidity configuration"
        return data
    liquidity = Web3.to_checksum_address(raw[0])
    # Native-underlying fTokens expose the sentinel; getData's boolean is
    # rewardsActive, not a native-asset flag. WETH.balanceOf(Liquidity) does
    # not measure the native currency that these contracts wrap on withdrawal.
    try:
        native_contract = get_deployed_contract(vault.web3, "fluid/FTokenNativeUnderlying.json", vault.address)
        native = _read(vault.web3, native_contract.functions.NATIVE_TOKEN_ADDRESS(), block)
    except (ContractLogicError, BadFunctionCallOutput, ABIFunctionNotFound, ExtraValueError):
        native = None
    if native is not None:
        if native.lower() != "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee":
            data["reason"] = "Fluid returned an unexpected native-token address; withdrawal capacity is unknown"
            return data
        cash = vault.web3.eth.get_balance(liquidity, block_identifier=block)
        data["liquidity_asset"] = native
    else:
        wrapped_native = WRAPPED_NATIVE_TOKEN.get(vault.chain_id)
        if wrapped_native is None or vault.denomination_token.address.lower() == wrapped_native.lower():
            if wrapped_native is not None:
                data["liquidity_native_cash"] = str(vault.denomination_token.convert_to_decimals(vault.web3.eth.get_balance(liquidity, block_identifier=block)))
            data["reason"] = "Fluid's liquidity asset could not be confirmed; a wrapped-token balance does not establish native liquidity"
            return data
        cash = _read(vault.web3, vault.denomination_token.contract.functions.balanceOf(liquidity), block)
        data["liquidity_asset"] = vault.denomination_token.address
    data["liquidity_cash"] = str(vault.denomination_token.convert_to_decimals(cash))
    if cash == 0:
        data.update(status="measured", current_capacity="0", reason="Fluid liquidity contract has zero denomination assets available for withdrawal")
    else:
        data["reason"] = "Fluid liquidity cash is nonzero; expanding withdrawal limits must be resolved before reporting complete capacity"
    return data


def morpho_v2_observation(vault: VaultBase, block: int) -> dict[str, Any]:  # ruff: ignore[too-many-locals] - Two verified route shapes share the same observation.
    """Measure only liquidity reachable through the configured ordinary exit route."""

    data = erc4626_observation(vault, block)
    outer = get_deployed_contract(vault.web3, "morpho/VaultV2.json", vault.address)
    address = _read(vault.web3, outer.functions.liquidityAdapter(), block)
    token = vault.denomination_token
    idle = _read(vault.web3, token.contract.functions.balanceOf(vault.address), block)
    available = idle
    try:  # ruff: ignore[too-many-statements-in-try-clause] - Treat unsupported ABI reads across either route as one partial observation.
        if int(address, 16):
            route = _read(vault.web3, outer.functions.liquidityData(), block)
            if len(route) == MORPHO_MARKET_ROUTE_BYTES:
                adapter = get_deployed_contract(vault.web3, "morpho/MorphoMarketV1AdapterV2.json", address)
                if _read(vault.web3, adapter.functions.parentVault(), block).lower() != vault.address.lower():
                    data["reason"] = "Liquidity adapter parent does not match the outer vault"
                    return data
                market_id = Web3.keccak(route)
                position = _read(vault.web3, adapter.functions.expectedSupplyAssets(market_id), block)
                blue = get_deployed_contract(vault.web3, "morpho/MorphoBlue.json", _read(vault.web3, adapter.functions.morpho(), block))
                supply, _, borrow, *_ = _read(vault.web3, blue.functions.market(market_id), block)
                available += min(position, max(0, supply - borrow))
            else:
                adapter = get_deployed_contract(vault.web3, "morpho/MorphoVaultV1Adapter.json", address)
                try:
                    inner_address = _read(vault.web3, adapter.functions.morphoVaultV1(), block)
                except (ContractLogicError, BadFunctionCallOutput):
                    data["reason"] = "Unsupported Morpho V2 liquidity adapter; accessible capacity is unknown"
                    return data
                if _read(vault.web3, adapter.functions.parentVault(), block).lower() != vault.address.lower():
                    data["reason"] = "Liquidity adapter parent does not match the outer vault"
                    return data
                inner = get_deployed_contract(vault.web3, "morpho/MetaMorpho.json", inner_address)
                # This is the real route owner, not an arbitrary zero-share account.
                available += _read(vault.web3, inner.functions.maxWithdraw(address), block)

    except (ContractLogicError, BadFunctionCallOutput, ABIFunctionNotFound, ExtraValueError):
        data["reason"] = "Unsupported Morpho V2 route interface; accessible capacity is unknown"
        return data

    total = token.convert_to_raw(Decimal(data["current_tvl"]))
    data.update(status="measured", current_capacity=str(token.convert_to_decimals(min(total, available))), reason="Idle assets plus the configured Morpho liquidity route, capped by supplied assets and market cash; paid recovery excluded")
    return data


def euler_observation(vault: VaultBase, block: int) -> dict[str, Any]:
    """Measure EVK cash and respect globally disabled/custom withdrawal hooks.

    ``cash()`` reports accounted, unborrowed assets; arbitrary token donations
    and the owner's share balance are not withdrawal capacity. Hook bits follow
    Euler Vault Kit's Constants.sol (deposit/mint 0/1, withdraw/redeem 2/3).
    """
    data = erc4626_observation(vault, block)
    contract = get_deployed_contract(vault.web3, "euler/EVault.json", vault.address)
    cash = _read(vault.web3, contract.functions.cash(), block)
    target, operations = vault.fetch_hook_config(block)
    custom = int(target, 16) != 0
    withdrawal_bits = (1 << 2) | (1 << 3)
    deposit_bits = (1 << 0) | (1 << 1)
    withdrawals_paused = not custom and operations & withdrawal_bits == withdrawal_bits
    data.update(deposits_paused=(None if custom and operations & deposit_bits else operations & deposit_bits == deposit_bits), withdrawals_paused=(None if custom and operations & withdrawal_bits else withdrawals_paused))
    if custom and operations & withdrawal_bits:
        data["reason"] = "Euler custom withdrawal hooks require a separate admission assessment; cash does not establish executable capacity"
        return data
    total = vault.denomination_token.convert_to_raw(Decimal(data["current_tvl"]))
    capacity = 0 if withdrawals_paused else min(cash, total)
    data.update(status="measured", current_capacity=str(vault.denomination_token.convert_to_decimals(capacity)), reason="Euler EVK accounted unborrowed cash, bounded by vault asset entitlement and global withdrawal hooks")
    return data


def _read(web3: Web3, call: ContractFunction, block: int) -> Any:
    """Coalesce identical same-block reads within one refresh observation batch.

    The cache is bound to this Web3 instance and numeric block, never global.
    Futures share both results and provider errors without serialising unrelated
    market reads. Standalone API callers need no cache or mutable global state.
    """
    context = getattr(web3, "_withdrawal_capacity_read_context", None)
    if context is None:
        return call.call(block_identifier=block)

    cache, lock = context
    key = (call.address.lower(), call._encode_transaction_data(), block)
    with lock:
        owner = key not in cache
        if owner:
            cache[key] = Future()
        future = cache[key]
    if owner:
        try:
            future.set_result(call.call(block_identifier=block))
        except BaseException as error:
            # Resolve waiters even when the owner is interrupted, then propagate.
            future.set_exception(error)
            raise
    return future.result()


def _with_usd_tvl(data: dict[str, Any], token: TokenDetails) -> dict[str, Any]:
    """Record USD TVL for recognised native USD stablecoin denominations.

    Reuse the catalogue's nominal USD stablecoin valuation convention, tied to
    verified chain/address maps rather than treating any ticker as USD. This is
    a face-value guideline, not an independent depeg-price oracle. Other assets
    need an explicit same-observation USD valuation; never treat ETH/EUR as USD.
    """
    chain = token.chain_id
    recognised = {str(mapping.get(chain, "")).lower() for mapping in (USDC_NATIVE_TOKEN, USDT_NATIVE_TOKEN, BRIDGED_USDC_TOKEN)}
    if token.address.lower() in recognised and data.get("current_tvl") is not None:
        data["current_tvl_usd"] = data["current_tvl"]
        data["usd_valuation_source"] = "Recognised USD stablecoin face-value guideline (1 denomination token = 1 USD)"
    return data
