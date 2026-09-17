"""Real HTTP integration coverage for the Rysk Premium pool catalogue."""

import os

import pytest

from eth_defi.erc_4626.vault_protocol.rysk.api import fetch_rysk_premium_pools, is_rysk_premium_test_pool

RUN_RYSK_API_INTEGRATION = os.environ.get("RUN_RYSK_API_INTEGRATION", "false").lower() == "true"

pytestmark = pytest.mark.skipif(
    not RUN_RYSK_API_INTEGRATION,
    reason="Set RUN_RYSK_API_INTEGRATION=true to call the live unauthenticated Rysk Premium catalogue",
)


def test_fetch_live_rysk_premium_catalogue() -> None:
    """Fetch and validate at least one current public Rysk product.

    :return:
        None.
    """

    pools = fetch_rysk_premium_pools()

    assert pools
    assert all(pool.chain_id in {1, 999} for pool in pools)
    assert all(pool.option_type in {"call", "put"} for pool in pools)
    assert any(not is_rysk_premium_test_pool(pool) for pool in pools)
