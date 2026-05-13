"""IPOR Fusion vault tests."""

import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: Bitcoin Dollar USDC vault on Ethereum.
#:
#: https://app.ipor.io/fusion/ethereum/0xf8f226da66244f89e70c5b5d1a5c5b0d505eb1d8
IPOR_BDUSD_ETHEREUM = "0xf8f226da66244f89e70c5b5d1a5c5b0d505eb1d8"

IPOR_BDUSD_DEPOSIT_FEE = 0.008


def test_internalised_fee_mode_preserves_explicit_deposit_fee():
    """Explicit deposit fees are investor-paid even if other fees are internalised."""
    fee_data = FeeData(
        fee_mode=VaultFeeMode.internalised_minting,
        management=0.005,
        performance=0.0,
        deposit=IPOR_BDUSD_DEPOSIT_FEE,
        withdraw=0.0,
    )

    net_fees = fee_data.get_net_fees()

    assert net_fees.management == 0
    assert net_fees.performance == 0
    assert net_fees.deposit == IPOR_BDUSD_DEPOSIT_FEE
    assert net_fees.withdraw == 0.0


@pytest.fixture(scope="module")
def web3() -> Web3:
    """Create an Ethereum connection."""
    if JSON_RPC_ETHEREUM is None:
        pytest.skip("JSON_RPC_ETHEREUM needed to run this test")

    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    assert web3.eth.chain_id == 1
    return web3


@pytest.fixture(scope="module")
def vault(web3: Web3) -> IPORVault:
    """Create the IPOR bdUSD vault instance."""
    vault = IPORVault(
        web3,
        VaultSpec(
            chain_id=1,
            vault_address=IPOR_BDUSD_ETHEREUM,
        ),
        features={ERC4626Feature.ipor_like},
    )
    return vault


def test_ipor_vault_description(vault: IPORVault):
    """Fetch vault description from IPOR's offchain customisation API.

    1. Read the description property which fetches from the customisation API
    2. Verify the Bitcoin Dollar USDC vault has a non-empty description
    3. Verify the prospectus link is appended as a markdown link
    """
    # 1. Read the description property
    description = vault.description

    # 2. Verify the description is present and contains expected content
    assert description is not None, "Bitcoin Dollar USDC vault should have a description in IPOR's customisation API"
    assert "Bitcoin Dollar" in description

    # 3. Verify the prospectus markdown link is appended
    assert "[View prospectus](" in description


def test_ipor_onboarding_fee(vault: IPORVault):
    """Read IPOR onboarding fee as an explicit deposit fee."""
    fee_data = vault.get_fee_data()

    assert fee_data.fee_mode == VaultFeeMode.internalised_minting
    assert fee_data.management == pytest.approx(0.005)
    assert fee_data.performance == pytest.approx(0.0)
    assert fee_data.deposit == pytest.approx(IPOR_BDUSD_DEPOSIT_FEE)
    assert fee_data.withdraw == pytest.approx(0.0)
    assert fee_data.get_net_fees().deposit == pytest.approx(IPOR_BDUSD_DEPOSIT_FEE)


def test_ipor_preview_deposit_is_net_of_onboarding_fee(vault: IPORVault):
    """IPOR previewDeposit() returns shares net of the onboarding fee."""
    raw_assets = vault.denomination_token.convert_to_raw(Decimal(1_000))

    gross_shares = vault.vault_contract.functions.convertToShares(raw_assets).call()
    net_shares = vault.vault_contract.functions.previewDeposit(raw_assets).call()

    assert gross_shares > 0
    assert net_shares > 0
    assert net_shares < gross_shares

    implied_fee = (gross_shares - net_shares) / gross_shares
    assert implied_fee == pytest.approx(IPOR_BDUSD_DEPOSIT_FEE, rel=0.000001)
