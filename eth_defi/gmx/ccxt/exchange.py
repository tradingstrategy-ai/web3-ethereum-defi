from eth_defi.ccxt.exchange_compatible import ExchangeCompatible
from eth_defi.gmx.ccxt.wrapper import GMXCCXTWrapper
from eth_defi.gmx.ccxt.properties import describe_gmx
from eth_defi.gmx.config import GMXConfig


class GMX(GMXCCXTWrapper, ExchangeCompatible):
    """CCXT-compatible exchange adapter for GMX.

    This class provides a clean interface for GMX CCXT integration,
    inheriting all CCXT method implementations from GMXCCXTWrapper
    while properly integrating with CCXT's Exchange base class via
    ExchangeCompatible.
    """

    def __init__(self, config: GMXConfig, subsquid_endpoint: str | None = None, **kwargs):
        """Initialize GMX exchange adapter.

        :param config: GMX configuration object
        :type config: GMXConfig
        :param subsquid_endpoint: Optional Subsquid GraphQL endpoint URL
        :type subsquid_endpoint: str | None
        """
        # Cooperative inheritance: calls both parent __init__ methods
        super().__init__(config=config, subsquid_endpoint=subsquid_endpoint, **kwargs)

    def describe(self):
        """Get CCXT exchange description."""
        return describe_gmx()
