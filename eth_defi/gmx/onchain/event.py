"""GMX onchain event reader.

- GMX uses a special contract called EventEmitter to emit logs
- GMX has its own topic structure on the top of Solidity's topic structure
- Here we have utilities to lift off this data directly onchain using HyperSync

See

- `EventEmitter source <https://github.com/gmx-io/gmx-synthetics/blob/e9c918135065001d44f24a2a329226cf62c55284/contracts/event/EventEmitter.sol>`__
- `EventUtils for packing data into the logs <https://github.com/gmx-io/gmx-synthetics/blob/e9c918135065001d44f24a2a329226cf62c55284/contracts/event/EventUtils.sol>`__

"""

from hypersync import HypersyncClient, ClientConfig
from hypersync import BlockField


def query_gmx_events(
    client: HypersyncClient,
    gmx_signature: bytes,
):
    """Query GMX events emitted by EventEmitter from HyperSync client."""


