"""Get JSON-RPC URL from environment varbless."""

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
