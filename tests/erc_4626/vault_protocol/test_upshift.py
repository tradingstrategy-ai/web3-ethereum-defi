"""Test Upshift vault metadata"""

import datetime
import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance, create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftMultiAssetHistoricalReader, UpshiftVault
from eth_defi.event_reader.multicall_batcher import read_multicall_historical
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.vault.risk import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")

UPSHIFT_MULTI_ASSET_FORK_BLOCK = 25_405_251
UPSHIFT_TORI_VAULT = "0xcd69123b3FBBfC666E1f6a501da27B564C00De54"
UPSHIFT_CTUSD_VAULT = "0xc87DBBB8C67e4F19fCD2E297c05937567b2572Ce"
UPSHIFT_TORI_HISTORY_START_BLOCK = 25_355_071
UPSHIFT_TORI_HISTORY_STEP_BLOCKS = 7_200
UPSHIFT_TORI_HISTORY_SAMPLE_COUNT = 8
UPSHIFT_TORI_HISTORY_END_BLOCK = UPSHIFT_TORI_HISTORY_START_BLOCK + UPSHIFT_TORI_HISTORY_STEP_BLOCKS * UPSHIFT_TORI_HISTORY_SAMPLE_COUNT


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=UPSHIFT_MULTI_ASSET_FORK_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_upshift(
    web3: Web3,
):
    """Read Upshift vault metadata.

    Example vault: https://etherscan.io/address/0x69fc3f84fd837217377d9dae0212068ceb65818e
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x69fc3f84fd837217377d9dae0212068ceb65818e",
    )

    assert isinstance(vault, UpshiftVault)
    assert vault.get_protocol_name() == "Upshift"
    assert vault.features == {ERC4626Feature.upshift_like}

    # Vault name should contain "Upshift"
    assert "Upshift" in vault.name
    assert vault.name == "Upshift AZT"
    assert vault.symbol == "upAZT"

    # Upshift has custom fees but they are not directly exposed
    assert vault.has_custom_fees() is True
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Upshift uses a daily claim processing system
    assert vault.get_estimated_lock_up().days == 1

    # Vault link should point to the Upshift app
    link = vault.get_link()
    assert "app.upshift.finance" in link
    assert "0x69FC3f84FD837217377d9Dae0212068cEB65818e" in link  # Checksummed address

    # Risk level should be None (not yet assessed)
    assert vault.get_risk() is VaultTechnicalRisk.severe

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Upshift doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@flaky.flaky
@pytest.mark.parametrize(
    "case",
    [
        (UPSHIFT_TORI_VAULT, "Tori Ecosystem Vault", "etrUSD", "trUSD", 18),
        (UPSHIFT_CTUSD_VAULT, "Earn ctUSD", "EctUSD", "USDC", 6),
    ],
)
def test_upshift_multi_asset_vault_metadata(
    web3: Web3,
    case: tuple[str, str, str, str, int],
):
    """Read Upshift multi-asset vault metadata.

    These vaults use the Upshift ``multiAssetVault`` implementation where the
    vault proxy exposes ``getSharePrice()`` and ``getTotalAssets()``, while
    ``lpTokenAddress()`` points to the ERC-20 share token.

    Tori: https://etherscan.io/address/0xcd69123b3FBBfC666E1f6a501da27B564C00De54
    Earn ctUSD: https://etherscan.io/address/0xc87DBBB8C67e4F19fCD2E297c05937567b2572Ce
    Implementation: https://etherscan.io/address/0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2#code
    """

    vault_address, expected_name, expected_symbol, expected_denomination_symbol, expected_share_decimals = case

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=vault_address,
    )

    assert isinstance(vault, UpshiftVault)
    assert vault.get_protocol_name() == "Upshift"
    assert vault.features == {ERC4626Feature.upshift_like, ERC4626Feature.upshift_multi_asset_like}
    assert vault.multi_asset_like is True

    assert vault.name == expected_name
    assert vault.symbol == expected_symbol
    assert vault.share_token.decimals == expected_share_decimals
    assert vault.denomination_token.symbol == expected_denomination_symbol

    assert isinstance(vault.get_historical_reader(stateful=False), UpshiftMultiAssetHistoricalReader)
    assert vault.fetch_share_price(UPSHIFT_MULTI_ASSET_FORK_BLOCK) > 0
    assert vault.fetch_total_assets(UPSHIFT_MULTI_ASSET_FORK_BLOCK) > 0
    assert vault.fetch_total_supply(UPSHIFT_MULTI_ASSET_FORK_BLOCK) > 0

    link = vault.get_link()
    assert "app.upshift.finance" in link
    assert Web3.to_checksum_address(vault_address) in link


@flaky.flaky
def test_upshift_tori_historical_reader_7d_share_price(
    tmp_path: Path,
):
    """Read seven days of Tori Ecosystem Vault historical share prices.

    The fixed range starts at the 2026-06-20 UTC block and samples eight daily
    points, giving a seven-day span between first and last sample. This covers
    the multi-asset Upshift reader path that uses ``getSharePrice()``,
    ``getTotalAssets()`` and the LP token ``totalSupply()``.
    """

    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM, hint="Ethereum RPC")
    web3factory = MultiProviderWeb3Factory(
        JSON_RPC_ETHEREUM,
        retries=2,
        skip_verification=True,
        expected_chain_id=1,
    )
    token_cache = TokenDiskCache(tmp_path / "tokens.sqlite")

    vault = create_vault_instance(
        web3,
        UPSHIFT_TORI_VAULT,
        features={ERC4626Feature.upshift_like, ERC4626Feature.upshift_multi_asset_like},
        token_cache=token_cache,
    )
    vault.first_seen_at_block = UPSHIFT_TORI_HISTORY_START_BLOCK

    reader = vault.get_historical_reader(stateful=False)
    calls = list(reader.construct_multicalls())
    combined_results = read_multicall_historical(
        chain_id=1,
        web3factory=web3factory,
        calls=calls,
        start_block=UPSHIFT_TORI_HISTORY_START_BLOCK,
        end_block=UPSHIFT_TORI_HISTORY_END_BLOCK,
        step=UPSHIFT_TORI_HISTORY_STEP_BLOCKS,
        max_workers=2,
        display_progress=False,
        require_multicall_result=True,
    )
    reads = [
        reader.process_result(
            block_number=combined_result.block_number,
            timestamp=combined_result.timestamp,
            call_results=combined_result.results,
        )
        for combined_result in combined_results
    ]

    reads = sorted(reads, key=lambda entry: entry.block_number)
    assert len(reads) == UPSHIFT_TORI_HISTORY_SAMPLE_COUNT
    assert reads[0].block_number == UPSHIFT_TORI_HISTORY_START_BLOCK
    assert reads[-1].block_number - reads[0].block_number == UPSHIFT_TORI_HISTORY_STEP_BLOCKS * (UPSHIFT_TORI_HISTORY_SAMPLE_COUNT - 1)
    assert reads[-1].timestamp - reads[0].timestamp >= datetime.timedelta(days=6, hours=20)

    assert all(read.vault.address == Web3.to_checksum_address(UPSHIFT_TORI_VAULT) for read in reads)
    assert all(read.errors is None for read in reads)
    assert all(read.share_price is not None and read.share_price > 0 for read in reads)
    assert all(read.total_assets is not None and read.total_assets > 0 for read in reads)
    assert all(read.total_supply is not None and read.total_supply > 0 for read in reads)
