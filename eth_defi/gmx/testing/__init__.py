"""Fork testing helpers for GMX.

Supports both Anvil and Tenderly for mocking oracles and impersonating keepers.

Submodules
----------

- :mod:`~eth_defi.gmx.testing.constants` -- address constants and resolution helpers
- :mod:`~eth_defi.gmx.testing.fork_provider` -- provider detection and RPC helpers
- :mod:`~eth_defi.gmx.testing.oracle` -- mock oracle setup and price queries
- :mod:`~eth_defi.gmx.testing.keeper` -- keeper impersonation and order execution
"""

from eth_defi.gmx.testing.constants import (
    ARBITRUM_DEFAULTS,
)
from eth_defi.gmx.testing.fork_provider import (
    deal_eth,
    deal_tokens,
    detect_provider_type,
    impersonate_account,
    mine_block,
    set_balance,
    set_code,
    set_next_block_timestamp,
    stop_impersonating_account,
)
from eth_defi.gmx.testing.keeper import (
    execute_order_and_get_result,
    execute_order_as_keeper,
    extract_order_key_from_receipt,
)
from eth_defi.gmx.testing.oracle import (
    fetch_on_chain_oracle_prices,
    get_mock_oracle_price,
    setup_mock_oracle,
)

__all__ = [
    "ARBITRUM_DEFAULTS",
    "deal_eth",
    "deal_tokens",
    "detect_provider_type",
    "execute_order_and_get_result",
    "execute_order_as_keeper",
    "extract_order_key_from_receipt",
    "fetch_on_chain_oracle_prices",
    "get_mock_oracle_price",
    "impersonate_account",
    "mine_block",
    "set_balance",
    "set_code",
    "set_next_block_timestamp",
    "setup_mock_oracle",
    "stop_impersonating_account",
]
