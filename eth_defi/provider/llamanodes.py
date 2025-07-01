"""LlamaNodes specific Python code.

- `LlamaNodes <https://llamanodes.com/>`__ runs RPC services at ``llamarpc.com``

- Their RPC nodes have some compatibility issues we address in this module

See also :py:mod:`eth_defi.provider.broken_provider`.
"""

from requests import Response


def is_llama_bad_grapql_reply(resp: Response):
    """Is the web server response fake 404 response from llamarpc.com

    llamarpc.com web server does not know how to use HTTP 404 status code.

    See :py:func:`eth_defi.chain.has_graphql_support`.
    """
    try:
        content = resp.json()
        return content.get("error").get("message") == "UserKey was not a ULID or UUID"
    except Exception:
        return False
