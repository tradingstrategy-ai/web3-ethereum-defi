"""Constants and address resolution helpers for GMX fork testing.

.. warning::

    This module needs a rewrite. It was mechanically extracted from a
    monolithic file and carries legacy patterns (hardcoded Arbitrum
    fallbacks, duplicated resolution logic).
"""

import logging

from eth_utils import to_checksum_address

from eth_defi.gmx.contracts import get_contract_addresses, get_token_address_normalized

logger = logging.getLogger(__name__)


def resolve_token_address(chain: str, symbol: str, fallback: str) -> str:
    """Return checksum token address for *chain* with safe fallback."""
    try:
        resolved = get_token_address_normalized(chain, symbol)
        if resolved:
            return to_checksum_address(resolved)
    except Exception as e:
        logger.debug("Failed to resolve %s on %s: %s", symbol, chain, e)
    return to_checksum_address(fallback)


def resolve_contract_address(chain: str, attr: str | tuple[str, ...], fallback: str) -> str:
    """Return checksum contract address from GMX registry with safe fallback."""
    try:
        addresses = get_contract_addresses(chain)
        attr_names = (attr,) if isinstance(attr, str) else attr
        for name in attr_names:
            resolved = getattr(addresses, name, None)
            if resolved:
                return to_checksum_address(resolved)
    except Exception as e:
        logger.debug("Failed to resolve %s for %s: %s", attr, chain, e)
    return to_checksum_address(fallback)


#: Known-good Arbitrum mainnet fallbacks to avoid brittle hardcoding elsewhere.
ARBITRUM_DEFAULTS = {
    "chainlink_provider": "0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD",
    "order_handler": "0x04315E233C1c6FfA61080B76E29d5e8a1f7B4A35",
    "role_store": "0x3c3d99FD298f679DBC2CEcd132b4eC4d0F5e6e72",
    "weth": resolve_token_address("arbitrum", "WETH", "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"),
    "usdc": resolve_token_address("arbitrum", "USDC", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"),
}
