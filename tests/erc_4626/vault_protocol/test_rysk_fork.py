"""Provider-backed Rysk Premium vault characterisation tests."""

import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.rysk.vault import RyskVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.vault.flag import VaultFlag

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
RYSK_KPK_WETH_PUT = HexAddress("0x1195826418541cb3e80a22ef5736a6794393c91a")
USDC_DECIMALS = 6

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    # Co-locate every same-block Ethereum sharer on one xdist worker.
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Return Web3 backed by the shared fixed-block Ethereum fork.

    The characterisation is read-only, so the pooled Anvil process needs no
    snapshot/revert isolation between tests.

    :param anvil_fork_pool:
        Session-scoped fixed-block Anvil pool.
    :return:
        Web3 connected to Ethereum at :data:`ETHEREUM_MIDNIGHT_BLOCK`.
    """

    assert JSON_RPC_ETHEREUM is not None
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


def test_rysk_vault_reads_real_provider_state(web3: Web3) -> None:
    """Autodetect Rysk and read its share and collateral metadata on a fork.

    Exact assertions at the shared fixed block exercise onchain feature probes,
    ERC-20 metadata, collateral precision and the unsupported legal-fund/flow
    capabilities through the real deployed contracts.

    :param web3:
        Shared fixed-block Ethereum fork.
    :return:
        None.
    """

    vault = create_vault_instance_autodetect(web3, RYSK_KPK_WETH_PUT)

    assert isinstance(vault, RyskVault)
    assert vault.features == {
        ERC4626Feature.rysk_premium_like,
        ERC4626Feature.share_price_equivalence,
    }
    assert vault.get_protocol_name() == "Rysk"
    assert vault.name == "USDC-WETH-KPK-Put-Ethereum"
    assert vault.symbol == "USDC-KPK-WETH-P-ETH"
    assert vault.share_token.decimals == USDC_DECIMALS
    assert vault.denomination_token.symbol == "USDC"
    assert vault.denomination_token.decimals == USDC_DECIMALS
    assert vault.fetch_total_supply() == Decimal(0)
    assert vault.get_deposit_manager_capability() is None
    assert VaultFlag.tokenised_fund not in vault.get_flags()
    assert vault.get_strategy_tags() is None
