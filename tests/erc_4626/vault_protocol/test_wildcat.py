"""Wildcat market adapter tests on the shared Ethereum midnight fork.

Markets at :py:data:`ETHEREUM_MIDNIGHT_BLOCK`:

- `wmtUSDC <https://app.wildcat.finance/lender/market/0xc9499006a149c553d18171747ed19aa7c6dd19e2>`__,
  Wintermute's open-term market with paid but unclaimed withdrawals
- `trinityUSDC <https://app.wildcat.finance/lender/market/0xfe7cf5680d2e59500f3938c2539fda4754876f94>`__,
  Wildcat Labs' empty fixed-term test market, without deposit access control
- `amberUSDC <https://app.wildcat.finance/lender/market/0xf76a7887521a91b47c62060ba57549dec1dc88c7>`__,
  a closed market
- `kPhoenixUSDC <https://app.wildcat.finance/lender/market/0x262dd546703760adda0c06279508e04bd1f60dee>`__,
  a fixed-term market delinquent beyond its grace period
- `hyperWildcatUSDC <https://app.wildcat.finance/lender/market/0x6231a192089fb636e704d2c7807d7a79c2457b07>`__,
  a delinquent market still within its grace period
"""

import datetime
import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.wildcat.vault import DEPOSIT_CLOSED_MARKET_CLOSED, WILDCAT_DEFAULT_NOTE, WildcatMarket
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.vault.base import DEPOSIT_CLOSED_CAP_REACHED
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.flag import TEST_VAULT, VaultFlag
from eth_defi.vault.strategy_tag import StrategyTag

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]

WINTERMUTE_USDC = "0xC9499006a149C553d18171747ED19Aa7C6Dd19E2"
TRINITY_USDC = "0xFe7cf5680d2e59500F3938c2539fdA4754876F94"
AMBER_USDC = "0xF76A7887521A91B47c62060BA57549dec1DC88c7"
KINTO_PHOENIX_USDC = "0x262dD546703760aDdA0c06279508e04bd1f60dEe"
HYPERITHM_USDC = "0x6231A192089fb636e704D2c7807d7A79c2457b07"


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Read-only Web3 backed by the shared Ethereum midnight fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


def create_market(web3: Web3, address: str) -> WildcatMarket:
    """Create a market adapter from its persisted scanner feature."""
    vault = create_vault_instance(web3, address, {ERC4626Feature.wildcat_like}, default_block_identifier=ETHEREUM_MIDNIGHT_BLOCK)
    assert isinstance(vault, WildcatMarket)
    return vault


def read_history(web3: Web3, vault: WildcatMarket):
    """Run the historical reader's Multicall reads at the fork block."""
    reader = vault.get_historical_reader(stateful=False)
    results = [call.call_as_result(web3=web3, block_identifier=ETHEREUM_MIDNIGHT_BLOCK) for call in reader.construct_multicalls()]
    return reader.process_result(ETHEREUM_MIDNIGHT_BLOCK, datetime.datetime(2026, 1, 1), results)


