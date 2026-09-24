"""Tests for Safe Transaction Service proposals."""

from types import SimpleNamespace

import pytest
from eth_account import Account
from hexbytes import HexBytes

import eth_defi.safe.tx as safe_tx_module
from eth_defi.safe.tx import SafeTxProposalError, propose_safe_transaction


def test_propose_safe_transaction_posts_owner_signed_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Post a Safe Transaction Service proposal signed by a Safe owner.

    1. Arrange a Safe whose sole owner is the local proposer.
    2. Build and sign a direct Safe Call transaction.
    3. Verify the Transaction Service receives the signed transaction without broadcasting it.
    """
    owner = Account.create()
    captured: dict[str, object] = {}
    safe_transaction = SimpleNamespace(
        safe_tx_hash=HexBytes("0x" + "ab" * 32),
        sign=lambda private_key: captured.setdefault("private_key", private_key),
    )
    safe = SimpleNamespace(
        address="0x0000000000000000000000000000000000000001",
        retrieve_owners=lambda: [owner.address],
        build_multisig_tx=lambda **kwargs: captured.setdefault("transaction", kwargs) and safe_transaction,
        ethereum_client=SimpleNamespace(get_network=lambda: "base"),
    )

    class FakeTransactionService:
        base_url = "https://safe-transaction-base.example"

        def __init__(self, network, ethereum_client) -> None:
            captured["network"] = network
            captured["ethereum_client"] = ethereum_client

        def post_transaction(self, transaction) -> bool:
            captured["posted_transaction"] = transaction
            return True

    monkeypatch.setattr(safe_tx_module, "TransactionServiceApi", FakeTransactionService)

    # 1. Arrange a Safe whose sole owner is the local proposer.
    private_key = owner.key.hex()

    # 2. Build and sign a direct Safe Call transaction.
    result = propose_safe_transaction(
        safe=safe,
        address="0x0000000000000000000000000000000000000002",
        private_key=private_key,
        data=b"\x12\x34",
    )

    # 3. Verify the Transaction Service receives the signed transaction without broadcasting it.
    assert result is safe_transaction
    assert captured["private_key"] == private_key
    assert captured["transaction"] == {
        "to": "0x0000000000000000000000000000000000000002",
        "value": 0,
        "data": b"\x12\x34",
        "operation": 0,
    }
    assert captured["network"] == "base"
    assert captured["posted_transaction"] is safe_transaction


def test_propose_safe_transaction_rejects_non_owner() -> None:
    """Reject a proposal key that cannot sign for the Safe.

    1. Arrange a Safe with an unrelated owner.
    2. Attempt a proposal using a different local key.
    3. Verify no unsigned Safe transaction is built.
    """
    safe = SimpleNamespace(
        address="0x0000000000000000000000000000000000000001",
        retrieve_owners=lambda: [Account.create().address],
        build_multisig_tx=lambda **_kwargs: pytest.fail("A non-owner must not build a Safe transaction"),
    )

    # 1. Arrange a Safe with an unrelated owner.
    private_key = Account.create().key.hex()

    # 2. Attempt a proposal using a different local key.
    with pytest.raises(SafeTxProposalError, match="not an owner"):
        propose_safe_transaction(
            safe=safe,
            address="0x0000000000000000000000000000000000000002",
            private_key=private_key,
            data=b"\x12\x34",
        )

    # 3. Verify no unsigned Safe transaction is built.
