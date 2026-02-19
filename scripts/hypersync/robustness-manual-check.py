"""Run Hypersync clients in a loop until they fail.

- Track error rate
"""

import asyncio
import time

import hypersync
import datetime

from eth_defi.hypersync.server import HYPERSYNC_SERVES

TESTED_CHAINS = [
    8453,
    56,
    1,
]


def get_hypersync_block_height(
    client: hypersync.HypersyncClient,
) -> int:
    """Get the latest block known to Hypersync.

    Wrapped around the async function.
    """

    async def _hypersync_asyncio_wrapper():
        return await client.get_height()

    return asyncio.run(_hypersync_asyncio_wrapper())


def main():
    started = datetime.datetime.now(datetime.timezone.utc)

    while True:
        for chain in TESTED_CHAINS:
            client = hypersync.HypersyncClient(hypersync.ClientConfig(url=HYPERSYNC_SERVES[chain]))
            last_block = get_hypersync_block_height(client)
            print(f"Chain {chain} - last block {last_block}")

        time.sleep(10)


if __name__ == "__main__":
    main()
