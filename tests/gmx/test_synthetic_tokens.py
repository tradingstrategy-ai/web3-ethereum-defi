"""GMX synthetic token supported chain regression tests.

:func:`eth_defi.gmx.synthetic_tokens.get_supported_gmx_chains` referenced the
``GMX_API_ENDPOINTS`` constant after it was removed in favour of the shared
:data:`eth_defi.gmx.constants.GMX_API_URLS` endpoints, so every call raised
``NameError``. These tests need no RPC or network access.
"""

from eth_defi.chain import get_chain_name
from eth_defi.gmx.constants import GMX_API_URLS
from eth_defi.gmx.synthetic_tokens import get_supported_gmx_chains


def test_get_supported_gmx_chains() -> None:
    """Supported GMX chains are returned as chain IDs.

    1. Call the previously broken function
    2. Check Arbitrum, Avalanche and Arbitrum Sepolia chain IDs are returned
    """
    # 1. Call the previously broken function
    chain_ids = get_supported_gmx_chains()

    # 2. Check Arbitrum, Avalanche and Arbitrum Sepolia chain IDs are returned
    assert chain_ids == [42161, 43114, 421614]


def test_get_supported_gmx_chains_round_trip() -> None:
    """Every supported chain ID resolves to a configured GMX API endpoint.

    1. Translate each supported chain ID back to a name the way ``fetch_gmx_synthetic_tokens()`` does
    2. Check every configured GMX API chain is covered exactly once
    """
    # 1. Translate each supported chain ID back to a name the way fetch_gmx_synthetic_tokens() does
    chain_names = [get_chain_name(chain_id).lower() for chain_id in get_supported_gmx_chains()]

    # 2. Check every configured GMX API chain is covered exactly once
    assert sorted(chain_names) == sorted(GMX_API_URLS.keys())
