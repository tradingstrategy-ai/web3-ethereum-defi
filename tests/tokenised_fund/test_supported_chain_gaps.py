"""Regression coverage for supported-chain tokenised-fund gap repairs."""

from types import SimpleNamespace

import pytest

from eth_defi.erc_4626.classification import create_vault_instance, identify_vault_features
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.scan import OPTIONAL_READ_EXCEPTIONS
from eth_defi.midas.constants import MIDAS_MBASIS_ETHEREUM, MIDAS_MTBILL_ETHEREUM
from eth_defi.midas.vault import MidasVault
from eth_defi.tokenised_fund.fdit.constants import FDIT_ETHEREUM, FDIT_HARDCODED_LEADS
from eth_defi.tokenised_fund.fdit.vault import FditVault
from eth_defi.tokenised_fund.kaio.constants import CASHX_ETHEREUM, KAIO_HARDCODED_LEADS
from eth_defi.tokenised_fund.kaio.vault import KaioVault
from eth_defi.tokenised_fund.libeara.constants import LIBEARA_ULTRA_ARBITRUM, LIBEARA_ULTRA_ETHEREUM
from eth_defi.tokenised_fund.openeden.constants import OPENEDEN_TBILL_ADDRESS, OPENEDEN_TBILL_DENOMINATION_TOKEN_ADDRESS
from eth_defi.tokenised_fund.openeden.vault import OpenEdenVault
from eth_defi.tokenised_fund.sygnum.constants import FILQ_D_ETHEREUM_ADDRESS, SYGNUM_HARDCODED_LEADS
from eth_defi.vault.flag import VaultFlag


def test_midas_marks_only_mtbill_as_a_tokenised_fund() -> None:
    """Keep Midas's product-level classification narrow."""

    assert MIDAS_MTBILL_ETHEREUM.is_tokenised_fund
    assert not MIDAS_MBASIS_ETHEREUM.is_tokenised_fund
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    mtbill = create_vault_instance(web3, MIDAS_MTBILL_ETHEREUM.token, features={ERC4626Feature.midas_like})
    mbasis = create_vault_instance(web3, MIDAS_MBASIS_ETHEREUM.token, features={ERC4626Feature.midas_like})
    assert isinstance(mtbill, MidasVault)
    assert isinstance(mbasis, MidasVault)
    assert VaultFlag.tokenised_fund in mtbill.get_flags()
    assert VaultFlag.tokenised_fund not in mbasis.get_flags()


def test_missing_erc20_fund_products_have_chain_scoped_adapters() -> None:
    """Route FDIT, CASHx and TBILL without treating them as ERC-4626 vaults."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    cases = (
        (FDIT_ETHEREUM.token, ERC4626Feature.fdit_like, FditVault, "Fidelity FDIT"),
        (CASHX_ETHEREUM.token, ERC4626Feature.kaio_like, KaioVault, "KAIO"),
        (OPENEDEN_TBILL_ADDRESS, ERC4626Feature.openeden_like, OpenEdenVault, "OpenEden"),
    )
    for address, feature, vault_class, protocol_name in cases:
        assert identify_vault_features(address, {}, "ignored", chain_id=1) == {feature}
        assert isinstance(create_vault_instance(web3, address, features={feature}), vault_class)
        assert get_vault_protocol_name({feature}) == protocol_name

    openeden = create_vault_instance(web3, OPENEDEN_TBILL_ADDRESS, features={ERC4626Feature.openeden_like})
    assert openeden.fetch_denomination_token_address().lower() == OPENEDEN_TBILL_DENOMINATION_TOKEN_ADDRESS


def test_new_chain_scoped_leads_keep_separate_share_classes() -> None:
    """Register distinct FILQ and ULTRA representations independently."""

    assert any(lead[1] == FILQ_D_ETHEREUM_ADDRESS for lead in SYGNUM_HARDCODED_LEADS)
    assert LIBEARA_ULTRA_ETHEREUM.chain_id != LIBEARA_ULTRA_ARBITRUM.chain_id
    assert LIBEARA_ULTRA_ETHEREUM.token != LIBEARA_ULTRA_ARBITRUM.token
    assert FDIT_HARDCODED_LEADS[0][1] == FDIT_ETHEREUM.token
    assert KAIO_HARDCODED_LEADS[0][1] == CASHX_ETHEREUM.token


def test_supply_only_funds_expose_unavailable_nav_as_optional() -> None:
    """Keep FDIT metadata valid while FILQ exposes its reviewed NAV source."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    fdit = create_vault_instance(web3, FDIT_ETHEREUM.token, features={ERC4626Feature.fdit_like})
    filq = create_vault_instance(web3, FILQ_D_ETHEREUM_ADDRESS, features={ERC4626Feature.sygnum_like})

    with pytest.raises(NotImplementedError):
        fdit.fetch_nav()
    with pytest.raises(NotImplementedError):
        fdit.fetch_total_assets()

    fdit_info = fdit.fetch_info()
    fdit_scan_data = fdit.fetch_scan_record_extra_data()
    assert fdit_info["denomination_token"] is None
    assert fdit_info["synthetic_usd_denomination"] is False
    assert fdit_scan_data["Denomination"] is None
    assert fdit_scan_data["_denomination_token"] is None

    filq_info = filq.fetch_info()
    assert filq_info["nav_available"] is True
    assert filq_info["nav_source"] == "chainlink_bundle_aggregator"

    assert NotImplementedError in OPTIONAL_READ_EXCEPTIONS
