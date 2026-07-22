"""Tests for Lagoon module library deployment selection."""

from contextlib import contextmanager
from types import SimpleNamespace

from _pytest.monkeypatch import MonkeyPatch
from eth_account import Account
from eth_account.signers.local import LocalAccount

from eth_defi.abi import ZERO_ADDRESS
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    deploy_safe_trading_strategy_module,
)


def _make_fake_web3() -> SimpleNamespace:
    """Create a tiny Web3 stub for module deployment tests."""
    return SimpleNamespace(
        eth=SimpleNamespace(
            chain_id=1,
            get_block=lambda _block_id: {"gasLimit": 30_000_000},
        )
    )


def _make_fake_safe() -> SimpleNamespace:
    """Create a tiny Safe stub for module deployment tests."""
    return SimpleNamespace(address="0x1000000000000000000000000000000000000001")


@contextmanager
def _no_op_big_blocks(_web3, _private_key_hex: str):
    """Replace HyperEVM big block toggling in unit tests."""
    yield


def test_deploy_safe_trading_strategy_module_skips_uniswap_library_when_disabled(
    monkeypatch: MonkeyPatch,
):
    """Test UniswapLib deployment is skipped when no Uniswap routes are configured and why.

    1. Build a fake deployment environment with no Uniswap configuration.
    2. Patch contract deployment helpers to capture linked library addresses.
    3. Deploy the module and verify UniswapLib is linked to the zero address.
    """

    # 1. Build a fake deployment environment with no Uniswap configuration.
    web3 = _make_fake_web3()
    safe = _make_fake_safe()
    deployer: LocalAccount = Account.create()
    deploy_calls: list[dict] = []

    # 2. Patch contract deployment helpers to capture linked library addresses.
    def fake_deploy_contract(_web3, contract_name: str, _deployer, *constructor_args, **kwargs):
        deploy_calls.append(
            {
                "contract_name": contract_name,
                "constructor_args": constructor_args,
                "kwargs": kwargs,
            }
        )
        return SimpleNamespace(address=f"0x{len(deploy_calls):040x}")

    monkeypatch.setattr(
        "eth_defi.erc_4626.vault_protocol.lagoon.deployment.deploy_contract",
        fake_deploy_contract,
    )
    monkeypatch.setattr(
        "eth_defi.hyperliquid.block.big_blocks_for_deployment",
        _no_op_big_blocks,
    )

    # 3. Deploy the module and verify UniswapLib is linked to the zero address.
    deploy_safe_trading_strategy_module(
        web3=web3,
        deployer=deployer,
        safe=safe,
        enable_on_safe=False,
        lagoon=False,
    )

    assert [call["contract_name"] for call in deploy_calls] == [
        "safe-integration/TradingStrategyModuleV0.json",
    ]
    assert deploy_calls[0]["kwargs"]["libraries"]["UniswapLib"] == ZERO_ADDRESS
    assert deploy_calls[0]["kwargs"]["libraries"]["LagoonLib"] == ZERO_ADDRESS


def test_deploy_safe_trading_strategy_module_deploys_uniswap_library_when_enabled(
    monkeypatch: MonkeyPatch,
):
    """Test UniswapLib deployment still happens when Uniswap routing is configured and why.

    1. Build a fake deployment environment with Uniswap v3 configured.
    2. Patch contract deployment helpers to capture the deployment sequence.
    3. Deploy the module and verify UniswapLib is deployed and linked.
    """

    # 1. Build a fake deployment environment with Uniswap v3 configured.
    web3 = _make_fake_web3()
    safe = _make_fake_safe()
    deployer: LocalAccount = Account.create()
    deploy_calls: list[dict] = []

    # 2. Patch contract deployment helpers to capture the deployment sequence.
    def fake_deploy_contract(_web3, contract_name: str, _deployer, *constructor_args, **kwargs):
        deploy_calls.append(
            {
                "contract_name": contract_name,
                "constructor_args": constructor_args,
                "kwargs": kwargs,
            }
        )
        return SimpleNamespace(address=f"0x{len(deploy_calls):040x}")

    monkeypatch.setattr(
        "eth_defi.erc_4626.vault_protocol.lagoon.deployment.deploy_contract",
        fake_deploy_contract,
    )
    monkeypatch.setattr(
        "eth_defi.hyperliquid.block.big_blocks_for_deployment",
        _no_op_big_blocks,
    )

    # 3. Deploy the module and verify UniswapLib is deployed and linked.
    deploy_safe_trading_strategy_module(
        web3=web3,
        deployer=deployer,
        safe=safe,
        enable_on_safe=False,
        uniswap_v3=SimpleNamespace(swap_router=SimpleNamespace(address="0x3000000000000000000000000000000000000003")),
        lagoon=False,
    )

    assert [call["contract_name"] for call in deploy_calls] == [
        "guard/UniswapLib.json",
        "safe-integration/TradingStrategyModuleV0.json",
    ]
    assert deploy_calls[1]["kwargs"]["libraries"]["UniswapLib"] == "0x0000000000000000000000000000000000000001"


def test_deploy_safe_trading_strategy_module_deploys_lagoon_library_by_default(
    monkeypatch: MonkeyPatch,
):
    """Deploy and link LagoonLib by default for source-chain Lagoon modules."""

    web3 = _make_fake_web3()
    safe = _make_fake_safe()
    deployer: LocalAccount = Account.create()
    deploy_calls: list[dict] = []

    def fake_deploy_contract(_web3, contract_name: str, _deployer, *constructor_args, **kwargs):
        deploy_calls.append(
            {
                "contract_name": contract_name,
                "constructor_args": constructor_args,
                "kwargs": kwargs,
            }
        )
        return SimpleNamespace(address=f"0x{len(deploy_calls):040x}")

    monkeypatch.setattr(
        "eth_defi.erc_4626.vault_protocol.lagoon.deployment.deploy_contract",
        fake_deploy_contract,
    )
    monkeypatch.setattr(
        "eth_defi.hyperliquid.block.big_blocks_for_deployment",
        _no_op_big_blocks,
    )

    deploy_safe_trading_strategy_module(
        web3=web3,
        deployer=deployer,
        safe=safe,
        enable_on_safe=False,
    )

    assert [call["contract_name"] for call in deploy_calls] == [
        "guard/LagoonLib.json",
        "safe-integration/TradingStrategyModuleV0.json",
    ]
    assert deploy_calls[1]["kwargs"]["libraries"]["LagoonLib"] == "0x0000000000000000000000000000000000000001"
