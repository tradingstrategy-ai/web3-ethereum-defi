"""Constants for the Pacifica native vault integration."""

#: Synthetic dataset namespace for Pacifica/Solana native vaults.  It is not
#: an EVM chain ID and must never be supplied to an EVM JSON-RPC provider.
PACIFICA_CHAIN_ID = 9994

#: Public Pacifica mainnet REST API used by the read-only vault collector.
PACIFICA_API_URL = "https://api.pacifica.fi/api/v1"
