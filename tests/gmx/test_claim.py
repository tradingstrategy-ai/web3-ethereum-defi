"""Offline unit tests for the GMX V2 funding-fee claim helpers.

These tests exercise :mod:`eth_defi.gmx.claim` without any RPC connection or
``.local-test.env``:

- calldata is encoded against the locally bundled ``ExchangeRouter`` ABI (no
  network needed to bind the contract);
- the signing wallet is a lightweight fake that records the transaction it is
  asked to sign;
- the broadcast (``web3.eth.send_raw_transaction``) and the gas estimation are
  stubbed.

The trailing guard-seam tests deploy the real ``GuardV0`` on an in-memory
``EthereumTesterProvider`` and prove that the payload produced by
:func:`eth_defi.gmx.claim.build_claim_funding_fees_multicall` passes the Lagoon
vault guard's ``claimFundingFees`` validation (and that a non-whitelisted
receiver is rejected). They skip cleanly if the guard contracts cannot be
deployed.
"""

from types import SimpleNamespace

import pytest
from hexbytes import HexBytes
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract

from eth_defi.abi import decode_function_args, get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.gmx.claim import (
    CLAIM_FUNDING_FEES_GAS_LIMIT,
    _is_claim_disabled,  # noqa: PLC2701 - the fail-open gate is the unit under test
    build_claim_funding_fees_multicall,
    claim_all_funding_fees,
    claim_funding_fees,
)
from eth_defi.gmx.contracts import get_contract_addresses, get_exchange_router_contract
from eth_defi.gmx.keys import claim_funding_fees_feature_disabled_key
from eth_defi.token import create_token

#: Outer ``multicall(bytes[])`` selector.
MULTICALL_SELECTOR = Web3.keccak(text="multicall(bytes[])")[:4]

#: Inner ``claimFundingFees(address[],address[],address)`` selector, matching the
#: hardcoded constant in the guard's ``GmxLib``.
CLAIM_FUNDING_FEES_SELECTOR = bytes.fromhex("c41b1ab3")

#: Arbitrary but valid, distinct addresses used to build payloads.
MARKET_A = Web3.to_checksum_address("0x00000000000000000000000000000000000000a1")
MARKET_B = Web3.to_checksum_address("0x00000000000000000000000000000000000000b2")
TOKEN_A = Web3.to_checksum_address("0x00000000000000000000000000000000000000c3")
TOKEN_B = Web3.to_checksum_address("0x00000000000000000000000000000000000000d4")
WALLET_ADDRESS = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
EXPLICIT_RECEIVER = Web3.to_checksum_address("0x000000000000000000000000000000000000dEaD")

#: A non-default gas limit used to check the override is honoured.
CUSTOM_GAS_LIMIT = 123_456

#: The documented default gas limit for a claim multicall.
EXPECTED_GAS_LIMIT = 800_000

#: The raw bytes our fake wallet pretends to have signed.
RAW_TRANSACTION = b"\x11\x22\x33"

#: The transaction hash our stubbed broadcaster returns.
BROADCAST_TX_HASH = HexBytes(b"\xaa" * 32)


class _FakeWallet:
    """Minimal signing wallet that records what it is asked to sign.

    Exposes exactly the surface :func:`eth_defi.gmx.claim.claim_funding_fees`
    relies on: ``address``, ``sync_nonce(web3)`` and
    ``sign_transaction_with_new_nonce(tx)``.
    """

    def __init__(self, address: str, raw_transaction: bytes = RAW_TRANSACTION):
        self.address = Web3.to_checksum_address(address)
        self._raw_transaction = raw_transaction
        self.synced_with: list[Web3] = []
        self.signed_transactions: list[dict] = []

    def sync_nonce(self, web3: Web3) -> None:
        self.synced_with.append(web3)

    def sign_transaction_with_new_nonce(self, tx: dict) -> SimpleNamespace:
        self.signed_transactions.append(dict(tx))
        return SimpleNamespace(raw_transaction=self._raw_transaction)


