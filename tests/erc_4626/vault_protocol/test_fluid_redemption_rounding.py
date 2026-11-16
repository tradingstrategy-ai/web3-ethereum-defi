"""Separate Fluid token rounding from interest against frozen fork liquidity.

Block 26402309 is an intentional lifecycle exception to the shared midnight
block: the matrix's fiUSDC pool had no idle USDC there. It reproduces the
capacity failure caused by the test-trade helper's artificial 24-hour wait.
"""

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from eth_abi import decode
from web3 import Web3
from web3.exceptions import ContractLogicError

from eth_defi.erc_4626.vault_protocol.fluid.vault import FluidVault
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil, mine
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable, extract_revert_data

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
FORK_BLOCK = 26_402_309
VAULT_ADDRESS = "0x7f47B1CEd85F5d773cd68cA606ed6F0fee381046"

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed"),
    pytest.mark.xdist_group("fork:ethereum:fluid-capacity"),
]


@pytest.fixture
def fork_connection(anvil_fork_pool: AnvilForkPool) -> tuple[Web3, AnvilLaunch]:
    """Use the original matrix block with its empty idle-liquidity balance."""
    return anvil_fork_pool.get_web3_with_launch(JSON_RPC_ETHEREUM, FORK_BLOCK)


@pytest.fixture
def web3(fork_connection: tuple[Web3, AnvilLaunch]) -> Web3:
    """Connect to the original matrix fork through the session pool."""
    return fork_connection[0]


@pytest.fixture(autouse=True)
def _evm_snapshot(fork_connection: tuple[Web3, AnvilLaunch]) -> Iterator[None]:
    """Restore all wallet funding and time changes after the regression."""
    yield from evm_snapshot_revert(fork_connection[1])


def test_fluid_rounding_does_not_hide_interest_liquidity_shortfall(web3: Web3) -> None:
    """Allow one raw asset unit in payout checks, never a real cash deficit.

    1. Deposit 1001 USDC into the original empty-liquidity state.
    2. Advance one day and prove both preflight and exact redemption refuse.
    3. Restore deposit time and redeem every share within one raw asset unit.
    """
    # 1. Deposit 1001 USDC into the original empty-liquidity state.
    vault = FluidVault(web3, VaultSpec(1, VAULT_ADDRESS))
    token = vault.denomination_token
    contract = vault.vault_contract
    owner = web3.eth.accounts[0]
    data = web3.eth.call({"to": VAULT_ADDRESS, "data": Web3.keccak(text="getData()")[:4]})
    liquidity = Web3.to_checksum_address(decode(["address"] * 5 + ["bool", "uint256", "uint256", "uint256"], data)[0])
    assert token.fetch_raw_balance_of(liquidity) == 0
    fund_erc20_on_anvil(web3, token.address, owner, 1_001_000_000)
    approval = token.contract.functions.approve(VAULT_ADDRESS, 1_001_000_000).transact({"from": owner, "gas": 200_000})
    assert web3.eth.wait_for_transaction_receipt(approval)["status"] == 1
    deposit = contract.functions.deposit(1_001_000_000, owner).transact({"from": owner, "gas": 3_000_000})
    assert web3.eth.wait_for_transaction_receipt(deposit)["status"] == 1
    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares > 0
    deposit_timestamp = web3.eth.get_block("latest")["timestamp"]
    snapshot = web3.provider.make_request("evm_snapshot", [])["result"]
    manager = vault.get_deposit_manager()

    # 2. Advance one day and prove both preflight and exact redemption refuse.
    try:
        mine(web3, increase_timestamp=86_400)
        assert token.fetch_raw_balance_of(liquidity) == 1_001_000_000
        assert contract.functions.convertToAssets(raw_shares).call() - 1_001_000_000 > 1
        with pytest.raises(VaultFlowUnavailable) as failure:
            manager.create_redemption_request(owner=owner, raw_shares=raw_shares)
        assert failure.value.requested_raw_amount == raw_shares
        assert raw_shares - failure.value.available_raw_amount > 1
        assert failure.value.phase == "preflight"
        with pytest.raises((ContractLogicError, ValueError)) as reverted:
            contract.functions.redeem(raw_shares, owner, owner).call({"from": owner})
        revert_data = extract_revert_data(reverted.value)
        assert revert_data is not None
        assert revert_data[:4] == Web3.keccak(text="Error(string)")[:4]
        assert decode(["string"], revert_data[4:])[0] == "ERC20: transfer amount exceeds balance"
    finally:
        assert web3.provider.make_request("evm_revert", [snapshot])["result"] is True

    # 3. Restore deposit time and redeem every share within one raw asset unit.
    request = manager.create_redemption_request(owner=owner, raw_shares=raw_shares)
    assert request.raw_shares == raw_shares
    # Reverting a snapshot does not reset the autominer's wall-clock offset.
    web3.provider.make_request("evm_setNextBlockTimestamp", [deposit_timestamp + 1])
    redemption = request.funcs[0].transact({"from": owner, "gas": 3_000_000})
    assert web3.eth.wait_for_transaction_receipt(redemption)["status"] == 1
    ticket = request.parse_redeem_transaction([redemption])
    analysis = manager.analyse_redemption(redemption, ticket)
    assert analysis.denomination_amount == pytest.approx(Decimal(1001), rel=0, abs=token.convert_to_decimals(1))
    assert analysis.denomination_amount == token.fetch_balance_of(owner)
    assert vault.share_token.convert_to_raw(analysis.share_count) == raw_shares
    assert vault.share_token.fetch_raw_balance_of(owner) == 0
