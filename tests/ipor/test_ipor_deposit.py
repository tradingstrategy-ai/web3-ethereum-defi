"""IPOR Base mainnet fork based tests.

- Deposit and redeem.
"""

import os
from decimal import Decimal

import flaky
import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.analysis import analyse_4626_flow_transaction
from eth_defi.erc_4626.estimate import estimate_4626_redeem, estimate_4626_deposit, estimate_value_by_share_price
from eth_defi.erc_4626.flow import deposit_4626, redeem_4626
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch, mine
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.trade import TradeSuccess

from eth_defi.vault.base import VaultSpec

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests")


@pytest.fixture()
def usdc_holder() -> HexAddress:
    # https://basescan.org/token/0x833589fcd6edb6e08f4c7c32d4f71b54bda02913#balances
    return "0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A"


@pytest.fixture(scope="module")
def test_block_number() -> int:
    return 27975506


@pytest.fixture()
def anvil_base_fork(usdc_holder, test_block_number: int) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    assert JSON_RPC_BASE, "JSON_RPC_BASE not set"
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[usdc_holder],
        fork_block_number=test_block_number,
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_base_fork) -> Web3:
    """Create a web3 connector.

    - By default use Anvil forked Base

    - Eanble Tenderly testnet with `JSON_RPC_TENDERLY` to debug
      otherwise impossible to debug Gnosis Safe transactions
    """

    tenderly_fork_rpc = os.environ.get("JSON_RPC_TENDERLY", None)

    if tenderly_fork_rpc:
        web3 = create_multi_provider_web3(tenderly_fork_rpc)
    else:
        web3 = create_multi_provider_web3(
            anvil_base_fork.json_rpc_url,
            default_http_timeout=(3, 250.0),  # multicall slow, so allow improved timeout
        )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def depositor(
    web3: Web3,
    base_usdc: TokenDetails,
    usdc_holder: HexAddress,
) -> HexAddress:
    """Setup a test account with ETH and USDC."""
    account = web3.eth.accounts[0]
    tx_hash = base_usdc.contract.functions.transfer(account, 999 * 10**6).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return account


@pytest.fixture()
def vault(web3) -> IPORVault:
    # https://app.ipor.io/fusion/base/0x45aa96f0b3188d47a1dafdbefce1db6b37f58216
    spec = VaultSpec(8545, "0x45aa96f0b3188d47a1dafdbefce1db6b37f58216")
    return IPORVault(web3, spec)


@pytest.fixture()
def base_usdc(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    )


@pytest.fixture()
def base_weth(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x4200000000000000000000000000000000000006",
    )


def test_ipor_deposit(
    web3: Web3,
    vault: IPORVault,
    depositor: HexAddress,
    base_usdc: TokenDetails,
    test_block_number,
):
    """Do ERC-4626 deposit into Ipor vautl."""

    amount = Decimal(100)

    shares = estimate_4626_deposit(
        vault,
        amount,
        block_identifier=test_block_number,
    )
    assert shares == pytest.approx(Decimal("96.75231846"))

    tx_hash = base_usdc.approve(
        vault.address,
        amount,
    ).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    bound_func = deposit_4626(
        vault,
        depositor,
        amount,
    )
    tx_hash = bound_func.transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)

    # Analyse the ERC-4626 deposit transaction
    analysis = analyse_4626_flow_transaction(
        vault=vault,
        tx_hash=tx_hash,
        tx_receipt=tx_receipt,
        direction="deposit",
    )
    assert isinstance(analysis, TradeSuccess)

    assert analysis.path == [base_usdc.address_lower, vault.share_token.address_lower]
    assert analysis.amount_in == 100 * 10**6
    assert analysis.amount_out == pytest.approx(9675231755)
    assert analysis.amount_out_decimals == 8  # IPOR has 8 decimals
    assert analysis.price == pytest.approx(Decimal("1.033566972663402121955991264"))

    share_price = vault.fetch_share_price("latest")
    assert share_price == pytest.approx(Decimal("1.033566972584479679488338198"))


# ValueError: RPC smoke test failed for ***: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
@flaky.flaky
def test_ipor_redeem(
    web3: Web3,
    vault: IPORVault,
    depositor: HexAddress,
    base_usdc: TokenDetails,
    test_block_number,
):
    """Do ERC-4626 redeem from a Ipor vautl."""

    amount = Decimal(100)

    tx_hash = base_usdc.approve(
        vault.address,
        amount,
    ).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    bound_func = deposit_4626(
        vault,
        depositor,
        amount,
    )
    tx_hash = bound_func.transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Redeem after 1 year
    mine(web3, increase_timestamp=24 * 3600 * 365)

    shares = vault.share_token.fetch_balance_of(depositor, "latest")
    assert shares == pytest.approx(Decimal("96.7523176"))

    # See how much we get after all this time
    estimated_usdc = estimate_4626_redeem(vault, depositor, shares, fallback_using_share_price=False)
    assert estimated_usdc == pytest.approx(Decimal("99.084206"))

    estimate_usdc_share_price = estimate_value_by_share_price(
        vault,
        shares,
    )
    assert estimate_usdc_share_price == pytest.approx(Decimal("99.084206"))

    tx_hash = vault.share_token.approve(vault.address, shares).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = redeem_4626(vault, depositor, shares).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)

    # Analyse the ERC-4626 deposit transaction
    analysis = analyse_4626_flow_transaction(
        vault=vault,
        tx_hash=tx_hash,
        tx_receipt=tx_receipt,
        direction="redeem",
    )
    assert isinstance(analysis, TradeSuccess)

    assert analysis.path == [vault.share_token.address_lower, base_usdc.address_lower]
    assert analysis.amount_in == pytest.approx(9675231789)
    assert analysis.amount_out == pytest.approx(99094116)  # Management fee removed?
    assert analysis.amount_in_decimals == 8  # IPOR has 8 decimals
    assert analysis.price == pytest.approx(Decimal("1.024203895809544282024791200"))

    # Share price has changed over 1yera
    share_price = vault.fetch_share_price("latest")
    assert share_price == pytest.approx(Decimal("1.024204051979538320520931622"))
