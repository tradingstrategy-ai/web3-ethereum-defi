"""Robust receipt visibility helper tests."""

import shutil

import pytest
from web3 import Web3
from web3.exceptions import ProviderConnectionError, TransactionNotFound

from eth_defi.abi import ZERO_ADDRESS
from eth_defi.compat import clear_middleware, create_http_provider
from eth_defi.provider import receipt as receipt_module
from eth_defi.provider.anvil import AnvilLaunch, launch_anvil
from eth_defi.provider.fallback import FallbackProvider
from eth_defi.provider.receipt import (
    ReceiptVisibilityMismatch,
    ReceiptVisibilityTimedOut,
    get_read_providers,
    wait_for_transaction_receipt_robust,
)


TX_HASH = "0x" + "12" * 32
BLOCK_HASH = "0x" + "34" * 32


def _raw_receipt(
    tx_hash: str = TX_HASH,
    block_hash: str = BLOCK_HASH,
    block_number: str = "0x1",
) -> dict:
    """Create a minimal raw JSON-RPC transaction receipt."""
    return {
        "transactionHash": tx_hash,
        "blockHash": block_hash,
        "blockNumber": block_number,
        "status": "0x1",
    }


class FakeProvider:
    """Minimal provider supporting direct raw JSON-RPC calls."""

    def __init__(
        self,
        name: str,
        receipts: list[dict | None] | None = None,
        block_number: str | list[str | BaseException] = "0x2",
    ):
        self.endpoint_uri = f"https://{name}.example"
        self.receipts = receipts or [_raw_receipt()]
        self.block_numbers = block_number if isinstance(block_number, list) else [block_number]
        self.calls: list[str] = []

    def make_request(self, method: str, params: list) -> dict:
        self.calls.append(method)
        if method == "eth_getTransactionReceipt":
            if len(self.receipts) > 1:
                result = self.receipts.pop(0)
            else:
                result = self.receipts[0]
            return {"jsonrpc": "2.0", "id": 1, "result": result}
        if method == "eth_blockNumber":
            if len(self.block_numbers) > 1:
                result = self.block_numbers.pop(0)
            else:
                result = self.block_numbers[0]
            if isinstance(result, BaseException):
                raise result
            return {"jsonrpc": "2.0", "id": 1, "result": result}
        raise NotImplementedError(method)


class FakeEth:
    """Minimal eth namespace for returning a typed Web3 receipt."""

    def __init__(self, receipt: dict | list[dict | BaseException] | None = None):
        self.receipts = receipt if isinstance(receipt, list) else [receipt or {"status": 1}]
        self.waited = False
        self.get_receipt_calls = 0

    def _next_receipt(self):
        if len(self.receipts) > 1:
            receipt = self.receipts.pop(0)
        else:
            receipt = self.receipts[0]
        if isinstance(receipt, BaseException):
            raise receipt
        return receipt

    def get_transaction_receipt(self, tx_hash):
        self.get_receipt_calls += 1
        return self._next_receipt()

    def wait_for_transaction_receipt(self, tx_hash, timeout: float = 120.0):
        self.waited = True
        return self._next_receipt()


class FakeWeb3:
    """Minimal Web3-like object for provider receipt tests."""

    def __init__(self, provider, eth: FakeEth | None = None):
        self.provider = provider
        self.eth = eth or FakeEth()
        self.client_version = "fake-web3"


class FakeMEVProvider:
    """Minimal MEV-style wrapper with a separate read provider."""

    def __init__(self, call_provider):
        self.call_provider = call_provider


