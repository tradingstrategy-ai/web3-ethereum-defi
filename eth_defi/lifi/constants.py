"""LI.FI API configuration and constants."""

from decimal import Decimal


#: LI.FI REST API base URL
LIFI_API_URL = "https://li.quest/v1"

#: Environment variable name for the optional LI.FI API key
LIFI_API_KEY_ENV = "LIFI_API_KEY"

#: LI.FI convention for native token address (zero address)
LIFI_NATIVE_TOKEN_ADDRESS = "0x0000000000000000000000000000000000000000"

#: Default minimum gas balance in USD before triggering a top-up
DEFAULT_MIN_GAS_USD = Decimal("5")

#: Default amount of gas to bridge (in USD) when topping up
DEFAULT_TOP_UP_GAS_USD = Decimal("20")