@pytest.fixture
def dummy_web3() -> Web3:
    """A disconnected Web3: binding the bundled contract ABI needs no RPC call."""
    return Web3(Web3.HTTPProvider("http://localhost:8545"))


@pytest.fixture
def tester_web3() -> Web3:
    """In-memory EVM Web3 so ``eth.chain_id`` resolves without a network."""
    return Web3(EthereumTesterProvider())


@pytest.fixture
def exchange_router(dummy_web3: Web3) -> Contract:
    """``ExchangeRouter`` contract bound to the pinned Arbitrum address."""
    return get_exchange_router_contract(dummy_web3, "arbitrum")


def _fake_config(web3: Web3) -> SimpleNamespace:
    """A GMXConfig-shaped object good enough for :func:`claim_funding_fees`."""
    return SimpleNamespace(web3=web3, chain="arbitrum", get_wallet_address=lambda: None)


def _decode_claim_args(exchange_router: Contract, multicall_data: bytes) -> dict:
    """Decode the inner ``claimFundingFees`` arguments from a multicall payload."""
    outer = decode_function_args(exchange_router.functions.multicall([]), multicall_data[4:])
    (inner_bytes,) = outer["data"]
    assert inner_bytes[:4] == CLAIM_FUNDING_FEES_SELECTOR, "inner call must be claimFundingFees"
    # The bound arguments are irrelevant for decoding; only the ABI is used.
    return decode_function_args(exchange_router.functions.claimFundingFees([], [], EXPLICIT_RECEIVER), inner_bytes[4:])


def _install_broadcast_stub(monkeypatch, web3: Web3) -> dict:
    """Stub gas estimation and the raw-transaction broadcast; return a sentinel dict."""
    sent: dict = {}

    def fake_send_raw_transaction(raw):
        sent["raw"] = raw
        return BROADCAST_TX_HASH

    monkeypatch.setattr(
        "eth_defi.gmx.claim.estimate_gas_fees",
        lambda _web3: SimpleNamespace(max_fee_per_gas=2_000_000_000, max_priority_fee_per_gas=100_000_000, legacy_gas_price=None),
    )
    monkeypatch.setattr(web3.eth, "send_raw_transaction", fake_send_raw_transaction)
    return sent


# =============================================================================
# Calldata construction
# =============================================================================


def test_build_multicall_outer_selector_is_multicall(dummy_web3: Web3):
    """The top-level payload is a ``multicall(bytes[])`` call."""
    data = build_claim_funding_fees_multicall(dummy_web3, "arbitrum", [MARKET_A, MARKET_B], [TOKEN_A, TOKEN_B], EXPLICIT_RECEIVER)
    assert data[:4] == MULTICALL_SELECTOR


def test_build_multicall_single_inner_call(dummy_web3: Web3, exchange_router: Contract):
    """The multicall wraps exactly one inner call."""
    data = build_claim_funding_fees_multicall(dummy_web3, "arbitrum", [MARKET_A], [TOKEN_A], EXPLICIT_RECEIVER)
    outer = decode_function_args(exchange_router.functions.multicall([]), data[4:])
    assert len(outer["data"]) == 1


def test_build_multicall_inner_claim_arguments(dummy_web3: Web3, exchange_router: Contract):
    """Decoding the inner call yields the expected markets, tokens and receiver."""
    markets = [MARKET_A, MARKET_B]
    tokens = [TOKEN_A, TOKEN_B]

    data = build_claim_funding_fees_multicall(dummy_web3, "arbitrum", markets, tokens, EXPLICIT_RECEIVER)
    decoded = _decode_claim_args(exchange_router, data)

    assert [a.lower() for a in decoded["markets"]] == [a.lower() for a in markets]
    assert [a.lower() for a in decoded["tokens"]] == [a.lower() for a in tokens]
    assert decoded["receiver"].lower() == EXPLICIT_RECEIVER.lower()


