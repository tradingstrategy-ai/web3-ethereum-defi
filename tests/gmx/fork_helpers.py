"""Fork testing helpers for GMX — backward-compatible re-exports.

All functionality has moved to :mod:`eth_defi.gmx.testing`.
This module re-exports everything so that existing test imports continue to work.
"""

from eth_defi.gmx.testing import (  # noqa: F401
    ARBITRUM_DEFAULTS,
    deal_eth,
    deal_tokens,
    detect_provider_type,
    execute_order_and_get_result,
    execute_order_as_keeper,
    extract_order_key_from_receipt,
    fetch_on_chain_oracle_prices,
    get_mock_oracle_price,
    impersonate_account,
    mine_block,
    set_balance,
    set_code,
    set_next_block_timestamp,
    setup_mock_oracle,
    stop_impersonating_account,
)
from eth_defi.gmx.testing.constants import (  # noqa: F401
    _arbitrum_token,
    _resolve_contract_address,
    _resolve_token_address,
)