def test_wildcat_open_term_market(web3: Web3) -> None:
    """Wintermute's market maps scaleFactor, lender value and lending metrics.

    Over 1.2M USDC of its cash belongs to paid but unclaimed withdrawals, so
    utilisation stays close to 100% even though the market holds 1.23M USDC.
    """
    vault = create_market(web3, WINTERMUTE_USDC)
    assert vault.get_protocol_name() == "Wildcat"
    assert vault.name == "Wintermute Trading USD Coin"
    assert vault.symbol == "wmtUSDC"
    assert vault.denomination_token.symbol == "USDC"
    assert vault.fetch_share_token_address() == WINTERMUTE_USDC
    assert vault.fetch_share_price(ETHEREUM_MIDNIGHT_BLOCK) == Decimal("1.154707549452571528809787112")
    assert vault.fetch_total_assets(ETHEREUM_MIDNIGHT_BLOCK) == Decimal("75107853.729868")
    assert vault.fetch_total_supply(ETHEREUM_MIDNIGHT_BLOCK) == Decimal("65044914.416187")
    assert vault.fetch_lending_metrics(ETHEREUM_MIDNIGHT_BLOCK) == (Decimal("12112.552604"), pytest.approx(0.9998387312111519))
    assert vault.fetch_deposit_open() is True
    assert vault.fetch_deposit_closed_reason() is None
    assert fetch_deposit_permission(vault) == VaultDepositPermission.whitelisted
    assert vault.hooks_version == "OpenTermHooks"
    assert vault.fetch_fixed_term_end() is None
    assert vault.get_withdrawal_period().max_period == datetime.timedelta(days=1)
    assert vault.get_flags() == set()
    assert vault.get_fee_data().fee_mode == VaultFeeMode.feeless
    assert vault.get_fee_data().management == 0.0
    assert vault.get_strategy_tags() == {StrategyTag.lending, StrategyTag.unsecured_credit}

    read = read_history(web3, vault)
    assert read.share_price == Decimal("1.154707549452571528809787112")
    assert read.total_assets == Decimal("75107853.729868")
    assert read.total_supply == Decimal("65044914.416187")
    assert read.max_deposit == Decimal("54892146.270132")
    assert read.available_liquidity == Decimal("12112.552604")
    assert read.utilisation == pytest.approx(0.9998387312111519)
    assert read.errors is None


def test_wildcat_fixed_term_market_without_access_control(web3: Web3) -> None:
    """A fixed-term market reads its term end and permissionless deposits.

    FixedTermHooks enables the deposit hook for minimum deposits, so the
    permission must come from ``depositRequiresAccess``.
    """
    vault = create_market(web3, TRINITY_USDC)
    # Wildcat Labs' own test market is hidden
    assert vault.get_flags() == {VaultFlag.unofficial}
    assert vault.get_notes() == TEST_VAULT
    assert vault.hooks_version == "FixedTermHooks"
    assert fetch_deposit_permission(vault) == VaultDepositPermission.permissionless
    assert vault.fetch_fixed_term_end() == datetime.datetime(2025, 12, 25)
    assert vault.get_withdrawal_period().max_period == datetime.timedelta(days=3)

    read = read_history(web3, vault)
    assert read.share_price == Decimal("1.071426959665144596651445966")
    assert read.total_assets == Decimal("0")
    assert read.max_deposit == Decimal("1000")
    assert read.utilisation is None
    assert read.errors == ["scaledTotalSupply zero"]


def test_wildcat_closed_market(web3: Web3) -> None:
    """A closed market reports closed deposits and zero-length withdrawal batches."""
    vault = create_market(web3, AMBER_USDC)
    assert vault.fetch_deposit_open() is False
    assert vault.fetch_deposit_closed_reason() == DEPOSIT_CLOSED_MARKET_CLOSED
    assert vault.get_withdrawal_period().max_period == datetime.timedelta(0)
    assert vault.get_flags() == set()

    read = read_history(web3, vault)
    assert read.share_price == Decimal("1.111369098331780776873792167")
    assert read.total_assets == Decimal("296987.235253")
    assert read.max_deposit == Decimal("0")
    assert read.utilisation == 0.0


def test_wildcat_delinquency_flag(web3: Web3) -> None:
    """Only a market delinquent beyond its grace period is flagged.

    Kinto Phoenix has been delinquent far beyond its three-day grace period.
    Hyperithm's market is delinquent but still inside its seven-day grace
    period, so the penalty rate does not yet apply.
    """
    defaulted = create_market(web3, KINTO_PHOENIX_USDC)
    assert defaulted.current_state.time_delinquent == 25_633_920
    assert defaulted.delinquency_grace_period == 259_200
    assert defaulted.get_flags() == {VaultFlag.delinquent_markets}
    assert defaulted.get_notes() == WILDCAT_DEFAULT_NOTE
    assert defaulted.fetch_deposit_closed_reason() == DEPOSIT_CLOSED_CAP_REACHED
    assert read_history(web3, defaulted).utilisation == 1.0

    in_grace = create_market(web3, HYPERITHM_USDC)
    assert in_grace.current_state.is_delinquent is True
    assert in_grace.current_state.time_delinquent == 517_824
    assert in_grace.delinquency_grace_period == 604_800
    assert in_grace.get_flags() == set()
