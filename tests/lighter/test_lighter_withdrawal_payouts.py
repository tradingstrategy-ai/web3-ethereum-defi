"""Test how a Lighter withdrawal is detected when its relayer claims it for us.

``withdrawPendingBalance`` is permissionless, and Lighter's relayer claims in
batches, so a withdrawal can reach the Safe with no claim from us and never show as
a pending balance for long. These tests cover the reader of the
``WithdrawPending`` event and the wait built on it.
"""

import asyncio
import os
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_mock import MockerFixture

import eth_defi.lighter.lagoon as lagoon
from eth_defi.lighter.lagoon import LIGHTER_LOG_CHUNK_BLOCKS, LIGHTER_WITHDRAW_PENDING_TOPIC, fetch_lighter_claimed_usdc, is_lighter_withdrawal_paid, wait_for_lighter_withdrawal
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")
SAFE = "0xc9129A57299b0Db10b752E27507569D3F37dBBA4"
USDC_ASSET_INDEX = 3
OTHER_ASSET_INDEX = 1
#: A block range holding two relayer payouts to the abandoned LIX Safe on 2026-11-05.
PAYOUT_FROM_BLOCK = 26348457
PAYOUT_TO_BLOCK = 26348510


def create_log(block: int, amount: int, asset_index: int = USDC_ASSET_INDEX) -> dict[str, Any]:
    """Build a ``WithdrawPending`` log as an RPC returns it."""
    return {"blockNumber": block, "data": asset_index.to_bytes(32, "big") + amount.to_bytes(32, "big")}


@pytest.fixture()
def chain(mocker: MockerFixture) -> SimpleNamespace:
    """Create a fake chain that rejects oversized log ranges and blocks past its head."""
    state = SimpleNamespace(logs=[], requests=[], head=10_000)

    def get_logs(request: dict[str, Any]) -> list[dict[str, Any]]:
        state.requests.append(request)
        assert request["toBlock"] - request["fromBlock"] < LIGHTER_LOG_CHUNK_BLOCKS, "range too large"
        assert request["toBlock"] <= state.head, "past the chain head"
        assert request["topics"][0] == LIGHTER_WITHDRAW_PENDING_TOPIC
        assert request["topics"][1].endswith(SAFE[2:].lower())
        return [log for log in state.logs if request["fromBlock"] <= log["blockNumber"] <= request["toBlock"]]

    state.web3 = SimpleNamespace(eth=SimpleNamespace(get_logs=get_logs, block_number=state.head))
    zk = SimpleNamespace(functions=SimpleNamespace(USDC_ASSET_INDEX=lambda: SimpleNamespace(call=lambda: USDC_ASSET_INDEX)))
    mocker.patch.object(lagoon, "get_deployed_contract", return_value=zk)
    state.usdc = SimpleNamespace(convert_to_decimals=lambda raw: Decimal(raw) / 10**6)
    return state


@pytest.mark.timeout(30)
def test_claimed_usdc_counts_only_usdc_claims_across_chunks(chain: SimpleNamespace) -> None:
    """Claims are summed over a range wider than one provider request.

    1. Add claims in different chunks, and one for another asset.
    2. Read from a block before them all.
    3. Assert only USDC counts, every request fits the provider's limit and the
       scan stops at the head.
    """
    # 1. Add claims in different chunks, and one for another asset.
    chain.logs = [create_log(9_400, 9_000_000), create_log(9_990, 1_000_000), create_log(9_500, 5_000_000, OTHER_ASSET_INDEX), create_log(100, 7_000_000)]

    # 2. Read from a block before them all.
    claimed = fetch_lighter_claimed_usdc(chain.web3, owner=SAFE, usdc=chain.usdc, from_block=9_000)

    # 3. Assert only USDC counts and the scan stays within the provider's limits.
    assert claimed == Decimal(10)
    assert len(chain.requests) == 5
    assert chain.requests[-1]["toBlock"] == chain.head


