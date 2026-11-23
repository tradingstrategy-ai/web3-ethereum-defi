"""Capacity observations at original simulation blocks; no injected liquidity."""

import os
from decimal import Decimal

import pytest

from eth_defi.erc_4626.classification import create_vault_instance, create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.fluid.vault import FluidVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import get_midnight_block

# Preserve the November states rather than replacing these with moving heads.
CASES = [
    (8453, 56688126, "0x4c1aeda9b43efcf1da1d1755b18802aabe90f61e", "euler"),
    (10, 147000000, "0x08dcdbf7bade91ccd42cb2a4ea8e5d199d285957", "40acres"),
    (1, 26402309, "0x04422053addbc9bb2759b248b574e3fca76bc145", "morpho"),
    (1, 26402309, "0xef53663bb775a51181f04d590f88fc38d6bd5751", "ipor"),
    (1, 26402309, "0x7f47b1ced85f5d773cd68ca606ed6f0fee381046", "fluid"),
    (1, 26402309, "0x73b04b604df15a6d920ee62e228548cd88dd3549", "pause"),
    (1, 26402309, "0xb2fda773822e5a04c8a70348d66257dd5cf442db", "pause"),
    (8453, 56688126, "0xf80c0529bd94c773844e459853cd91b9263dd525", "morpho"),
    (8453, 56688126, "0x61bfd6f7df1f72427f472144d043c25d742d145e", "morpho"),
    (8453, 56688126, "0xb99b6df96d4d5448cc0a5b3e0ef7896df9507cf5", "40acres"),
    (43114, 100155045, "0x124d00b1ce4453ffc5a5f65ce83af13a7709bac7", "40acres"),
    (43114, 100155045, "0xc0485c4bafb594ae1457820fb6e5b67e8a04bcfd", "40acres"),
]
RPC_NAMES = {10: "JSON_RPC_OPTIMISM", 1: "JSON_RPC_ETHEREUM", 8453: "JSON_RPC_BASE", 43114: "JSON_RPC_AVALANCHE"}


@pytest.mark.xdist_group("withdrawal-capacity-pinned")
@pytest.mark.parametrize("chain,block,address,kind", CASES)
def test_capacity_source(anvil_fork_pool, chain, block, address, kind):
    rpc = os.environ.get(RPC_NAMES[chain])
    if not rpc:
        pytest.skip(f"{RPC_NAMES[chain]} archive provider needed")
    web3 = anvil_fork_pool.get_web3(rpc, block)
    vault = create_vault_instance_autodetect(web3, address)
    result = vault.fetch_withdrawal_capacity_limit(block)
    assert result.observation["block_number"] == block
    evidence_path = os.environ.get("WITHDRAWAL_CAPACITY_TEST_EVIDENCE")
    if evidence_path:
        import json
        from pathlib import Path

        target = Path(evidence_path)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{chain}-{address}.json").write_text(json.dumps({"adapter": type(vault).__name__, **result.to_dict(), "observation": result.observation}, indent=2))
    token = vault.denomination_token
    total_fn = vault.vault_contract.functions.getTotalAssets if getattr(vault, "multi_asset_like", False) else vault.vault_contract.functions.totalAssets
    total = total_fn().call(block_identifier=block)
    assert result.current_tvl == token.convert_to_decimals(total)
    assert result.tiny_tvl is (token.convert_to_decimals(total) < Decimal("10000"))
    if kind == "pause":
        assert result.pause_warning is True
        assert result.observation["deposits_paused"] is True
    else:
        if kind == "ipor":
            assert result.current_tvl == Decimal("57.010412")
            assert result.current_capacity == Decimal("42.590261")
            assert result.capacity_warning is False
        if kind == "euler":
            assert result.current_tvl == Decimal("455383.420654")
            assert result.current_capacity == Decimal("0")
            assert result.observation["withdrawals_paused"] is False
        assert result.current_capacity is not None, result.capacity_reason
        assert 0 <= result.current_capacity <= result.current_tvl
        if kind == "40acres":
            cash = token.contract.functions.balanceOf(vault.address).call(block_identifier=block)
            assert result.current_capacity == token.convert_to_decimals(min(cash, total))
            assert result.capacity_warning is True
        elif kind == "euler":
            from eth_defi.abi import get_deployed_contract

            cash = get_deployed_contract(web3, "euler/EVault.json", vault.address).functions.cash().call(block_identifier=block)
            assert result.current_capacity == token.convert_to_decimals(min(cash, total))
            assert result.capacity_warning is True
            assert type(vault).__name__ == "EulerVault"
        elif kind == "fluid":
            assert result.current_capacity == Decimal(0)
            assert result.capacity_warning is True