@pytest.fixture(scope="module")
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend."""
    anvil = launch_anvil()
    try:
        yield anvil
    finally:
        anvil.close()


def test_get_read_providers_plain(monkeypatch: pytest.MonkeyPatch):
    """Get read providers from a plain Web3 provider.

    1. Create a plain fake provider.
    2. Disable Anvil detection for the fake Web3.
    3. Check the provider is returned unchanged.
    """

    # 1. Create a plain fake provider.
    provider = FakeProvider("plain")
    web3 = FakeWeb3(provider)

    # 2. Disable Anvil detection for the fake Web3.
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 3. Check the provider is returned unchanged.
    assert get_read_providers(web3) == [provider]


def test_get_read_providers_fallback(monkeypatch: pytest.MonkeyPatch):
    """Get all read providers from a direct fallback provider.

    1. Create two fake read providers.
    2. Wrap them in a fallback provider.
    3. Check both providers are returned.
    """

    # 1. Create two fake read providers.
    provider_1 = FakeProvider("read-1")
    provider_2 = FakeProvider("read-2")

    # 2. Wrap them in a fallback provider.
    fallback_provider = FallbackProvider([provider_1, provider_2])
    web3 = FakeWeb3(fallback_provider)
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 3. Check both providers are returned.
    assert get_read_providers(web3) == [provider_1, provider_2]


def test_get_read_providers_mev_wrapper(monkeypatch: pytest.MonkeyPatch):
    """Get read providers from a MEV-style wrapped provider.

    1. Create two fake read providers behind a fallback provider.
    2. Wrap the fallback provider in an object with call_provider.
    3. Check transaction providers are ignored and read providers are returned.
    """

    # 1. Create two fake read providers behind a fallback provider.
    provider_1 = FakeProvider("read-1")
    provider_2 = FakeProvider("read-2")
    fallback_provider = FallbackProvider([provider_1, provider_2])

    # 2. Wrap the fallback provider in an object with call_provider.
    web3 = FakeWeb3(FakeMEVProvider(fallback_provider))
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 3. Check transaction providers are ignored and read providers are returned.
    assert get_read_providers(web3) == [provider_1, provider_2]


def test_get_read_providers_anvil_active_only(monkeypatch: pytest.MonkeyPatch):
    """Get only the active read provider on Anvil.

    1. Create a fallback provider with two fake providers.
    2. Mark the fake Web3 as Anvil and switch the active provider.
    3. Check only the active provider is returned.
    """

    # 1. Create a fallback provider with two fake providers.
    provider_1 = FakeProvider("read-1")
    provider_2 = FakeProvider("read-2")
    fallback_provider = FallbackProvider([provider_1, provider_2])

    # 2. Mark the fake Web3 as Anvil and switch the active provider.
    fallback_provider.currently_active_provider = 1
    web3 = FakeWeb3(fallback_provider)
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: True)

    # 3. Check only the active provider is returned.
    assert get_read_providers(web3) == [provider_2]


def test_wait_for_transaction_receipt_robust_immediate(monkeypatch: pytest.MonkeyPatch):
    """Wait for an immediately visible receipt on all read providers.

    1. Create two fake providers that already see the receipt.
    2. Wait for robust receipt visibility.
    3. Check the original Web3 receipt is returned.
    """

    # 1. Create two fake providers that already see the receipt.
    provider_1 = FakeProvider("read-1")
    provider_2 = FakeProvider("read-2")
    web3 = FakeWeb3(FallbackProvider([provider_1, provider_2]), FakeEth({"status": 1, "source": "original-web3"}))
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Wait for robust receipt visibility.
    receipt = wait_for_transaction_receipt_robust(web3, TX_HASH, timeout=1, poll_delay=0.001, max_poll_delay=0.002, confirmation_block_count=0)

    # 3. Check the original Web3 receipt is returned.
    assert receipt == {"status": 1, "source": "original-web3"}


def test_wait_for_transaction_receipt_robust_lagging_provider(monkeypatch: pytest.MonkeyPatch):
    """Wait until a lagging read provider can see the receipt.

    1. Create one immediate provider and one lagging provider.
    2. Wait for robust receipt visibility.
    3. Check the lagging provider was polled more than once.
    """

    # 1. Create one immediate provider and one lagging provider.
    provider_1 = FakeProvider("read-1")
    provider_2 = FakeProvider("read-2", receipts=[None, _raw_receipt()])
    web3 = FakeWeb3(FallbackProvider([provider_1, provider_2]))
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Wait for robust receipt visibility.
    wait_for_transaction_receipt_robust(web3, TX_HASH, timeout=1, poll_delay=0.001, max_poll_delay=0.002, confirmation_block_count=0)

    # 3. Check the lagging provider was polled more than once.
    assert provider_2.calls.count("eth_getTransactionReceipt") == 2


def test_wait_for_transaction_receipt_robust_extra_sleep_after_visibility(monkeypatch: pytest.MonkeyPatch):
    """Sleep once after all read providers can see the receipt.

    1. Create one immediate provider and one lagging provider.
    2. Wait with an extra sleep and record sleep calls.
    3. Check the extra sleep happens after the lagging provider is retried.
    """

    # 1. Create one immediate provider and one lagging provider.
    sleep_calls = []
    provider_1 = FakeProvider("read-1")
    provider_2 = FakeProvider("read-2", receipts=[None, _raw_receipt()])
    web3 = FakeWeb3(FallbackProvider([provider_1, provider_2]))
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)
    monkeypatch.setattr(receipt_module.time, "sleep", sleep_calls.append)

    # 2. Wait with an extra sleep and record sleep calls.
    wait_for_transaction_receipt_robust(
        web3,
        TX_HASH,
        timeout=1,
        poll_delay=0.001,
        max_poll_delay=0.002,
        extra_sleep=0.123,
        confirmation_block_count=0,
    )

    # 3. Check the extra sleep happens after the lagging provider is retried.
    assert provider_2.calls.count("eth_getTransactionReceipt") == 2
    assert sleep_calls == [0.001, 0.123]


def test_wait_for_transaction_receipt_robust_timeout(monkeypatch: pytest.MonkeyPatch):
    """Fail when a read provider never sees the receipt.

    1. Create one visible provider and one permanently missing provider.
    2. Wait for robust receipt visibility with a short timeout.
    3. Check the timeout names the missing provider.
    """

    # 1. Create one visible provider and one permanently missing provider.
    provider_1 = FakeProvider("read-1")
    provider_2 = FakeProvider("read-2", receipts=[None])
    web3 = FakeWeb3(FallbackProvider([provider_1, provider_2]))
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Wait for robust receipt visibility with a short timeout.
    with pytest.raises(ReceiptVisibilityTimedOut) as exc_info:
        wait_for_transaction_receipt_robust(web3, TX_HASH, timeout=0.01, poll_delay=0.001, max_poll_delay=0.002)

    # 3. Check the timeout names the missing provider.
    assert "read-2.example" in str(exc_info.value)


def test_wait_for_transaction_receipt_robust_mismatch(monkeypatch: pytest.MonkeyPatch):
    """Fail when read providers keep disagreeing on the receipt block hash.

    1. Create two providers with different block hashes for the same tx.
    2. Wait for robust receipt visibility until the timeout.
    3. Check a persistent receipt mismatch is raised.
    """

    # 1. Create two providers with different block hashes for the same tx.
    provider_1 = FakeProvider("read-1", receipts=[_raw_receipt(block_hash="0x" + "aa" * 32)])
    provider_2 = FakeProvider("read-2", receipts=[_raw_receipt(block_hash="0x" + "bb" * 32)])
    web3 = FakeWeb3(FallbackProvider([provider_1, provider_2]))
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Wait for robust receipt visibility until the timeout.
    with pytest.raises(ReceiptVisibilityMismatch):
        wait_for_transaction_receipt_robust(web3, TX_HASH, timeout=0.01, poll_delay=0.001, max_poll_delay=0.002)

    # 3. Check a persistent receipt mismatch is raised.
    assert provider_1.calls.count("eth_getTransactionReceipt") > 1


def test_wait_for_transaction_receipt_robust_transient_mismatch(monkeypatch: pytest.MonkeyPatch):
    """Retry when read providers temporarily disagree on the receipt block hash.

    1. Create two providers that first disagree and then return the same receipt.
    2. Wait for robust receipt visibility.
    3. Check the original Web3 receipt is returned after retrying.
    """

    # 1. Create two providers that first disagree and then return the same receipt.
    final_receipt = _raw_receipt(block_hash="0x" + "cc" * 32)
    provider_1 = FakeProvider(
        "read-1",
        receipts=[_raw_receipt(block_hash="0x" + "aa" * 32), final_receipt],
    )
    provider_2 = FakeProvider(
        "read-2",
        receipts=[_raw_receipt(block_hash="0x" + "bb" * 32), final_receipt],
    )
    web3 = FakeWeb3(FallbackProvider([provider_1, provider_2]), FakeEth({"status": 1, "source": "original-web3"}))
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Wait for robust receipt visibility.
    receipt = wait_for_transaction_receipt_robust(web3, TX_HASH, timeout=1, poll_delay=0.001, max_poll_delay=0.002, confirmation_block_count=0)

    # 3. Check the original Web3 receipt is returned after retrying.
    assert receipt == {"status": 1, "source": "original-web3"}
    assert provider_1.calls.count("eth_getTransactionReceipt") == 2


def test_wait_for_transaction_receipt_robust_confirmation_success(monkeypatch: pytest.MonkeyPatch):
    """Wait until all providers have enough receipt confirmations.

    1. Create one provider that needs one more block for confirmation.
    2. Wait for robust receipt visibility with one required confirmation.
    3. Check block numbers were polled until confirmations were high enough.
    """

    # 1. Create one provider that needs one more block for confirmation.
    provider_1 = FakeProvider("read-1", block_number=["0x1", "0x2"])
    provider_2 = FakeProvider("read-2", block_number="0x2")
    web3 = FakeWeb3(FallbackProvider([provider_1, provider_2]))
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Wait for robust receipt visibility with one required confirmation.
    wait_for_transaction_receipt_robust(
        web3,
        TX_HASH,
        timeout=1,
        poll_delay=0.001,
        max_poll_delay=0.002,
        confirmation_block_count=1,
    )

    # 3. Check block numbers were polled until confirmations were high enough.
    assert provider_1.calls.count("eth_blockNumber") == 2


def test_wait_for_transaction_receipt_robust_default_confirmations(monkeypatch: pytest.MonkeyPatch):
    """Wait for the default two confirmations on a live chain when none is given.

    1. Create a provider that climbs from one to two confirmations over the receipt block.
    2. Wait without passing confirmation_block_count, so the default resolves to 2.
    3. Check block numbers were polled until two confirmations were reached.
    """

    # 1. Create a provider that climbs from one to two confirmations over the receipt block.
    #    Receipt block is 0x1, so 0x2 is one confirmation (insufficient) and 0x3 is two (enough).
    provider_1 = FakeProvider("read-1", block_number=["0x2", "0x3"])
    provider_2 = FakeProvider("read-2", block_number="0x3")
    web3 = FakeWeb3(FallbackProvider([provider_1, provider_2]))
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Wait without passing confirmation_block_count, so the default resolves to 2.
    wait_for_transaction_receipt_robust(web3, TX_HASH, timeout=1, poll_delay=0.001, max_poll_delay=0.002)

    # 3. Check block numbers were polled until two confirmations were reached.
    assert receipt_module.DEFAULT_CONFIRMATION_BLOCK_COUNT == 2
    assert provider_1.calls.count("eth_blockNumber") == 2


def test_wait_for_transaction_receipt_robust_confirmation_block_time(monkeypatch: pytest.MonkeyPatch):
    """Convert a wall-clock confirmation time to a per-chain block count on a fast chain.

    1. Create an Arbitrum-like chain (0.25 s blocks) where 1 second converts to 4 blocks.
    2. Wait with confirmation_block_time=1.0 and no block count requirement.
    3. Check block numbers were polled until 4 confirmations were reached.
    """

    # 1. Create an Arbitrum-like chain (0.25 s blocks) where 1 second converts to 4 blocks.
    #    Receipt block is 0x1, so 0x4 is three confirmations (insufficient) and 0x5 is four (enough).
    provider_1 = FakeProvider("read-1", block_number=["0x4", "0x5"])
    provider_2 = FakeProvider("read-2", block_number="0x5")
    web3 = FakeWeb3(FallbackProvider([provider_1, provider_2]))
    web3.eth.chain_id = 42161
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Wait with confirmation_block_time=1.0 and no block count requirement.
    wait_for_transaction_receipt_robust(
        web3,
        TX_HASH,
        timeout=1,
        poll_delay=0.001,
        max_poll_delay=0.002,
        confirmation_block_count=0,
        confirmation_block_time=1.0,
    )

    # 3. Check block numbers were polled until 4 confirmations were reached.
    assert provider_1.calls.count("eth_blockNumber") == 2


def test_wait_for_transaction_receipt_robust_confirmation_block_time_max(monkeypatch: pytest.MonkeyPatch):
    """A larger explicit block count wins over the time-based block count.

    1. Create an Arbitrum-like chain where confirmation_block_time=1.0 gives 4 blocks but confirmation_block_count is 6.
    2. Wait so the effective requirement is max(4, 6) = 6 confirmations.
    3. Check 4 confirmations were not enough and polling continued to 6.
    """

    # 1. Create an Arbitrum-like chain where confirmation_block_time=1.0 gives 4 blocks but confirmation_block_count is 6.
    #    Receipt block is 0x1, so 0x5 is four confirmations (insufficient) and 0x7 is six (enough).
    provider_1 = FakeProvider("read-1", block_number=["0x5", "0x7"])
    provider_2 = FakeProvider("read-2", block_number="0x7")
    web3 = FakeWeb3(FallbackProvider([provider_1, provider_2]))
    web3.eth.chain_id = 42161
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Wait so the effective requirement is max(4, 6) = 6 confirmations.
    wait_for_transaction_receipt_robust(
        web3,
        TX_HASH,
        timeout=1,
        poll_delay=0.001,
        max_poll_delay=0.002,
        confirmation_block_count=6,
        confirmation_block_time=1.0,
    )

    # 3. Check 4 confirmations were not enough and polling continued to 6.
    assert provider_1.calls.count("eth_blockNumber") == 2


def test_wait_for_transaction_receipt_robust_count_zero_opts_out_of_block_time(monkeypatch: pytest.MonkeyPatch):
    """An explicit confirmation_block_count=0 keeps pure receipt-visibility semantics.

    1. Create an Arbitrum-like chain where the default 25 s confirmation time would mean 100 blocks.
    2. Wait with confirmation_block_count=0 only, the documented pure receipt-visibility opt-out.
    3. Check no block numbers were polled, so no confirmation wait was applied.
    """

    # 1. Create an Arbitrum-like chain where the default 25 s confirmation time would mean 100 blocks.
    provider_1 = FakeProvider("read-1", block_number="0x2")
    web3 = FakeWeb3(FallbackProvider([provider_1]))
    web3.eth.chain_id = 42161
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Wait with confirmation_block_count=0 only, the documented pure receipt-visibility opt-out.
    wait_for_transaction_receipt_robust(
        web3,
        TX_HASH,
        timeout=1,
        poll_delay=0.001,
        max_poll_delay=0.002,
        confirmation_block_count=0,
    )

    # 3. Check no block numbers were polled, so no confirmation wait was applied.
    assert provider_1.calls.count("eth_blockNumber") == 0


def test_wait_for_transaction_receipt_robust_confirmation_block_time_unknown_chain(monkeypatch: pytest.MonkeyPatch):
    """Fall back to the plain block count when the chain block time is unknown.

    1. Create a chain id missing from the block time table, with the default 25 s confirmation time.
    2. Wait with confirmation_block_count=1, so the time requirement is ignored with a warning.
    3. Check a single confirmation was enough.
    """

    # 1. Create a chain id missing from the block time table, with the default 25 s confirmation time.
    #    Receipt block is 0x1, so 0x2 is one confirmation.
    provider_1 = FakeProvider("read-1", block_number="0x2")
    web3 = FakeWeb3(FallbackProvider([provider_1]))
    web3.eth.chain_id = 424242424242
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Wait with confirmation_block_count=1, so the time requirement is ignored with a warning.
    assert receipt_module.DEFAULT_CONFIRMATION_BLOCK_TIME == 25.0
    wait_for_transaction_receipt_robust(
        web3,
        TX_HASH,
        timeout=1,
        poll_delay=0.001,
        max_poll_delay=0.002,
        confirmation_block_count=1,
    )

    # 3. Check a single confirmation was enough.
    assert provider_1.calls.count("eth_blockNumber") == 1


def test_wait_for_transaction_receipt_robust_confirmation_block_number_error(monkeypatch: pytest.MonkeyPatch):
    """Treat a transient block number read error as insufficient confirmations.

    1. Create one provider whose first block number read fails.
    2. Wait for robust receipt visibility with one required confirmation.
    3. Check the provider is retried instead of crashing the wait loop.
    """

    # 1. Create one provider whose first block number read fails.
    provider_1 = FakeProvider(
        "read-1",
        block_number=[ProviderConnectionError("temporary block number error"), "0x2"],
    )
    provider_2 = FakeProvider("read-2", block_number="0x2")
    web3 = FakeWeb3(FallbackProvider([provider_1, provider_2]))
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Wait for robust receipt visibility with one required confirmation.
    wait_for_transaction_receipt_robust(
        web3,
        TX_HASH,
        timeout=1,
        poll_delay=0.001,
        max_poll_delay=0.002,
        confirmation_block_count=1,
    )

    # 3. Check the provider is retried instead of crashing the wait loop.
    assert provider_1.calls.count("eth_blockNumber") == 2


def test_wait_for_transaction_receipt_robust_confirmation_timeout(monkeypatch: pytest.MonkeyPatch):
    """Time out when providers never have enough receipt confirmations.

    1. Create providers that stay on the receipt block.
    2. Wait for robust receipt visibility with one required confirmation.
    3. Check timeout details mention insufficient confirmations.
    """

    # 1. Create providers that stay on the receipt block.
    provider_1 = FakeProvider("read-1", block_number="0x1")
    provider_2 = FakeProvider("read-2", block_number="0x1")
    web3 = FakeWeb3(FallbackProvider([provider_1, provider_2]))
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Wait for robust receipt visibility with one required confirmation.
    with pytest.raises(ReceiptVisibilityTimedOut) as exc_info:
        wait_for_transaction_receipt_robust(
            web3,
            TX_HASH,
            timeout=0.01,
            poll_delay=0.001,
            max_poll_delay=0.002,
            confirmation_block_count=1,
        )

    # 3. Check timeout details mention insufficient confirmations.
    assert "Insufficient confirmations" in str(exc_info.value)


def test_wait_for_transaction_receipt_robust_final_typed_receipt_retry(monkeypatch: pytest.MonkeyPatch):
    """Retry the final typed receipt fetch after raw providers see the receipt.

    1. Create providers that already see the raw receipt.
    2. Make the original Web3 receipt fetch fail once.
    3. Check the typed receipt is retried and returned.
    """

    # 1. Create providers that already see the raw receipt.
    provider_1 = FakeProvider("read-1")
    provider_2 = FakeProvider("read-2")
    eth = FakeEth(
        [
            TransactionNotFound("typed receipt not yet visible"),
            {"status": 1, "source": "typed-retry"},
        ]
    )
    web3 = FakeWeb3(FallbackProvider([provider_1, provider_2]), eth)
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)

    # 2. Make the original Web3 receipt fetch fail once.
    receipt = wait_for_transaction_receipt_robust(web3, TX_HASH, timeout=1, poll_delay=0.001, max_poll_delay=0.002, confirmation_block_count=0)

    # 3. Check the typed receipt is retried and returned.
    assert receipt == {"status": 1, "source": "typed-retry"}
    assert eth.get_receipt_calls == 2


def test_wait_for_transaction_receipt_robust_anvil_path(monkeypatch: pytest.MonkeyPatch):
    """Use normal Web3 receipt waiting on Anvil.

    1. Create a fake Anvil Web3.
    2. Wait for robust receipt visibility.
    3. Check the normal Web3 wait path was used.
    """

    # 1. Create a fake Anvil Web3.
    provider = FakeProvider("anvil")
    eth = FakeEth({"status": 1})
    web3 = FakeWeb3(provider, eth)
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: True)

    # 2. Wait for robust receipt visibility.
    receipt = wait_for_transaction_receipt_robust(web3, TX_HASH, timeout=1)

    # 3. Check the normal Web3 wait path was used.
    assert receipt == {"status": 1}
    assert eth.waited
    assert provider.calls == []


@pytest.mark.skipif(shutil.which("anvil") is None, reason="Install anvil to run this test")
def test_wait_for_transaction_receipt_robust_real_fallback_provider(anvil: AnvilLaunch, monkeypatch: pytest.MonkeyPatch):
    """Wait for a real transaction receipt through a real fallback provider.

    1. Create two real HTTP providers pointing at the same Anvil instance.
    2. Send a transaction through the fallback Web3.
    3. Force the live robust path and check receipt visibility succeeds.
    """

    # 1. Create two real HTTP providers pointing at the same Anvil instance.
    provider_1 = create_http_provider(anvil.json_rpc_url, exception_retry_configuration=None)
    provider_2 = create_http_provider(anvil.json_rpc_url, exception_retry_configuration=None)
    clear_middleware(provider_1)
    clear_middleware(provider_2)
    web3 = Web3(FallbackProvider([provider_1, provider_2], sleep=0.1, backoff=1))

    # 2. Send a transaction through the fallback Web3.
    account = web3.eth.accounts[0]
    tx_hash = web3.eth.send_transaction({"from": account, "to": ZERO_ADDRESS, "value": 1})

    # 3. Force the live robust path and check receipt visibility succeeds.
    monkeypatch.setattr(receipt_module, "_is_anvil", lambda web3: False)
    receipt = wait_for_transaction_receipt_robust(web3, tx_hash, timeout=10, poll_delay=0.01, max_poll_delay=0.05, confirmation_block_count=0)
    assert receipt["status"] == 1