@pytest.mark.timeout(30)
def test_wait_recognises_a_relayer_payout(chain: SimpleNamespace, mocker: MockerFixture) -> None:
    """A withdrawal paid before we looked is reported as paid, with nothing pending."""
    chain.logs = [create_log(9_900, 10_000_000)]
    mocker.patch.object(lagoon, "fetch_lighter_pending_balance", return_value=Decimal(0))
    assert wait_for_lighter_withdrawal(chain.web3, owner=SAFE, usdc=chain.usdc, amount=Decimal(10), pending_before=Decimal(0), from_block=9_800, timeout=0) is True
    assert is_lighter_withdrawal_paid(chain.web3, owner=SAFE, usdc=chain.usdc, amount=Decimal(10), pending_before=Decimal(0), from_block=9_800) is True


@pytest.mark.timeout(30)
def test_wait_reports_a_claimable_withdrawal(chain: SimpleNamespace, mocker: MockerFixture) -> None:
    """A pending balance waits for our claim, and an older one is not mistaken for ours.

    1. A new pending balance with no claims needs our claim.
    2. A relayer claim of only an older balance leaves ours waiting for a claim.
    3. An older balance alone is not this withdrawal, so the wait times out.
    4. A partly claimed withdrawal cannot be claimed in full, so the wait times out too.
    """
    # 1. A new pending balance with no claims needs our claim.
    mocker.patch.object(lagoon, "fetch_lighter_pending_balance", return_value=Decimal(10))
    assert wait_for_lighter_withdrawal(chain.web3, owner=SAFE, usdc=chain.usdc, amount=Decimal(10), pending_before=Decimal(0), from_block=9_800, timeout=0) is False

    # 2. A relayer claim of only an older balance leaves ours waiting for a claim.
    chain.logs = [create_log(9_900, 2_000_000)]
    assert wait_for_lighter_withdrawal(chain.web3, owner=SAFE, usdc=chain.usdc, amount=Decimal(10), pending_before=Decimal(2), from_block=9_800, timeout=0) is False
    assert is_lighter_withdrawal_paid(chain.web3, owner=SAFE, usdc=chain.usdc, amount=Decimal(10), pending_before=Decimal(2), from_block=9_800) is False

    # 3. An older balance alone is not this withdrawal, so the wait times out.
    chain.logs = []
    mocker.patch.object(lagoon, "fetch_lighter_pending_balance", return_value=Decimal(2))
    with pytest.raises(TimeoutError):
        wait_for_lighter_withdrawal(chain.web3, owner=SAFE, usdc=chain.usdc, amount=Decimal(10), pending_before=Decimal(2), from_block=9_800, timeout=0)

    # 4. A partly claimed withdrawal cannot be claimed in full, so the wait times out too.
    chain.logs = [create_log(9_900, 4_000_000)]
    mocker.patch.object(lagoon, "fetch_lighter_pending_balance", return_value=Decimal(6))
    with pytest.raises(TimeoutError):
        wait_for_lighter_withdrawal(chain.web3, owner=SAFE, usdc=chain.usdc, amount=Decimal(10), pending_before=Decimal(0), from_block=9_800, timeout=0)


@pytest.mark.timeout(30)
def test_claimed_usdc_never_reads_past_the_head(chain: SimpleNamespace) -> None:
    """An upper bound beyond the head is capped, because providers refuse it."""
    chain.logs = [create_log(9_990, 1_000_000)]
    assert fetch_lighter_claimed_usdc(chain.web3, owner=SAFE, usdc=chain.usdc, from_block=9_900, to_block=20_000) == Decimal(1)
    assert chain.requests[-1]["toBlock"] == chain.head


