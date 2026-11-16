"""Tests for LagoonGMXTradingWallet - GMX trading through Lagoon vaults.

These tests use mocks to verify the wallet logic without requiring
real blockchain access or GMX API calls.
"""

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from hexbytes import HexBytes
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3RPCError


@pytest.fixture
def mock_web3():
    """Mock Web3 instance."""
    web3 = Mock()
    web3.eth.chain_id = 42161  # Arbitrum
    web3.eth.get_balance.return_value = 10**18  # 1 ETH
    return web3


@pytest.fixture
def mock_vault(mock_web3):
    """Mock Lagoon vault."""
    vault = Mock()
    vault.web3 = mock_web3
    vault.vault_address = "0x1234567890123456789012345678901234567890"
    vault.safe_address = "0xSafe1234567890123456789012345678901234"
    vault.trading_strategy_module_address = "0xModule12345678901234567890123456789012"

    # Mock trading strategy module
    module = Mock()
    perform_call_result = Mock()
    perform_call_result._encode_transaction_data.return_value = b"encoded_data"
    perform_call_result.address = vault.trading_strategy_module_address
    module.functions.performCall.return_value = perform_call_result
    vault.trading_strategy_module = module

    return vault


@pytest.fixture
def mock_asset_manager(mock_web3):
    """Mock asset manager hot wallet."""
    wallet = Mock()
    wallet.address = "0xAssetManager123456789012345678901234567"
    wallet.sync_nonce = Mock()
    wallet.allocate_nonce = Mock(return_value=42)

    # Mock signing
    signed_tx = Mock()
    signed_tx.rawTransaction = HexBytes(b"\x00" * 32)
    signed_tx.raw_transaction = HexBytes(b"\x00" * 32)
    signed_tx.hash = HexBytes(b"\x01" * 32)
    signed_tx.nonce = 42
    signed_tx.r = 1
    signed_tx.s = 2
    signed_tx.v = 27
    wallet.sign_bound_call_with_new_nonce.return_value = signed_tx

    return wallet


def test_lagoon_wallet_is_base_wallet(mock_vault, mock_asset_manager):
    """Test that LagoonGMXTradingWallet implements BaseWallet interface."""
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet
    from eth_defi.basewallet import BaseWallet

    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)

    # Should be a BaseWallet subclass
    assert isinstance(wallet, BaseWallet)


def test_lagoon_router_preflight_is_scoped_to_arbitrum(mock_vault, mock_asset_manager):
    """The v2.2.1 router preflight has no Avalanche pin to resolve."""
    from eth_defi.gmx.lagoon.wallet import GMX_PREFLIGHT_CHAIN_NAMES

    assert GMX_PREFLIGHT_CHAIN_NAMES[42161] == "arbitrum"
    assert GMX_PREFLIGHT_CHAIN_NAMES.get(43114) is None


def test_lagoon_wallet_init(mock_vault, mock_asset_manager):
    """Test LagoonGMXTradingWallet initialisation."""
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)

    assert wallet.vault == mock_vault
    assert wallet.asset_manager == mock_asset_manager
    assert wallet.web3 == mock_vault.web3


def test_lagoon_wallet_address_returns_safe(mock_vault, mock_asset_manager):
    """Test that wallet.address returns the Safe address, not asset manager."""
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)

    # Address should be the Safe, not the asset manager
    assert wallet.address == mock_vault.safe_address
    assert wallet.get_main_address() == mock_vault.safe_address


def test_lagoon_wallet_sync_nonce_delegates(mock_vault, mock_asset_manager, mock_web3):
    """Test that sync_nonce delegates to asset manager."""
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)
    wallet.sync_nonce(mock_web3)

    mock_asset_manager.sync_nonce.assert_called_once_with(mock_web3)


def test_lagoon_wallet_allocate_nonce_delegates(mock_vault, mock_asset_manager):
    """Test that allocate_nonce delegates to asset manager."""
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)
    nonce = wallet.allocate_nonce()

    assert nonce == 42
    mock_asset_manager.allocate_nonce.assert_called_once()


