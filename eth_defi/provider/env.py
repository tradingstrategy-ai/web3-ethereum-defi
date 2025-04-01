"""Get JSON-RPC URL from environment varbless."""
from eth_defi.chain import CHAIN_NAMES


def get_json_rpc_env(chain: int) -> str:
    """Get the JSON-RPC URL environment variable based on the chain id.

    - Map chain id to a name and from there to environment variables.
    """
    chain_name = CHAIN_NAMES.get(chain)
    assert chain_name, f"CHAIN_NAMES not configured for chain if {chain}"
    return f"JSON_RPC_{chain_name.upper()}"
