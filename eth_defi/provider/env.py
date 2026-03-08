"""Get JSON-RPC URL from environment variables.

To support multiple blockchains, we use a naming convention for chains:

- `JSON_RPC_ETHEREUM` for Ethereum Mainnet
- `JSON_RPC_ARBITRUM` for Arbitrum
- `JSON_RPC_OPTIMISM` for Optimism
- `JSON_RPC_ARBITRUM_SEPOLIA` for Arbitrum Sepolia
- `JSON_RPC_BASE_SEPOLIA` for Base Sepolia

All environment variables support multiple RPC providers per chain using a space-separated round robin mechanism.
See :ref:`multi rpc` for more details.

Example:

.. code-block:: bash

    export JSON_RPC_BINANCE="https://bsc-dataseed.bnbchain.org https://bsc-dataseed.ninicoin.io https://bnb.rpc.subquery.network/public"


For the chain names and more information see :py:mod:`eth_defi.chain`.
"""

import os

from eth_defi.chain import CHAIN_NAMES


def get_json_rpc_env(chain: int) -> str:
    """Get the JSON-RPC URL environment variable based on the chain id.

    - Map chain id to a name and from there to environment variables.
    """
    chain_name = CHAIN_NAMES.get(chain)
    assert chain_name, f"CHAIN_NAMES not configured for chain if {chain}"
    return f"JSON_RPC_{chain_name.upper()}"


def read_json_rpc_url(chain: int) -> str:
    """Read JSON-RPC URL from environment variable based on the chain id.

    :raises ValueError: If the environment variable is not set for the given chain.
    """
    assert type(chain) is int, f"Chain ID must be an integer: {type(chain)}"
    env_var = get_json_rpc_env(chain)
    json_rpc_url = os.environ.get(env_var)
    if not json_rpc_url:
        raise ValueError(f"Environment variable {env_var} is not set for chain {chain}")
    return json_rpc_url
