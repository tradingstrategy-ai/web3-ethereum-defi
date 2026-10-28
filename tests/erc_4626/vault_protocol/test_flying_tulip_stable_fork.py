"""Real-provider characterisation of Ethereum ssftUSD at a reviewed block.

The July canonical Ethereum fork predates this deployment. Reuse the shared
Anvil pool at a dedicated fixed post-deployment block instead of moving the
canonical block for unrelated protocols.
"""

import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_SSFTUSD_BY_CHAIN, FLYING_TULIP_SSFTUSD_NOTES
from eth_defi.erc_4626.vault_protocol.flying_tulip.vault import FlyingTulipStableVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.vault.base import normalise_vault_description, normalise_vault_short_description

#: Existing vault observation on 2026-10-20, after ssftUSD was deployed.
SSFTUSD_REVIEW_BLOCK = 26_234_307
JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed for the real-provider test"),
    pytest.mark.xdist_group("fork:ethereum:26234307"),
]


@pytest.fixture(scope="module")
def stable_web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Return the shared read-only post-deployment Ethereum fork.

    No test mutates state, so this pool member needs no snapshot resets.

    :param anvil_fork_pool:
        Session-wide fork and warm RPC-cache registry.
    :return:
        Web3 connection pinned to the ssftUSD review block.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, SSFTUSD_REVIEW_BLOCK)


def test_flying_tulip_stable_real_prices_and_scan_record(stable_web3: Web3) -> None:
    """Read real ssftUSD state and export complete, correctly classified metadata.

    Six-decimal assets and eighteen-decimal shares must produce the actual
    redeemable price, without external FT reward-equivalence metadata.

    :param stable_web3:
        Shared Ethereum fork with real archive-provider state.
    :return:
        ``None`` after checking fixed values and scanner output.
    """
    address = FLYING_TULIP_SSFTUSD_BY_CHAIN[1]
    vault = create_vault_instance_autodetect(stable_web3, address)
    assert isinstance(vault, FlyingTulipStableVault)
    assert vault.features == {ERC4626Feature.flying_tulip_stable_like}
    assert vault.denomination_token.decimals == 6
    assert vault.share_token.decimals == 18
    assert vault.denomination_token.symbol == "ftUSD"
    assert vault.share_token.symbol == "ssftUSD"
    assert vault.fetch_total_assets(SSFTUSD_REVIEW_BLOCK) == Decimal("411946.118918")
    assert vault.fetch_total_supply(SSFTUSD_REVIEW_BLOCK) == Decimal("409264.10240710532903198")
    assert vault.fetch_share_price(SSFTUSD_REVIEW_BLOCK) == Decimal("1.006553265959853966066145662")
    assert vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call() == 0
    assert vault.vault_contract.functions.maxDeposit(Web3.to_checksum_address("0x0000000000000000000000000000000000000001")).call() == 2**256 - 1
    assert int.from_bytes(stable_web3.eth.call({"to": address, "data": Web3.keccak(text="vestingPeriod()")[:4]}), "big") == 8 * 60 * 60
    assert vault.fetch_deposit_closed_reason() is None
    assert vault.get_flags() == set()
    assert vault.get_deposit_manager_capability() is None
    reader = vault.get_historical_reader(False)
    assert {call.extra_data["function"] for call in reader.construct_multicalls()} == {"total_assets", "total_supply", "convertToAssets"}
    detection = ERC4262VaultDetection(1, address, SSFTUSD_REVIEW_BLOCK, native_datetime_utc_now(), vault.features, native_datetime_utc_now(), 36, 0)
    row = create_vault_scan_record(stable_web3, detection, SSFTUSD_REVIEW_BLOCK, None)
    assert row["Protocol"] == "Flying Tulip"
    assert row["_description"] == normalise_vault_description(vault.description)
    assert row["_short_description"] == normalise_vault_short_description(vault.short_description)
    assert row["_notes"] == FLYING_TULIP_SSFTUSD_NOTES
    assert row["_deposit_closed_reason"] is None
    assert "_external_reward_token" not in row
    assert "_historical_share_price_type" not in row
