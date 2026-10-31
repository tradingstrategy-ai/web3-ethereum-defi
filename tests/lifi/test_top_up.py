"""Funding options and ERC-20 execution boundaries without live transfers."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

# ruff: noqa: PLR2004 -- explicit chain IDs and raw USDC units are test expectations.
from hexbytes import HexBytes

from eth_defi.lifi import crosschain, top_up
from eth_defi.lifi.constants import LIFI_NATIVE_TOKEN_ADDRESS
from eth_defi.token import USDC_NATIVE_TOKEN


@pytest.fixture(autouse=True)
def config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a dummy key and isolate configuration from the private test environment."""
    for name in ("SOURCE_CHAIN", "TARGET_CHAINS", "ETHEREUM_USDC_AMOUNT", "SOURCE_TOKEN", "DRY_RUN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PRIVATE_KEY", "unused-in-unit-tests")


def test_funding_defaults() -> None:
    """Ordinary gas funding never silently adds a USDC transfer."""
    config = top_up.read_top_up_config()
    assert config.source_chain_id == 42161
    assert config.target_chain_ids == [1, 999]
    assert config.ethereum_usdc_amount == 0
    assert config.source_token_address == LIFI_NATIVE_TOKEN_ADDRESS


def test_delivery_status_uses_prefixed_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """A completed bridge must not remain pending because its hash lacks ``0x``.

    The live Ethereum to HyperEVM transfer on 2026-10-27 returned pending for
    the unprefixed hash, while the same prefixed hash returned completed.
    """
    tx_hash = HexBytes("0x" + "12" * 32)
    result = Mock(tx_hash=tx_hash)
    result.swap.source_chain_id = 1
    result.swap.target_chain_id = 999
    fetch = Mock(return_value={"status": "DONE", "substatus": "COMPLETED"})
    monkeypatch.setattr(crosschain, "fetch_lifi_status", fetch)
    statuses = crosschain.wait_crosschain_swaps([result], max_wait_seconds=1, progress=False)
    fetch.assert_called_once_with(tx_hash=tx_hash.to_0x_hex(), from_chain_id=1, to_chain_id=999)
    assert statuses[tx_hash]["substatus"] == "COMPLETED"


@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity"])
def test_invalid_usdc_amount(monkeypatch: pytest.MonkeyPatch, amount: str) -> None:
    """Invalid spending limits fail before connections or quotes."""
    monkeypatch.setenv("ETHEREUM_USDC_AMOUNT", amount)
    with pytest.raises(ValueError, match="finite and non-negative"):
        top_up.read_top_up_config()


def test_usdc_requires_cross_chain_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ethereum cannot be both ends of the explicit transfer."""
    monkeypatch.setenv("ETHEREUM_USDC_AMOUNT", "5")
    monkeypatch.setenv("SOURCE_CHAIN", "ethereum")
    with pytest.raises(ValueError, match="non-Ethereum"):
        top_up.read_top_up_config()


def test_usdc_only_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty gas target list still connects to the USDC destination."""
    monkeypatch.setenv("ETHEREUM_USDC_AMOUNT", "5")
    monkeypatch.setenv("TARGET_CHAINS", "")
    config = top_up.read_top_up_config()
    monkeypatch.setattr(top_up, "read_json_rpc_url", str)
    monkeypatch.setattr(top_up, "create_multi_provider_web3", lambda rpc: SimpleNamespace(eth=SimpleNamespace(chain_id=int(rpc))))
    monkeypatch.setattr(top_up.HotWallet, "from_private_key", Mock())
    _, targets, _ = top_up.create_connections(config)
    assert set(targets) == {1}
    assert config.target_chain_ids == []


def test_usdc_quote_uses_canonical_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """The explicit spend uses USDC units and the same recipient, independently of gas."""
    source, destination = Mock(), Mock()
    source.eth.chain_id, destination.eth.chain_id = 42161, 1
    tokens = {c: Mock(address=USDC_NATIVE_TOKEN[c]) for c in (42161, 1)}
    tokens[42161].convert_to_raw.return_value = 5_000_000
    tokens[42161].convert_to_decimals.return_value = Decimal(5)
    tokens[42161].fetch_balance_of.return_value = Decimal(6)
    tokens[1].fetch_balance_of.return_value = Decimal(50)
    monkeypatch.setattr(top_up, "fetch_erc20_details", lambda w, _a: tokens[w.eth.chain_id])
    quote = Mock(estimate_to_amount_min=4_800_000)
    fetch = Mock(return_value=quote)
    monkeypatch.setattr(top_up, "fetch_lifi_quote", fetch)
    wallet = Mock(address="0x1234567890123456789012345678901234567890")
    swap = top_up.prepare_ethereum_usdc_transfer(source, destination, wallet, Decimal(5))
    assert swap.from_amount_raw == 5_000_000
    assert fetch.call_args.kwargs["from_token"] == USDC_NATIVE_TOKEN[42161]
    assert fetch.call_args.kwargs["to_token"] == USDC_NATIVE_TOKEN[1]
    assert fetch.call_args.kwargs["to_address"] == wallet.address
    tokens[42161].fetch_balance_of.return_value = Decimal(4)
    with pytest.raises(AssertionError, match="Insufficient source USDC"):
        top_up.prepare_ethereum_usdc_transfer(source, destination, wallet, Decimal(5))