def test_lagoon_wallet_sign_wraps_in_perform_call(mock_vault, mock_asset_manager):
    """Test that sign_transaction_with_new_nonce wraps tx in performCall."""
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)

    # Create a mock GMX transaction
    tx = {
        "to": "0xExchangeRouter123456789012345678901234",
        "data": HexBytes(b"\x00" * 100),
        "value": 10**16,  # 0.01 ETH
        "gas": 1_500_000,
    }

    # Sign it
    signed = wallet.sign_transaction_with_new_nonce(tx)

    # Verify performCall was called with correct args
    mock_vault.trading_strategy_module.functions.performCall.assert_called_once_with(
        tx["to"],
        tx["data"],
        tx["value"],
    )

    # Verify asset_manager.sign_bound_call_with_new_nonce was called
    mock_asset_manager.sign_bound_call_with_new_nonce.assert_called_once()

    # Check gas was increased by buffer
    call_args = mock_asset_manager.sign_bound_call_with_new_nonce.call_args
    tx_params = call_args.kwargs.get("tx_params", {})
    assert tx_params["gas"] == 1_500_000 + 200_000  # Original + buffer


def test_lagoon_wallet_get_native_balance(mock_vault, mock_asset_manager, mock_web3):
    """Test that get_native_currency_balance returns Safe balance."""
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)
    balance = wallet.get_native_currency_balance(mock_web3)

    # Should query Safe address, not asset manager
    mock_web3.eth.get_balance.assert_called_once_with(mock_vault.safe_address)
    assert balance == Decimal("1")  # 1 ETH


def test_lagoon_wallet_rejects_invalid_vault(mock_asset_manager):
    """Test that LagoonGMXTradingWallet rejects objects without trading_strategy_module."""
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    invalid_vault = Mock(spec=[])  # No trading_strategy_module attribute

    with pytest.raises(TypeError, match="trading_strategy_module"):
        LagoonGMXTradingWallet(invalid_vault, mock_asset_manager)


def test_lagoon_wallet_rejects_invalid_asset_manager(mock_vault):
    """Test that LagoonGMXTradingWallet rejects objects without sign_bound_call_with_new_nonce."""
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    invalid_manager = Mock(spec=[])  # No sign_bound_call_with_new_nonce attribute

    with pytest.raises(TypeError, match="HotWallet-like"):
        LagoonGMXTradingWallet(mock_vault, invalid_manager)


def test_lagoon_wallet_rejects_vault_without_module_address(mock_asset_manager, mock_web3):
    """Test that LagoonGMXTradingWallet rejects vault without TradingStrategyModuleV0."""
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    vault = Mock()
    vault.web3 = mock_web3
    vault.trading_strategy_module = Mock()
    vault.trading_strategy_module_address = None  # Not configured
    vault.vault_address = "0x1234"

    with pytest.raises(ValueError, match="no TradingStrategyModuleV0"):
        LagoonGMXTradingWallet(vault, mock_asset_manager)


def test_approve_gmx_collateral_via_vault():
    """Test approving GMX collateral through vault."""
    with patch("eth_defi.gmx.lagoon.approvals.get_contract_addresses") as mock_addresses:
        addresses = Mock()
        addresses.syntheticsrouter = "0xSyntheticsRouter1234567890123456789012"
        mock_addresses.return_value = addresses

        # Create mocks
        mock_web3 = Mock()
        mock_web3.eth.chain_id = 42161
        mock_web3.to_hex.return_value = "0xabcd1234"

        mock_vault = Mock()
        mock_vault.web3 = mock_web3
        mock_vault.transact_via_trading_strategy_module.return_value = Mock()

        mock_wallet = Mock()
        mock_wallet.sync_nonce = Mock()
        signed_tx = Mock()
        signed_tx.raw_transaction = HexBytes(b"\x00" * 32)
        mock_wallet.sign_bound_call_with_new_nonce.return_value = signed_tx

        mock_token = Mock()
        mock_token.symbol = "USDC"
        mock_token.address = "0xUSDC123456789012345678901234567890123"
        mock_token.convert_to_raw.return_value = 10**9  # 1000 USDC
        mock_token.contract.functions.approve.return_value = Mock()

        mock_web3.eth.send_raw_transaction.return_value = HexBytes(b"\x00" * 32)

        # Mock assert_transaction_success
        with patch("eth_defi.gmx.lagoon.approvals.assert_transaction_success_with_explanation"):
            from eth_defi.gmx.lagoon.approvals import approve_gmx_collateral_via_vault

            tx_hash = approve_gmx_collateral_via_vault(
                vault=mock_vault,
                asset_manager=mock_wallet,
                collateral_token=mock_token,
                amount=Decimal("1000"),
            )

            assert tx_hash is not None
            mock_vault.transact_via_trading_strategy_module.assert_called_once()
            mock_wallet.sign_bound_call_with_new_nonce.assert_called_once()


