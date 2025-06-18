"""Helper methods to extract the URL endpoint and name of a provider.

See also

- :py:mod:`eth_defi.provider.mev_blocker`

- :py:mod:`eth_defi.provider.fallback`

"""

from abc import abstractproperty, ABC, abstractmethod
from typing import TypeAlias

from web3 import HTTPProvider
from web3.providers import JSONBaseProvider, BaseProvider

from eth_defi.utils import get_url_domain


class BaseNamedProvider(ABC, JSONBaseProvider):
    """A base class for getting a JSON-RPC provider name and URL."""

    @property
    @abstractmethod
    def endpoint_uri(self) -> str:
        """Return the active node URI endpoint.

        .. warning::

            Endpoint URIs often contain API keys.
            They should be never publicly displayed as is.

        """

    @property
    def call_endpoint_uri(self) -> str:
        """Return the active node URI where call JSON-RPCs go.

        .. warning::

            Endpoint URIs often contain API keys.
            They should be never publicly displayed as is.

        """
        return self.endpoint_uri


#: Named providers including web3.py core providers
NamedProvider: TypeAlias = BaseNamedProvider | HTTPProvider


def get_provider_name(provider: BaseProvider) -> str:
    """Get loggable name of the JSON-RPC provider.

    Strips out API keys from the URL of a JSON-RPC API provider.

    Supports :py:mod:`eth_defi` customer provider classes
    as well as :py:mod:`web3` core providers.

    Example:

    .. code-block:: python

        print(get_provider_name(web3.provider))

    :return:
        HTTP provider URL's domain name if available.

        Assume any API keys are not part of the domain name.
    """

    from eth_defi.provider.fallback import FallbackProvider

    if isinstance(provider, FallbackProvider):
        return "fallbacks " + ", ".join(get_provider_name(p) for p in provider.providers)
    elif hasattr(provider, "endpoint_uri"):
        return get_url_domain(provider.endpoint_uri)
    return str(provider)
