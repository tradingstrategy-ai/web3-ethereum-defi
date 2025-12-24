"""Look token cache for bad entries and attempt to heal them."""

import os

from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache


def main():
    # json_rpc_url = os.environ.get("JSON_RPC_URL")
    # assert json_rpc_url, "JSON_RPC_URL environment variable is required"
    # web3 = create_multi_provider_web3(json_rpc_url)

    cache = TokenDiskCache()

    bad_entries = []

    for spec, cache_data in cache.items():
        chain, address = spec.split("-")
        chain = int(chain)
        decimals = cache_data.get("decimals")
        if decimals == 99:
            print(f"Bad token cache entry found for {chain}: {address}")
            bad_entries.append(spec)


if __name__ == "__main__":
    main()