def test_usdc_dry_run_with_sufficient_gas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enough destination gas must not suppress the USDC request or enable signing."""
    monkeypatch.setenv("ETHEREUM_USDC_AMOUNT", "5")
    monkeypatch.setenv("DRY_RUN", "true")
    source, destination, wallet = Mock(), Mock(), Mock()
    monkeypatch.setattr(top_up, "create_connections", Mock(return_value=(source, {1: destination, 999: Mock()}, wallet)))
    for name in ("display_balances", "display_proposed_swaps", "setup_console_logging"):
        monkeypatch.setattr(top_up, name, Mock())
    monkeypatch.setattr(top_up, "fetch_erc20_details", Mock())
    monkeypatch.setattr(top_up, "prepare_crosschain_swaps", Mock(return_value=[]))
    prepare = Mock()
    execute = Mock()
    monkeypatch.setattr(top_up, "prepare_ethereum_usdc_transfer", prepare)
    monkeypatch.setattr(top_up, "execute_crosschain_swaps", execute)
    top_up.perform_top_up()
    prepare.assert_called_once_with(source, destination, wallet, Decimal(5))
    execute.assert_not_called()
    wallet.sync_nonce.assert_not_called()


@pytest.mark.parametrize("allowance,approvals", [(0, 1), (5_000_000, 0)])
def test_approve_before_bridge(monkeypatch: pytest.MonkeyPatch, allowance: int, approvals: int) -> None:
    """Only a short allowance produces an approval, confirmed before the bridge."""
    web3, wallet, token = Mock(), Mock(), Mock(symbol="USDC")
    web3.eth.chain_id = 42161
    token.fetch_raw_balance_of.return_value = 5_000_000
    token.contract.functions.allowance.return_value.call.return_value = allowance
    token.convert_to_decimals.return_value = Decimal(5)
    spender = "0x1234567890123456789012345678901234567890"
    quote = Mock(from_token=USDC_NATIVE_TOKEN[42161], data={"estimate": {"approvalAddress": spender}})
    swap = crosschain.CrossChainSwap(42161, 1, 5_000_000, Decimal(5), Decimal(0), Decimal(0), Decimal(5), {"to": spender, "data": "0x", "value": "0x0"}, quote)
    monkeypatch.setattr(crosschain, "fetch_erc20_details", Mock(return_value=token))
    monkeypatch.setattr(crosschain, "estimate_gas_price", Mock())
    monkeypatch.setattr(crosschain, "apply_gas", Mock())
    broadcast = Mock(return_value={HexBytes("0x1234"): {}})
    monkeypatch.setattr(crosschain, "broadcast_and_wait_transactions_to_complete", broadcast)
    crosschain.execute_crosschain_swaps(wallet, web3, [swap], progress=False)
    assert wallet.sign_bound_call_with_new_nonce.call_count == approvals
    assert broadcast.call_count == approvals + 1
    if approvals:
        token.approve.assert_called_once_with(spender, Decimal(5))
        assert broadcast.call_args_list[0].args[1] == [wallet.sign_bound_call_with_new_nonce.return_value]
    assert broadcast.call_args_list[-1].args[1] == [wallet.sign_transaction_with_new_nonce.return_value]
    broadcast.reset_mock()
    with pytest.raises(AssertionError, match="Insufficient combined"):
        crosschain.execute_crosschain_swaps(wallet, web3, [swap, swap], progress=False)
    broadcast.assert_not_called()


@pytest.mark.parametrize("status,substatus", [("PENDING", ""), ("FAILED", ""), ("DONE", "PARTIAL"), ("DONE", "REFUNDED")])
def test_unsuccessful_delivery_not_reported_as_funded(monkeypatch: pytest.MonkeyPatch, status: str, substatus: str) -> None:
    """A terminal source transfer does not guarantee the destination asset arrived."""
    result = Mock(tx_hash=HexBytes("0x1234"))
    result.swap.target_chain_id = 1
    result.swap.from_amount_usd = Decimal(5)
    monkeypatch.setattr(top_up, "wait_crosschain_swaps", Mock(return_value={result.tx_hash: {"status": status, "substatus": substatus}}))
    with pytest.raises(RuntimeError, match="not delivered"):
        top_up.verify_and_display_final([result], Mock(), {}, Mock(), Mock())


def test_live_path_checks_observed_usdc_credit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful API status still needs the actual Ethereum USDC credit.

    All signing and delivery are doubles; this exercises the live branch's
    result handling without making external calls or transactions.
    """
    monkeypatch.setenv("ETHEREUM_USDC_AMOUNT", "5")
    monkeypatch.setenv("TARGET_CHAINS", "")
    source, destination, wallet = Mock(), Mock(), Mock()
    monkeypatch.setattr(top_up, "create_connections", Mock(return_value=(source, {1: destination}, wallet)))
    for name in ("display_balances", "display_proposed_swaps", "display_results", "verify_and_display_final", "setup_console_logging"):
        monkeypatch.setattr(top_up, name, Mock())
    token = Mock(address=USDC_NATIVE_TOKEN[1])
    token.fetch_balance_of.side_effect = [Decimal(50), Decimal(54)]
    token.convert_to_decimals.return_value = Decimal("4.9")
    monkeypatch.setattr(top_up, "fetch_erc20_details", Mock(return_value=token))
    monkeypatch.setattr(top_up, "prepare_crosschain_swaps", lambda **_kwargs: [])
    swap = Mock(target_chain_id=1)
    swap.quote.to_token = USDC_NATIVE_TOKEN[1]
    monkeypatch.setattr(top_up, "prepare_ethereum_usdc_transfer", Mock(return_value=swap))
    monkeypatch.setattr(top_up, "execute_crosschain_swaps", Mock(return_value=[SimpleNamespace(swap=swap)]))
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    with pytest.raises(AssertionError, match="not credited"):
        top_up.perform_top_up()
    token.fetch_balance_of.side_effect = [Decimal(50), Decimal(55)]
    top_up.perform_top_up()
