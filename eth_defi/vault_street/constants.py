"""Vault Street production contract metadata."""

import datetime

from eth_typing import HexAddress

#: Ethereum mainnet chain id.
VAULT_STREET_CHAIN_ID = 1

#: Vault Street's permissioned, yield-bearing USD product token.
#:
#: https://etherscan.io/token/0x7ea76108975ec0998b9bc2db04b4eca986400dd7
PRIME_USD_ADDRESS = HexAddress("0x7ea76108975ec0998b9bc2db04b4eca986400dd7")

#: Vault Street ``PriceStorage`` contract exposing ``getPrice()``.
#:
#: https://etherscan.io/address/0x8cda03e2004c35e07963fb792c6b7511dabee369
PRIME_USD_PRICE_ORACLE_ADDRESS = HexAddress("0x8cda03e2004c35e07963fb792c6b7511dabee369")

#: Vault Street request manager for permissioned USDC deposits and redemptions.
#:
#: https://etherscan.io/address/0x8c14b6e8ec9968cd9c69eedea4a1295aec5e5d6e
PRIME_USD_REQUEST_MANAGER_ADDRESS = HexAddress("0x8c14b6e8ec9968cd9c69eedea4a1295aec5e5d6e")

#: Native USDC token on Ethereum mainnet, the primeUSD denomination token.
PRIME_USD_DENOMINATION_TOKEN_ADDRESS = HexAddress("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")

#: ``PriceStorage.getPrice()`` fixed-point decimal divisor.
PRIME_USD_PRICE_DECIMALS = 8

#: Scanner label for the on-chain primeUSD NAV source.
VAULT_STREET_NAV_SOURCE = "vault_street_price_storage_getPrice"

#: First Ethereum block containing primeUSD bytecode.
PRIME_USD_FIRST_SEEN_AT_BLOCK = 25_293_536

#: Timestamp of :py:data:`PRIME_USD_FIRST_SEEN_AT_BLOCK`, stored as naive UTC.
PRIME_USD_FIRST_SEEN_AT = datetime.datetime(2026, 6, 11, 10, 11, 47, tzinfo=datetime.UTC).replace(tzinfo=None)

#: Hardcoded discovery lead for the non-ERC-4626 primeUSD product.
VAULT_STREET_HARDCODED_LEADS = (
    (
        VAULT_STREET_CHAIN_ID,
        PRIME_USD_ADDRESS,
        PRIME_USD_FIRST_SEEN_AT_BLOCK,
        PRIME_USD_FIRST_SEEN_AT,
    ),
)