@pytest.mark.timeout(120)
@pytest.mark.skipif(not JSON_RPC_ETHEREUM, reason="Needs JSON_RPC_ETHEREUM")
def test_claimed_usdc_reads_real_relayer_payouts() -> None:
    """Read the two real relayer payouts of 9 and 1 USDC to the abandoned LIX Safe."""
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    usdc = fetch_erc20_details(web3, USDC_NATIVE_TOKEN[1], chain_id=1)
    assert fetch_lighter_claimed_usdc(web3, owner=SAFE, usdc=usdc, from_block=PAYOUT_FROM_BLOCK, to_block=PAYOUT_TO_BLOCK) == Decimal(10)


def test_hypersync_payout_prefix_and_rpc_tail(chain: SimpleNamespace, mocker: MockerFixture) -> None:
    """Use indexed events and RPC tail without gaps or double-counting.

    1. Mock indexer pages to control coverage, including another asset.
    2. Read through RPC head and compare with the RPC-only result.
    3. Verify query filters, empty indexed coverage and Anvil bypass.
    """
    # 1. Mock indexer pages to control coverage, including another asset.
    client = mocker.Mock()
    client.get_height = mocker.AsyncMock(return_value=9_801)
    configure = mocker.patch.object(lagoon, "configure_hypersync_from_env", return_value=SimpleNamespace(hypersync_client=client))
    anvil = mocker.patch.object(lagoon, "is_anvil", return_value=False)
    receiver = mocker.AsyncMock()
    page = SimpleNamespace(
        next_block=9_801,
        data=SimpleNamespace(
            logs=[
                SimpleNamespace(data="0x" + create_log(9_400, 9_000_000)["data"].hex()),
                SimpleNamespace(data="0x" + create_log(9_500, 5_000_000, OTHER_ASSET_INDEX)["data"].hex()),
            ]
        ),
    )
    receiver.recv.side_effect = [page, None]
    stream = mocker.patch.object(lagoon, "open_hypersync_stream", return_value=receiver)
    chain.logs = [create_log(9_400, 9_000_000), create_log(9_990, 1_000_000)]

    # 2. Read through RPC head and compare with the RPC-only result.
    kwargs = dict(owner=SAFE, usdc=chain.usdc, from_block=9_000)
    assert fetch_lighter_claimed_usdc(chain.web3, **kwargs, hypersync_api_key="test-key") == Decimal(10)
    assert chain.requests[0]["fromBlock"] == 9_801
    assert fetch_lighter_claimed_usdc(chain.web3, **kwargs) == Decimal(10)
    receiver.close.assert_awaited_once()

    # 3. Verify query filters, empty indexed coverage and Anvil bypass.
    query = stream.call_args.args[1]
    assert query.to_block == 9_801
    assert query.logs[0].topics == [[LIGHTER_WITHDRAW_PENDING_TOPIC], ["0x" + SAFE[2:].lower().rjust(64, "0")]]
    assert configure.call_args.kwargs["hypersync_api_key"] == "test-key"
    receiver.recv.side_effect = [SimpleNamespace(next_block=9_801, data=SimpleNamespace(logs=[])), None]
    assert fetch_lighter_claimed_usdc(chain.web3, **kwargs, hypersync_api_key="test-key") == Decimal(1)
    configure.reset_mock()
    anvil.return_value = True
    assert fetch_lighter_claimed_usdc(chain.web3, **kwargs, hypersync_api_key="test-key") == Decimal(10)
    configure.assert_not_called()