def _configure_guard(mock_vault, allowed: bool, order_vault: str | None = None):
    """Make the mocked TradingStrategyModuleV0 answer ``isAllowedGMXRouter``."""
    from eth_defi.gmx.contracts import get_contract_addresses

    guard_functions = mock_vault.trading_strategy_module.functions
    guard_functions.isAllowedGMXRouter.return_value.call.return_value = allowed
    guard_functions.gmxOrderVaults.return_value.call.return_value = order_vault or get_contract_addresses("arbitrum").ordervault
    mock_vault.trading_strategy_module.address = "0x33e391A6dce6874198177BdCC89B2230A2BC6202"
    return guard_functions.isAllowedGMXRouter


def test_lagoon_wallet_refuses_unwhitelisted_exchange_router(mock_vault, mock_asset_manager):
    """An order to a router the guard rejects raises before any gas is spent."""
    from eth_defi.gmx.contracts import get_contract_addresses
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet
    from eth_defi.gmx.whitelist import GMXRouterNotWhitelisted

    router = get_contract_addresses("arbitrum").exchangerouter
    _configure_guard(mock_vault, allowed=False)
    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)

    with pytest.raises(GMXRouterNotWhitelisted) as exc_info:
        wallet.sign_transaction_with_new_nonce({"to": router, "data": b"", "value": 0, "gas": 1_000_000})

    assert router in str(exc_info.value)
    assert mock_vault.trading_strategy_module.address in str(exc_info.value)
    mock_asset_manager.sign_bound_call_with_new_nonce.assert_not_called()


def test_lagoon_wallet_caches_successful_router_preflight(mock_vault, mock_asset_manager):
    """An allowed router is checked on the guard once, then served from the cache."""
    from eth_defi.gmx.contracts import get_contract_addresses
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    router = get_contract_addresses("arbitrum").exchangerouter
    is_allowed = _configure_guard(mock_vault, allowed=True)
    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)

    tx = {"to": router, "data": b"", "value": 0, "gas": 1_000_000}
    wallet.sign_transaction_with_new_nonce(tx)
    wallet.sign_transaction_with_new_nonce(tx)

    assert is_allowed.call_count == 1
    mock_vault.trading_strategy_module.functions.gmxOrderVaults.assert_called_once_with(router)
    assert mock_asset_manager.sign_bound_call_with_new_nonce.call_count == 2


@pytest.mark.parametrize(
    "rpc_error",
    [Web3RPCError("connection failed: https://secret.example/rpc-key")],
    ids=["web3-rpc-error"],
)
def test_lagoon_wallet_rpc_preflight_failure_is_not_cached_or_blocking(mock_vault, mock_asset_manager, caplog, rpc_error):
    """An unavailable RPC cannot prevent signing and is retried next time."""
    from eth_defi.gmx.contracts import get_contract_addresses
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    router = get_contract_addresses("arbitrum").exchangerouter
    is_allowed = _configure_guard(mock_vault, allowed=True)
    is_allowed.return_value.call.side_effect = rpc_error
    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)
    tx = {"to": router, "data": b"", "value": 0, "gas": 1_000_000}

    wallet.sign_transaction_with_new_nonce(tx)
    wallet.sign_transaction_with_new_nonce(tx)

    assert is_allowed.call_count == 2
    assert mock_asset_manager.sign_bound_call_with_new_nonce.call_count == 2
    assert "secret.example" not in caplog.text


