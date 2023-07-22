"""MEV blocking RPC provider functionality.

Malicious Extractable Value (MEV) is a nuisance on all
EVM-based blockchains. It can be mitigated by using a special
JSON-RPC node that provides a private mempool.
This module provides methods to create special
:py:class:`web3.Web3` instances that use MEV blocking
JSON-RPC endpoint for all transactions, but a normal JSON-RPC
node for reading data from the blockchain.

"""
from collections import Counter
from typing import Any

from web3.providers import JSONBaseProvider
from web3.types import RPCEndpoint, RPCResponse

#: List of RPC methods that execution transactions
#:
TRANSACT_METHODS = (
    "eth_sendTransaction",
    "eth_sendRawTransaction",
)


class MEVBlockerProvider(JSONBaseProvider):
    """Routes methods that execute transaction through a special MEV proof endpoint.

    - Depending on whether we are sending a transaction or reading from the blockchain,
      switch between the JSON-RPC endpoint.

    - Route all outgoing transactions through a special MEV blocker endpoint
    """

    def __init__(
            self,
            call_provider: JSONBaseProvider,
            transact_provivder: JSONBaseProvider,
            transact_methods=TRANSACT_METHODS,
    ):
        super().__init__()
        self.call_provider = call_provider
        self.transact_provider = transact_provivder
        self.transact_methods = transact_methods

        #: Keep tabs on how much API traffic we generate through each endpoint
        self.provider_counter = Counter({
            "call": 0,
            "transact": 0,
        })

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