def test_build_multicall_misaligned_raises(dummy_web3: Web3):
    """Mismatched markets/tokens are rejected before encoding."""
    with pytest.raises(ValueError, match="aligned"):
        build_claim_funding_fees_multicall(dummy_web3, "arbitrum", [MARKET_A], [], EXPLICIT_RECEIVER)


def test_claim_funding_fees_gas_limit_constant():
    """The default gas limit is a fixed, documented value."""
    assert CLAIM_FUNDING_FEES_GAS_LIMIT == EXPECTED_GAS_LIMIT


# =============================================================================
# Transaction orchestration
# =============================================================================


def test_claim_funding_fees_empty_markets_returns_none(tester_web3: Web3):
    """Nothing to claim means no transaction is signed or sent."""
    wallet = _FakeWallet(WALLET_ADDRESS)
    assert claim_funding_fees(_fake_config(tester_web3), wallet, [], []) is None
    assert wallet.signed_transactions == []


def test_claim_funding_fees_misaligned_raises(tester_web3: Web3):
    """Mismatched markets/tokens are rejected before signing."""
    wallet = _FakeWallet(WALLET_ADDRESS)
    with pytest.raises(ValueError, match="aligned"):
        claim_funding_fees(_fake_config(tester_web3), wallet, [MARKET_A], [])


def test_claim_funding_fees_orchestration_default_receiver(monkeypatch, tester_web3: Web3):
    """The signed tx targets the ExchangeRouter and defaults the receiver to the wallet."""
    wallet = _FakeWallet(WALLET_ADDRESS)
    sent = _install_broadcast_stub(monkeypatch, tester_web3)

    result = claim_funding_fees(_fake_config(tester_web3), wallet, [MARKET_A], [TOKEN_A])

    assert result == BROADCAST_TX_HASH
    assert sent["raw"] == RAW_TRANSACTION, "the wallet's signed raw tx must be broadcast"
    assert wallet.synced_with == [tester_web3], "nonce must be synced exactly once"
    assert len(wallet.signed_transactions) == 1

    tx = wallet.signed_transactions[0]
    assert tx["to"] == get_contract_addresses("arbitrum").exchangerouter
    assert tx["from"] == WALLET_ADDRESS
    assert tx["value"] == 0, "a funding claim needs no native execution fee"
    assert tx["gas"] == CLAIM_FUNDING_FEES_GAS_LIMIT

    expected_data = build_claim_funding_fees_multicall(tester_web3, "arbitrum", [MARKET_A], [TOKEN_A], WALLET_ADDRESS)
    assert tx["data"] == expected_data

    decoded = _decode_claim_args(get_exchange_router_contract(tester_web3, "arbitrum"), tx["data"])
    assert decoded["receiver"].lower() == WALLET_ADDRESS.lower(), "receiver must default to the wallet address"


def test_claim_funding_fees_orchestration_explicit_receiver(monkeypatch, tester_web3: Web3):
    """An explicit receiver overrides the wallet default."""
    wallet = _FakeWallet(WALLET_ADDRESS)
    _install_broadcast_stub(monkeypatch, tester_web3)

    claim_funding_fees(_fake_config(tester_web3), wallet, [MARKET_A], [TOKEN_A], receiver=EXPLICIT_RECEIVER)

    tx = wallet.signed_transactions[0]
    decoded = _decode_claim_args(get_exchange_router_contract(tester_web3, "arbitrum"), tx["data"])
    assert decoded["receiver"].lower() == EXPLICIT_RECEIVER.lower()


def test_claim_funding_fees_orchestration_custom_gas_limit(monkeypatch, tester_web3: Web3):
    """A custom gas limit is forwarded to the transaction."""
    wallet = _FakeWallet(WALLET_ADDRESS)
    _install_broadcast_stub(monkeypatch, tester_web3)

    claim_funding_fees(_fake_config(tester_web3), wallet, [MARKET_A], [TOKEN_A], gas_limit=CUSTOM_GAS_LIMIT)

    assert wallet.signed_transactions[0]["gas"] == CUSTOM_GAS_LIMIT