def test_lagoon_wallet_does_not_ignore_guard_contract_reverts(mock_vault, mock_asset_manager):
    """A deterministic guard revert is not treated as a transport outage."""
    from eth_defi.gmx.contracts import get_contract_addresses
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    router = get_contract_addresses("arbitrum").exchangerouter
    is_allowed = _configure_guard(mock_vault, allowed=True)
    is_allowed.return_value.call.side_effect = ContractLogicError("execution reverted")
    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)

    with pytest.raises(ContractLogicError):
        wallet.sign_transaction_with_new_nonce({"to": router, "data": b"", "value": 0})
    mock_asset_manager.sign_bound_call_with_new_nonce.assert_not_called()


def test_lagoon_wallet_empty_guard_result_is_configuration_error(mock_vault, mock_asset_manager):
    """An empty guard response blocks signing with guard and router context."""
    from eth_defi.gmx.contracts import get_contract_addresses
    from eth_defi.gmx.lagoon.wallet import GMXRouterPreflightConfigurationError, LagoonGMXTradingWallet

    router = get_contract_addresses("arbitrum").exchangerouter
    is_allowed = _configure_guard(mock_vault, allowed=True)
    is_allowed.return_value.call.side_effect = BadFunctionCallOutput("empty result")
    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)

    with pytest.raises(GMXRouterPreflightConfigurationError, match=f"router {router}.*guard {mock_vault.trading_strategy_module.address}"):
        wallet.sign_transaction_with_new_nonce({"to": router, "data": b"", "value": 0})
    mock_asset_manager.sign_bound_call_with_new_nonce.assert_not_called()


def test_lagoon_wallet_chain_id_transport_failure_is_fail_open_and_uncached(mock_vault, mock_asset_manager, caplog):
    """Chain-ID transport failures fail open, while unrelated and cached targets avoid the RPC."""
    from eth_defi.gmx.contracts import get_contract_addresses
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    router = get_contract_addresses("arbitrum").exchangerouter
    is_allowed = _configure_guard(mock_vault, allowed=True)
    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)

    class UnavailableChainId:
        def __init__(self):
            self.calls = 0

        @property
        def chain_id(self):
            self.calls += 1
            raise Web3RPCError("connection failed: https://secret.example/rpc-key")

    failing_eth = UnavailableChainId()
    mock_vault.web3.eth = failing_eth
    wallet.preflight_exchange_router("0x0000000000000000000000000000000000000001")
    assert failing_eth.calls == 0

    wallet.preflight_exchange_router(router)
    assert failing_eth.calls == 1
    assert router not in wallet._verified_routers
    assert is_allowed.call_count == 0
    assert "secret.example" not in caplog.text

    mock_vault.web3.eth = Mock(chain_id=42161)
    wallet.preflight_exchange_router(router)
    assert is_allowed.call_count == 1

    mock_vault.web3.eth = failing_eth
    wallet.preflight_exchange_router(router)
    assert failing_eth.calls == 1
    assert is_allowed.call_count == 1


def test_lagoon_wallet_skips_unpinned_router_target(mock_vault, mock_asset_manager):
    """Unknown router addresses are not candidates under the fixed release pin."""
    from eth_defi.gmx.contracts import get_contract_addresses
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    old_router = get_contract_addresses("arbitrum").exchangerouter
    new_router = "0x7dE39FF2e232A2203196788d37e234cF8F1b83f1"
    assert old_router != new_router
    is_allowed = _configure_guard(mock_vault, allowed=True)
    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)
    wallet.sign_transaction_with_new_nonce({"to": old_router, "data": b"", "value": 0})

    wallet.sign_transaction_with_new_nonce({"to": new_router, "data": b"", "value": 0})

    assert is_allowed.call_count == 1


def test_lagoon_wallet_ignores_non_router_targets(mock_vault, mock_asset_manager):
    """Token approvals and other calls do not trigger the guard preflight."""
    from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet

    is_allowed = _configure_guard(mock_vault, allowed=False)
    wallet = LagoonGMXTradingWallet(mock_vault, mock_asset_manager)

    wallet.sign_transaction_with_new_nonce({"to": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "data": b"", "value": 0})

    is_allowed.assert_not_called()