@pytest.mark.xdist_group("fork:ethereum:midnight")
def test_fluid_lite_preserves_tvl_and_idle_usdc(anvil_fork_pool: AnvilForkPool) -> None:
    """Fluid Lite retains observable assets with an unmeasured withdrawal route."""
    rpc = os.environ.get("JSON_RPC_ETHEREUM")
    if not rpc:
        pytest.skip("JSON_RPC_ETHEREUM archive provider needed")
    block = get_midnight_block(1)
    web3 = anvil_fork_pool.get_web3(rpc, block)
    vault = create_vault_instance(
        web3,
        "0x273DA948ACa9261043fbdb2a857bc255ECC29012",
        features={ERC4626Feature.fluid_like},
        default_block_identifier=block,
    )
    assert isinstance(vault, FluidVault)

    result = vault.fetch_withdrawal_capacity_limit(block)

    assert result.observation["status"] == "partial"
    assert result.observation["denomination_symbol"] == "USDC"
    assert "Fluid Lite withdrawal route" in result.observation["reason"]
    assert result.current_tvl == Decimal("36833943.425030")
    assert Decimal(result.observation["idle_assets"]) == Decimal("0.002383")
    assert result.current_capacity is None
    assert result.capacity_warning is None


@pytest.mark.xdist_group("fork:ethereum:midnight")
def test_fluid_native_liquidity_is_not_zero_weth(anvil_fork_pool):
    """Wrapped-token dust is not the native-backed fWETH route's reserve balance."""
    from eth_defi.abi import get_deployed_contract
    from eth_defi.token import WRAPPED_NATIVE_TOKEN

    rpc = os.environ.get("JSON_RPC_ETHEREUM")
    if not rpc:
        pytest.skip("JSON_RPC_ETHEREUM archive provider needed")
    block = get_midnight_block(1)
    web3 = anvil_fork_pool.get_web3(rpc, block)
    address = "0x90551c1795392094FE6D29B758EcCD233cFAa260"
    vault = create_vault_instance_autodetect(web3, address)
    assert vault.denomination_token.address.lower() == WRAPPED_NATIVE_TOKEN[1].lower()
    contract = get_deployed_contract(web3, "fluid/FTokenNativeUnderlying.json", address)
    liquidity = contract.functions.getData().call(block_identifier=block)[0]
    native_cash = web3.eth.get_balance(liquidity, block_identifier=block)
    wrapped_cash = vault.denomination_token.contract.functions.balanceOf(liquidity).call(block_identifier=block)
    assert native_cash == 20_272_873_023_509_267_077_386
    assert wrapped_cash == 100_000_000_000
    result = vault.fetch_withdrawal_capacity_limit(block)
    assert result.observation["status"] == "partial"
    assert result.current_capacity is None
    assert result.capacity_warning is None
    assert result.tiny_tvl is None
    recorded_native_cash = result.observation.get("liquidity_native_cash", result.observation.get("liquidity_cash"))
    assert Decimal(recorded_native_cash) == vault.denomination_token.convert_to_decimals(native_cash)
    evidence_path = os.environ.get("WITHDRAWAL_CAPACITY_TEST_EVIDENCE")
    if evidence_path:
        import json
        from pathlib import Path

        target = Path(evidence_path)
        target.mkdir(parents=True, exist_ok=True)
        (target / "fluid-native.json").write_text(json.dumps({"vault": address, "block": block, "native_cash_raw": native_cash, "wrapped_cash_raw": wrapped_cash, **result.to_dict(), "observation": result.observation}, indent=2))
