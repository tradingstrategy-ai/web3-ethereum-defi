"""dRPC specific Web3.py functionality.
"""

import logging
from typing import Any

from web3 import HTTPProvider
from web3._utils.request import get_response_from_post_request
from web3.types import RPCEndpoint, RPCResponse

logger = logging.getLogger(__name__)


class DRPCProvider(HTTPProvider):
    """Add header logging capabilities to dRPC provider."""

    def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        request_data = self.encode_rpc_request(method, params)
        raw_response = get_response_from_post_request(
            self.endpoint_uri,
            data=request_data,
            **self.get_request_kwargs(),
        )
        raw_response.raise_for_status()

        try:
            return self.decode_rpc_response(raw_response)
        except Exception as e:
            logger.error(
                "Unexpected decode RPC response error: %s, current provider ID is %s",
                str(e),
                raw_response.headers.get("x-drpc-trace-id"),
            )
            logger.exception(e, extra={"response_headers": raw_response.headers})
            raise
