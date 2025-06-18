"""MEV blocking RPC provider functionality.

`Malicious Extractable Value (MEV) <https://tradingstrategy.ai/glossary/mev>`__
is a problem for all trading activity on EVM-based blockchains.

It can be mitigated by using a special
JSON-RPC node that provides a private mempool.

This module provides methods to create special
:py:class:`web3.Web3` instances that

- Use MEV blocking JSON-RPC endpoint for all transactions

- Normal JSON-RPC node for reading data from the blockchain

"""

from collections import Counter
from typing import Any

from web3 import Web3
from web3.providers import JSONBaseProvider
from web3.types import RPCEndpoint, RPCResponse

from eth_defi.provider.named import BaseNamedProvider

#: List of RPC methods that execution transactions
#:
TRANSACT_METHODS = (
    "eth_sendTransaction",
    "eth_sendRawTransaction",
)


class MEVBlockerProvider(BaseNamedProvider):
    """Routes methods that execute transaction through a special MEV proof endpoint.

    - Depending on whether we are sending a transaction or reading from the blockchain,
      switch between the JSON-RPC endpoint.

    - Route all outgoing transactions through a special MEV blocker endpoint
    """

    def __init__(
        self,
        call_provider: BaseNamedProvider,
        transact_provider: BaseNamedProvider,
        transact_methods=TRANSACT_METHODS,
    ):
        super().__init__()
        self.call_provider = call_provider
        self.transact_provider = transact_provider
        self.transact_methods = transact_methods

        #: Keep tabs on how much API traffic we generate through each endpoint
        self.provider_counter = Counter(
            {
                "call": 0,
                "transact": 0,
            }
        )

    def is_transact_method(self, method: RPCEndpoint) -> bool:
        """Does this RPC method do a transaction"""
        return method in self.transact_methods

    def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        if self.is_transact_method(method):
            self.provider_counter["transact"] += 1
            return self.transact_provider.make_request(method, params)
        else:
            self.provider_counter["call"] += 1
            return self.call_provider.make_request(method, params)

    @property
    def endpoint_uri(self) -> str:
        """Map us to the transact provider by the default"""
        return self.transact_provider.endpoint_uri

    @property
    def call_endpoint_uri(self) -> str:
        return self.call_provider.endpoint_uri


def get_mev_blocker_provider(web3: Web3) -> MEVBlockerProvider | None:
    provider = web3.provider
    if isinstance(provider, MEVBlockerProvider):
        return provider
    return None