# =============================================================================
# Optional guard seam
# =============================================================================
#
# These deploy the real GuardV0 (with GmxLib) on an in-memory EthereumTesterProvider,
# mirroring tests/guard/test_guard_gmx_validation.py, and feed the helper's output
# straight into guard.functions.validateCall(). No RPC or env is required; the
# fixture skips cleanly if the guard artifacts are unavailable.


def _deploy_whitelisted_guard(web3: Web3) -> SimpleNamespace:
    """Deploy ``GuardV0`` with ``GmxLib`` and whitelist a market, token and receiver."""
    deployer, owner, asset_manager, safe, attacker, exchange_router, synthetics_router, order_vault, market = web3.eth.accounts[:9]

    usdc = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**6)
    gmx_lib = deploy_contract(web3, "guard/GmxLib.json", deployer)
    libraries = {**GUARD_LIBRARIES, "GmxLib": gmx_lib.address}
    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=libraries)
    vault.functions.initialiseOwnership(owner).transact({"from": deployer})

    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())
    guard.functions.whitelistGMX(exchange_router, synthetics_router, order_vault, [usdc.address], "Allow GMX").transact({"from": owner})
    guard.functions.whitelistGMXMarket(market, "Allow market").transact({"from": owner})
    guard.functions.allowReceiver(safe, "Allow Safe").transact({"from": owner})

    return SimpleNamespace(
        web3=web3,
        guard=guard,
        asset_manager=asset_manager,
        exchange_router=exchange_router,
        market=market,
        token=usdc.address,
        receiver=safe,
        attacker=attacker,
    )


@pytest.fixture
def guard_env(guard_web3: Web3) -> SimpleNamespace:
    """Deploy and whitelist a GuardV0 for claim-flow validation, or skip."""
    try:
        return _deploy_whitelisted_guard(guard_web3)
    except Exception as e:  # pragma: no cover - only when the guard build is missing
        pytest.skip(f"Guard test contracts unavailable: {e}")


@pytest.fixture
def guard_web3() -> Web3:
    """In-memory EVM for the guard-seam tests."""
    return Web3(EthereumTesterProvider())


def test_guard_accepts_claim_payload_for_whitelisted_receiver(guard_env: SimpleNamespace):
    """The helper's multicall payload passes the guard for the whitelisted Safe."""
    data = build_claim_funding_fees_multicall(guard_env.web3, "arbitrum", [guard_env.market], [guard_env.token], guard_env.receiver)

    # validateCall is a view function: it must not revert.
    guard_env.guard.functions.validateCall(guard_env.asset_manager, guard_env.exchange_router, data).call()


def test_guard_rejects_claim_payload_for_unknown_receiver(guard_env: SimpleNamespace):
    """SECURITY: a payload claiming to a non-whitelisted receiver is rejected."""
    data = build_claim_funding_fees_multicall(guard_env.web3, "arbitrum", [guard_env.market], [guard_env.token], guard_env.attacker)

    with pytest.raises(Exception, match="GMX: receiver not allowed"):
        guard_env.guard.functions.validateCall(guard_env.asset_manager, guard_env.exchange_router, data).call()


# =============================================================================
# Feature-disabled gate: _is_claim_disabled()
# =============================================================================

#: Claiming-module (contract) addresses used to derive the DataStore flag key.
MODULE_A = Web3.to_checksum_address("0x00000000000000000000000000000000000000e5")
MODULE_B = Web3.to_checksum_address("0x00000000000000000000000000000000000000f6")


class _FakeBoolCall:
    """Stub for the ``getBool(key)`` bound function; only ``.call()`` is exercised."""

    def __init__(self, *, value: bool, raises: bool):
        self._value = value
        self._raises = raises

    def call(self) -> bool:
        if self._raises:
            msg = "simulated DataStore read failure"
            raise ValueError(msg)
        return self._value


