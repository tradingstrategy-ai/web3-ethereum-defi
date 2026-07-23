"""IPOR Fusion vault tests."""

import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.ipor.deposit_redeem import IPORDepositManager
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable
from eth_defi.vault.fee import FeeData, VaultFeeMode

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: Bitcoin Dollar USDC vault on Ethereum.
#:
#: https://app.ipor.io/fusion/ethereum/0xf8f226da66244f89e70c5b5d1a5c5b0d505eb1d8
IPOR_BDUSD_ETHEREUM = "0xf8f226da66244f89e70c5b5d1a5c5b0d505eb1d8"

#: BL USDC WSR Loop, a vault whose deposit selector is restricted by IPOR's
#: AccessManager for the report's simulated wallet.
IPOR_RESTRICTED_ETHEREUM = "0x95b2ed8f821570f85fd0e3e6e7088c6296587088"

#: Simulated wallet from trade-executor's unsupported-vault report.
REPORT_CALLER = "0xa2b04c6a053ab2efbc699f5dd0f0957742a41629"

#: This fee has been set to 0 on-chain as of 2026-05-22.
IPOR_BDUSD_DEPOSIT_FEE = 0.0


def test_internalised_fee_mode_preserves_explicit_deposit_fee():
    """Explicit deposit fees are investor-paid even if other fees are internalised.

    1. Create a FeeData with internalised_minting mode and a non-zero deposit fee
    2. Call get_net_fees() to compute investor-visible fees
    3. Verify management and performance are zeroed (internalised) but deposit fee survives
    """
    # 1. Create FeeData with a non-zero deposit fee (hardcoded, not tied to current on-chain state)
    deposit_fee = 0.008
    fee_data = FeeData(
        fee_mode=VaultFeeMode.internalised_minting,
        management=0.005,
        performance=0.0,
        deposit=deposit_fee,
        withdraw=0.0,
    )

    # 2. Compute investor-visible fees
    net_fees = fee_data.get_net_fees()

    # 3. Verify deposit fee survives while management/performance are zeroed
    assert net_fees.management == 0
    assert net_fees.performance == 0
    assert net_fees.deposit == deposit_fee
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
    """IPOR previewDeposit() returns shares net of the onboarding fee.

    1. Convert 1,000 denomination tokens to raw amount
    2. Compare convertToShares (gross) with previewDeposit (net)
    3. Verify implied fee matches the on-chain onboarding fee
    """
    # 1. Convert 1,000 denomination tokens to raw amount
    raw_assets = vault.denomination_token.convert_to_raw(Decimal(1_000))

    # 2. Compare convertToShares (gross) with previewDeposit (net)
    gross_shares = vault.vault_contract.functions.convertToShares(raw_assets).call()
    net_shares = vault.vault_contract.functions.previewDeposit(raw_assets).call()

    assert gross_shares > 0
    assert net_shares > 0
    assert net_shares <= gross_shares

    # 3. Verify implied fee matches the on-chain onboarding fee
    implied_fee = (gross_shares - net_shares) / gross_shares
    assert implied_fee == pytest.approx(IPOR_BDUSD_DEPOSIT_FEE, abs=0.001)


def test_ipor_deposit_permission_and_restricted_caller_preflight(web3: Web3):
    """Map IPOR AccessManager policy and reject its known private caller.

    The test deliberately uses a raw amount of one: admission must fail before
    the common manager checks the caller's token balance or allowance.
    """
    public_vault = IPORVault(web3, VaultSpec(chain_id=1, vault_address=IPOR_BDUSD_ETHEREUM))
    restricted_vault = IPORVault(web3, VaultSpec(chain_id=1, vault_address=IPOR_RESTRICTED_ETHEREUM))

    assert public_vault.is_whitelisted_deposit() is False
    assert public_vault.is_account_whitelisted(REPORT_CALLER) is True
    assert restricted_vault.is_whitelisted_deposit() is True
    assert restricted_vault.is_account_whitelisted(REPORT_CALLER) is False

    manager = restricted_vault.get_deposit_manager()
    assert isinstance(manager, IPORDepositManager)
    assert manager.can_create_deposit_request(REPORT_CALLER) is False

    with pytest.raises(VaultFlowUnavailable, match="does not allow immediate") as exc_info:
        manager.create_deposit_request(REPORT_CALLER, raw_amount=1)

    assert exc_info.value.function_selector == restricted_vault.get_deposit_function_selector()
    assert exc_info.value.access_delay == 0
