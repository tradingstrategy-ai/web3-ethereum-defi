"""Helper methods to extract the URL endpoint and name of a provider."""

from abc import abstractproperty, ABC, abstractmethod
from typing import TypeAlias

from web3 import HTTPProvider
from web3.providers import JSONBaseProvider

from eth_defi.utils import get_url_domain


class BaseNamedProvider(ABC, JSONBaseProvider):
    """A base class for getting a JSON-RPC provider name and URL."""

    @property
    @abstractmethod
    def endpoint_uri(self) -> str:
        """Return the active node URI endpoint.

        For :py:class:`HTTPProvider` compatibility.
        """


#: Named providers including web3.py core providers
NamedProvider: TypeAlias = BaseNamedProvider | HTTPProvider


def get_provider_name(provider: NamedProvider) -> str:
    """Get loggable name of the JSON-RPC provider.

    Supports :py:class:`HTTPProvider` and others
    that leave outside our Python package.

    Strips out API keys from the URL of a JSON-RPC API provider.

    :return:
        HTTP provider URL's domain name if available.

        Assume any API keys are not part of the domain name.
    """
    if isinstance(provider, HTTPProvider):
        return get_url_domain(provider.endpoint_uri)
    return str(provider)
