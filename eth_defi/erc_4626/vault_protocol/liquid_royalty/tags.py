"""Maintained strategy classifications for Liquid Royalty vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
    #: Vault: ALAR SailOut Royalty.
    #: Added: 2026-08-18.
    #: Decision material: The current vault listing names this product ALAR
    #: SailOut Royalty and identifies Liquid Royalty as its protocol. Liquid
    #: Royalty's current website exposes SAIL.r as a royalty token and refers
    #: to the next royalty airdrop, supporting the real-world royalty-stream
    #: classification.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/alar-sailout-royalty-2
    #: - https://www.liquidroyalty.com/vaults
    #: - https://berascan.com/address/0x09cea16a2563c2d7d807c86f5b8da760389b5915
    #: - eth_defi/erc_4626/vault_protocol/liquid_royalty/vault.py
    HexAddress("0x09cea16a2563c2d7d807c86f5b8da760389b5915"): {StrategyTag.rwa_royalties},
}
