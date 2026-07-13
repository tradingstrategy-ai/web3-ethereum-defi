"""Known KiloEx Hybrid Vault deployment details."""

from eth_typing import HexAddress

KILOEX_EARN_URL = "https://app.kiloex.io/earn/"

#: Known KiloEx Hybrid Vault deployments.
#:
#: KiloEx uses a Gains-compatible contract interface, including
#: ``maxDiscountP()``, so detection must remain address-based. The deployments
#: were manually checked against the KiloEx Earn application on 2026-07-13.
KILOEX_VAULTS_BY_CHAIN: frozenset[tuple[int, HexAddress]] = frozenset(
    {
        # kREX, kBOX, and kUSDT on BNB Smart Chain.
        (56, HexAddress("0xa40e085d0584eed39daaa077fcc4cd153ae9a5b0")),
        (56, HexAddress("0x6e7a6eb5feec64bf6401a744757aba89c5c7e813")),
        (56, HexAddress("0x1c3f35f7883fc4ea8c4bca1507144dc6087ad0fb")),
        # kUSDC on Base.
        (8453, HexAddress("0x43e3e6ffb2e363e64cd480cbb7cd0cf47bc6b477")),
    }
)

#: Address-only index used to reject a known address on the wrong chain.
KILOEX_VAULT_ADDRESSES = frozenset(address for _, address in KILOEX_VAULTS_BY_CHAIN)

#: Native KiloEx Earn application pages for known vault deployments.
KILOEX_VAULT_LINK_MATRIX: dict[tuple[int, str], str] = {
    (chain_id, address.lower()): f"{KILOEX_EARN_URL}chain/{chain_name}/"
    for chain_id, address, chain_name in (
        (56, "0xa40e085d0584eed39daaa077fcc4cd153ae9a5b0", "BNB"),
        (56, "0x6e7a6eb5feec64bf6401a744757aba89c5c7e813", "BNB"),
        (56, "0x1c3f35f7883fc4ea8c4bca1507144dc6087ad0fb", "BNB"),
        (8453, "0x43e3e6ffb2e363e64cd480cbb7cd0cf47bc6b477", "Base"),
    )
}