class _FakeDataStoreFunctions:
    """Stub for ``data_store.functions`` that records every queried bytes32 key."""

    def __init__(self, store: "_FakeDataStore"):
        self._store = store

    def getBool(self, key: bytes) -> _FakeBoolCall:  # noqa: N802 - mirrors the DataStore ABI name
        self._store.queried_keys.append(key)
        return _FakeBoolCall(value=self._store.value, raises=self._store.raises)


class _FakeDataStore:
    """Minimal ``DataStore`` stub exposing only ``functions.getBool``."""

    def __init__(self, *, value: bool = True, raises: bool = False):
        self.value = value
        self.raises = raises
        self.queried_keys: list[bytes] = []
        self.functions = _FakeDataStoreFunctions(self)


def _install_datastore_stub(monkeypatch, *, value: bool = True, raises: bool = False) -> _FakeDataStore:
    """Point ``claim.get_datastore_contract`` at a recording in-memory stub."""
    store = _FakeDataStore(value=value, raises=raises)
    monkeypatch.setattr("eth_defi.gmx.claim.get_datastore_contract", lambda *_args, **_kwargs: store)
    return store


def test_is_claim_disabled_true_when_flag_set(monkeypatch, dummy_web3: Web3):
    """A truthy flag read disables claiming and queries the documented key."""
    store = _install_datastore_stub(monkeypatch, value=True)

    assert _is_claim_disabled(dummy_web3, "arbitrum", [MODULE_A]) is True
    assert store.queried_keys == [claim_funding_fees_feature_disabled_key(MODULE_A)]


def test_is_claim_disabled_false_when_flag_clear(monkeypatch, dummy_web3: Web3):
    """A falsy flag read leaves claiming enabled."""
    _install_datastore_stub(monkeypatch, value=False)

    assert _is_claim_disabled(dummy_web3, "arbitrum", [MODULE_A]) is False


def test_is_claim_disabled_fails_open_on_read_error(monkeypatch, dummy_web3: Web3):
    """A reverting flag read must not block a claim (fail-open)."""
    _install_datastore_stub(monkeypatch, raises=True)

    assert _is_claim_disabled(dummy_web3, "arbitrum", [MODULE_A]) is False


def test_is_claim_disabled_fails_open_when_datastore_binding_fails(monkeypatch, dummy_web3: Web3):
    """A DataStore bind failure must not block a claim (fail-open)."""

    def _boom(*_args, **_kwargs):
        msg = "no RPC"
        raise ConnectionError(msg)

    monkeypatch.setattr("eth_defi.gmx.claim.get_datastore_contract", _boom)

    assert _is_claim_disabled(dummy_web3, "arbitrum", [MODULE_A]) is False


def test_is_claim_disabled_probes_every_module_key(monkeypatch, dummy_web3: Web3):
    """Each candidate module is probed with its own feature-disabled key."""
    store = _install_datastore_stub(monkeypatch, value=False)

    assert _is_claim_disabled(dummy_web3, "arbitrum", [MODULE_A, MODULE_B]) is False
    assert store.queried_keys == [
        claim_funding_fees_feature_disabled_key(MODULE_A),
        claim_funding_fees_feature_disabled_key(MODULE_B),
    ]


# =============================================================================
# Feature gate short-circuit in claim_funding_fees()
# =============================================================================


def test_claim_funding_fees_skips_signing_when_disabled(monkeypatch, tester_web3: Web3):
    """When the gate reports disabled, nothing is signed or broadcast."""
    wallet = _FakeWallet(WALLET_ADDRESS)
    monkeypatch.setattr("eth_defi.gmx.claim._is_claim_disabled", lambda *_args, **_kwargs: True)

    result = claim_funding_fees(_fake_config(tester_web3), wallet, [MARKET_A], [TOKEN_A])

    assert result is None
    assert wallet.signed_transactions == []
    assert wallet.synced_with == [], "the gate must short-circuit before nonce sync/signing"


