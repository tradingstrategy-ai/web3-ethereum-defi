"""Reviewed Pallas vault deployment constants."""

import datetime

from eth_typing import HexAddress

#: HyperEVM / Hyperliquid chain id.
HYPERLIQUID_CHAIN_ID = 999

#: Pallas Basis Trading HIP-3 vault on HyperEVM.
#:
#: https://hyperevmscan.io/address/0x9b3aa83BD833123437d4efa656E7121B7F317899
PALLAS_BASIS_TRADING_HIP_3_VAULT = HexAddress("0x9b3aa83bd833123437d4efa656e7121b7f317899")

#: Pallas Directional Volatility vault on HyperEVM.
#:
#: https://hyperevmscan.io/address/0xa642188e1345AEe1809f6db5431464b079978c68
PALLAS_DIRECTIONAL_VOLATILITY_VAULT = HexAddress("0xa642188e1345aee1809f6db5431464b079978c68")

#: First HyperEVM block containing the Basis Trading HIP-3 proxy runtime bytecode.
PALLAS_BASIS_TRADING_HIP_3_FIRST_SEEN_AT_BLOCK = 30_253_028

#: Basis Trading HIP-3 proxy deployment timestamp, stored as naive UTC.
PALLAS_BASIS_TRADING_HIP_3_FIRST_SEEN_AT = datetime.datetime(2026, 3, 20, 12, 59, tzinfo=datetime.UTC).replace(tzinfo=None)

#: First HyperEVM block containing the Directional Volatility proxy runtime bytecode.
PALLAS_DIRECTIONAL_VOLATILITY_FIRST_SEEN_AT_BLOCK = 41_854_853

#: Directional Volatility proxy deployment timestamp, stored as naive UTC.
PALLAS_DIRECTIONAL_VOLATILITY_FIRST_SEEN_AT = datetime.datetime(2026, 7, 30, 15, 17, tzinfo=datetime.UTC).replace(tzinfo=None)

#: Reviewed Pallas vault deployments, keyed by chain to prevent cross-chain address collisions.
PALLAS_VAULTS_BY_CHAIN: frozenset[tuple[int, HexAddress]] = frozenset(
    {
        (HYPERLIQUID_CHAIN_ID, PALLAS_BASIS_TRADING_HIP_3_VAULT),
        (HYPERLIQUID_CHAIN_ID, PALLAS_DIRECTIONAL_VOLATILITY_VAULT),
    }
)

#: Address-only Pallas deployment index used to reject a reviewed address on another chain.
PALLAS_VAULT_ADDRESSES: frozenset[HexAddress] = frozenset(address for _, address in PALLAS_VAULTS_BY_CHAIN)

#: Pallas vault leads retained independently of historical ERC-4626 event discovery.
PALLAS_HARDCODED_LEADS = (
    (
        HYPERLIQUID_CHAIN_ID,
        PALLAS_BASIS_TRADING_HIP_3_VAULT,
        PALLAS_BASIS_TRADING_HIP_3_FIRST_SEEN_AT_BLOCK,
        PALLAS_BASIS_TRADING_HIP_3_FIRST_SEEN_AT,
    ),
    (
        HYPERLIQUID_CHAIN_ID,
        PALLAS_DIRECTIONAL_VOLATILITY_VAULT,
        PALLAS_DIRECTIONAL_VOLATILITY_FIRST_SEEN_AT_BLOCK,
        PALLAS_DIRECTIONAL_VOLATILITY_FIRST_SEEN_AT,
    ),
)

#: Direct Pallas application routes keyed by reviewed vault address.
PALLAS_VAULT_LINKS: dict[str, str] = {
    PALLAS_BASIS_TRADING_HIP_3_VAULT.lower(): "https://app.pallas.fund/vault/basis-trading-hip-3",
    PALLAS_DIRECTIONAL_VOLATILITY_VAULT.lower(): "https://app.pallas.fund/vault/directional-volatility",
}