@pytest.mark.parametrize("failure", ["partial", "incomplete", "timeout"])
def test_hypersync_payout_failure_uses_full_rpc_range(chain: SimpleNamespace, mocker: MockerFixture, failure: str) -> None:
    """Discard failed indexer output and propagate failure if RPC also fails.

    1. Mock a partially successful, truncated or stalled indexer stream.
    2. Check full-range RPC retry returns the actual payout exactly once.
    3. Fail RPC too and verify the reader refuses to invent an amount.
    """
    # 1. Mock a partially successful, truncated or stalled indexer stream.
    client = mocker.Mock()
    client.get_height = mocker.AsyncMock(return_value=9_801)
    mocker.patch.object(lagoon, "configure_hypersync_from_env", return_value=SimpleNamespace(hypersync_client=client))
    mocker.patch.object(lagoon, "is_anvil", return_value=False)
    receiver = mocker.AsyncMock()
    mocker.patch.object(lagoon, "open_hypersync_stream", return_value=receiver)
    page = SimpleNamespace(next_block=9_801, data=SimpleNamespace(logs=[SimpleNamespace(data="0x" + create_log(9_400, 9_000_000)["data"].hex())]))
    if failure == "timeout":

        async def stalled_receive():
            await asyncio.sleep(10)

        receiver.recv.side_effect = stalled_receive
    else:
        receiver.recv.side_effect = [page, RuntimeError("stream failed")] if failure == "partial" else [None]
    chain.logs = [create_log(9_400, 9_000_000), create_log(9_990, 1_000_000)]
    kwargs = dict(owner=SAFE, usdc=chain.usdc, from_block=9_000, hypersync_api_key="test-key", hypersync_timeout=0.01)

    # 2. Check full-range RPC retry returns the actual payout exactly once.
    assert fetch_lighter_claimed_usdc(chain.web3, **kwargs) == Decimal(10)
    assert chain.requests[0]["fromBlock"] == 9_000
    receiver.close.assert_awaited_once()

    # 3. Fail RPC too and verify the reader refuses to invent an amount.
    receiver.recv.side_effect = RuntimeError("indexer failed")
    chain.web3.eth.get_logs = mocker.Mock(side_effect=RuntimeError("RPC failed"))
    with pytest.raises(RuntimeError, match="RPC failed"):
        fetch_lighter_claimed_usdc(chain.web3, **kwargs)


@pytest.mark.skipif(not JSON_RPC_ETHEREUM or not HYPERSYNC_API_KEY, reason="Needs Ethereum RPC and HyperSync credentials")
# 2026-11-13: CI reported Envio remaining=0/100; earlier PR runs passed.
@pytest.mark.hypersync_retry
def test_hypersync_reads_real_relayer_payouts() -> None:
    """Compare real HyperSync payout data with RPC without hiding fallback.

    1. Connect to Ethereum and create the configured indexer client.
    2. Read the fixed abandoned Lix Safe payout range through HyperSync directly.
    3. Compare its complete coverage and amount with the RPC reader.
    """
    # 1. Connect to Ethereum and create the configured indexer client.
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    usdc = fetch_erc20_details(web3, USDC_NATIVE_TOKEN[1], chain_id=1)
    client = lagoon.configure_hypersync_from_env(web3, hypersync_api_key=HYPERSYNC_API_KEY).hypersync_client
    asset_index = lagoon.get_deployed_contract(web3, "lighter/ZkLighter.json", lagoon.LIGHTER_L1_CONTRACT).functions.USDC_ASSET_INDEX().call()
    # 2. Read the fixed abandoned Lix Safe payout range through HyperSync directly.
    raw, next_block = lagoon.run_vault_flow_log_fetch(
        lagoon._fetch_lighter_claims_hypersync(
            client,
            lagoon.LIGHTER_L1_CONTRACT,
            "0x" + SAFE[2:].lower().rjust(64, "0"),
            asset_index,
            PAYOUT_FROM_BLOCK,
            PAYOUT_TO_BLOCK,
            30,
        )
    )
    # 3. Compare its complete coverage and amount with the RPC reader.
    assert next_block == PAYOUT_TO_BLOCK + 1
    assert usdc.convert_to_decimals(raw) == Decimal(10)
    assert fetch_lighter_claimed_usdc(web3, owner=SAFE, usdc=usdc, from_block=PAYOUT_FROM_BLOCK, to_block=PAYOUT_TO_BLOCK, use_hypersync=False) == usdc.convert_to_decimals(raw)