def test_claim_funding_fees_builds_tx_when_gate_skipped(monkeypatch, tester_web3: Web3):
    """``check_feature_disabled=False`` never consults the gate and signs the tx."""
    wallet = _FakeWallet(WALLET_ADDRESS)
    sent = _install_broadcast_stub(monkeypatch, tester_web3)

    def _unexpected(*_args, **_kwargs):
        msg = "_is_claim_disabled must not be called when check_feature_disabled=False"
        raise AssertionError(msg)

    monkeypatch.setattr("eth_defi.gmx.claim._is_claim_disabled", _unexpected)

    result = claim_funding_fees(_fake_config(tester_web3), wallet, [MARKET_A], [TOKEN_A], check_feature_disabled=False)

    assert result == BROADCAST_TX_HASH
    assert sent["raw"] == RAW_TRANSACTION
    assert len(wallet.signed_transactions) == 1


# =============================================================================
# claim_all_funding_fees() discovery + delegation
# =============================================================================


def _install_fake_reader(monkeypatch, markets: list, tokens: list, captured: dict) -> None:
    """Replace ``claim.GetClaimableFundingFees`` with a reader returning fixed claim args."""

    class _FakeGetClaimableFundingFees:
        def __init__(self, config, account):
            captured["config"] = config
            captured["account"] = account
            self._markets = markets
            self._tokens = tokens

        def get_claim_args(self):
            return list(self._markets), list(self._tokens)

    monkeypatch.setattr("eth_defi.gmx.claim.GetClaimableFundingFees", _FakeGetClaimableFundingFees)


def test_claim_all_funding_fees_builds_and_sends_claim(monkeypatch, tester_web3: Web3):
    """The discovered ``(markets, tokens)`` are claimed for the wallet's own account."""
    wallet = _FakeWallet(WALLET_ADDRESS)
    sent = _install_broadcast_stub(monkeypatch, tester_web3)
    captured: dict = {}
    _install_fake_reader(monkeypatch, [MARKET_A], [TOKEN_A], captured)
    monkeypatch.setattr("eth_defi.gmx.claim._is_claim_disabled", lambda *_args, **_kwargs: False)

    result = claim_all_funding_fees(_fake_config(tester_web3), wallet)

    assert captured["account"] == WALLET_ADDRESS, "the reader must key off the wallet's own address"
    assert result == BROADCAST_TX_HASH
    assert sent["raw"] == RAW_TRANSACTION
    assert len(wallet.signed_transactions) == 1

    tx = wallet.signed_transactions[0]
    assert tx["to"] == get_contract_addresses("arbitrum").exchangerouter
    assert tx["from"] == WALLET_ADDRESS
    assert tx["value"] == 0

    decoded = _decode_claim_args(get_exchange_router_contract(tester_web3, "arbitrum"), tx["data"])
    assert [a.lower() for a in decoded["markets"]] == [MARKET_A.lower()]
    assert [a.lower() for a in decoded["tokens"]] == [TOKEN_A.lower()]
    assert decoded["receiver"].lower() == WALLET_ADDRESS.lower(), "receiver must default to the wallet address"


def test_claim_all_funding_fees_returns_none_without_receipts(monkeypatch, tester_web3: Web3):
    """An empty ``get_claim_args`` short-circuits before the feature gate."""
    wallet = _FakeWallet(WALLET_ADDRESS)
    _install_fake_reader(monkeypatch, [], [], {})

    def _unexpected(*_args, **_kwargs):
        msg = "the gate must not be consulted when there is nothing to claim"
        raise AssertionError(msg)

    monkeypatch.setattr("eth_defi.gmx.claim._is_claim_disabled", _unexpected)

    assert claim_all_funding_fees(_fake_config(tester_web3), wallet) is None
    assert wallet.signed_transactions == []
